"""Device facts, candidate review integrity, restricted authoring and local edits."""
import copy
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))

import pytest

from src.agent.scene_agent.common import SceneError, digest, expression, read_json, write_json
from src.agent.scene_agent.device_ir import parse_device_ir, values, facts_digest, apply_patch, validate_device_ir
from src.agent.scene_agent.rules import ComponentLibrary, ApprovalRequired, validate_rule, RULE_FORMAT
from src.agent.scene_agent.blender import validate_script
from src.agent.scene_agent.author import SceneGenerationAgent

AML=Path(__file__).resolve().parents[1]/'examples/aml/factoryforge_sorting_reference.aml'


@pytest.fixture
def ir():return parse_device_ir(AML)


def simple_rule():
    return {'format':RULE_FORMAT,'component_type':'Fixture','version':'0.1.0',
        'parameter_bounds':{'width_m':[.1,.5]},'assumptions':['static geometry only'],'unresolved':[],
        'model':{'behavior':'none','geoms':[{'name':'base','type':'box','size':['=width_m/2',.1,.1]}],
                 'interfaces':[{'name':'mount','position_m':[0,0,.1]}]}}


def test_reference_preserves_device_and_io(ir):
    from src.agent.aml_parser import parse_aml, build_io_list
    original,_=parse_aml(AML);io,_=build_io_list(original)
    assert len(ir['components'])==18 and len(ir['joints'])==1 and len(ir['io'])==19
    assert ir['pending']==[]
    assert [{k:p[k] for k in ('name','dir','type','address')} for p in ir['io']]==[
        {k:p[k] for k in ('name','dir','type','address')} for p in io]
    ramp=next(c for c in ir['components'] if c['type']=='GravityChute')
    angle=ramp['parameters']['incline_deg']
    assert angle['unit']=='rad' and angle['value']==pytest.approx(angle['original_value']*3.141592653589793/180)
    assert angle['source']['kind'] and angle['children']
    assert all(p['component_id'] for p in ir['io'])
    assert {p['writer'] for p in ir['io']}=={'plc','simulation','operator','fault_harness'}


@pytest.mark.parametrize('mutation,match',[
    (lambda r:r['components'].append(copy.deepcopy(r['components'][0])),'Duplicate component'),
    (lambda r:r['io'].__setitem__(1,copy.deepcopy(r['io'][0])),'Duplicate IO'),
    (lambda r:r['joints'][0].__setitem__('axis',[0,0,0]),'Zero joint axis'),
    (lambda r:r['joints'][0].__setitem__('initial',999),'initial outside'),
    (lambda r:r['components'][0].__setitem__('parent_id',r['components'][0]['id']),'cyclic'),
    (lambda r:r['io'][0].__setitem__('writer','simulation'),'writer mismatch'),
])
def test_invalid_device_facts_rejected(ir,mutation,match):
    mutation(ir)
    with pytest.raises(SceneError,match=match):validate_device_ir(ir)


def test_namespace_and_unknown_unit(tmp_path,ir):
    content=AML.read_text(encoding='utf-8-sig')
    assert 'xmlns=' in content
    target=tmp_path/'missing_unit.aml'
    target.write_text(content.replace('Unit="m"','Unit="furlong"',1),encoding='utf-8')
    revised=parse_device_ir(target)
    assert any(p['reason']=='unknown_unit' for p in revised['pending'])


@pytest.mark.parametrize('expr',["=__import__('os')","=width_m.real","=width_m/0","=2**10","=float('nan')"])
def test_expression_language_rejects_arbitrary_code(expr):
    with pytest.raises(SceneError):expression(expr,{'width_m':.3})


def test_expression_scales_geometry():
    assert expression(['$width_m','=width_m/2'],{'width_m':.3})==[.3,.15]
    with pytest.raises(SceneError,match='outside'):validate_rule(simple_rule(),{'width_m':2})


@pytest.mark.parametrize('source',[
    'import bpy\ndef build(params, api): pass',
    'def build(params, api):\n    open("file")',
    'def build(params, api):\n    api.__class__',
    'def build(params, api):\n    params.clear()',
    'def build(params, api, *, extra=1): pass',
    'def build(params, api):\n    params["x"]=1',
])
def test_geometry_candidate_restricts_execution(source):
    with pytest.raises(SceneError):validate_script(source)


