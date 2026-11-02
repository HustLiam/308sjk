"""Physical tests plus deliberate faults: the script cannot rewrite its oracle."""
import copy
import hashlib
from pathlib import Path
import xml.etree.ElementTree as ET
import sys

import pytest

pytest.importorskip('mujoco')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from src.agent.scene_agent.common import SceneError, read_json
from src.agent.scene_agent.device_ir import parse_device_ir, facts_digest, validate_device_ir
from src.agent.scene_agent.rules import ComponentLibrary
from src.agent.scene_agent.simulation_tests import generate_test_plan, validate_test_plan, run_test_plan
from examples.scene_agent.reference_candidates import make_reference_candidates
from runtime.device_scene import build_device_scene

REPO = Path(__file__).resolve().parents[1]
AML = REPO/'examples/aml/factoryforge_sorting_reference.aml'


@pytest.fixture(scope='module')
def physical(tmp_path_factory):
    folder = tmp_path_factory.mktemp('aml_simulation_tests')
    ir = parse_device_ir(AML); library = ComponentLibrary(folder/'library'); entries = {}
    for rule in make_reference_candidates(ir):
        path = library.stage(rule, origin='isolated_test_only')
        entries[rule['component_type']] = {'rule': rule, 'directory': str(path)}
    meta, xml = build_device_scene(ir, library, folder/'scene', candidates=entries)
    return ir, meta, xml


def test_aml_requirements_are_normalized_hashed_and_cover_all_facts(physical):
    ir, _, _ = physical; req = ir['simulation_requirements']
    assert req['format'] == '308sjk.simulation_requirements/1'
    assert req['position_tolerance_m'] == .002 and req['allowed_simplifications']
    plan = generate_test_plan(ir)
    assert plan['requirements_source'] == 'aml'
    assert len(plan['modules']) == len(ir['joints'])+len(ir['io'])
    assert all(m['requirements'] for m in plan['modules'])
    changed = copy.deepcopy(ir); changed['simulation_requirements']['position_tolerance_m'] = .003
    assert facts_digest(changed) != facts_digest(ir)
    validate_test_plan(ir, plan)


def test_all_modules_check_actual_mujoco_state_and_export_replays(physical, tmp_path):
    ir, meta, xml = physical; plan = generate_test_plan(ir)
    report = run_test_plan(ir, meta, xml, plan, output=tmp_path)
    assert report['complete'], [(m['label'], m['status'], m['uncovered'], [c for c in m['checks'] if not c['passed']]) for m in report['modules'] if m['status'] != 'passed']
    assert report['plc_acceptance'] == 'not_run'
    for module in report['modules']:
        replay = read_json(tmp_path/module['replay'])
        assert replay['test_id'] == module['id'] and replay['plan_sha256'] == report['plan_sha256']
        assert replay['frames'] and replay['frames'][0]['signals']
        assert all(b['time_s'] > a['time_s'] for a, b in zip(replay['frames'], replay['frames'][1:]))


@pytest.mark.parametrize('mutation', ['reverse_axis', 'remove_joint', 'swap_io', 'wrong_scale'])
def test_known_faults_cannot_pass(physical, mutation):
    ir, original, xml = physical; meta = copy.deepcopy(original); plan = generate_test_plan(ir)
    if mutation in ('reverse_axis', 'remove_joint'):
        root = ET.fromstring(xml); joint = root.find('.//joint[@name="j_'+ir['joints'][0]['id']+'"]')
        if mutation == 'reverse_axis':joint.set('axis', ' '.join(str(-v) for v in ir['joints'][0]['axis']))
        else:
            # Turn the movable connection into a fixed connection, retaining
            # a compilable model so the checker must notice lost mechanics.
            for body in root.iter('body'):
                if joint in list(body):body.remove(joint)
            for group in ('actuator', 'equality'):
                for node in list(root.find(group)):
                    if node.get('joint') == joint.get('name') or node.get('joint1') == joint.get('name'):root.find(group).remove(node)
        xml = ET.tostring(root, encoding='unicode'); meta['model_xml_sha256'] = hashlib.sha256(xml.encode()).hexdigest()
        selected = [m['id'] for m in plan['modules'] if m['kind'] == 'mechanical_joint']
    else:
        names = [p['name'] for p in ir['io'] if p['quantity'] in ('extended', 'retracted')]
        for p in meta['io']:
            if p['name'] == names[0]:p['physical_per_plc_unit'] = 2 if mutation == 'wrong_scale' else p['physical_per_plc_unit']; p['quantity'] = 'retracted' if mutation == 'swap_io' else p['quantity']
            if p['name'] == names[1] and mutation == 'swap_io':p['quantity'] = 'extended'
        selected = [m['id'] for m in plan['modules'] if m['target'] in names]
    report = run_test_plan(ir, meta, xml, plan, selected)
    assert report['status'] == 'failed' and not report['complete']


