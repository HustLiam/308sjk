"""浏览器工作台：选择一致性、异步对话/生成、产物归属与 HTTP 接口。"""
import base64
import json
import sys
import threading
import time
from pathlib import Path

import pytest
import requests

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO/'src'))
from agent import web
from agent.svg_input import SVGError, plan_svg, svg_catalog


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(web, 'get_api_key', lambda:'')
    instance = web.SVGWorkbench(tmp_path)
    yield instance
    instance.close()


def imported(app):
    return app.import_svg('house.svg', (REPO/'examples/svg/house.svg').read_bytes())


def wait_finished(app, ident):
    deadline = time.monotonic()+5
    while time.monotonic()<deadline:
        state = app.state(ident)
        if not state['busy']:
            return state
        time.sleep(.01)
    pytest.fail('后台任务未完成')


def test_catalog_uses_stable_paths_and_fits_canvas(tmp_path):
    source = tmp_path/'a.svg'
    source.write_text('<svg><path id="roof" d="M0 0 L1 100"/><rect id="door" x="5" y="50" width="1" height="4"/></svg>')
    catalog = svg_catalog(source)
    assert [p['id'] for p in catalog['paths']]==[1,2]
    assert [p['label'] for p in catalog['paths']]==['roof','door']
    assert all(0<=x<=680 and 0<=y<=440 for path in catalog['paths'] for x,y in path['points'])


def test_selection_keeps_original_geometry_and_pen_lifts():
    full = plan_svg(REPO/'examples/svg/house.svg')
    part = plan_svg(REPO/'examples/svg/house.svg', selected_paths=[3,1])
    assert part['params']['selected_paths']==[1,3]
    assert part['params']['path_count']==2
    chosen = [s for s in full['steps'] if s.get('path') in [1,3]]
    strokes = [s for s in part['steps'] if s['action']=='polyline_sweep']
    assert [s['waypoints'] for s in strokes]==[s['waypoints'] for s in chosen]
    assert part['source']['sha256']==full['source']['sha256']
    assert [s['action'] for s in part['steps']]==['pen_up_move','pen_down_move','polyline_sweep','pen_up_move']*2+['pen_up_move']


@pytest.mark.parametrize('ids', [[], [0], [99], [1,1], [True], ['1']])
def test_invalid_selected_paths_are_rejected(ids):
    with pytest.raises(SVGError):
        plan_svg(REPO/'examples/svg/house.svg', selected_paths=ids)


def test_plan_is_atomic_and_invalidates_old_spec(app):
    state = imported(app)
    with app.lock:
        app._session(state['id'])['spec']={'task_goal':'old'}
    with pytest.raises(SVGError):
        app.plan(state['id'], state['revision'], [1], dict(state['settings'], width=-1))
    assert app.state(state['id'])['spec']=={'task_goal':'old'}
    revised = app.plan(state['id'], state['revision'], [2,4], state['settings'])
    assert revised['spec'] is None and revised['revision']==2
    assert revised['trajectory']['params']['selected_paths']==[2,4]
    assert app.file(state['id'],'draft','trajectory-preview.html').is_file()
    with pytest.raises(web.WorkbenchError, match='版本'):
        app.plan(state['id'], 1, [1], state['settings'])


def test_empty_selection_is_persisted_and_blocks_generation(app, monkeypatch):
    state = imported(app)
    empty = app.plan(state['id'], 1, [], state['settings'])
    assert empty['trajectory'] is None and empty['preview_url'] is None
    with pytest.raises(web.WorkbenchError, match='选择'):
        app.chat(state['id'], 2, '开始')
    app.sessions.clear()
    assert app.state(state['id'])['selected_paths']==[]


