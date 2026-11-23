"""Cancellation stops actual work and retains per-run wall-clock timings."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from test_svg_workbench import app, imported, wait_finished
from test_workbench_validation import execution, solve
from test_workbench_motion import delivery
from agent.client import BigModelClient
from agent.generation_control import GenerationControl, GenerationCancelled
from agent import web


def test_cancel_interrupts_a_silent_model_stream_and_never_retries():
    entered, release = threading.Event(), threading.Event()
    calls, errors = [], []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            calls.append(1)
            self.send_response(200)
            self.send_header('Content-Type','text/event-stream')
            self.end_headers()
            chunk={'choices':[{'delta':{'content':'x'*2000}}]}
            self.wfile.write(('data: '+json.dumps(chunk)+'\n\n: '+'.'*1024+'\n\n').encode())
            self.wfile.flush()
            release.wait(4)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    serving=threading.Thread(target=server.serve_forever,daemon=True);serving.start()
    client=BigModelClient('unused',base_url='http://127.0.0.1:'+str(server.server_port))
    control=client.control=GenerationControl()
    def call():
        try:client.chat_completions({},prefer_stream=True,on_chunk=lambda *args:entered.set())
        except Exception as exc:errors.append(exc)
    worker=threading.Thread(target=call,daemon=True);worker.start()
    try:
        assert entered.wait(2)
        control.request();worker.join(2)
        assert not worker.is_alive()
        assert len(errors)==1 and isinstance(errors[0],GenerationCancelled)
        with pytest.raises(GenerationCancelled):client.chat_completions({})
        assert calls==[1] and control.responses==[]
    finally:
        release.set();worker.join(3)
        client.session.close();server.shutdown();server.server_close();serving.join(2)


def test_cancel_from_progress_callback_is_not_swallowed():
    client=BigModelClient('unused')
    control=client.control=GenerationControl()
    class Response:
        status_code=200
        closed=False
        def iter_lines(self,**kwargs):
            yield 'data: '+json.dumps({'choices':[{'delta':{'content':'partial'}}]})
        def close(self):self.closed=True
    response=Response()
    client.session.post=lambda *args,**kwargs:response
    def chunk(*args):control.request();control.check()
    with pytest.raises(GenerationCancelled):client.chat_completions({},prefer_stream=True,on_chunk=chunk)
    assert response.closed


def test_cancel_keeps_busy_until_worker_cleanup_and_persists_timings(app,monkeypatch):
    monkeypatch.setattr(web,'get_api_key',lambda:'unused')
    state=imported(app);state=app.default_process(state['id'],state['revision'])
    entered, release=threading.Event(),threading.Event()
    def run(snapshot,directory,log):
        (directory/'candidate.xml').write_text('<project/>')
        control=app.generation_control
        with control.stage('simulation'):
            entered.set();assert release.wait(4)
            control.check()
    monkeypatch.setattr(app,'_solve',run)
    try:
        state=app.generate(state['id'],state['revision']);task=state['jobs'][-1]['id']
        assert entered.wait(1)
        other=app.create_session()
        with pytest.raises(web.WorkbenchError):app.stop_generation(other['id'],task)
        stopped=app.stop_generation(state['id'],task)
        assert stopped['phase']=='stopping' and stopped['busy']
        assert app.stop_generation(state['id'],task)['busy']
        with pytest.raises(web.WorkbenchError):app.generate(state['id'],state['revision'])
    finally:release.set()
    stopped=wait_finished(app,state['id'])
    job=stopped['jobs'][-1]
    assert stopped['phase']=='cancelled' and job['status']=='cancelled' and not job['files']
    assert app.active is None and not stopped['can_stop_generation']
    assert (app.root/task/'candidate.xml').is_file() and not (app.root/task/'final').exists()
    timing=json.loads((app.root/task/'timing.json').read_text())
    assert timing==job['timing'] and timing['generation']['finished_at']
    assert timing['simulation'][0]['finished_at']
    assert timing['generation']['elapsed_seconds']>=timing['simulation'][0]['elapsed_seconds']
    assert app.stop_generation(state['id'],task)['phase']=='cancelled'
    app.sessions.clear()
    assert app.state(state['id'])['jobs'][-1]['timing']==timing
    # Each new generation receives an independent timer and rejects a delayed stop.
    entered.clear();release.clear()
    try:
        next_state=app.generate(state['id'],state['revision'])
        assert entered.wait(1)
        with pytest.raises(web.WorkbenchError):app.stop_generation(state['id'],task)
        assert not app.generation_control.cancelled.is_set()
        assert next_state['jobs'][-1]['timing']['generation']['started_at']!=timing['generation']['started_at']
        app.stop_generation(state['id'],next_state['jobs'][-1]['id'])
    finally:release.set()
    assert wait_finished(app,state['id'])['jobs'][0]['timing']==timing


def test_cancel_during_deploy_waits_then_stops_runtime_without_acceptance(execution):
    e=execution;control=e.orch.control=GenerationControl();calls=[]
    def action(name,raw=None):
        calls.append(name)
        if name=='deploy':control.request()
        return {'status':'OK'}
    e.runtime.action=action
    with pytest.raises(GenerationCancelled):solve(e)
    assert calls==['deploy','stop'] and not e.motions and not e.generator.feedback
    assert not (e.directory/'final').exists()


def test_cancel_during_physics_stops_motion_saves_trace_and_no_repair(execution):
    e=execution;records=[]
    control=e.orch.control=GenerationControl(lambda name,record:records.append((name,record)))
    motion_class=__import__('agent.workbench_validation',fromlist=['Motion']).Motion
    class Running(motion_class):
        def start(self):self.alive=True;control.request()
    original=e.runtime.action
    e.runtime.action=lambda name,raw=None:({'status':'OK'} if name=='stop' else original(name,raw))
    e.orch.on_motion=lambda motion:None
    import agent.workbench_validation as validation
    original_motion=validation.Motion;validation.Motion=Running
    try:
        with pytest.raises(GenerationCancelled):solve(e)
    finally:validation.Motion=original_motion
    assert len(e.motions)==1 and not e.motions[0].alive and not e.generator.feedback
    assert (e.directory/'iter_001/cancelled-motion.json').is_file()
    assert (e.directory/'iter_001/physical_trace.json').is_file()
    assert not (e.directory/'final').exists()
    assert len(records)==2 and records[-1][1]['finished_at']


def test_cancel_endpoint_requires_workbench_token(app):
    server=web.make_server(app,0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    client=requests.Session();client.trust_env=False
    url='http://127.0.0.1:'+str(server.server_port)+'/api/generate/stop'
    try:
        assert client.post(url,json={}).status_code==403
        assert client.post(url,json={},headers={'X-Agent-Token':app.token,'Origin':'http://evil.example'}).status_code==403
        state=app.create_session()
        assert client.post(url,json={'session':state['id'],'task':'stale'},headers={'X-Agent-Token':app.token}).status_code==409
    finally:
        client.close();server.shutdown();server.server_close();thread.join(2)


@pytest.mark.parametrize('stop_fails',[False,True])
def test_cancel_at_delivery_boundary_withdraws_final_and_checks_cleanup(app,monkeypatch,stop_fails):
    monkeypatch.setattr(web,'get_api_key',lambda:'unused')
    state=imported(app);state=app.default_process(state['id'],state['revision'])
    cleanups=[]
    def cleanup():
        cleanups.append(1)
        if stop_fails:raise RuntimeError('stop not confirmed')
    def run(snapshot,directory,log):
        (directory/'final').mkdir()
        (directory/'final/plcopen.xml').write_text('<project/>')
        app.generation_control.cleanup=cleanup
        app.generation_control.request()
        return {'status':'final'}
    monkeypatch.setattr(app,'_solve',run)
    monkeypatch.setattr(app,'_generation_validation',lambda *args:{'status':'passed'})
    app.generate(state['id'],state['revision']);state=wait_finished(app,state['id'])
    job=state['jobs'][-1];directory=app.root/job['id']
    assert cleanups==[1] and job['status']==('failed' if stop_fails else 'cancelled')
    assert not job['files'] and not (directory/'final').exists()
    assert (directory/'cancelled-final/plcopen.xml').is_file()
    assert job['timing']['generation']['finished_at']
    if stop_fails:assert '停止未确认' in state['workflow_error']
