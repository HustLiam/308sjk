"""Studio 接入的任务隔离、IO 所有权和真实验收记录。"""
import json
import base64
import copy
import hashlib
from types import SimpleNamespace
import http.client
import threading
import time
import pytest

from test_workbench_motion import delivery, Client, wait_until
from agent.motion import Motion
from agent.web import WorkbenchError
from agent.task_inputs import load_task_spec
from agent.studio_web import read_signals, save_verification


def test_requirement_folder_takes_priority_and_legacy_task_keeps_working(delivery):
    _, _, _, directory = delivery
    legacy = load_task_spec(directory)
    assert legacy["axes"] and all(p.get("address") for p in legacy["io_list"])
    target = directory/"requirements/requirement_spec.json"
    target.parent.mkdir()
    target.write_text(json.dumps(dict(legacy, task_goal="new layout")), encoding="utf-8")
    assert load_task_spec(directory)["task_goal"] == "new layout"


def test_manual_controls_keep_enable_and_reset_trigger_pulse(delivery):
    app, ident, task, directory = delivery
    client = Client()
    motion = Motion(directory, ident, "unused", client=client, mode="manual")
    app.motion = motion
    motion.start()
    wait_until(lambda:motion.status()["status"]=="manual")
    assert not client.bits[motion.bits["run"]]
    spec_point = next(p for p in motion.spec["io_list"] if p["name"]=="cmd_draw")
    spec_point["trigger"] = True
    app.studio_signal(ident, 0, task, "run", True)
    app.studio_signal(ident, 0, task, "cmd_draw", True)
    wait_until(lambda:client.bits[motion.bits["cmd_draw"]])
    wait_until(lambda:not client.bits[motion.bits["cmd_draw"]])
    assert client.bits[motion.bits["run"]]
    snapshot = app.studio_state(ident)
    assert snapshot["writable"] and snapshot["trace"] and snapshot["values"]
    with pytest.raises(WorkbenchError):app.studio_signal(ident,0,task,"plot_done",True)
    other=app.create_session()["id"]
    with pytest.raises(WorkbenchError):app.studio_signal(other,0,task,"run",True)
    motion.stop()
    assert not client.bits[motion.bits["run"]]
    assert motion.verification["verdict"]=="UNVERIFIED"
    state=app.studio_state(ident)
    assert not state["writable"] and state["records"][0]["samples"]>0
    app.motion=None
    assert app.studio_state(ident)["trace"]


def test_automatic_simulation_does_not_allow_hmi_writes(delivery):
    _,ident,_,directory=delivery
    motion=Motion(directory,ident,"unused",client=Client())
    motion.start()
    try:
        wait_until(lambda:motion.status()["status"]=="running")
        with pytest.raises(ValueError,match="只读"):motion.signal_command("run",False)
    finally:motion.stop()
    assert motion.verification["verdict"]=="UNVERIFIED"


def test_missing_real_edge_cannot_pass_and_failure_keeps_diagnostics(delivery):
    _,_,_,directory=delivery
    spec=load_task_spec(directory)
    spec["acceptance"]=[{"id":"END", "desc":"完成时序", "type":"event_delay",
        "from":{"signal":"cmd_draw","edge":"rising"},
        "to":{"signal":"plot_done","edge":"rising"},"value":10,"op":"<="}]
    verdict=save_verification(directory,"20261020_000000_12345678",spec,
        [{"t":0,"cmd_draw":True,"plot_done":True}],"completed","verify",None)
    assert verdict["verdict"]=="FAIL" and "真实" in verdict["details"][0]["evidence"]
    assert (directory/"simulation/verifications"/verdict["id"]/"report.md").is_file()
    assert set(verdict["sha256"])=={"xml","scene","io_map"}


def test_communication_error_does_not_turn_into_zero_values(delivery):
    _,_,_,directory=delivery
    client=Client();client.fail=True
    with pytest.raises(ConnectionError):read_signals(client,load_task_spec(directory))


def external_files(directory, configured=False):
    files = {key:{"name":path.name,"data":base64.b64encode(path.read_bytes()).decode()}
             for key,path in (("scene",directory/"final/scene.spec.json"),("xml",directory/"final/plcopen.xml"))}
    if configured:
        files["spec"]={"name":"requirement_spec.json","data":base64.b64encode(
            json.dumps(load_task_spec(directory)).encode()).decode()}
    return files


def test_external_pair_loads_without_changing_generated_delivery(delivery):
    app,ident,task,directory=delivery
    original=(directory/"final/plcopen.xml").read_bytes()
    state=app.import_simulation(ident,0,external_files(directory))
    imported=state["workflow"]["task"]
    assert imported!=task and state["workflow"]["simulation"]["status"]=="ready"
    assert state["workflow"]["source"]=="external" and not state["workflow"]["verification_configured"]
    assert (directory/"final/plcopen.xml").read_bytes()==original
    with pytest.raises(WorkbenchError,match="未配置"):app.control_motion(ident,0,"start",imported,mode="verify")
    restored=app.select_simulation(ident,0,task)
    assert restored["workflow"]["task"]==task