def test_geometry_example_script_is_bounded():
    rule=read_json(AML.parents[1]/'scene_agent/hollow_bracket.candidate.json')
    assert validate_script(rule['blender_script'])


def test_review_is_bound_to_bytes_and_validation(tmp_path):
    library=ComponentLibrary(tmp_path/'isolated_test_library')
    candidate=library.stage(simple_rule())
    with pytest.raises(ApprovalRequired):library.resolve('Fixture',{'width_m':.3})
    token=library.submit_validation(candidate,{'status':'passed','checks':[{'name':'fixture_only','passed':True}]})
    (candidate/'unexpected.txt').write_text('modified',encoding='utf-8')
    with pytest.raises(SceneError,match='stale'):library.approve(candidate,reviewer='test_only',expected_review_sha256=token,note='unit test')
    token=library.submit_validation(candidate,{'status':'failed','checks':[{'name':'fixture_only','passed':False}]})
    with pytest.raises(SceneError,match='failed'):library.approve(candidate,reviewer='test_only',expected_review_sha256=token,note='unit test')
    token=library.submit_validation(candidate,{'status':'passed','checks':[{'name':'fixture_only','passed':True}]})
    approved=library.approve(candidate,reviewer='test_only',expected_review_sha256=token,note='isolated test data only')
    assert library.resolve('Fixture',{'width_m':.3})['directory']==str(approved)
    with pytest.raises(ApprovalRequired):library.resolve('Fixture',{'width_m':1})
    modified=simple_rule();modified['model']['geoms'][0]['size'][1]=.3
    with pytest.raises(SceneError,match='immutable'):library.stage(modified)
    (approved/'unexpected.txt').write_text('tampered')
    with pytest.raises(SceneError,match='modified'):library.resolve('Fixture',{'width_m':.3})


def test_implementation_changes_invalidate_review(tmp_path,monkeypatch):
    from src.agent.scene_agent import rules
    library=ComponentLibrary(tmp_path/'test_library');candidate=library.stage(simple_rule())
    token=library.submit_validation(candidate,{'status':'passed','checks':[{'name':'fixture','passed':True}]})
    monkeypatch.setattr(rules,'implementation_hashes',lambda:{'changed':'version'})
    with pytest.raises(SceneError,match='implementation changed'):
        library.approve(candidate,reviewer='test_only',expected_review_sha256=token,note='test')


def test_local_edit_preserves_unaffected_and_io(ir):
    selected=next(c for c in ir['components'] if c['type']=='DiffusePhotoelectricSensor')
    patch={'base_sha256':facts_digest(ir),'editable_ids':[selected['id']],'reason':'Raise this sensor',
           'operations':[{'op':'set_pose','component_id':selected['id'],'position_m':[1.2,.2,.3],'rpy_rad':[0,0,0]}]}
    revised=apply_patch(ir,patch)
    assert revised['io']==ir['io'] and revised['joints']==ir['joints']
    assert [c for c in revised['components'] if c['id']!=selected['id']]==[c for c in ir['components'] if c['id']!=selected['id']]
    assert revised['revision']==1 and ir['revision']==0
    with pytest.raises(SceneError,match='Stale'):apply_patch(revised,patch)
    patch['editable_ids']=[]
    with pytest.raises(SceneError,match='scope'):apply_patch(ir,patch)


def test_joint_edit_requires_both_connected_bodies(ir):
    j=ir['joints'][0]
    patch={'base_sha256':facts_digest(ir),'editable_ids':[j['id']],
        'operations':[{'op':'set_joint','component_id':j['id'],'changes':{'range':[0,.3]}}]}
    with pytest.raises(SceneError,match='connected bodies'):apply_patch(ir,patch)
    patch['editable_ids']+=[j['parent_body'],j['child_body']]
    revised=apply_patch(ir,patch)
    assert revised['joints'][0]['range']==[0,.3]
    assert revised['io']==ir['io']


