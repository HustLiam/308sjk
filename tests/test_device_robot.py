"""Real MuJoCo joint-space driver and independent angular IO checks."""
import copy
from pathlib import Path
import sys

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
mujoco=pytest.importorskip('mujoco')
from src.agent.scene_agent.common import SceneError
from src.agent.scene_agent.device_ir import parse_device_ir
from src.agent.scene_agent.rules import ComponentLibrary
from src.agent.scene_agent.simulation_tests import generate_test_plan,run_test_plan
from runtime.device_scene import build_device_scene,DeviceRuntime


@pytest.fixture(scope='module')
def robot(tmp_path_factory):
    folder=tmp_path_factory.mktemp('robot_driver')
    ir=parse_device_ir(Path(__file__).resolve().parents[1]/'examples/aml/factoryforge_sorting_reference.aml')
    ir.update(components=[],interfaces=[],connections=[],joints=[],assemblies=[],io=[],pending=[])
    ir['simulation_requirements']['checks']=[{'id':'joints','kind':'mechanical_joint','target':'*','description':'mechanical'},
        {'id':'io','kind':'io_binding','target':'*','description':'IO'}]
    ir['simulation_requirements']['response_timeout_s']=4
    def component(cid,kind,position,params=None):
        c={'id':cid,'name':cid,'type':kind,'parent_id':None if cid=='robot' else 'robot','role':None,
            'parameters':{k:{'value':v} for k,v in (params or {}).items()},'pose':{'position_m':position,'rpy_rad':[0,0,0]},'interfaces':[],'source':{}}
        ir['components'].append(c)
    component('robot','Controller',[0,0,0],{'estop_io_name':'estop','self_collision_policy':'excluded_for_joint_space_validation','environment_contact_policy':'not_in_joint_space_validation'})
    component('base','Base',[0,0,.5]);component('panel','Panel',[0,0,0])
    def point(name,cid,quantity,direction,typ,address,initial=False):
        p={'id':name,'name':name,'component_id':cid,'quantity':quantity,'dir':direction,'type':typ,'address':address,
           'writer':'plc' if direction=='output' else 'operator' if quantity in ('estop_ok','start') else 'simulation',
           'initial':initial,'physical_per_plc_unit':.001 if typ=='INT' else 1,'source':{}}
        if typ=='INT':p['range']=[-500,500]
        ir['io'].append(p)
    for name,quantity,direction,address in [('enable','enable_cmd','output','%QX0.0'),('reset','reset_cmd','output','%QX0.1'),('ready','ready','input','%QX1.0'),('fault','fault','input','%QX1.1')]:
        point(name,'robot',quantity,direction,'BOOL',address)
    point('estop','panel','estop_ok','input','BOOL','%QX2.0',True)
    point('panel_start','panel','start','input','BOOL','%QX2.1',False)
    for i in (1,2):
        cid='link'+str(i);jid='joint'+str(i)
        component(cid,'Link',[0,i*.1,.5],{'driven_joint_id':jid,'max_velocity_rad_s':1,'max_acceleration_rad_s2':2,
            'position_tolerance_rad':.008726646,'gravity_compensation':1})
        ir['joints'].append({'id':jid,'name':jid,'type':'hinge','parent_body':'base','child_body':cid,
            'axis':[0,0,1] if i==1 else [0,1,0],'axis_frame':'child','range':[-.5,.5],'initial':0,
            'parent_anchor_m':[0,i*.1,0],'child_anchor_m':[0,0,0]})
        point('cmd'+str(i),cid,'joint_position_cmd','output','INT','%QW'+str(i),0)
        point('fb'+str(i),cid,'joint_position_fb','input','INT','%QW'+str(i+8),0)
        point('inpos'+str(i),cid,'in_position','input','BOOL','%QX3.'+str(i-1))
    library=ComponentLibrary(folder/'library');entries={}
    for kind,behavior,geoms,physical in [('Controller','robot_control',[],{}),('Panel','panel',[],{}),
        ('Base','none',[{'name':'base','type':'box','size':[.02,.02,.02]}],{}),
        ('Link','joint_position',[{'name':'link','type':'box','size':[.01,.01,.01]}],{'mass_kg':1,'position_kp':5000,'damping':100})]:
        rule={'format':'308sjk.component_rule/0.1-draft','component_type':kind,'version':'0.1.0','parameter_bounds':{},
            'assumptions':['isolated synthetic fixture'],'unresolved':[],'model':{'behavior':behavior,'geoms':geoms,'physical_parameters':physical}}
        path=library.stage(rule);entries[kind]={'rule':rule,'directory':str(path)}
    meta,xml=build_device_scene(ir,library,folder/'scene',candidates=entries)
    return ir,meta,xml


def test_robot_all_modules_use_actual_state_and_encoding(robot):
    ir,meta,xml=robot;report=run_test_plan(ir,meta,xml,generate_test_plan(ir))
    assert report['complete'],[(m['label'],m['status'],[c for c in m['checks'] if not c['passed']]) for m in report['modules'] if m['status']!='passed']


def test_measured_feedback_is_not_a_command_echo_and_stop_is_bounded(robot):
    _,meta,xml=robot;r=DeviceRuntime(meta,xml)
    r.step({'enable':True,'cmd1':300},{'estop':True})
    assert r.signals['fb1']!=300 and not r.signals['inpos1']
    for _ in range(1200):r.step()
    assert r.signals['fb1']==pytest.approx(300,abs=9) and r.signals['inpos1']
    for _ in range(1000):r.step({'enable':False})
    assert abs(r.robot.states['link1']['velocity'])<1e-9
    assert abs(r.properties['link1']['velocity_rad_s'])<.01