def test_geometry_chat_without_model_updates_preview_keeps_selection(app):
    state = imported(app)
    state = app.plan(state['id'], 1, [2,4], state['settings'])
    revised = app.chat(state['id'], state['revision'], '宽度30，高度40，中心(45,50)')
    assert not revised['busy']
    assert revised['trajectory']['params']['selected_paths']==[2,4]
    assert revised['settings']['width']==30 and revised['settings']['center']==[45,50]
    assert revised['spec'] is None
    assert '未配置模型' in revised['messages'][-1]['text']


def test_async_spec_refinement_and_frozen_generation_snapshot(app, monkeypatch):
    from agent.requirement import RequirementUnderstander
    monkeypatch.setattr(web, 'get_api_key', lambda:'test-key-do-not-expose')
    ready = threading.Event()
    entered = threading.Event()
    calls = []
    spec = json.loads((REPO/'examples/specs/plotter3axis.spec.json').read_text(encoding='utf-8'))
    def understand(self, request, **kwargs):
        calls.append(('understand',request))
        entered.set()
        assert ready.wait(3)
        return {'spec':dict(spec),'report':{'pending':[]}}
    def refine(self, previous, request, **kwargs):
        calls.append(('refine',kwargs['correction']))
        return {'spec':dict(spec),'report':{'pending':[]}}
    monkeypatch.setattr(RequirementUnderstander, 'understand', understand)
    monkeypatch.setattr(RequirementUnderstander, 'refine', refine)
    state = imported(app)
    state = app.plan(state['id'], 1, [1,3], state['settings'])
    busy = app.chat(state['id'], state['revision'], '启动按钮后绘图，急停时清除完成状态')
    assert entered.wait(1)
    assert busy['busy']
    with pytest.raises(web.WorkbenchError, match='正在处理'):
        app.plan(state['id'], busy['revision'], [2], state['settings'])
    ready.set()
    state = wait_finished(app, state['id'])
    assert state['spec'] and state['phase']=='ready'
    state = app.chat(state['id'], state['revision'], '完成后点亮完成灯')
    state = wait_finished(app, state['id'])
    assert [c[0] for c in calls]==['understand','refine']
    def solve(snapshot, directory, log):
        assert snapshot['prepared']['trajectory']['params']['selected_paths']==[1,3]
        assert snapshot['spec']['task_id']==directory.name
        log('test generation progress')
        (directory/'final').mkdir()
        (directory/'final/plcopen.xml').write_text('<project/>')
        (directory/'final/gate.json').write_text('{"ok":true}')
        (directory/'simulation').mkdir()
        (directory/'simulation/build.json').write_text('{"runtime_ready":true,"runtime_problems":[]}')
        return {'status':'final', 'iter':1, 'run_dir':directory}
    monkeypatch.setattr(app, '_solve', solve)
    monkeypatch.setattr(app,'_generation_validation',lambda path:{'status':'passed'})
    app.generate(state['id'], state['revision'])
    state = wait_finished(app, state['id'])
    assert state['phase']=='completed'
    assert state['jobs'][0]['status']=='final'
    assert 'test generation progress' in [e['text'] for e in state['logs']]
    task = state['jobs'][0]['id']
    snapshot = json.loads((app.root/task/'web-request.json').read_text(encoding='utf-8'))
    assert snapshot['selected_paths']==[1,3]
    assert app.file(state['id'],task,'final/plcopen.xml').read_text()=='<project/>'
    assert (app.root/task/'input.svg').read_bytes()==(REPO/'examples/svg/house.svg').read_bytes()
    assert '物理仿真验收' in state['messages'][-1]['text']
    state = app.plan(state['id'],state['revision'],[2],state['settings'])
    assert state['spec'] is None
    assert snapshot['selected_paths']==[1,3]


def test_failure_clears_busy_and_redacts_key(app, monkeypatch):
    monkeypatch.setattr(web,'get_api_key',lambda:'private-key')
    def fail(*args):
        raise RuntimeError('model failed private-key')
    monkeypatch.setattr(app,'_understand',fail)
    state = imported(app)
    app.chat(state['id'],1,'启动后描画')
    state = wait_finished(app,state['id'])
    assert state['phase']=='failed' and app.active is None
    assert '[hidden]' in state['messages'][-1]['text']
    assert 'private-key' not in json.dumps(state)