class ReplyClient:
    def __init__(self,replies):self.replies=iter(replies);self.calls=[]
    def chat_completions(self,payload,**kwargs):
        self.calls.append(payload)
        return {'choices':[{'finish_reason':'stop','message':{'content':next(self.replies)}}]}


def test_agent_repairs_invalid_json_and_only_authors_candidates(tmp_path):
    component={'id':'a','name':'Fixture','type':'Fixture','role':'fixture','parameters':{'width_m':{'value':.3}},'pose':{}}
    client=ReplyClient(['not JSON',json.dumps({'rules':[simple_rule()]})])
    result=SceneGenerationAgent(client).draft_rules({'components':[component],'joints':[],'io':[]},'fixture',{'Fixture'},tmp_path)
    assert result==[simple_rule()] and len(client.calls)==2
    assert 'Repair only' in client.calls[1]['messages'][-1]['content']
    assert not list(tmp_path.rglob('approval.json'))


def test_author_normalizes_only_empty_behavior_placeholder_and_keeps_unresolved(tmp_path):
    rule=simple_rule();rule['model']['behavior_parameters']={};rule['unresolved']=['Revolute drive is not implemented']
    component={'id':'link1','type':'Fixture','name':'link','role':'body','pose':{},'parameters':{'width_m':{'value':.3}}}
    client=ReplyClient([json.dumps({'rules':[rule]})])
    rules=SceneGenerationAgent(client).draft_rules({'components':[component],'joints':[],'io':[]},'preview',{'Fixture'},tmp_path)
    assert len(client.calls)==1 and 'behavior_parameters' not in rules[0]['model']
    assert rules[0]['unresolved']==rule['unresolved']
    assert rule['model']['behavior_parameters']=={}  # caller input is untouched
    assert read_json(tmp_path/'round_001/normalization.json')['operations'][0]['path']=='rules[0].model.behavior_parameters'
    raw=read_json(tmp_path/'round_001/response.json')['choices'][0]['message']['content']
    assert json.loads(raw)['rules'][0]['model']['behavior_parameters']=={}
    with pytest.raises(SceneError,match='Unknown modeling rule fields'):validate_rule(rule)


@pytest.mark.parametrize('value',[{'speed':2},[],None,False])
def test_author_never_discards_nonempty_or_nonobject_behavior_data(tmp_path,value):
    bad=simple_rule();bad['model']['behavior_parameters']=value
    component={'id':'link1','type':'Fixture','name':'link','role':'body','pose':{},'parameters':{'width_m':{'value':.3}}}
    client=ReplyClient([json.dumps({'rules':[bad]}),json.dumps({'rules':[simple_rule()]})])
    result=SceneGenerationAgent(client).draft_rules({'components':[component],'joints':[],'io':[]},'fixture',{'Fixture'},tmp_path)
    assert result==[simple_rule()] and len(client.calls)==2
    feedback=client.calls[1]['messages'][-1]['content']
    assert 'rules[0]' in feedback and 'behavior_parameters' in feedback and 'allowed fields' in feedback
    assert not (tmp_path/'round_001/normalization.json').exists()


def test_author_names_unknown_field_and_reuses_normalizer_for_repairs(tmp_path):
    bad=simple_rule();bad['model']['invented_controller']={'drive':'fake'}
    with pytest.raises(SceneError,match=r'Fixture.*invented_controller'):
        SceneGenerationAgent._candidate_envelope({'rules':[bad]},tmp_path)
    rule=simple_rule();rule['model']['behavior_parameters']={};rule['unresolved']=['unsupported controller']
    component={'id':'link1','type':'Fixture','parameters':{'width_m':{'value':.3}},'pose':{}}
    client=ReplyClient([json.dumps({'rules':[rule]})])
    result=SceneGenerationAgent(client).repair_candidate({'components':[component]},'repair',rule,[],tmp_path/'repair')
    assert 'behavior_parameters' not in result['model'] and result['unresolved']==rule['unresolved']


