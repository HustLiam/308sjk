"""三阶段中的确定性场景构建、冻结输入和 PLC 接线前置检查。"""
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import threading
import shutil

import pytest
import requests

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO/'src'))
from agent.simulation import build_scene
from agent.scene_gen import SceneSpecGenerator
from runtime.physics_assets import load_physics
from agent import web, simulation


@pytest.fixture
def task(tmp_path):
    tmp_path = tmp_path/'frozen-task'
    tmp_path.mkdir()
    spec = json.loads((REPO/'examples/specs/plotter_square_csp.spec.json').read_text(encoding='utf-8'))
    scene = SceneSpecGenerator().generate(spec)['scene']
    (tmp_path/'final').mkdir()
    (tmp_path/'assets').mkdir()
    (tmp_path/'request.json').write_text(json.dumps(spec), encoding='utf-8')
    (tmp_path/'final/scene.spec.json').write_text(json.dumps(scene), encoding='utf-8')
    (tmp_path/'assets/physics.json').write_text(json.dumps(load_physics()), encoding='utf-8')
    (tmp_path/'final/plcopen.xml').write_bytes((REPO/'src/plc/plotter_square_csp.xml').read_bytes())
    return tmp_path


def test_build_uses_frozen_physics_and_preserves_deliverables(task):
    originals = {name:(task/'final'/name).read_bytes() for name in ('scene.spec.json','plcopen.xml')}
    physics = load_physics();physics['gantry']['mass'][0] = 7
    (task/'assets/physics.json').write_text(json.dumps(physics))
    built = build_scene(task)
    assert built['runtime_ready'] and built['joints']==3 and built['actuators']==3
    root = ET.parse(task/'simulation/scene.xml').getroot()
    assert root.find('.//geom[@name="saddle"]').get('mass')=='7'
    for name, data in originals.items():assert (task/'final'/name).read_bytes()==data
    assert not (task/'io_map.json').exists()


def test_addresses_come_from_actual_xml_not_allocator_order(task):
    path = task/'final/plcopen.xml'
    root = ET.parse(path)
    for v in root.iter():
        if v.get('name')=='x_cmd' and v.get('address'):v.set('address','%QW10')
    root.write(path, encoding='utf-8')
    build_scene(task)
    entries = json.loads((task/'simulation/io_map.json').read_text())
    assert next(e for e in entries if e['plc_var']=='x_cmd')['modbus']['plc_addr']=='%QW10'


def test_synthetic_channels_do_not_make_velocity_program_runnable(task):
    path = task/'final/plcopen.xml'
    path.write_text('<project/>')
    result = build_scene(task)
    assert result['status']=='ready' and not result['runtime_ready']
    assert any('x_cmd' in problem for problem in result['runtime_problems'])
    entries = json.loads((task/'simulation/io_map.json').read_text())
    assert all('plc_addr' not in entry['modbus'] for entry in entries)


def test_missing_snapshot_fails_without_falling_back_to_current_assets(task):
    (task/'assets/physics.json').unlink()
    with pytest.raises(FileNotFoundError):build_scene(task)
    assert not (task/'simulation').exists()


def test_restoring_identical_inputs_reuses_existing_delivery_without_llm(tmp_path):
    app=web.SVGWorkbench(tmp_path)
    state=app.import_svg('house.svg',(REPO/'examples/svg/house.svg').read_bytes())
    stored=app._session(state['id'])
    task_id='svg_web_'+'d'*32
    directory=app.root/task_id;directory.mkdir()
    snapshot={key:stored[key] for key in ('assets','request','selected_paths','settings')}
    (directory/'web-request.json').write_text(json.dumps(dict(snapshot,revision=1)),encoding='utf-8')
    stored['jobs']=[{'id':task_id,'revision':1,'status':'final','files':[]}]
    try:
        changed=app.plan(state['id'],1,[2],state['settings'])
        assert not changed['workflow']['can_build']
        restored=app.plan(state['id'],2,state['selected_paths'],state['settings'])
        assert restored['revision']==3 and restored['workflow']['can_build']
        stored['request']+='新增工艺要求'
        assert not app.state(state['id'])['workflow']['can_build']
    finally:app.close()


@pytest.mark.parametrize('camera',[(float('nan'),-35,1.8),(0,float('inf'),1.8),
                                  (0,-35,.1),(0,-90,2),(400,-35,2),
                                  (0,-35,2,float('nan'),0),(0,-35,2,0,5)])
def test_camera_rejects_invalid_parameters_before_opening_renderer(camera):
    with pytest.raises(ValueError):simulation.render_scene('unused.xml',*camera)


def test_scene_http_guards_ownership_and_runtime_requires_revision(task,tmp_path,monkeypatch):
    app=web.SVGWorkbench(tmp_path/'app')
    state=app.create_session()
    task_id='svg_web_'+'c'*32
    shutil.copytree(task,app.root/task_id)
    stored=app._session(state['id'])
    stored['jobs']=[{'id':task_id,'revision':0,'status':'final','files':[]}]
    app._build_simulation(state['id'],task_id)
    calls=[]
    def render(path,*camera):
        calls.append(path)
        return b'\x89PNG\r\n\x1a\n'
    monkeypatch.setattr(simulation,'render_scene',render)
    server=web.make_server(app,0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    client=requests.Session();client.trust_env=False
    base='http://127.0.0.1:'+str(server.server_port)
    try:
        view=client.get(base+'/api/state',params={'session':state['id']}).json()['workflow']['simulation']
        response=client.get(base+view['frame_url'])
        assert response.status_code==200 and response.headers['Content-Type']=='image/png'
        assert calls==[app.root/task_id/'simulation/scene.xml']
        stranger=app.create_session()
        response=client.get(base+'/api/simulation/frame',params={'session':stranger['id'],'task':task_id})
        assert response.status_code==404 and len(calls)==1
        response=client.post(base+'/api/runtime/start',json={'session':state['id']},headers={'X-Agent-Token':app.token})
        assert response.status_code==409
    finally:
        client.close();server.shutdown();server.server_close();thread.join(timeout=2);app.close()


@pytest.mark.parametrize('online',[True,False])
def test_runtime_status_only_reads_existing_deploy_service(tmp_path,monkeypatch,online):
    app=web.SVGWorkbench(tmp_path)
    ident=app.create_session()['id']
    calls=[]
    class ReadOnlyClient:
        trust_env=True
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def request(self,method,url,timeout):
            assert method=='GET' and not self.trust_env and timeout==(3,15)
            calls.append(url)
            if not online:raise requests.ConnectionError('offline')
            return self
        def raise_for_status(self):pass
        ok=True
        def json(self):return {'runtime':{'status':'RUNNING'},'program':'plotter_square_csp','prog_id':4}
    monkeypatch.setattr(requests,'Session',ReadOnlyClient)
    monkeypatch.setenv('DEPLOY_URL','http://127.0.0.1:8600/deploy')
    try:
        result=app.runtime_status(ident)
        assert result['service']==('online' if online else 'offline')
        assert result['runtime']==('RUNNING' if online else 'unknown')
        assert result['control']==('deploy_only' if online else 'not_connected')
        assert result['simulation']=='not_started'
        assert calls==['http://127.0.0.1:8600/status']
    finally:app.close()