def test_http_upload_selection_download_and_origin_guards(app):
    server = web.make_server(app, 0)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    client = requests.Session()
    client.trust_env = False
    base = 'http://127.0.0.1:'+str(server.server_port)
    try:
        assert client.get(base).status_code==200
        assert client.get(base+'/app.js').status_code==200
        config = client.get(base+'/api/config').json()
        assert 'api_key' not in config
        headers = {'X-Agent-Token':config['token']}
        assert client.post(base+'/api/example',json={}).status_code==403
        assert client.post(base+'/api/example',json={},headers=dict(headers,Origin='http://evil.example')).status_code==403
        assert client.get(base+'/api/config',headers={'Host':'evil.example'}).status_code==403
        response = client.post(base+'/api/import',headers=headers,json={'name':'drawing.svg',
            'data':base64.b64encode((REPO/'examples/svg/house.svg').read_bytes()).decode()})
        state = response.json()
        assert response.status_code==200 and state['trajectory']['params']['path_count']==4
        selected = client.post(base+'/api/plan',headers=headers,json={'session':state['id'],'revision':1,
            'selected_paths':[2], 'settings':state['settings']}).json()
        assert selected['trajectory']['params']['path_count']==1
        page = client.get(base+selected['preview_url'])
        assert page.status_code==200 and '轨迹预览' in page.text
        source = client.get(base+'/file/'+state['id']+'/draft/input.svg')
        assert source.headers['Content-Disposition'].startswith('attachment')
        assert client.get(base+'/file/'+state['id']+'/draft/..%2Fsession.json').status_code==404
        assert client.get(base+'/file/'+state['id']+'/svg_web_'+'0'*32+'/final/plcopen.xml').status_code==404
        assert client.post(base+'/api/import',headers=headers,json={'name':'a.svg','data':'%bad'}).status_code==400
        assert client.post(base+'/api/plan',headers=headers,json=[]).status_code==400
        assert client.post(base+'/api/import',headers=headers,json={'name':'x.svg','data':base64.b64encode(b'<svg><script>alert(1)</script></svg>').decode()}).status_code==400
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_generation_requires_online_gates_and_selected_points(app, monkeypatch):
    from agent.orchestrator import Orchestrator
    monkeypatch.setattr(web,'get_api_key',lambda:'fake-key')
    captured = {}
    def solve(self, spec, generator, **options):
        captured.update(spec=spec,generator=generator,**options)
        return {'status':'best_effort'}
    monkeypatch.setattr(Orchestrator,'solve',solve)
    state = imported(app)
    state = app.plan(state['id'],1,[2],state['settings'])
    snapshot = app._session(state['id'])
    snapshot['spec']={'task_id':'svg_web_test','io_list':[]}
    app._solve(snapshot,app.root/'dummy',lambda text:None)
    assert captured['trajectory']['params']['selected_paths']==[2]
    assert captured['deploy'] is True and captured['acceptance']=='workbench' and captured['require_online'] is True
    assert captured['generator'].no_cards


def test_generated_xml_without_physical_evidence_cannot_be_delivered(app,monkeypatch):
    monkeypatch.setattr(web,'get_api_key',lambda:'test-key')
    state=imported(app)
    state=app.default_process(state['id'],state['revision'])
    def fake_solve(snapshot,directory,log):
        (directory/'final').mkdir()
        (directory/'final/plcopen.xml').write_text('<project/>')
        return {'status':'final','validation':{'status':'passed'}}
    monkeypatch.setattr(app,'_solve',fake_solve)
    app.generate(state['id'],state['revision'])
    state=wait_finished(app,state['id'])
    assert state['phase']=='failed' and state['jobs'][-1]['status']=='failed'


