"""Real MuJoCo model fixtures; Modbus transport is mocked, never online acceptance."""
import copy
from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))

import pytest

mujoco=pytest.importorskip('mujoco')
import numpy as np

from examples.scene_agent.reference_candidates import make_reference_candidates
from src.agent.scene_agent.common import SceneError, read_json, write_json
from src.agent.scene_agent.device_ir import parse_device_ir, facts_digest
from src.agent.scene_agent.rules import ComponentLibrary, ApprovalRequired
from src.agent.scene_agent.validation import validate_candidate, check_assembly, check_device_behaviors
from runtime.device_scene import build_device_scene, DeviceRuntime
from runtime.device_plc_link import DevicePlcLink

AML=Path(__file__).resolve().parents[1]/'examples/aml/factoryforge_sorting_reference.aml'


@pytest.fixture(scope='module')
def device(tmp_path_factory):
    folder=tmp_path_factory.mktemp('physical_device_test_only')
    ir=parse_device_ir(AML);library=ComponentLibrary(folder/'library');entries={}
    for rule in make_reference_candidates(ir):
        path=library.stage(rule,origin='isolated_automated_test_only')
        entries[rule['component_type']]={'rule':rule,'directory':str(path)}
    metadata,xml=build_device_scene(ir,library,folder/'scene',candidates=entries)
    return ir,library,entries,metadata,xml,folder


def test_scene_has_no_gantry_and_exact_aml_io(device):
    ir,_,_,meta,xml,_=device
    assert 'gantry' not in xml.lower()
    assert len(meta['io_map'])==19 and len(meta['products'])==8
    assert len(meta['rules'])==17  # Includes product blueprints in approval verification.
    assert [(p['plc_var'],p['address'],p['type']) for p in meta['io_map']]==[(p['name'],p['address'],p['type']) for p in ir['io']]
    assert len(meta['joints'])==1 and all(c['passed'] for c in meta['model_checks'])


def test_formal_build_requires_approval(device,tmp_path):
    ir,library,*_=device
    with pytest.raises(ApprovalRequired):build_device_scene(ir,library,tmp_path/'formal')


def test_actual_sorter_behavior(device):
    *_,meta,xml,folder=device
    report=check_device_behaviors(meta,xml)
    assert report['status']=='passed' and report['behavior_checks_complete']
    assert report['plc_acceptance']=='not_run'
    outlets=[e for e in report['events'] if e['event']=='outlet_entry']
    assert len(outlets)==2 and len({e['category'] for e in outlets})==2
    assert len({e['item_id'] for e in outlets})==2


def test_missing_product_pool_reports_component_before_dynamic_fixture(device,tmp_path):
    from src.agent.scene_agent.workflow import check_task
    *_,meta,xml,_=device
    meta=copy.deepcopy(meta);meta['products']=[]
    folder=tmp_path/'candidate_scene'
    write_json(tmp_path/'task.json',{'scene':{'model_frozen':False}})
    write_json(folder/'scene.physical.json',meta)
    (folder/'scene.xml').write_text(xml,encoding='utf-8')
    for name in ('behavior_replay.json','model.freeze.json'):write_json(folder/name,{'stale':True})
    with pytest.raises(SceneError,match='没有可运动的实例') as error:
        check_task(tmp_path,isolated_candidates=True)
    assert len(error.value.details['missing_products'])==2
    report=read_json(folder/'behavior_checks.json')
    assert report['status']=='failed' and not report['behavior_checks_complete']
    assert len(report['checks'])==2 and all(not c['passed'] for c in report['checks'])
    assert 'TypeError' not in str(error.value) and report['plc_acceptance']=='not_run'
    assert not (folder/'behavior_replay.json').exists() and not (folder/'model.freeze.json').exists()
    assert read_json(tmp_path/'task.json')['scene']['behavior_checks']=='failed'


def test_source_requires_edge_and_does_not_spawn_spontaneously(device):
    *_,meta,xml,_=device
    runtime=DeviceRuntime(meta,xml)
    for _ in range(20):runtime.step()
    assert not runtime.events
    for _ in range(20):runtime.step({'emitter_emit':True})
    assert len(runtime.events)==1
    runtime.step({'emitter_emit':False});runtime.step({'emitter_emit':True})
    assert len(runtime.events)==2


@pytest.mark.parametrize('name,value',[('sensor_low_detect',True),('pusher_extended',True),('counter_short',9)])
def test_model_feedback_cannot_be_spoofed(device,name,value):
    *_,meta,xml,_=device
    runtime=DeviceRuntime(meta,xml)
    with pytest.raises(SceneError,match='physical feedback'):runtime.step(external_inputs={name:value})
    with pytest.raises(SceneError,match='cannot write input'):runtime.step({name:value})


