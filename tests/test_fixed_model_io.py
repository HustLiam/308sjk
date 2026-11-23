"""Fixed model inspection and real MuJoCo with a scan-driven PLC test double."""
import copy
import hashlib
from collections import defaultdict
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from test_device_robot import robot
from runtime.device_scene import build_device_scene
from src.agent.scene_agent.common import SceneError,read_json,write_json
from src.agent.scene_agent.rules import ComponentLibrary
from src.agent.scene_agent.verification import configure,parameters,validate_parameters,PHYSICS,joint_from_interfaces
from src.agent.scene_agent.simulation_tests import generate_test_plan,run_test_plan,validate_test_plan
from src.agent.scene_agent.plc_test import harness,PlcTestTransport


@pytest.fixture
def fixed(robot,tmp_path):
    ir,meta,_=robot;ir=configure(ir);entries={}
    for c in ir['components']:
        if c['id']=='base' or c['id'].startswith('link'):
            n=.04 if c['id']=='base' else .02
            for a in 'xyz':c['parameters']['size_'+a+'_m']={'value':n}
    for r in meta['rules']:
        rule=read_json(r['directory']+'/rule.json');entries[rule['component_type']]={'rule':rule,'directory':r['directory']}
    meta,xml=build_device_scene(ir,ComponentLibrary(tmp_path/'library'),tmp_path/'scene',candidates=entries)
    return ir,meta,xml


class ScanPlc:
    """Only scan() writes outputs and observed inputs, separate from client writes."""
    def __init__(self,ir):
        self.contract,_=harness(ir);self.words=defaultdict(int);self.bits=defaultdict(bool);self.writes=[];self.enabled=True;self.corrupt=False
        self.scan()

    def response(self,**data):return SimpleNamespace(isError=lambda:False,**data)

    def scan(self):
        if not self.enabled:return
        c=self.contract
        for i,a in enumerate(c['identity']):self.words[a]=int(c['identity_sha256'][i*4:i*4+4],16)
        if self.words[c['request']]!=self.words[c['commit']]:return
        for p in c['io']:
            if p['dir']=='output':
                v=self.words[c['staging'][p['name']]]
                if p['type']=='BOOL':self.bits[PlcTestTransport.coil(p['address'])]=bool(v)
                else:self.words[int(p['address'][3:])]=v
            else:
                v=self.bits[PlcTestTransport.coil(p['address'])] if p['type']=='BOOL' else self.words[int(p['address'][3:])]
                self.words[c['observed'][p['name']]]=int(v)^(1 if self.corrupt else 0)
        self.words[c['ack']]=self.words[c['request']]

    def read_holding_registers(self,address,count=1):self.scan();return self.response(registers=[self.words[address+i] for i in range(count)])
    def write_register(self,address,value):self.words[address]=value;self.writes.append((address,value));self.scan();return self.response()
    def read_coils(self,address,count=1):self.scan();return self.response(bits=[self.bits[address+i] for i in range(count)])
    def write_coils(self,address,values):
        for i,v in enumerate(values):self.bits[address+i]=v
        self.scan();return self.response()
    def close(self):pass


@pytest.mark.parametrize('fault',['dimension','io','range','extra'])
def test_parameter_json_cannot_replace_aml_facts(fixed,fault):
    ir,_,_=fixed;data=parameters(ir)
    if fault=='dimension':data['components'][1]['parameters']['size_x_m']['value']=9
    elif fault=='io':data['io'][0]['address']='%QX99.0'
    elif fault=='range':data['joints'][0]['range'][0]=-100
    else:data['assertions']=['always pass']
    with pytest.raises(SceneError,match='JSON'):validate_parameters(ir,data)


def test_structure_passes_without_full_sweeps_and_io_requires_plc(fixed):
    ir,meta,xml=fixed;plan=generate_test_plan(ir);report=run_test_plan(ir,meta,xml,plan)
    assert plan['generator']=='fixed_model_io/1' and not report['complete']
    assert all(m['status']=='passed' for m in report['modules'] if m['kind']!='io_binding'),report
    assert all(m['status']=='blocked' and m['failure_category']=='plc_environment' for m in report['modules'] if m['kind']=='io_binding')
    assert all(m['steps']==[{'op':'inspect_structure'}] for m in plan['modules'] if m['kind']!='io_binding')
    assert meta['physics_profile']==PHYSICS