def test_retry_keeps_failed_source_context_only_for_unchanged_frozen_inputs(app,monkeypatch):
    monkeypatch.setattr(web,'get_api_key',lambda:'test-key')
    state=imported(app);state=app.default_process(state['id'],state['revision'])
    resumed=[]
    def fake_solve(snapshot,directory,log):
        resumed.append(bool(snapshot.get('_repair_candidate')))
        if resumed[-1]:
            assert (directory/'repair-source.xml').is_file()
            assert (directory/'repair-source.json').is_file()
        iteration=directory/'iter_001';iteration.mkdir()
        (iteration/'plcopen.xml').write_text('<project/>',encoding='utf-8')
        (iteration/'gate.json').write_text('{"errors":["compile failure"]}',encoding='utf-8')
        return {'status':'best_effort'}
    monkeypatch.setattr(app,'_solve',fake_solve)
    for _ in range(2):
        app.generate(state['id'],state['revision']);state=wait_finished(app,state['id'])
    state=app.plan(state['id'],state['revision'],[1],state['settings'])
    state=app.default_process(state['id'],state['revision'])
    app.generate(state['id'],state['revision']);state=wait_finished(app,state['id'])
    assert resumed==[False,True,False]
    assert all(not job['files'] for job in state['jobs'])
    assert not state['jobs'][-1]['files'] and not state['workflow']['can_build']


def test_restart_marks_interrupted_work(app):
    state = imported(app)
    with app.lock:
        stored = app._session(state['id'])
        stored['busy']=True
        stored['phase']='generating'
        app._save(stored)
        app.sessions.clear()
    reloaded = app.state(state['id'])
    assert not reloaded['busy'] and reloaded['phase']=='interrupted'


@pytest.mark.parametrize('status', ['final', 'best_effort', 'failed', 'running'])
def test_existing_jobs_only_publish_final_deliverables(app, status):
    state = imported(app)
    task = 'svg_web_'+'a'*32
    directory = app.root/task
    (directory/'final').mkdir(parents=True)
    (directory/'iter_001').mkdir()
    scene = {'io_map': [{'plc_var':'x_fb','dir':'input','type':'float',
                         'bind':{'asset':'gantry','quantity':'x_axis.pos','range':[0,1]}}]}
    raw_scene = json.dumps(scene).encode()
    (directory/'final/scene.spec.json').write_bytes(raw_scene)
    (directory/'final/plcopen.xml').write_text('<final/>')
    (directory/'final/gate.json').write_text('{}')
    (directory/'iter_001/plcopen.xml').write_text('<draft/>')
    stored = app._session(state['id'])
    stored['jobs'].append({'id':task,'status':status,'files':[
        {'name':'final/gate.json','url':'old-gate'},
        {'name':'iter_001/plcopen.xml','url':'old-draft'}]})
    app._save(stored)
    app.sessions.clear()  # 模拟升级前已经保存的历史任务。
    files = app.state(state['id'])['jobs'][0]['files']
    assert (directory/'final/scene.spec.json').read_bytes()==raw_scene
    assert (directory/'iter_001/plcopen.xml').is_file()
    if status != 'final':
        assert files==[] and not (directory/'io_map.json').exists()
        return
    assert [f['name'] for f in files]==['final/scene.spec.json','final/plcopen.xml']
    assert not (directory/'io_map.json').exists()
    with pytest.raises(web.WorkbenchError):
        app.file(state['id'],task,'io_map.json')
    with pytest.raises(web.WorkbenchError):
        app.file(app.create_session()['id'],task,'final/scene.spec.json')
    server = web.make_server(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = requests.Session();client.trust_env = False
    try:
        response = client.get('http://127.0.0.1:'+str(server.server_port)+files[0]['url'])
        assert response.status_code==200 and response.json()==scene
        assert 'attachment' in response.headers['Content-Disposition']
    finally:
        client.close();server.shutdown();server.server_close();thread.join(timeout=2)