def test_candidate_and_smoke_only_cannot_freeze(device,tmp_path):
    *_,meta,xml,_=device
    runtime=DeviceRuntime(meta,xml)
    with pytest.raises(ApprovalRequired):runtime.freeze(tmp_path,{'status':'passed','behavior_checks_complete':True})
    assert not (tmp_path/'model.freeze.json').exists()


def test_model_hash_tampering_rejected(device):
    *_,meta,xml,_=device
    with pytest.raises(SceneError,match='differs'):DeviceRuntime(meta,xml+' ')


def make_mount_ir(ir):
    ir=copy.deepcopy(ir);ir.update(joints=[],assemblies=[],interfaces=[],connections=[],io=[],pending=[])
    root=copy.deepcopy(ir['components'][0]);root.update(id='base',type='MountFixture',parent_id=None,parameters={},interfaces=[])
    root['pose']={'position_m':[1,0,1],'rpy_rad':[0,0,np.pi/2]}
    child=copy.deepcopy(root);child.update(id='tool',name='Tool',parent_id=None)
    child['pose']={'position_m':[9,9,9],'rpy_rad':[0,0,0]}
    ir['components']=[root,child]
    for cid,name in [('base','top'),('tool','bottom')]:
        ir['interfaces'].append({'id':cid+'_mount','name':name,'component_id':cid,'type':'MechanicalMount','parameters':{}})
    ir['assemblies']=[{'id':'mount','parent_component':'base','child_component':'tool',
        'parent_interface':'base_mount','child_interface':'tool_mount','placement':'solve','offset':{}}]
    return ir


def test_interface_solver_accounts_for_local_mount_coordinates(device,tmp_path):
    ir,library,*_=device;ir=make_mount_ir(ir)
    rule={'format':'308sjk.component_rule/0.1-draft','component_type':'MountFixture','version':'0.1.0',
        'parameter_bounds':{},'assumptions':['test fixture only'],'unresolved':[],
        'model':{'geoms':[{'name':'base','type':'box','size':[.05,.05,.05]}],
                 'interfaces':[{'name':'top','position_m':[.1,0,0]},{'name':'bottom','position_m':[-.1,0,0]}]}}
    path=library.stage(rule);entries={'MountFixture':{'rule':rule,'directory':str(path)}}
    meta,xml=build_device_scene(ir,library,tmp_path/'mount',candidates=entries)
    assert np.asarray(meta['components']['tool']['world_transform'])[:3,3]==pytest.approx([1,.2,1])
    assert check_device_behaviors(meta,xml)['behavior_checks_complete'] is False
    ir['assemblies'][0]['placement']='check'
    with pytest.raises(SceneError,match='do not align'):build_device_scene(ir,library,tmp_path/'bad_mount',candidates=entries)


def test_internal_geometries_belong_to_moving_bodies(device,tmp_path):
    ir,library,*_=device;ir=make_mount_ir(ir);ir['assemblies']=[];ir['components']=ir['components'][:1];ir['interfaces']=[]
    rule={'format':'308sjk.component_rule/0.1-draft','component_type':'MountFixture','version':'0.2.0',
        'parameter_bounds':{},'assumptions':['passive test articulation'],'unresolved':[],
        'model':{'bodies':[{'name':'slider','position_m':[0,0,.2]}],
                 'geoms':[{'body':'slider','name':'finger','type':'box','size':[.02,.03,.04],'mass':.2}],
                 'joints':[{'name':'motion','body':'slider','type':'slide','axis':[1,0,0],'range':[0,.1]}]}}
    path=library.stage(rule);entries={'MountFixture':{'rule':rule,'directory':str(path)}}
    meta,xml=build_device_scene(ir,library,tmp_path/'articulated',candidates=entries)
    runtime=DeviceRuntime(meta,xml)
    gid=mujoco.mj_name2id(runtime.model,mujoco.mjtObj.mjOBJ_GEOM,'c_base__finger')
    assert mujoco.mj_id2name(runtime.model,mujoco.mjtObj.mjOBJ_BODY,runtime.model.geom_bodyid[gid])=='c_base__slider'
    assert runtime.model.nv==1


def test_body_mass_is_total_and_visuals_do_not_duplicate_it():
    from runtime.device_scene import _mass_geometries
    geoms=[{'name':'a','type':'box','size':[1,1,1]}, {'name':'b','type':'box','size':[.5,1,1]},
           {'name':'visual','type':'mesh','collision':False}]
    result=_mass_geometries(geoms,{'mass_kg':3})
    assert result[0]['mass']==pytest.approx(2) and result[1]['mass']==pytest.approx(1)
    assert 'mass' not in result[2] and 'mass' not in geoms[0]