def test_author_removes_identical_single_collider_mass_without_changing_total(tmp_path):
    rule=simple_rule();rule['model']['physical_parameters']={'mass_kg':2.5};rule['model']['geoms'][0]['mass']=2.5
    result=SceneGenerationAgent._candidate_envelope({'rules':[rule]},tmp_path)['rules'][0]
    assert result['model']['physical_parameters']['mass_kg']==2.5
    assert 'mass' not in result['model']['geoms'][0] and rule['model']['geoms'][0]['mass']==2.5
    SceneGenerationAgent._validate_candidate(result,[{'id':'link','type':'Fixture','parameters':{'width_m':.3}}])
    operation=read_json(tmp_path/'normalization.json')['operations'][0]
    assert operation['op']=='remove_identical_single_collider_mass'


def test_author_rejects_conflicting_mass_instead_of_dropping_physics(tmp_path):
    rule=simple_rule();rule['model']['physical_parameters']={'mass_kg':2.5};rule['model']['geoms'][0]['mass']=3.5
    result=SceneGenerationAgent._candidate_envelope({'rules':[rule]},tmp_path)['rules'][0]
    assert result['model']['geoms'][0]['mass']==3.5
    with pytest.raises(SceneError,match=r'geoms\[0\].*conflicts'):
        SceneGenerationAgent._validate_candidate(result,[{'id':'link','type':'Fixture','parameters':{'width_m':.3}}])


def test_author_repairs_string_body_with_specific_feedback_and_keeps_raw_reply(tmp_path):
    bad=simple_rule();bad['model']['bodies']=['link']
    component={'id':'link1','type':'Fixture','name':'link','role':'body','pose':{},
               'parameters':{'width_m':{'value':.3,'unit':'m'},'stroke_min':{'value':-1.2,'unit':'rad'}}}
    frame={'id':'mount1','component_id':'link1','name':'mount','position_m':[.02,0,0]}
    client=ReplyClient([json.dumps({'rules':[bad]}),json.dumps({'rules':[simple_rule()]})])
    result=SceneGenerationAgent(client).draft_rules({'components':[component],'joints':[],
        'interfaces':[frame],'io':[]},'fixture',{'Fixture'},tmp_path)
    assert result==[simple_rule()] and len(client.calls)==2
    feedback=client.calls[1]['messages'][-1]['content']
    assert 'Fixture: model.bodies[0]' in feedback and 'must be an object' in feedback
    assert 'root is implicit' in feedback and 'external DeviceIR joints' in feedback
    raw=read_json(tmp_path/'round_001/response.json')['choices'][0]['message']['content']
    assert json.loads(raw)['rules'][0]['model']['bodies']==['link']
    inputs=json.loads(client.calls[0]['messages'][1]['content'])['components'][0]
    assert inputs['parameter_units']['stroke_min']=='rad' and inputs['parameters']['stroke_min']==-1.2
    assert inputs['interfaces']==[frame]


@pytest.mark.parametrize('bodies,match',[
    ('link','array of body objects'),
    ([{'name':'link','mass':2}],r'bodies\[0\].*mass'),
    ([{'name':'bad name'}],r'bodies\[0\].name'),
    ([{'name':'root'}],'root is implicit'),
    ([{'name':'link','parent':'missing'}],r'bodies\[0\].parent'),
])
def test_author_reports_body_path_without_discarding_data(bodies,match):
    rule=simple_rule();rule['model']['bodies']=bodies
    original=copy.deepcopy(rule)
    with pytest.raises(SceneError,match=match):SceneGenerationAgent._validate_candidate(rule,[])
    assert rule==original


def test_author_accepts_explicit_internal_body_tree():
    rule=simple_rule();rule['model']['bodies']=[{'name':'housing'},
        {'name':'slider','parent':'housing','position_m':[0,0,.1],'rpy_rad':[0,0,0]}]
    rule['model']['geoms'][0]['body']='slider'
    SceneGenerationAgent._validate_candidate(rule,[{'id':'fixture','type':'Fixture','parameters':{'width_m':.3}}])


