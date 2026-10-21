"""Language trajectories use the same frozen-input, preview and generation workflow as SVG."""
import copy
import json
from pathlib import Path
import sys
import threading
import time

import pytest
import requests

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from agent import web


@pytest.fixture
def app(tmp_path,monkeypatch):
    monkeypatch.setattr(web,'get_api_key',lambda:'')
    instance=web.SVGWorkbench(tmp_path)
    yield instance
    instance.close()


def test_language_chat_and_default_process_need_no_svg_or_selection(app):
    state=app.create_session()
    assert state['drawing_mode']=='language' and not state['assets']['svg']
    state=app.chat(state['id'],state['revision'],'画一个正方形')
    assert state['trajectory']['shape']=='square' and state['trajectory']['params']['size']==60
    assert not state['selected_paths'] and app._session(state['id'])['prepared']['sources']['size']=='default'
    assert app.file(state['id'],'draft','trajectory-preview.html').is_file()
    state=app.default_process(state['id'],state['revision'])
    assert state['phase']=='ready' and state['spec']
    assert 'SVG' not in app._session(state['id'])['prepared']['request_text']
    assert '正方形' in state['drawing_summary']


def test_language_geometry_edits_and_clear_keep_current_geometry(app):
    state=app.create_session()
    state=app.plan_language(state['id'],state['revision'],'画一个边长为40的正方形，中心为(45,50)')
    assert state['trajectory']['params']=={'size':40,'center':[45,50]}
    state=app.chat(state['id'],state['revision'],'边长30')
    assert state['trajectory']['params']=={'size':30,'center':[45,50]}
    state=app.chat(state['id'],state['revision'],'按启动按钮运行，完成后抬笔')
    before=copy.deepcopy(state['trajectory'])
    state=app.clear_context(state['id'],state['revision'])
    assert state['trajectory']==before and not state['messages'] and state['spec'] is None
    assert '按启动按钮' not in app._session(state['id'])['prepared']['request_text']
    state=app.chat(state['id'],state['revision'],'改成圆，半径15，段数48')
    assert state['trajectory']['shape']=='circle'
    assert state['trajectory']['params']=={'center':[45,50],'radius':15,'segments':48}


@pytest.mark.parametrize('text',['画三角形','画一个正方形，边长200','画一个圆，半径0','画一个正方形，边长10mm','仅启动设备'])
def test_invalid_or_unsupported_language_geometry_is_atomic(app,text):
    state=app.create_session()
    before=copy.deepcopy(app._session(state['id']))
    with pytest.raises(ValueError):app.plan_language(state['id'],state['revision'],text)
    assert app._session(state['id'])==before


def test_source_switch_retains_svg_but_invalidates_spec_and_uses_only_active_geometry(app):
    state=app.import_svg('house.svg',(ROOT/'examples/svg/house.svg').read_bytes())
    state=app.plan(state['id'],state['revision'],[2],state['settings'])
    svg=copy.deepcopy(state['assets']['svg']);settings=copy.deepcopy(state['settings'])
    state=app.default_process(state['id'],state['revision'])
    state=app.drawing_source(state['id'],state['revision'],'language')
    assert state['spec'] is None and state['trajectory'] is None
    state=app.chat(state['id'],state['revision'],'画一个正方形，边长40')
    assert state['assets']['svg']==svg and state['selected_paths']==[2]
    assert state['trajectory']['shape']=='square' and 'SVG' not in app._session(state['id'])['prepared']['request_text']
    assert not (app._directory(state['id'])/'draft/input.svg').exists()
    state=app.default_process(state['id'],state['revision'])
    state=app.drawing_source(state['id'],state['revision'],'svg')
    assert state['spec'] is None and state['trajectory']['shape']=='svg'
    assert state['trajectory']['params']['selected_paths']==[2] and state['settings']==settings
    state=app.drawing_source(state['id'],state['revision'],'language')
    assert state['trajectory']['shape']=='square' and state['trajectory']['params']['size']==40
    app.sessions.clear()
    restored=app.state(state['id'])
    assert restored['drawing_mode']=='language' and restored['trajectory']==state['trajectory']


