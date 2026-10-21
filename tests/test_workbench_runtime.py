"""lx 执行侧接入：真实 HTTP 适配、冻结产物校验、操作串行化与故障回传。"""
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
import requests

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO/'src'))
from agent import web
from agent.runtime_control import LXRuntime, RuntimeControlError, deployment_preflight
from agent.scene_gen import SceneSpecGenerator
from pipeline import serve


@pytest.fixture
def lx(tmp_path, monkeypatch):
    (tmp_path/'workspace').mkdir()
    monkeypatch.setattr(serve, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(serve, 'OPERATION_LOCK', threading.Lock())
    monkeypatch.setattr(serve, 'LAST_DEPLOYMENT', None)
    calls=[]
    class Client:
        session=SimpleNamespace(close=lambda:None)
        def status(self):return 'STOPPED'
        def start(self):calls.append('start');return 'RUNNING'
        def stop(self):calls.append('stop');return 'STOPPED'
        def runtime_logs(self):calls.append('logs');return 'scan=24\nPLC running'
    monkeypatch.setattr(serve, 'runtime_client', Client)
    monkeypatch.setattr(serve, 'connect', lambda **kw: (_ for _ in ()).throw(ConnectionError('offline')))
    server=serve.ThreadingHTTPServer(('127.0.0.1',0),serve.DeployHandler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setenv('DEPLOY_URL','http://127.0.0.1:'+str(server.server_port)+'/deploy')
    yield LXRuntime(),calls,tmp_path
    server.shutdown();server.server_close();thread.join(2)


@pytest.fixture
def delivery(tmp_path):
    app=web.SVGWorkbench(tmp_path/'app',aml=REPO/'examples/aml/plotter3axis_station.aml')
    state=app.create_session();stored=app._session(state['id'])
    task='svg_web_'+'e'*32
    directory=app.root/task;directory.mkdir();(directory/'final').mkdir()
    app._snapshot_assets(stored,directory)
    spec=json.loads((REPO/'examples/specs/plotter3axis.spec.json').read_text(encoding='utf-8'))
    scene=SceneSpecGenerator().generate(spec, stored['device_model'])['scene']
    (directory/'request.json').write_text(json.dumps(spec),encoding='utf-8')
    (directory/'final/scene.spec.json').write_text(json.dumps(scene),encoding='utf-8')
    shutil.copyfile(REPO/'src/plc/plotter3axis.xml',directory/'final/plcopen.xml')
    stored['jobs']=[{'id':task,'revision':0,'status':'final','files':[]}]
    yield app,state['id'],task,directory
    app.close()


def finished(app, ident):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        state=app.state(ident)
        if not state['busy']:return state
        time.sleep(.01)
    pytest.fail('Runtime worker did not finish')


def test_lx_control_and_logs_reuse_client(lx):
    adapter,calls,_=lx
    assert adapter.status()['capabilities']==['deploy','start','stop','logs']
    assert adapter.action('start')['runtime']=='RUNNING'
    assert adapter.action('stop')['runtime']=='STOPPED'
    assert 'scan=24' in adapter.logs()['logs']
    assert calls==['start','stop','logs']


def test_lx_busy_and_cross_origin_cannot_mutate(lx):
    adapter,calls,_=lx
    with serve.OPERATION_LOCK, pytest.raises(RuntimeControlError,match='正在执行'):
        adapter.action('stop')
    with pytest.raises(RuntimeControlError,match='本地工作台'):
        adapter.request('POST','/runtime/start',headers={'Origin':'http://other.invalid'},timeout=2)
    assert not calls


def test_lx_unexpected_state_is_failure(lx,monkeypatch):
    adapter,_,_=lx
    client=serve.runtime_client();client.start=lambda:'STOPPED'
    monkeypatch.setattr(serve,'runtime_client',lambda:client)
    with pytest.raises(RuntimeControlError,match='RUNNING'):adapter.action('start')


def test_status_and_logs_do_not_interrupt_runtime_operation(lx,monkeypatch):
    adapter,_,_=lx
    monkeypatch.setattr(serve,'runtime_client',lambda:pytest.fail('must not query Runtime during deployment'))
    with serve.OPERATION_LOCK:
        assert adapter.status()['runtime']['status']=='BUSY'
        with pytest.raises(RuntimeControlError,match='其他操作'):adapter.logs()


def test_control_waits_for_status_query_instead_of_rejecting_it(lx,monkeypatch):
    adapter,calls,_=lx
    entered=threading.Event();release=threading.Event()
    client=serve.runtime_client()
    def status():
        entered.set()
        assert release.wait(5)
        return 'STOPPED'
    client.status=status
    monkeypatch.setattr(serve,'runtime_client',lambda:client)
    with ThreadPoolExecutor(2) as pool:
        reading=pool.submit(adapter.status)
        try:
            assert entered.wait(3)
            starting=pool.submit(adapter.action,'start')
            deadline=time.monotonic()+3
            while not serve.OPERATION_LOCK.locked() and time.monotonic()<deadline:time.sleep(.01)
            assert serve.OPERATION_LOCK.locked() and not starting.done()
            assert adapter.status()['runtime']['status']=='BUSY'
        finally:release.set()
        assert reading.result(timeout=3)['runtime']['status']=='STOPPED'
        assert starting.result(timeout=3)['runtime']=='RUNNING' and calls==['start']


@pytest.mark.parametrize('initial', ['RUNNING','STOPPED','UNKNOWN','timeout'])
def test_pipeline_stops_before_upload_and_records_http_failure(delivery,tmp_path,monkeypatch,initial):
    from pipeline import run_deploy
    _,_,_,directory=delivery
    work=tmp_path/'deployment';work.mkdir()
    monkeypatch.setattr(run_deploy,'WORKSPACE',work)
    monkeypatch.setattr(run_deploy,'RESULT_JSON',work/'deploy_result.json')
    monkeypatch.setattr(sys,'argv',['run_deploy.py','--xml',str(directory/'final/plcopen.xml')])
    calls=[]
    class Client:
        session=SimpleNamespace(close=lambda:calls.append('close'))
        def login(self):calls.append('login')
        def status(self):return 'RUNNING' if initial=='timeout' else initial
        def stop(self):calls.append('stop');return 'STOPPED'
        def upload_and_compile(self,text,name):
            calls.append('upload')
            if initial=='timeout':raise requests.ReadTimeout('upload timed out')
            return '42.st'
        def wait_compilation(self):calls.append('compile');return True,''
        def start(self):calls.append('start');return 'RUNNING'
    monkeypatch.setattr(run_deploy,'OpenPLCClient',lambda **kw:Client())
    code=run_deploy.main()
    record=json.loads((work/'deploy_result.json').read_text(encoding='utf-8'))
    assert calls[-1]=='close'
    if initial=='UNKNOWN':
        assert code==1 and calls==['login','close'] and record['status']=='FAILED'
    elif initial=='timeout':
        assert code==1 and record['status']=='FAILED'
        assert record['steps'][-1]['step']=='runtime HTTP request'
        assert record['steps'][0]['ok'] and 'upload timed out' in record['errors'][0]
    else:
        assert code==0 and record['status']=='OK'
        assert calls==['login']+(['stop'] if initial=='RUNNING' else [])+['upload','compile','start','close']


@pytest.mark.parametrize('status', [200,404])
def test_runtime_log_route_matches_installed_openplc(monkeypatch,status):
    from pipeline.openplc_client import OpenPLCClient,OpenPLCError
    client=OpenPLCClient();calls=[]
    def request(method,path):
        calls.append((method,path))
        return SimpleNamespace(status_code=status,text='PLC log')
    monkeypatch.setattr(client,'_request',request)
    try:
        if status==200:assert client.runtime_logs()=='PLC log'
        else:
            with pytest.raises(OpenPLCError,match='404'):client.runtime_logs()
        assert calls==[('GET','/runtime_logs')]
    finally:client.session.close()


def test_deploy_cannot_return_stale_result_and_preserves_failure_steps(lx,monkeypatch):
    adapter,_,root=lx
    result=root/'workspace/deploy_result.json'
    result.write_text('{"status":"OK"}')
    monkeypatch.setattr(serve.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=1,stdout=b'failed'))
    with pytest.raises(RuntimeControlError,match='未产出'):adapter.action('deploy',b'<?xml version="1.0"?><project/>')
    assert not result.exists() and not list((root/'workspace').glob('deploy_*.xml'))
    def fail(*a,**kw):
        result.write_text(json.dumps({'status':'FAILED','steps':[{'step':'matiec','ok':False}],
                                      'errors':['matiec: invalid token']}))
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(serve.subprocess,'run',fail)
    with pytest.raises(RuntimeControlError,match='invalid token') as caught:
        adapter.action('deploy',b'<?xml version="1.0"?><project/>')
    assert caught.value.result['steps'][0]['step']=='matiec'


def test_deploy_receipt_hash_matches_exact_uploaded_xml(lx,monkeypatch):
    adapter,_,root=lx
    xml=b'<?xml version="1.0"?><project/>'
    def deploy(command,**kw):
        assert Path(command[-1]).read_bytes()==xml
        (root/'workspace/deploy_result.json').write_text('{"status":"OK","steps":[],"errors":[]}')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(serve.subprocess,'run',deploy)
    result=adapter.action('deploy',xml)
    assert result['deployment']['sha256']==hashlib.sha256(xml).hexdigest()
    assert adapter.status()['deployment']==result['deployment']


def test_frozen_preflight_rejects_aml_address_mismatch(delivery):
    app,ident,task,directory=delivery
    assert deployment_preflight(directory)==(directory/'final/plcopen.xml').read_bytes()
    xml=directory/'final/plcopen.xml'
    xml.write_text(xml.read_text(encoding='utf-8').replace('address="%QW13"','address="%QW27"'),encoding='utf-8')
    with pytest.raises(RuntimeControlError,match='R6'):deployment_preflight(directory)


def test_failed_deploy_survives_runtime_restart_and_explains_old_program(delivery,monkeypatch):
    app,ident,task,directory=delivery
    def runtime_action(action,xml):
        if action=='deploy':
            raise RuntimeControlError('matiec: invalid located variable declaration',
                {'steps':[{'step':'compile (matiec)','ok':False}]})
        return {'status':'OK','steps':[]}
    monkeypatch.setattr(app.runtime,'action',runtime_action)
    monkeypatch.setattr(app.runtime,'status',lambda:{'runtime':{'status':'RUNNING'},
        'deployment':None,'capabilities':['deploy','start','stop','logs'],'prog_id':2})
    app.control_runtime(ident,0,'deploy',task)
    assert finished(app,ident)['runtime_deployment']['status']=='failed'
    app.control_runtime(ident,0,'start')
    state=finished(app,ident)
    assert state['runtime_action']['status']=='success'
    assert state['runtime_deployment']['status']=='failed'
    probe=app.runtime_status(ident)
    assert not probe['deployment_matches'] and '部署失败' in probe['deployment_error']
    with pytest.raises(web.WorkbenchError,match='invalid located variable declaration'):
        app._start_motion(ident,task)
    assert app.motion is None
    monkeypatch.setattr(app.runtime,'status',lambda:{'runtime':{'status':'STOPPED'},'deployment':None})
    with pytest.raises(web.WorkbenchError,match='部署失败'):
        app._start_motion(ident,task)
    monkeypatch.setattr(app.runtime,'status',lambda:{'runtime':{'status':'RUNNING'},
        'deployment':{'sha256':hashlib.sha256((directory/'final/plcopen.xml').read_bytes()).hexdigest()}})
    probe=app.runtime_status(ident)
    assert probe['deployment_matches'] and not probe.get('deployment_error')


def test_frontend_deploys_only_current_owned_final(delivery,monkeypatch):
    app,ident,task,directory=delivery
    calls=[]
    monkeypatch.setattr(app.runtime,'action',lambda action,xml:(calls.append((action,xml)) or {'status':'OK','steps':[]}))
    original=(directory/'final/plcopen.xml').read_bytes()
    app.control_runtime(ident,0,'deploy',task)
    result=finished(app,ident)
    assert result['runtime_action']['status']=='success' and calls==[('deploy',original)]
    assert (directory/'final/plcopen.xml').read_bytes()==original
    stranger=app.create_session()
    with pytest.raises(web.WorkbenchError):app.control_runtime(stranger['id'],0,'deploy',task)
    with pytest.raises(web.WorkbenchError):app.control_runtime(ident,1,'deploy',task)
    app._session(ident)['revision']=1
    with pytest.raises(web.WorkbenchError,match='重新生成'):app.control_runtime(ident,1,'deploy',task)
    assert len(calls)==1


def test_failed_preflight_never_contacts_lx(delivery,monkeypatch):
    app,ident,task,directory=delivery
    (directory/'assets/device.aml').unlink()
    monkeypatch.setattr(app.runtime,'action',lambda *a:pytest.fail('should not deploy'))
    app.control_runtime(ident,0,'deploy',task)
    result=finished(app,ident)
    assert result['runtime_action']['status']=='failed' and result['runtime_action']['errors']


@pytest.mark.parametrize('module', ['src.agent.web', 'agent.web'])
def test_deployment_from_clean_workbench_cli(delivery, lx, monkeypatch, module):
    """真实启动独立解释器，避免测试 sys.path 掩盖仓库根目录启动时的导入问题。"""
    app,ident,task,directory=delivery
    adapter,calls,root=lx
    original=(directory/'final/plcopen.xml').read_bytes()
    def deploy(command,**kwargs):
        assert Path(command[-1]).read_bytes()==original
        calls.append('deploy')
        (root/'workspace/deploy_result.json').write_text('{"status":"OK","steps":[],"errors":[]}')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(serve.subprocess,'run',deploy)
    app._save(app._session(ident))
    env=os.environ.copy()
    env.pop('PYTHONPATH',None)
    env['PYTHONIOENCODING']='utf-8'
    if module=='agent.web':env['PYTHONPATH']=str(REPO/'src')
    proc=subprocess.Popen([sys.executable,'-m',module,'--port','0','--runs-root',str(app.root)],
                          cwd=REPO,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                          text=True,encoding='utf-8')
    lines=queue.Queue()
    threading.Thread(target=lambda:lines.put(proc.stdout.readline()),daemon=True).start()
    client=requests.Session();client.trust_env=False
    try:
        line=lines.get(timeout=20)
        assert 'http://127.0.0.1:' in line, line
        base='http://'+line.split('http://',1)[1].strip()
        token=client.get(base+'/api/config',timeout=3).json()['token']
        response=client.post(base+'/api/runtime/deploy',json={'session':ident,'revision':0,'task':task},
                             headers={'X-Agent-Token':token},timeout=3)
        assert response.status_code==200, response.text
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            state=client.get(base+'/api/state',params={'session':ident},timeout=3).json()
            if not state['busy']:break
            time.sleep(.05)
        assert not state['busy'] and state['runtime_action']['status']=='success',state.get('runtime_action')
        assert calls==['deploy']
        assert (directory/'final/plcopen.xml').read_bytes()==original
    finally:
        client.close();proc.terminate()
        try:proc.wait(timeout=5)
        except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
        proc.stdout.close();proc.stderr.close()


def test_control_http_requires_token_revision_and_reports_async_errors(lx,tmp_path,monkeypatch):
    adapter,calls,_=lx
    app=web.SVGWorkbench(tmp_path/'web');app.runtime=adapter
    ident=app.create_session()['id']
    server=web.make_server(app,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    client=requests.Session();client.trust_env=False
    base='http://127.0.0.1:'+str(server.server_port)
    try:
        body={'session':ident,'revision':0}
        assert client.post(base+'/api/runtime/start',json=body).status_code==403
        headers={'X-Agent-Token':app.token}
        assert client.post(base+'/api/runtime/start',json=dict(body,revision=1),headers=headers).status_code==409
        assert not calls
        assert client.post(base+'/api/runtime/start',json=body,headers=headers).status_code==200
        record=finished(app,ident)['runtime_action']
        assert record['status']=='success' and calls==['start'],record
        assert client.get(base+'/api/runtime/logs',params={'session':ident}).json()['logs'].startswith('scan=24')
        monkeypatch.setattr(app.runtime,'action',lambda *a:(_ for _ in ()).throw(RuntimeControlError('compile failed',{'steps':[{'step':'matiec','ok':False}]})))
        app.control_runtime(ident,0,'stop')
        result=finished(app,ident)['runtime_action']
        assert result['status']=='failed' and result['steps'][0]['step']=='matiec'
    finally:
        client.close();server.shutdown();server.server_close();thread.join(2);app.close()
