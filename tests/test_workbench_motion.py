"""真实 MuJoCo + 假 Modbus：验证指令来源、反馈、停止与任务隔离，不访问真实设备。"""
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'src'))
from agent.web import SVGWorkbench,WorkbenchError
from agent.motion import Motion
from agent.aml_parser import build_io_list
from agent.position_control import convert_position_program,repair_svg_sequence
from agent.scene_gen import SceneSpecGenerator
from agent.simulation import build_scene


@pytest.fixture
def delivery(tmp_path):
    app=SVGWorkbench(tmp_path)
    state=app.create_session();stored=app._session(state['id'])
    task='svg_web_'+'c'*32;directory=tmp_path/task
    (directory/'final').mkdir(parents=True)
    app._snapshot_assets(stored,directory)
    spec=json.loads((REPO/'examples/specs/plotter3axis.spec.json').read_text(encoding='utf-8'))
    spec.update(io_list=build_io_list(stored['device_model'])[0],prog_id=2)
    scene=SceneSpecGenerator().generate(spec,stored['device_model'])['scene']
    xml=convert_position_program((REPO/'src/plc/plotter3axis.xml').read_text(encoding='utf-8'),stored['device_model'])
    for path,value in [('request.json',spec),('final/scene.spec.json',scene)]:
        (directory/path).write_text(json.dumps(value),encoding='utf-8')
    (directory/'final/plcopen.xml').write_text(xml,encoding='utf-8')
    built=build_scene(directory)
    stored['jobs']=[{'id':task,'revision':0,'status':'final','files':[],'simulation':built}]
    yield app,state['id'],task,directory
    app.close()


class Client:
    def __init__(self):
        self.bits=[False]*48;self.bits[8]=True;self.bits[15]=True
        self.registers=[0]*32;self.registers[20]=2
        self.registers[3:6]=[3500,2500,1000]
        self.writes=[];self.feedback=[];self.fail=False
    def connect(self):return True
    def close(self):pass
    def response(self,**kw):return SimpleNamespace(isError=lambda:self.fail,**kw)
    def read_coils(self,address,count,**kw):return self.response(bits=self.bits[address:address+count])
    def write_coils(self,address,values,**kw):
        self.writes.append((address,list(values)))
        self.bits[address:address+len(values)]=values
        return self.response()
    def read_holding_registers(self,address,count,**kw):return self.response(registers=self.registers[address:address+count])
    def write_registers(self,address,values,**kw):
        self.feedback.append((address,list(values)))
        self.registers[address:address+len(values)]=values
        return self.response()


def wait_until(check,timeout=4):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if check():return
        time.sleep(.02)
    raise AssertionError('condition timed out')


def test_motion_reads_real_commands_writes_feedback_and_stops(delivery):
    app,ident,task,directory=delivery
    client=Client();motion=Motion(directory,ident,'unused',client=client)
    app.motion=motion;motion.start()
    wait_until(lambda:motion.status()['positions'].get('X',0)>.06)
    assert motion.status()['positions']['Y']>0
    with pytest.raises(WorkbenchError,match='先停止模型运动'):app.clear_context(ident,0)
    assert client.feedback and all(a==0 and len(v)==3 for a,v in client.feedback)
    result=app.control_motion(ident,-1,'stop',task) # 停止不受旧 revision 阻挡。
    assert result['motion']['status']=='stopped' and not motion.alive
    frozen=motion.snapshot();time.sleep(.1);assert motion.snapshot()==frozen
    assert not client.bits[0] and not client.bits[16] and client.bits[5]
    assert client.bits[15] # 同组非目标位保留。
    assert all(a%16==0 and len(v)==16 for a,v in client.writes)
    assert json.loads((directory/'simulation/motion.json').read_text(encoding='utf-8'))['active'] is False
    app.motion=None
    assert app.motion_status(ident)['status']=='stopped'
    restored=app.motion_frame(ident,task,0,None)
    assert restored['transforms'] and not restored['active']


def test_wrong_identity_never_sends_commands(delivery):
    _,ident,_,directory=delivery
    client=Client();client.registers[20]=99
    motion=Motion(directory,ident,'unused',client=client);motion.start()
    wait_until(lambda:not motion.alive)
    assert motion.status()['status']=='failed' and '程序已改变' in motion.status()['error']
    assert not client.writes and not client.feedback


def test_communication_loss_ends_worker_and_reports_stop_failure(delivery):
    _,ident,_,directory=delivery
    client=Client();motion=Motion(directory,ident,'unused',client=client);motion.start()
    wait_until(lambda:motion.status()['status']=='running')
    client.fail=True;wait_until(lambda:not motion.alive)
    assert motion.status()['status']=='failed' and '停止确认失败' in motion.status()['error']


