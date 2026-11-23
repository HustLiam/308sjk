"""会话清空、默认工艺与面向用户的简洁工艺目标。"""
import copy
import json
from pathlib import Path
import sys
import threading
import time

import pytest
import requests

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'src'))
from agent import web
from agent.presentation import process_summary
from agent.requirement import RequirementUnderstander,build_io_list


@pytest.fixture
def app(tmp_path,monkeypatch):
    monkeypatch.setattr(web,'get_api_key',lambda:'')
    instance=web.SVGWorkbench(tmp_path)
    yield instance
    instance.close()


def imported(app):
    return app.import_svg('house.svg',(REPO/'examples/svg/house.svg').read_bytes())


@pytest.mark.parametrize('message',['画正方形','画一个圆形','draw square','绘制 正方形'])
def test_shape_request_cannot_conflict_with_retained_svg_or_spec(app,monkeypatch,message):
    state=imported(app)
    app.default_process(state['id'],state['revision'])
    before=copy.deepcopy(app._session(state['id']))
    monkeypatch.setattr(web,'get_api_key',lambda:'configured')
    with pytest.raises(web.SVGError,match='更换 SVG'):
        app.chat(state['id'],before['revision'],message)
    assert app._session(state['id'])==before and app.active is None


def finished(app,ident):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        state=app.state(ident)
        if not state['busy']:return state
        time.sleep(.01)
    pytest.fail('后台任务未结束')


def test_clear_resets_server_context_and_keeps_assets_geometry_and_history(app):
    state=imported(app)
    state=app.chat(state['id'],1,'宽度30，自定义机密工艺 custom_rule')
    before=copy.deepcopy(app._session(state['id']))
    before['jobs']=[{'id':'svg_web_'+'e'*32,'status':'final','files':[]}]
    stored=app._session(state['id'])
    stored.update(jobs=before['jobs'],spec={'old':True},report={'old':True})
    stored['logs']=[{'text':'old context'}]
    result=app.clear_context(state['id'],state['revision'])
    raw=app._session(state['id'])
    assert result['revision']==state['revision']+1 and not result['messages'] and not result['logs']
    assert result['spec'] is None and result['report'] is None
    assert raw['assets']==before['assets'] and raw['settings']==before['settings']
    assert raw['prepared']['trajectory']==before['prepared']['trajectory']
    assert raw['jobs']==before['jobs']
    assert 'custom_rule' not in raw['request'] and 'custom_rule' not in raw['prepared']['request_text']
    app.sessions.clear()
    assert not app.state(state['id'])['messages']


def test_next_chat_after_clear_does_not_refine_old_spec_or_request(app,monkeypatch):
    state=imported(app)
    state=app.chat(state['id'],1,'旧工艺 old_custom_rule')
    app._session(state['id'])['spec']={'old':True}
    state=app.clear_context(state['id'],state['revision'])
    monkeypatch.setattr(web,'get_api_key',lambda:'test-only')
    def understand(self,request,**options):
        assert 'old_custom_rule' not in request
        return RequirementUnderstander()._template_spec(request,build_io_list(options['device_model'])[0],options['device_model'],options['task_id'])
    def result(self,request,**options):return {'spec':understand(self,request,**options),'report':{'pending':[]}}
    monkeypatch.setattr(RequirementUnderstander,'understand',result)
    monkeypatch.setattr(RequirementUnderstander,'refine',lambda *a,**k:pytest.fail('不得细化旧规格'))
    app.chat(state['id'],state['revision'],'完成后点亮指示灯')
    final=finished(app,state['id'])
    assert final['phase']=='ready' and final['messages'][-1]['text'].startswith('工艺目标：')


def test_default_process_needs_no_llm_and_preserves_aml_io_and_selection(app,monkeypatch):
    state=imported(app)
    state=app.plan(state['id'],1,[1,3],state['settings'])
    state=app.chat(state['id'],2,'旧工艺 old_custom_rule')
    monkeypatch.setattr(RequirementUnderstander,'_call',lambda *a,**k:pytest.fail('默认工艺不得调用 LLM'))
    ready=app.default_process(state['id'],state['revision'])
    assert ready['phase']=='ready' and ready['spec']
    assert ready['spec']['io_list']==build_io_list(ready['device_model'])[0]
    assert ready['selected_paths']==[1,3]
    assert 'old_custom_rule' not in ready['request']
    assert 'old_custom_rule' not in app._session(state['id'])['prepared']['request_text']
    text=ready['messages'][-1]['text']
    assert '急停' in text and '完成后' in text and len(text)<160
    assert 'io_list' not in text and 'task_id' not in text