@pytest.mark.parametrize('mutation', ['drop', 'tolerance', 'target', 'code', 'remove_boundary'])
def test_scripts_cannot_drop_coverage_or_modify_judgement(physical, mutation):
    ir, _, _ = physical; plan = generate_test_plan(ir)
    if mutation == 'drop':plan['modules'].pop()
    elif mutation == 'tolerance':plan['modules'][0]['tolerance'] = 999
    elif mutation == 'target':plan['modules'][0]['target'] = 'arbitrary'
    elif mutation == 'code':plan['modules'][0]['steps'][0] = {'op': 'exec', 'code': 'print("pass")'}
    else:plan['modules'][0]['steps'] = plan['modules'][0]['steps'][1:]
    with pytest.raises(SceneError):validate_test_plan(ir, plan)


def test_selected_subset_is_never_complete_and_stale_source_is_rejected(physical):
    ir, meta, xml = physical; plan = generate_test_plan(ir)
    report = run_test_plan(ir, meta, xml, plan, [plan['modules'][0]['id']])
    assert report['status'] == 'passed' and not report['complete']
    changed = copy.deepcopy(ir); changed['simulation_requirements']['response_timeout_s'] = 4
    with pytest.raises(SceneError):run_test_plan(changed, meta, xml, plan)
    with pytest.raises(SceneError):run_test_plan(ir, meta, xml, plan, ['old_test_id'])


def test_missing_required_object_and_unsupported_encoding_are_explicit(physical):
    ir, meta, xml = physical; ir = copy.deepcopy(ir); meta = copy.deepcopy(meta)
    ir['simulation_requirements']['checks'].append({'id': 'required_mount', 'kind': 'fixed_mount', 'target': '*', 'description': '必须验证安装连接'})
    meta['device_ir_sha256'] = facts_digest(ir); plan = generate_test_plan(ir)
    selected = [m['id'] for m in plan['modules'] if m['kind'] == 'missing_requirement']
    report = run_test_plan(ir, meta, xml, plan, selected)
    assert report['status'] == 'blocked' and not report['complete']
    p = next(p for p in ir['io'] if p['quantity'] == 'count')
    p['physical_per_plc_unit'] = 2
    next(x for x in meta['io'] if x['name'] == p['name'])['physical_per_plc_unit'] = 2
    next(x for x in meta['io_map'] if x['plc_var'] == p['name'])['physical_per_plc_unit'] = 2
    meta['device_ir_sha256'] = facts_digest(ir); plan = generate_test_plan(ir)
    report = run_test_plan(ir, meta, xml, plan, [m['id'] for m in plan['modules'] if m['target'] == p['name']])
    assert report['status'] == 'blocked'


def test_unknown_aml_requirements_fail_before_generation(physical):
    ir, _, _ = physical; ir = copy.deepcopy(ir)
    ir['simulation_requirements']['checks'][0]['kind'] = 'llm_verdict'
    with pytest.raises(SceneError):validate_device_ir(ir)


@pytest.mark.parametrize('fractions',[[.1,.9],[-1],[True]])
def test_agent_selects_legal_poses_but_cannot_change_oracle(physical,tmp_path,fractions):
    import json
    from src.agent.scene_agent.author import SceneGenerationAgent
    ir,_,_=physical;plan=generate_test_plan(ir);before=copy.deepcopy(plan)
    class Client:
        def chat_completions(self,payload,**kwargs):
            context=json.loads(payload['messages'][1]['content'])
            assert context['simulation_requirements']==ir['simulation_requirements']
            result={'joint_poses':[{'test_id':plan['modules'][0]['id'],'fractions':fractions}]}
            return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result)}}]}
    author=SceneGenerationAgent(Client())
    if fractions==[.1,.9]:
        result=author.draft_test_sequences(ir,plan,tmp_path)
        assert len(result['modules'][0]['steps'])==7
        assert result['modules'][1:]==plan['modules'][1:]
    else:
        with pytest.raises(SceneError):author.draft_test_sequences(ir,plan,tmp_path)
    assert plan==before