@pytest.mark.parametrize('fault',['size','axis','position'])
def test_actual_compiled_geometry_and_joints_are_checked(fixed,fault):
    ir,meta,xml=fixed;meta=copy.deepcopy(meta);root=ET.fromstring(xml)
    if fault=='size':root.find('.//body[@name="c_link1"]/geom').set('size','.1 .01 .01')
    elif fault=='axis':root.find('.//joint[@name="j_joint1"]').set('axis','0 0 -1')
    else:root.find('.//body[@name="c_link1"]').set('pos','0 .12 0')
    xml=ET.tostring(root,encoding='unicode');meta['model_xml_sha256']=hashlib.sha256(xml.encode()).hexdigest()
    plan=generate_test_plan(ir);selected=[m['id'] for m in plan['modules'] if m['kind']!='io_binding']
    report=run_test_plan(ir,meta,xml,plan,selected)
    assert report['status']=='failed',report


def test_fixed_plan_cannot_be_rewritten_by_author(fixed):
    ir,_,_=fixed;plan=generate_test_plan(ir);plan['modules'].pop()
    with pytest.raises(SceneError):validate_test_plan(ir,plan)


def test_plc_exchange_never_writes_declared_word_outputs(fixed):
    ir,_,_=fixed;client=ScanPlc(ir);link=PlcTestTransport(ir,client)
    actual=link.exchange({'cmd1':-20},{'fb1':-18})
    assert actual['cmd1']==-20 and link.trace[-1]['plc_observed_inputs']['fb1']==-18
    addresses={int(p['address'][3:]) for p in ir['io'] if p['dir']=='output' and p['type']=='INT'}
    assert not addresses.intersection(a for a,v in client.writes)
    client.corrupt=True
    with pytest.raises(SceneError,match='实际读取'):link.exchange(inputs={'fb1':10})


def test_wrong_plc_identity_is_read_only_and_no_scan_times_out(fixed):
    ir,_,_=fixed;client=ScanPlc(ir);client.enabled=False
    client.words[client.contract['identity'][0]]^=1
    with pytest.raises(SceneError,match='未运行'):PlcTestTransport(ir,client)
    assert not client.writes
    client=ScanPlc(ir);link=PlcTestTransport(ir,client,timeout=.005);client.enabled=False
    with pytest.raises(SceneError,match='超时'):link.exchange({'cmd1':5})


def test_fixed_io_runs_actual_model_with_plc_scan_evidence(fixed,tmp_path):
    ir,meta,xml=fixed;client=ScanPlc(ir);link=PlcTestTransport(ir,client)
    plan=generate_test_plan(ir);report=run_test_plan(ir,meta,xml,plan,transport=link,output=tmp_path/'results')
    assert report['complete'],[(m['label'],m['status'],m['uncovered'],[c for c in m['checks'] if not c['passed']]) for m in report['modules'] if m['status']!='passed']
    assert report['whole_machine_functional_tests']=='not_requested'
    assert len(list((tmp_path/'results/plc_traces').glob('*.json')))==len(ir['io'])
    feedback=next(m for m in report['modules'] if m['target']=='fb1')
    trace=read_json(tmp_path/'results/plc_traces'/(feedback['id']+'.json'))
    assert len({e['plc_observed_inputs']['fb1'] for e in trace})>=3