def test_blender_instances_regenerate_at_each_parameter_set(device,tmp_path,monkeypatch):
    from src.agent.scene_agent import validation
    ir,library,*_=device;ir=make_mount_ir(ir);ir['assemblies']=[];ir['interfaces']=[]
    for c,width in zip(ir['components'],(.2,.4)):
        c['parameters']={'width_m':{'value':width}}
    rule={'format':'308sjk.component_rule/0.1-draft','component_type':'MountFixture','version':'0.3.0',
        'parameter_bounds':{'width_m':[.2,.4]},'assumptions':['test generator'],'unresolved':[],
        'model':{'blender_bodies':[{'name':'visual','body':'root','type':'mesh','mesh':'geometry/bodies/root.obj','collision':False}]},
        'blender_script':'def build(params, api):\n    pass'}
    calls=[]
    def fake_geometry(candidate,params,directory,**kwargs):
        calls.append(params['width_m']);directory=Path(directory);(directory/'bodies').mkdir(parents=True)
        # Distinct tetrahedra keep the test independent of Blender installation.
        (directory/'bodies/root.obj').write_text('v 0 0 0\nv '+str(params['width_m'])+' 0 0\nv 0 0.1 0\nv 0 0 0.1\nf 1 2 3\nf 1 4 2\nf 1 3 4\nf 2 4 3\n')
        return {'blender_version':'test'},[{'name':'fixture','passed':True}],[]
    monkeypatch.setattr(validation,'instantiate_geometry',fake_geometry)
    path=library.stage(rule)
    meta,_=build_device_scene(ir,library,tmp_path/'instances',candidates={'MountFixture':{'rule':rule,'directory':str(path)}})
    assert calls==[.2,.4] and len(meta['assets'])==2
    assert not (path/'geometry').exists()  # Never mutate reviewed source bundle.


@pytest.fixture(scope='module')
def formal_device(device):
    """Human-review records here are synthetic fixtures confined to pytest temp data."""
    ir,library,entries,_,_,folder=device
    for kind,entry in entries.items():
        sample=next(c for c in ir['components'] if c['type']==kind)
        params={k:v['value'] for k,v in sample['parameters'].items()}
        report,token=validate_candidate(library,entry['directory'],params)
        assert report['status']=='passed'
        library.approve(entry['directory'],reviewer='automated_test_fixture_only',expected_review_sha256=token,
                        note='Synthetic review for tests; not user component-library approval')
    meta,xml=build_device_scene(ir,library,folder/'formal_scene')
    return meta,xml,folder/'formal_scene'


def test_freeze_requires_complete_matching_behavior_report(formal_device):
    meta,xml,folder=formal_device;runtime=DeviceRuntime(meta,xml)
    smoke=check_assembly(meta,xml,steps=5)
    with pytest.raises(SceneError,match='behavior checks required'):runtime.freeze(folder,smoke)
    report=check_device_behaviors(meta,xml)
    altered=copy.deepcopy(report);altered['definition_sha256']='wrong'
    with pytest.raises(SceneError,match='different device'):runtime.freeze(folder,altered)
    manifest=runtime.freeze(folder,report)
    assert manifest['status']=='model_frozen' and manifest['plc_acceptance']=='not_run'


class MockModbus:
    def __init__(self):self.coils=[False]*128;self.registers={20:9};self.writes=[]
    def read_holding_registers(self,address,count):return SimpleNamespace(registers=[self.registers.get(address,0)],isError=lambda:False)
    def read_coils(self,address,count):return SimpleNamespace(bits=self.coils[address:address+count],isError=lambda:False)
    def write_coils(self,address,values):
        self.writes.append(('coils',address,list(values)));self.coils[address:address+len(values)]=values
        return SimpleNamespace(isError=lambda:False)
    def write_register(self,address,value):
        self.writes.append(('register',address,value));self.registers[address]=value
        return SimpleNamespace(isError=lambda:False)


def test_modbus_identity_and_safe_coil_writes(formal_device):
    meta,xml,folder=formal_device
    runtime=DeviceRuntime(meta,xml)
    report=check_device_behaviors(meta,xml);runtime.freeze(folder,report)
    client=MockModbus();link=DevicePlcLink(runtime,client,folder/'model.freeze.json',9)
    feedback=link.cycle({'panel_start':True},substeps=5)
    assert feedback['panel_start'] is True and runtime.data.time==pytest.approx(.01)
    assert all(len(values)>=16 for kind,address,values in client.writes if kind=='coils')
    client.registers[20]=8;count=len(client.writes)
    with pytest.raises(SceneError,match='identity mismatch'):link.cycle()
    assert len(client.writes)==count


def test_frozen_io_definition_cannot_be_modified(formal_device):
    meta,xml,folder=formal_device;runtime=DeviceRuntime(meta,xml)
    runtime.freeze(folder,check_device_behaviors(meta,xml))
    modified=copy.deepcopy(meta);modified['io'][0]['address']='%QX90.0'
    other=DeviceRuntime(modified,xml)
    with pytest.raises(SceneError,match='behavior/IO'):DevicePlcLink(other,MockModbus(),folder/'model.freeze.json',9)