def test_author_legacy_axis_units_do_not_change_device_values():
    c={'id':'j1','type':'Fixture','parameters':{
        'axis_type':{'value':'rotary_finite'},'unit':{'value':'deg'},
        'stroke_min':{'value':-170},'vmax':{'value':60},'accel':{'value':120}}}
    original=copy.deepcopy(c)
    result=SceneGenerationAgent._component_summary(c,{})
    assert result['parameters']['stroke_min']==-170
    assert result['parameter_units']['stroke_min']=='deg'
    assert result['parameter_units']['vmax']=='deg/s'
    assert result['parameter_units']['accel']=='deg/s^2'
    assert result['parameter_unit_sources']['stroke_min']=='legacy_axis_component_unit'
    assert c==original


@pytest.mark.parametrize('axis_type,axis_unit,explicit_unit,expected',[
    ('rotary_finite','deg','rad','rad'),
    ('rotary_modulo','rad',None,'rad'),
    ('rotary_finite',None,None,None),
    ('linear','deg',None,None),
    ('rotary_finite','unknown',None,None),
])
def test_author_unit_context_preserves_explicit_units_and_unknowns(axis_type,axis_unit,explicit_unit,expected):
    c={'id':'j1','type':'Fixture','parameters':{'axis_type':{'value':axis_type},
       'unit':{'value':axis_unit},'stroke_min':{'value':-2.9,'unit':explicit_unit,
           'original_unit':'deg' if explicit_unit else None}}}
    result=SceneGenerationAgent._component_summary(c,{})
    assert result['parameter_units']['stroke_min']==expected
    assert result['parameters']['stroke_min']==-2.9


def test_author_bounds_retry_includes_actual_units_and_all_instance_range(tmp_path):
    components=[{'id':'j'+str(i),'name':'link','type':'Fixture','role':'body','pose':{},
        'parameters':{'width_m':{'value':.3},'axis_type':{'value':'rotary_finite'},
                      'unit':{'value':'deg'},'stroke_min':{'value':value}}}
        for i,value in enumerate([-170,-90,-135,-180,-120,-180],1)]
    bad=simple_rule();bad['parameter_bounds']['stroke_min']=[-6.28,6.28]
    good=copy.deepcopy(bad);good['parameter_bounds']['stroke_min']=[-180,-90]
    client=ReplyClient([json.dumps({'rules':[bad]}),json.dumps({'rules':[good]})])
    ir={'components':components,'joints':[],'io':[]};original=copy.deepcopy(ir)
    result=SceneGenerationAgent(client).draft_rules(ir,'robot geometry',{'Fixture'},tmp_path)
    assert result==[good] and ir==original
    feedback=client.calls[1]['messages'][-1]['content']
    assert 'actual=-170' in feedback and "represented unit='deg'" in feedback
    assert '[-6.28, 6.28]' in feedback and '[-180, -90]' in feedback and 'ALL instances' in feedback
    assert read_json(tmp_path/'round_001/error.json')['error'].endswith('or silently widen an approved rule.')
    assert json.loads(read_json(tmp_path/'round_001/response.json')['choices'][0]['message']['content'])['rules']==[bad]


def test_author_repair_receives_the_same_legacy_unit_context(tmp_path):
    c={'id':'j1','type':'Fixture','pose':{},'parameters':{'width_m':{'value':.3},
       'axis_type':{'value':'rotary_finite'},'unit':{'value':'deg'},'stroke_min':{'value':-170}}}
    good=simple_rule();good['parameter_bounds']['stroke_min']=[-180,-90]
    client=ReplyClient([json.dumps({'rules':[good]})])
    result=SceneGenerationAgent(client).repair_candidate({'components':[c]},'repair',good,[],tmp_path)
    component=json.loads(client.calls[0]['messages'][1]['content'])['components'][0]
    assert component['parameter_units']['stroke_min']=='deg'
    assert result==good


@pytest.mark.parametrize('gain',[None,0,-1,True,float('inf')])
def test_author_rejects_missing_or_invalid_external_actuator_gain(gain):
    rule=simple_rule();rule['model']['physical_parameters']={}
    if gain is not None:rule['model']['physical_parameters']['position_kp']=gain
    summary=[{'id':'moving','type':'Fixture','parameters':{'width_m':.3},
              'external_joints':[{'type':'hinge'}]}]
    with pytest.raises(SceneError,match='positive model.physical_parameters.position_kp'):
        SceneGenerationAgent._validate_candidate(rule,summary)