def test_default_process_can_feed_generation_without_chat(app,monkeypatch):
    state=imported(app)
    ready=app.default_process(state['id'],1)
    monkeypatch.setattr(web,'get_api_key',lambda:'test-only')
    def solve(snapshot,directory,log):
        assert snapshot['spec']['io_list']==build_io_list(snapshot['device_model'])[0]
        assert snapshot['prepared']['trajectory']['params']['selected_paths']==[1,2,3,4]
        assert '急停时停止绘制' in snapshot['spec']['task_goal']
        (directory/'final').mkdir()
        (directory/'final/plcopen.xml').write_text('<project/>')
        (directory/'simulation').mkdir()
        (directory/'simulation/build.json').write_text('{"runtime_ready":true,"runtime_problems":[]}')
        return {'status':'final'}
    monkeypatch.setattr(app,'_solve',solve)
    monkeypatch.setattr(app,'_generation_validation',lambda path:{'status':'passed'})
    app.generate(ready['id'],ready['revision'])
    assert finished(app,ready['id'])['phase']=='completed'


@pytest.mark.parametrize('method',['clear_context','default_process'])
def test_context_mutations_reject_busy_and_stale_pages(app,method):
    state=imported(app)
    with pytest.raises(web.WorkbenchError,match='版本'):
        getattr(app,method)(state['id'],0)
    app._session(state['id'])['busy']=True
    with pytest.raises(web.WorkbenchError,match='正在处理'):
        getattr(app,method)(state['id'],1)


def test_default_requires_selected_trajectory(app):
    state=imported(app)
    state=app.plan(state['id'],1,[],state['settings'])
    with pytest.raises(web.WorkbenchError,match='轨迹'):app.default_process(state['id'],2)
    assert app.clear_context(state['id'],2)['selected_paths']==[]


def test_summary_keeps_custom_intent_without_identifiers_or_contract_mutation():
    spec={'task_goal':'逆时针绘制，按cmd_draw按钮启动；quickstop时立即停止，plot_done表示结束，x_fb为位置反馈。',
          'io_list':[{'name':'custom_signal'}]}
    before=copy.deepcopy(spec)
    text=process_summary(spec)
    assert '逆时针' in text and '急停' in text and '立即停止' in text
    assert all(name not in text for name in ('cmd_draw','quickstop','plot_done','x_fb','io_list'))
    assert spec==before


def test_legacy_verbose_summary_is_simplified_only_for_display(app):
    state=imported(app)
    old='需求规格已整理，请核对。\n工艺目标: 按cmd_draw启动，quickstop停止。\nio_list: 26 点'
    app._session(state['id'])['messages'].append({'role':'agent','text':old})
    shown=app.state(state['id'])['messages'][-1]['text']
    assert shown.startswith('工艺目标：') and '急停' in shown and 'cmd_draw' not in shown
    assert app._session(state['id'])['messages'][-1]['text']==old


def test_default_and_clear_http_use_existing_token_and_revision_guards(app):
    state=imported(app)
    server=web.make_server(app,0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    client=requests.Session();client.trust_env=False
    base='http://127.0.0.1:'+str(server.server_port)
    try:
        body={'session':state['id'],'revision':1}
        assert client.post(base+'/api/context/clear',json=body).status_code==403
        headers={'X-Agent-Token':app.token}
        response=client.post(base+'/api/process/default',json=body,headers=headers)
        assert response.status_code==200 and response.json()['phase']=='ready'
        assert client.post(base+'/api/context/clear',json=body,headers=headers).status_code==409
        response=client.post(base+'/api/context/clear',json=dict(body,revision=2),headers=headers)
        assert response.status_code==200 and response.json()['messages']==[]
    finally:client.close();server.shutdown();server.server_close();thread.join(timeout=2)