def test_interface_positive_z_derives_joint_axis_and_anchors(fixed):
    ir,_,_=fixed;joint=ir['joints'][0];joint_id=joint['id']
    for name,cid,pos in [('parent_body','base',[0,.1,0]),('child_body','link1',[0,0,0])]:
        local={'id':'j_'+name,'component_id':joint_id,'name':name,'parameters':{}}
        remote={'id':'f_'+name,'component_id':cid,'name':'mount','parameters':{}}
        for axis,n in zip('xyz',pos):remote['parameters']['position_'+axis+'_m']={'value':n}
        remote['parameters']['rotation_x_deg']={'value':-90}
        ir['interfaces'] += [local,remote]
        ir['connections'].append({'a':local['id'],'b':remote['id']})
    derived=joint_from_interfaces(ir,joint,required=True)
    assert derived['axis']==pytest.approx([0,1,0]) and derived['parent_anchor_m']==[0,.1,0]


def test_new_prepare_never_asks_author_for_test_scripts(fixed,tmp_path):
    from src.agent.scene_agent.workflow import prepare
    ir,meta,_=fixed
    class Author:
        extracted=False
        def verification_parameters(self,ir):self.extracted=True;return parameters(ir)
        def draft_collision_free_tests(self,*a):raise AssertionError('No author-written tests in new profile')
        def draft_test_sequences(self,*a):raise AssertionError('No author-written tests in new profile')
        def repair_candidate(self,*a):raise AssertionError('Valid fixture must not need repair')
    author=Author();rules={}
    for r in meta['rules']:
        rule=read_json(r['directory']+'/rule.json');rules[rule['component_type']]=rule
    out=tmp_path/'task'
    task=prepare(ir,'fixed verification',out,ComponentLibrary(tmp_path/'fresh'),candidate_rules=list(rules.values()),author=author)
    assert author.extracted and task['verification_profile']=='fixed_model_io/1'
    assert (out/'io_test.xml').is_file() and (out/'verification_parameters.json').is_file()
    assert read_json(out/'simulation_tests.json')['generator']=='fixed_model_io/1'
    assert not (out/'test_safety.json').exists()


def test_public_entry_rejects_parameter_tamper_before_connect_and_invalidates_old_pass(fixed,tmp_path,monkeypatch):
    from src.agent.scene_agent.workflow import test_task as run_selected
    from src.agent.scene_agent import plc_test
    ir,meta,xml=fixed;folder=tmp_path/'public';scene=folder/'candidate_scene';scene.mkdir(parents=True)
    write_json(folder/'device_ir.json',ir);write_json(scene/'scene.physical.json',meta);(scene/'scene.xml').write_text(xml,encoding='utf-8')
    write_json(folder/'simulation_tests.json',generate_test_plan(ir))
    data=parameters(ir);data['io'][0]['address']='%QX99.0';write_json(folder/'verification_parameters.json',data)
    for name in ('simulation_test_results.json','behavior_checks.json','model.freeze.json'):write_json(scene/name,{'status':'passed'})
    def forbidden(*args):raise AssertionError('Must validate parameters before PLC connection')
    monkeypatch.setattr(plc_test,'connect',forbidden)
    with pytest.raises(SceneError,match='JSON'):run_selected(folder,isolated_candidates=True,plc_config={'host':'unused','port':502})
    assert not (scene/'model.freeze.json').exists() and not (scene/'simulation_test_results.json').exists()


def test_new_parameter_and_plan_schemas(fixed):
    import jsonschema
    from pathlib import Path
    ir,_,_=fixed;root=Path(__file__).resolve().parents[1]/'schemas/scene_agent'
    for name,value in [('verification_parameters',parameters(ir)),('simulation_tests',generate_test_plan(ir)),('device_ir',ir)]:
        jsonschema.validate(value,read_json(root/(name+'.schema.json')))


def test_missing_requirements_cannot_pass_but_can_be_imported(fixed):
    ir,meta,xml=fixed;ir=copy.deepcopy(ir);ir.pop('simulation_requirements',None)
    from src.agent.scene_agent.device_ir import facts_digest
    meta=copy.deepcopy(meta);meta['device_ir_sha256']=facts_digest(ir)
    validate_parameters(ir,parameters(ir))
    report=run_test_plan(ir,meta,xml,generate_test_plan(ir))
    assert report['status']=='blocked' and not report['complete']
    assert all(m['status']=='blocked' for m in report['modules'])