def test_language_generation_freezes_geometry_and_never_archives_retained_svg(app,monkeypatch):
    state=app.import_svg('house.svg',(ROOT/'examples/svg/house.svg').read_bytes())
    state=app.drawing_source(state['id'],state['revision'],'language')
    state=app.plan_language(state['id'],state['revision'],'画一个圆，半径20，段数48')
    state=app.default_process(state['id'],state['revision'])
    captured={}
    def solve(snapshot,directory,log):
        captured.update(snapshot=snapshot,directory=directory)
        assert snapshot['prepared']['trajectory']['shape']=='circle'
        assert not (directory/'input.svg').exists()
        return {'status':'best_effort','stop_reason':'test_only'}
    monkeypatch.setattr(app,'_solve',solve)
    monkeypatch.setattr(web,'get_api_key',lambda:'test-key')
    app.generate(state['id'],state['revision'])
    deadline=time.monotonic()+4
    while app.state(state['id'])['busy'] and time.monotonic()<deadline:time.sleep(.01)
    assert not app.state(state['id'])['busy'] and captured
    frozen=json.loads((captured['directory']/'web-request.json').read_text(encoding='utf-8'))
    assert frozen['drawing_mode']=='language' and frozen['language_geometry']['params']['radius']==20
    assert json.loads((captured['directory']/'trajectory.json').read_text(encoding='utf-8'))['shape']=='circle'
    job=app.state(state['id'])['jobs'][-1]
    assert app._job_current(job,app._session(state['id']))
    app.drawing_source(state['id'],state['revision'],'svg')
    assert not app._job_current(job,app._session(state['id']))


def test_direct_language_chat_reaches_spec_understanding_without_selected_paths(app,monkeypatch):
    from agent.requirement import RequirementUnderstander
    original=RequirementUnderstander.understand;requests_seen=[]
    def understand(self,text,**kwargs):
        requests_seen.append(text)
        return original(RequirementUnderstander(),text,**kwargs)
    monkeypatch.setattr(RequirementUnderstander,'understand',understand)
    monkeypatch.setattr(web,'get_api_key',lambda:'configured-test-key')
    state=app.create_session()
    app.chat(state['id'],state['revision'],'画一个正方形，边长40，按启动按钮绘图')
    deadline=time.monotonic()+4
    while app.state(state['id'])['busy'] and time.monotonic()<deadline:time.sleep(.01)
    state=app.state(state['id'])
    assert not state['busy'] and state['spec'] and state['trajectory']['shape']=='square'
    assert not state['assets']['svg'] and not state['selected_paths']
    assert len(requests_seen)==1 and '边长 40' in requests_seen[0] and 'SVG' not in requests_seen[0]


def test_legacy_svg_session_keeps_source_and_legacy_empty_session_can_use_language(app):
    state=app.import_svg('house.svg',(ROOT/'examples/svg/house.svg').read_bytes())
    stored=app._session(state['id'])
    for key in ('drawing_mode','drawing_text','language_geometry'):stored.pop(key,None)
    app._save(stored);app.sessions.clear()
    assert app.state(state['id'])['drawing_mode']=='svg'
    empty=app.create_session();stored=app._session(empty['id'])
    for key in ('drawing_mode','drawing_text','language_geometry'):stored.pop(key,None)
    stored['request']=web._DEFAULT_REQUEST;app._save(stored);app.sessions.clear()
    restored=app.state(empty['id'])
    assert restored['drawing_mode']=='language' and not restored['request']


def test_language_asset_change_replans_without_falling_back_to_svg(app):
    state=app.create_session()
    state=app.plan_language(state['id'],state['revision'],'画一个正方形，边长40')
    before=state['trajectory']
    state=app.replace_asset(state['id'],state['revision'],'aml',restore=True)
    assert state['trajectory']==before and state['spec'] is None


def test_source_and_language_plan_have_http_token_revision_and_busy_guards(app):
    server=web.make_server(app,0)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    client=requests.Session();client.trust_env=False
    base='http://127.0.0.1:'+str(server.server_port)
    try:
        state=client.post(base+'/api/session',json={},headers={'X-Agent-Token':app.token}).json()
        body={'session':state['id'],'revision':state['revision'],'text':'画一个正方形，边长40'}
        assert client.post(base+'/api/drawing/plan',json=body).status_code==403
        headers={'X-Agent-Token':app.token}
        result=client.post(base+'/api/drawing/plan',json=body,headers=headers)
        assert result.status_code==200 and result.json()['trajectory']['shape']=='square'
        assert client.post(base+'/api/drawing/plan',json=body,headers=headers).status_code==409
        app._session(state['id'])['busy']=True
        body.update(revision=result.json()['revision'],mode='svg')
        assert client.post(base+'/api/drawing/source',json=body,headers=headers).status_code==409
        app._session(state['id'])['busy']=False
        assert client.post(base+'/api/drawing/source',json=body,headers=headers).json()['drawing_mode']=='svg'
    finally:
        client.close();server.shutdown();server.server_close();worker.join(timeout=2)