def test_reset_is_released_before_waiting_for_enable(delivery):
    _,ident,_,directory=delivery
    class ResetBlocksEnable(Client):
        def __init__(self):
            super().__init__()
            self.bits[8]=False
            self.reset_seen=False
        def write_coils(self,address,values,**kw):
            result=super().write_coils(address,values,**kw)
            assert not (self.bits[7] and self.bits[0]), 'reset must finish before enabling'
            self.reset_seen |= self.bits[7]
            self.bits[8]=self.reset_seen and not self.bits[7] and self.bits[0]
            return result
    client=ResetBlocksEnable()
    motion=Motion(directory,ident,'unused',client=client)
    try:
        motion.start()
        wait_until(lambda:motion.status()['status']=='running')
        assert client.reset_seen and not client.bits[7] and client.bits[8]
    finally:
        motion.stop()


def test_receipt_must_match_current_xml_before_creating_bridge(delivery):
    app,ident,task,_=delivery
    app.runtime=SimpleNamespace(status=lambda:{'runtime':{'status':'RUNNING'},'prog_id':2,'deployment':{'sha256':'wrong'}})
    with pytest.raises(WorkbenchError,match='当前 XML 尚未成功部署'):app._start_motion(ident,task)
    assert app.motion is None
    other=app.create_session()['id']
    with pytest.raises(WorkbenchError):app.control_motion(other,0,'start',task)


def test_sparse_or_reversed_feedback_is_rejected(delivery):
    _,ident,_,directory=delivery
    path=directory/'simulation/scene.spec.json';scene=json.loads(path.read_text())
    scene['io_map'][0]['modbus']['plc_addr']='%QW19';path.write_text(json.dumps(scene))
    with pytest.raises(ValueError,match='连续地址'):Motion(directory,ident,'unused',client=Client())


def test_live_render_uses_matching_task_snapshot(delivery,monkeypatch):
    app,ident,task,_=delivery
    snapshot={'qpos':[.2,.3,.01],'ink':[]};received={}
    app.motion=SimpleNamespace(session=ident,task=task,snapshot=lambda:snapshot,stop=lambda:None)
    monkeypatch.setattr('agent.simulation.render_scene',lambda *a,**kw:received.update(kw) or b'png')
    assert app.scene_frame(ident,task)==b'png' and received['snapshot']==snapshot


def test_browser_geometry_and_incremental_ink_are_task_scoped(delivery):
    app,ident,task,directory=delivery
    scene=app.scene_view(ident,task)
    assert len(scene['geoms'])==len(scene['transforms']) and len(scene['geoms'])>3
    assert all(len(t)==12 for t in scene['transforms'])
    motion=Motion(directory,ident,'unused',client=Client());app.motion=motion
    motion.ink=[[0.,0.,.042],[.001,0.,.042]];motion.strokes=[1,1]
    frame=app.motion_frame(ident,task,99,'old-run')
    assert frame['ink_reset'] and len(frame['ink'])==2
    assert app.motion_frame(ident,task,1,frame['run'])['ink']==[[.001,0.,.042]]
    other=app.create_session()['id']
    with pytest.raises(WorkbenchError):app.scene_view(other,task)


def test_svg_repair_moves_completion_after_interpolators_and_keeps_route():
    import xml.etree.ElementTree as ET
    ns='http://www.plcopen.org/xml/tc6_0201'
    body='''CASE pl_step OF
  1: pl_tx := 23; pl_ty := 47; pl_tz := 0;
END_CASE;
IF (pl_step <> 0) AND NOT ix_x.Busy AND NOT ix_y.Busy AND NOT ix_z.Busy
   AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
  pl_step := pl_step + 1;
END_IF;
ix_x(fire := go_x_exe, pos_target := pl_tx);
ix_y(fire := go_y_exe, pos_target := pl_ty);
ix_z(fire := go_z_exe, pos_target := pl_tz);
IF ix_x.Done OR ix_x.Aborted THEN go_x_exe := FALSE; hm_x_exe := FALSE; rel_x_exe := FALSE; END_IF;
IF ix_y.Done OR ix_y.Aborted THEN go_y_exe := FALSE; hm_y_exe := FALSE; rel_y_exe := FALSE; END_IF;
IF ix_z.Done OR ix_z.Aborted THEN go_z_exe := FALSE; hm_z_exe := FALSE; rel_z_exe := FALSE; END_IF;
(* CSP_POSITION_OUTPUT: location *)
x_cmd := ix_x.Setpoint;
'''
    root=ET.Element('{'+ns+'}project');pou=ET.SubElement(root,'{'+ns+'}pou',name='PLC_PRG')
    st=ET.SubElement(ET.SubElement(pou,'{'+ns+'}body'),'{'+ns+'}ST')
    ET.SubElement(st,'{'+ns+'}xhtml').text=body
    repaired=repair_svg_sequence(ET.tostring(root,encoding='unicode'))
    text=''.join(ET.fromstring(repaired).find('.//{'+ns+'}ST').itertext())
    assert text.index('ix_z(fire')<text.index('IF (pl_step <> 0)')
    assert 'ABS(x_fb_i - pl_tx) <= 1' in text
    assert 'IF NOT draw_active THEN' in text
    assert 'pl_tx := 23; pl_ty := 47; pl_tz := 0;' in text
    assert repair_svg_sequence(repaired)==repaired