def test_author_repairs_gain_before_scene_build_and_preserves_aml_tuning(tmp_path):
    c={'id':'moving','type':'Fixture','parameters':{'width_m':{'value':.3},
       'position_kp':{'value':1200},'damping':{'value':20}}}
    ir={'components':[c],'joints':[{'id':'j1','type':'hinge','child_body':'moving'}],'io':[]}
    bad=simple_rule();good=simple_rule()
    good['model']['physical_parameters']={'position_kp':'$position_kp','damping':'$damping','mass_kg':1}
    client=ReplyClient([json.dumps({'rules':[bad]}),json.dumps({'rules':[good]})])
    result=SceneGenerationAgent(client).draft_rules(ir,'preview',{'Fixture'},tmp_path)
    assert result==[good] and len(client.calls)==2
    assert 'position_kp' in client.calls[1]['messages'][-1]['content']
    summary=[SceneGenerationAgent._component_summary(c,ir)]
    good['model']['physical_parameters']['position_kp']=100
    with pytest.raises(SceneError,match='must preserve AML 1200'):SceneGenerationAgent._validate_candidate(good,summary)


def test_agent_cannot_expand_user_edit_scope(ir,tmp_path):
    cid=ir['components'][0]['id']
    client=ReplyClient([json.dumps({'editable_ids':[cid,'another']})])
    with pytest.raises(SceneError,match='expanded'):SceneGenerationAgent(client).local_edit(ir,'move',[cid],tmp_path)


def test_local_edit_uses_patch_prompt_and_repairs_rule_wrapper(ir,tmp_path):
    c=next(c for c in ir['components'] if c['type']=='DiffusePhotoelectricSensor')
    pose=copy.deepcopy(c['pose']);pose['position_m'][0]+=.01
    patch={'base_sha256':facts_digest(ir),'editable_ids':[c['id']],'reason':'move selected sensor 10mm',
           'operations':[{'op':'set_pose','component_id':c['id'],**pose}]}
    client=ReplyClient([json.dumps({'rules':[],'output':patch}),json.dumps(patch)])
    revised,result=SceneGenerationAgent(client).local_edit(ir,'move selected sensor 10mm',[c['id']],tmp_path)
    assert result==patch and revised['io']==ir['io']
    assert 'not authoring' in client.calls[0]['messages'][0]['content']
    assert len(client.calls)==2


def test_legacy_generator_does_not_invent_gantry(ir):
    from src.agent.scene_gen import SceneSpecGenerator
    from src.agent.aml_parser import parse_aml
    legacy,_=parse_aml(AML)
    with pytest.raises(ValueError,match='scene_agent'):SceneSpecGenerator().generate({'axes':[]},legacy)


def test_draft_schemas_match_examples(ir):
    schema=pytest.importorskip('jsonschema')
    root=AML.parents[2]
    schema.validate(ir,read_json(root/'schemas/scene_agent/device_ir.schema.json'))
    schema.validate(simple_rule(),read_json(root/'schemas/scene_agent/component_rule.schema.json'))


def test_failed_component_build_is_returned_to_agent_for_local_repair(ir,tmp_path):
    pytest.importorskip('mujoco')
    from src.agent.scene_agent.workflow import prepare
    c=copy.deepcopy(ir['components'][0]);c.update(type='Fixture',parameters={'width_m':{'value':.3}})
    ir.update(components=[c],joints=[],assemblies=[],interfaces=[],connections=[],io=[],pending=[])
    class Author:
        repairs=0
        def draft_rules(self,*args):
            bad=simple_rule();bad['model']['geoms'][0]['size'][1]=-1
            return [bad]
        def repair_candidate(self,device,request,rule,reports,evidence):
            assert reports[0]['status']=='failed' and device['io']==[]
            self.repairs+=1
            return simple_rule()
    author=Author();library=ComponentLibrary(tmp_path/'library')
    task=prepare(ir,'static fixture',tmp_path/'task',library,author=author)
    assert author.repairs==1 and task['candidates'][0]['attempts']==2
    assert task['status']=='awaiting_component_approval'
    assert not list((library.root/'approved').rglob('approval.json'))