def test_external_pair_accepts_matching_test_spec_and_rejects_invalid_xml(delivery):
    app,ident,_,directory=delivery
    state=app.import_simulation(ident,0,external_files(directory,True))
    assert state["workflow"]["verification_configured"]
    files=external_files(directory)
    files["xml"]["data"]=base64.b64encode(b"<project/>").decode()
    before={p.name for p in app.root.glob("svg_web_*")}
    with pytest.raises(WorkbenchError,match="XML"):app.import_simulation(ident,0,files)
    assert before=={p.name for p in app.root.glob("svg_web_*")}


def test_builtin_legacy_scene_upgrade_preserves_original_files(delivery):
    app,ident,task,directory=delivery
    stored=app._session(ident)
    library=app.assets.library(stored["assets"]["library"])
    next(p for p in library["contract"]["types"]["gantry_xyz"]["params"] if p["name"]=="travel_z")["minimum"]=.01
    stored["assets"]["library"]=app.assets.put("library","legacy.json",app.assets.encode(library))
    path=directory/"final/scene.spec.json";scene=json.loads(path.read_text())
    next(a for a in scene["assets"] if a["type"]=="gantry_xyz")["params"]["travel_z"]=.011
    path.write_text(json.dumps(scene));original=path.read_bytes();xml=(directory/"final/plcopen.xml").read_bytes()
    app._save(stored);app.sessions.clear()
    state=app.state(ident)
    assert state["revision"]==1 and state["workflow"]["source"]=="migrated"
    assert state["workflow"]["task"]!=task and state["workflow"]["current"]
    updated=app.root/state["workflow"]["task"]
    assert (updated/"final/plcopen.xml").read_bytes()==xml
    assert path.read_bytes()==original
    new_scene=json.loads((updated/"final/scene.spec.json").read_text())
    assert next(a for a in new_scene["assets"] if a["type"]=="gantry_xyz")["params"]["travel_z"]==.01
    again=app.state(ident)
    assert again["workflow"]["task"]==state["workflow"]["task"]


def test_transient_runtime_busy_is_retried_before_start(delivery,monkeypatch):
    app,ident,task,directory=delivery
    probes=iter([{"runtime":{"status":"BUSY"}},
                 {"runtime":{"status":"RUNNING"},"prog_id":2,
                  "modbus":{"host":"unused","port":502},
                  "deployment":{"sha256":hashlib.sha256((directory/"final/plcopen.xml").read_bytes()).hexdigest()}}])
    app.runtime=SimpleNamespace(status=lambda:next(probes))
    started=[]
    monkeypatch.setattr('agent.motion.Motion',lambda *a,**kw:SimpleNamespace(start=lambda:started.append(True),stop=lambda:None))
    app._start_motion(ident,task)
    assert started==[True]


def test_precision_path_uses_real_physics_without_rewriting_raw_plc_samples(delivery):
    _,_,_,directory=delivery
    spec=load_task_spec(directory)
    spec["acceptance"]=[{"id":"AC1","desc":"量程","type":"sim_health"}]
    (directory/"trajectory.json").write_text(json.dumps({"steps":[{"no":1,"to":[58.62,28.93,10]}],"pen_z":{"down":0}}))
    raw=dict(read_signals(Client(),spec),t=0,x_fb=59,y_fb=29,z_fb=9)
    physics=[{"t":.01,"x_fb":58.62,"y_fb":28.93,"z_fb":10,"pen_down":False}]
    result=save_verification(directory,"20261020_000001_12345678",spec,[raw],"completed","verify",None,physics)
    assert result["verdict"]=="PASS"
    assert result["details"][-1]["source"]=="mujoco_joint_feedback"
    path=directory/"simulation/verifications"/result["id"]
    assert json.loads((path/"trace.json").read_text())[0]["z_fb"]==9
    assert json.loads((path/"physical_trace.json").read_text())[0]["z_fb"]==10


def test_snapshot_polling_reuses_http_connection_and_errors_close_it(delivery):
    from agent.web import make_server
    app,ident,_,_=delivery
    server=make_server(app,0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    client=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
    try:
        client.request('GET','/api/config');response=client.getresponse();response.read()
        assert response.version==11
        port=client.sock.getsockname()[1]
        for _ in range(10):
            client.request('GET','/api/studio/state?session='+ident)
            response=client.getresponse();response.read()
            assert response.status==200 and client.sock.getsockname()[1]==port
        for _ in range(10):
            client.request('POST','/api/studio/signal',body='{}',headers={'Content-Type':'application/json'})
            response=client.getresponse();body=response.read()
            assert response.status==403 and response.will_close
            assert json.loads(body)['error']
    finally:
        client.close();server.shutdown();server.server_close();thread.join(2)