@pytest.fixture
def linked_device(physical,tmp_path):
    ir=copy.deepcopy(physical[0]);ir.update(components=[],joints=[],assemblies=[],interfaces=[],connections=[],io=[],pending=[])
    base={'id':'base','name':'base','type':'Fixture','parent_id':None,'parameters':{},'interfaces':[],
          'source':{},'pose':{'position_m':[0,0,1],'rpy_rad':[0,0,0]}}
    ir['components']=[base]
    for index in (1,2):
        c=copy.deepcopy(base);c.update(id='slider'+str(index),name='slider'+str(index),parent_id='base')
        c['pose']['position_m']=[0,index*.1,0];ir['components'].append(c)
        ir['joints'].append({'id':'joint'+str(index),'name':'joint'+str(index),'type':'slide','parent_body':'base',
            'child_body':c['id'],'axis':[1,0,0] if index==1 else [0,1,0],'axis_frame':'child',
            'range':[0,.2/index],'initial':0,'parent_anchor_m':[0,index*.1,0],'child_anchor_m':[0,0,0]})
    ir['couplings']=[{'id':'coupling_joint2','leader':'joint1','follower':'joint2','ratio':.5,'offset':0}]
    ir['simulation_requirements']['checks']=[{'id':'linked','kind':'joint_coupling','target':'coupling_joint2','description':'验证比例联动'}]
    rule={'format':'308sjk.component_rule/0.1-draft','component_type':'Fixture','version':'0.1.0',
          'parameter_bounds':{},'assumptions':['isolated mechanical test fixture'],'unresolved':[],
          'model':{'behavior':'none','physical_parameters':{'mass_kg':1,'position_kp':100,'damping':5},
                   'geoms':[{'name':'body','type':'box','size':[.01,.01,.01]}]}}
    library=ComponentLibrary(tmp_path/'library');path=library.stage(rule)
    meta,xml=build_device_scene(ir,library,tmp_path/'scene',candidates={'Fixture':{'rule':rule,'directory':str(path)}})
    return ir,meta,xml


@pytest.mark.parametrize('fault',[None,'remove','wrong_ratio'])
def test_actual_coupling_and_constraint_mutations(linked_device,fault):
    ir,meta,xml=linked_device
    if fault:
        root=ET.fromstring(xml);constraint=root.find('./equality/joint[@name="coupling_joint2"]')
        if fault=='remove':root.find('equality').remove(constraint)
        else:constraint.set('polycoef','0 -0.5 0 0 0')
        xml=ET.tostring(root,encoding='unicode');meta['model_xml_sha256']=hashlib.sha256(xml.encode()).hexdigest()
    plan=generate_test_plan(ir)
    report=run_test_plan(ir,meta,xml,plan,[m['id'] for m in plan['modules'] if m['kind']=='joint_coupling'])
    assert report['status']==('passed' if fault is None else 'failed'),report


def test_fixed_mount_checks_real_transform_and_detects_detached_body(linked_device,tmp_path):
    ir,_,_=linked_device;ir['couplings']=[];ir['joints']=[];ir['components']=ir['components'][:2]
    for cid,xyz,role in [('base',[0,.1,0],'parent'),('slider1',[0,0,0],'child')]:
        ir['interfaces'].append({'id':cid+'_mount','name':'mount','component_id':cid,'type':'MechanicalMount',
            'parameters':{'position_'+a+'_m':{'value':v} for a,v in zip('xyz',xyz)}})
    ir['assemblies']=[{'id':'mount1','parent_component':'base','child_component':'slider1',
        'parent_interface':'base_mount','child_interface':'slider1_mount','placement':'check','offset':{}}]
    ir['simulation_requirements']['checks']=[{'id':'mount','kind':'fixed_mount','target':'mount1','description':'验证固定连接'}]
    rule={'format':'308sjk.component_rule/0.1-draft','component_type':'Fixture','version':'0.2.0','parameter_bounds':{},
        'assumptions':['fixed-mount test fixture'],'unresolved':[],
        'model':{'behavior':'none','geoms':[{'name':'body','type':'box','size':[.01,.01,.01],'mass':1}]}}
    library=ComponentLibrary(tmp_path/'mount_library');path=library.stage(rule)
    meta,xml=build_device_scene(ir,library,tmp_path/'mounted',candidates={'Fixture':{'rule':rule,'directory':str(path)}})
    plan=generate_test_plan(ir)
    assert run_test_plan(ir,meta,xml,plan)['complete']
    root=ET.fromstring(xml);body=root.find('.//body[@name="c_slider1"]');root.find('.//body[@name="c_base"]').remove(body)
    root.find('worldbody').append(body);body.set('pos','0 0.1 1');ET.SubElement(body,'freejoint',name='detached')
    xml=ET.tostring(root,encoding='unicode');meta['model_xml_sha256']=hashlib.sha256(xml.encode()).hexdigest()
    report=run_test_plan(ir,meta,xml,plan)
    assert report['status']=='failed' and not report['complete']