def test_estop_fault_is_latched_and_reset_requires_cause_removed(robot):
    _,meta,xml=robot;r=DeviceRuntime(meta,xml)
    r.step({'enable':True,'reset':True},{'estop':False});assert r.signals['fault'] and not r.signals['ready']
    r.step({'reset':False},{'estop':True});assert r.signals['fault']
    r.step({'reset':True});assert not r.signals['fault'] and r.signals['ready']
    with pytest.raises(ValueError):r.step({'cmd1':501})
    with pytest.raises(ValueError):r.step(external_inputs={'fb1':10})


def test_robot_self_collision_scope_includes_pedestal_and_logical_contacts(robot):
    import xml.etree.ElementTree as ET
    _,meta,xml=robot;root=ET.fromstring(xml)
    assert root.find('./contact/exclude[@body1="c_base"][@body2="c_link1"]') is not None
    r=DeviceRuntime(meta,xml);r.step(external_inputs={'panel_start':True});assert r.signals['panel_start'] is True
    r.step(external_inputs={'panel_start':False});assert r.signals['panel_start'] is False


def test_visual_only_multigeometry_has_no_invented_mass():
    import xml.etree.ElementTree as ET
    from src.agent.scene_agent.common import finalize_visual_inertia
    root=ET.fromstring('<mujoco><worldbody><body name="lamp"><geom type="box" size=".1 .1 .1" mass="0"/><geom type="sphere" size=".05" mass="0" pos="0 0 .2"/></body></worldbody></mujoco>')
    body=root.find('.//body');finalize_visual_inertia(body)
    m=mujoco.MjModel.from_xml_string(ET.tostring(root,encoding='unicode'))
    assert m.body_mass[1]==0 and body.find('inertial') is not None


def test_mechanical_replay_is_continuous_fk_with_bounded_speed(robot,tmp_path):
    import numpy as np
    from src.agent.scene_agent.common import read_json
    ir,meta,xml=robot;plan=generate_test_plan(ir);module=plan['modules'][0]
    result=run_test_plan(ir,meta,xml,plan,[module['id']],output=tmp_path)
    assert result['status']=='passed'
    frames=read_json(tmp_path/result['modules'][0]['replay'])['frames']
    times=np.array([f['time_s'] for f in frames]);q=np.array([f['joint_positions']['joint1'] for f in frames])
    velocity=np.diff(q)/np.diff(times)
    assert len(frames)>100 and q[0]==q[-1]==0 and q.min()==-.5 and q.max()==.5
    assert np.max(np.abs(velocity))<=1.001 and np.max(np.abs(np.diff(q)))<=np.deg2rad(1.001)
    r=DeviceRuntime(meta,xml);parent=mujoco.mj_name2id(r.model,mujoco.mjtObj.mjOBJ_BODY,'c_base')
    child=mujoco.mj_name2id(r.model,mujoco.mjtObj.mjOBJ_BODY,'c_link1')
    for frame in frames:
        a,b=[np.array(frame['body_transforms'][bid]) for bid in (parent,child)]
        anchor=a[:3]+a[3:].reshape(3,3)@np.array([0,.1,0])
        assert np.allclose(anchor,b[:3],atol=1e-9)


def test_nonadjacent_overlap_is_detected_even_when_solver_excludes_contacts(robot,tmp_path):
    import hashlib
    import xml.etree.ElementTree as ET
    from src.agent.scene_agent.common import read_json
    ir,meta,xml=robot;meta=copy.deepcopy(meta);root=ET.fromstring(xml)
    # Two sibling moving links have no mounting exemption. Their original
    # solver exclusions must not conceal this deliberate geometry error.
    geom=root.find('.//body[@name="c_link2"]/geom');geom.set('pos','0 -.1 0')
    xml=ET.tostring(root,encoding='unicode');meta['model_xml_sha256']=hashlib.sha256(xml.encode()).hexdigest()
    plan=generate_test_plan(ir);report=run_test_plan(ir,meta,xml,plan,[plan['modules'][0]['id']],output=tmp_path)
    assert report['status']=='failed' and not report['complete']
    module=report['modules'][0];failure=next(c for c in module['checks'] if c['name']=='collision_free_path')
    assert failure['passed'] is False and failure['contacts'][0]['penetration_m']>0
    replay=read_json(tmp_path/module['replay'])
    assert len(replay['frames'])==1 and replay['frames'][-1]['collisions']


def test_enabled_robot_collision_exempts_only_connected_endpoints(robot,tmp_path):
    import xml.etree.ElementTree as ET
    ir,meta,_=robot;ir=copy.deepcopy(ir)
    ir['components'][0]['parameters']['self_collision_policy']['value']='enabled_non_adjacent'
    ir['components'][0]['parameters']['environment_contact_policy']['value']='enabled'
    entries={c['type']:{'rule':__import__('json').loads((Path(c['rule_directory'])/'rule.json').read_text()),'directory':c['rule_directory']} for c in meta['components'].values()}
    _,xml=build_device_scene(ir,ComponentLibrary(tmp_path/'library'),tmp_path/'scene',candidates=entries)
    root=ET.fromstring(xml)
    pairs={frozenset((e.get('body1'),e.get('body2'))) for e in root.findall('./contact/exclude')}
    assert pairs=={frozenset(('c_base','c_link1')),frozenset(('c_base','c_link2'))}
    assert root.find('./worldbody/geom').get('contype')!='0'
