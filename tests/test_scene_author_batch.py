"""Regressions for conveyor AML batches and scoped candidate corrections."""
import copy
import json

import pytest

from src.agent.scene_agent.author import SceneGenerationAgent
from src.agent.scene_agent.common import SceneError, read_json
from src.agent.scene_agent.rules import RULE_FORMAT


class ReplyClient:
    def __init__(self,replies):self.replies=iter(replies);self.calls=[]
    def chat_completions(self,payload,**kwargs):
        self.calls.append(copy.deepcopy(payload))
        return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(next(self.replies))}}]}


def rule(kind):
    return {'format':RULE_FORMAT,'component_type':kind,'version':'0.1.0',
        'parameter_bounds':{'width_m':[.1,.5]},'assumptions':[],'unresolved':[],
        'model':{'behavior':'none','geoms':[{'name':'housing','type':'box','size':['=width_m/2',.1,.1]}]}}


def device():
    return {'components':[{'id':name,'type':kind,'parameters':{'width_m':{'value':width}}}
        for name,kind,width in [('belt','BeltConveyor',.3),('low','Sensor',.1),('high','Sensor',.2)]],
        'joints':[],'io':[]}


def test_partial_repair_preserves_valid_conveyor_and_checks_all_sensor_instances(tmp_path):
    belt=rule('BeltConveyor');sensor=rule('Sensor');bad=copy.deepcopy(sensor)
    bad['model']['geoms'][0]['size'][0]='=width_m-0.15'
    client=ReplyClient([{'rules':[belt,bad]},{'rules':[sensor]}])
    ir=device();original=copy.deepcopy(ir)
    result=SceneGenerationAgent(client).draft_rules(ir,'conveyor',{'BeltConveyor','Sensor'},tmp_path)
    assert result==[belt,sensor] and ir==original and len(client.calls)==2
    feedback=json.loads(client.calls[1]['messages'][-1]['content'].split('retain device facts: ',1)[1])
    assert feedback['remaining_component_types']==['Sensor']
    assert feedback['retained_validated_types']==['BeltConveyor']
    assert 'instance low' in feedback['errors'][0] and 'model.geoms[0].size' in feedback['errors'][0]
    assert 'evaluated=' in feedback['errors'][0]
    progress=read_json(tmp_path/'round_002/validation_progress.json')
    assert progress['validated_in_round']=={'BeltConveyor':1,'Sensor':2}
    assert progress['approval']=='not_granted' and not list(tmp_path.rglob('approval.json'))


def test_duplicate_instance_rules_request_one_shared_type_rule(tmp_path):
    belt=rule('BeltConveyor');low=rule('Sensor');high=copy.deepcopy(low)
    high['model']['geoms'][0]['size'][2]=.2
    client=ReplyClient([{'rules':[belt,low,high]},{'rules':[low]}])
    assert SceneGenerationAgent(client).draft_rules(device(),'conveyor',{'BeltConveyor','Sensor'},tmp_path)==[belt,low]
    feedback=client.calls[1]['messages'][-1]['content']
    assert 'Duplicate rule type' in feedback and 'ALL provided instances' in feedback
    first=json.loads(client.calls[0]['messages'][1]['content'])
    assert first['instances_by_type']=={'BeltConveyor':['belt'],'Sensor':['low','high']}


def test_full_retry_cannot_change_previously_validated_geometry(tmp_path):
    belt=rule('BeltConveyor');changed=copy.deepcopy(belt)
    changed['model']['geoms'][0]['size'][1]=999
    sensor=rule('Sensor')
    client=ReplyClient([{'rules':[belt]},{'rules':[changed,sensor]}])
    assert SceneGenerationAgent(client).draft_rules(device(),'conveyor',{'BeltConveyor','Sensor'},tmp_path)==[belt,sensor]
    assert read_json(tmp_path/'round_002/validation_progress.json')['ignored_previously_validated_types']==['BeltConveyor']


def test_invalid_rule_envelope_does_not_discard_other_valid_types(tmp_path):
    belt=rule('BeltConveyor');bad=rule('Sensor');bad['model']['invented_drive']={'speed':1}
    good=rule('Sensor');client=ReplyClient([{'rules':[belt,bad]},{'rules':[good]}])
    assert SceneGenerationAgent(client).draft_rules(device(),'conveyor',{'BeltConveyor','Sensor'},tmp_path)==[belt,good]
    assert 'invented_drive' in client.calls[1]['messages'][-1]['content']


def test_repair_omission_retains_all_valid_types_until_last_round(tmp_path):
    belt=rule('BeltConveyor');sensor=rule('Sensor')
    client=ReplyClient([{'rules':[belt]},{'rules':[]},{'rules':[sensor]}])
    assert SceneGenerationAgent(client).draft_rules(device(),'conveyor',{'BeltConveyor','Sensor'},tmp_path)==[belt,sensor]
    assert read_json(tmp_path/'round_002/validation_progress.json')['remaining_component_types']==['Sensor']


def test_unrequested_type_is_rejected_and_partial_evidence_kept(tmp_path):
    belt=rule('BeltConveyor')
    client=ReplyClient([{'rules':[belt]},{'rules':[rule('Unexpected')]}])
    with pytest.raises(SceneError,match='unexpected component_type'):
        SceneGenerationAgent(client,max_rounds=2).draft_rules(device(),'conveyor',{'BeltConveyor','Sensor'},tmp_path)
    assert read_json(tmp_path/'rules.validated.partial.json')=={'rules':[belt]}
    assert not (tmp_path/'rules.candidate.json').exists()


@pytest.mark.parametrize('size,match',[
    ([.1,.1],r'model.geoms\[0\].size.*expected 3'),
    (['=$width_m',.1,.1],r'model.geoms\[0\].size\[0\].*no \$ inside'),
    ([float('nan'),.1,.1],r'model.geoms\[0\].size.*positive and finite'),
])
def test_geometry_feedback_names_field_and_preserves_raw_candidate(size,match):
    candidate=rule('Sensor');candidate['model']['geoms'][0]['size']=size
    with pytest.raises(SceneError,match=match):
        SceneGenerationAgent._validate_candidate(candidate,[{'id':'low','type':'Sensor','parameters':{'width_m':.1}}])
    assert candidate['model']['geoms'][0]['size'] is size


def test_missing_moving_mass_is_repaired_before_assembly(tmp_path):
    candidate=rule('Sensor');candidate['model']['physical_parameters']={'position_kp':2000}
    fixed=copy.deepcopy(candidate);fixed['model']['physical_parameters']['mass_kg']='$mass_kg'
    ir=device();ir['components']=ir['components'][1:2]
    ir['components'][0]['parameters']['mass_kg']={'value':1,'unit':'kg'}
    ir['joints']=[{'id':'slide','type':'slide','child_body':'low'}]
    client=ReplyClient([{'rules':[candidate]},{'rules':[fixed]}])
    assert SceneGenerationAgent(client).draft_rules(ir,'moving plate',{'Sensor'},tmp_path)==[fixed]
    assert 'Moving body mass must be explicit' in client.calls[1]['messages'][-1]['content']


def test_external_moving_mass_cannot_be_supplied_by_child_body_or_visual_only():
    candidate=rule('Sensor');candidate['model']['physical_parameters']={'position_kp':2000}
    candidate['model']['bodies']=[{'name':'child'}]
    candidate['model']['geoms'][0]['body']='child'
    candidate['model']['geoms'][0]['mass']=1
    with pytest.raises(SceneError,match='Moving body mass must be explicit'):
        SceneGenerationAgent._validate_candidate(candidate,[{'id':'low','type':'Sensor',
            'parameters':{'width_m':.1},'external_joints':[{'type':'slide'}]}])


def test_author_preserves_declared_body_mass_and_geometry_ownership():
    candidate=rule('Sensor');candidate['model']['physical_parameters']={'mass_kg':2,'position_kp':2000}
    component={'id':'low','type':'Sensor','parameters':{'width_m':.1,'mass_kg':1},'external_joints':[{'type':'slide'}]}
    with pytest.raises(SceneError,match='mass_kg must preserve AML 1'):
        SceneGenerationAgent._validate_candidate(candidate,[component])
    component['parameters']={'width_m':.1,'geometry_enabled':False};component['external_joints']=[]
    with pytest.raises(SceneError,match='must not duplicate child geometry'):
        SceneGenerationAgent._validate_candidate(candidate,[component])


def test_controller_receives_external_joint_and_child_geometry_ownership():
    ir={'components':[{'id':'drive','type':'Pusher','parameters':{'driven_joint_id':{'value':'slide'}}},
                      {'id':'low','name':'plate','parent_id':'drive','type':'Sensor','parameters':{}}],
        'joints':[{'id':'slide','type':'slide','child_body':'low'}]}
    summary=SceneGenerationAgent._component_summary(ir['components'][0],ir)
    assert summary['controlled_joints']==ir['joints'] and summary['external_joints']==[]
    assert summary['child_components']==[{'id':'low','name':'plate','type':'Sensor'}]


@pytest.mark.parametrize('parameter,field,value',[('collision_friction','friction',.01),('collision_priority','priority',1)])
def test_conveyor_preserves_explicit_runtime_contact_parameters(parameter,field,value):
    candidate=rule('BeltConveyor');candidate['model']['behavior']='conveyor'
    summary=[{'id':'belt','type':'BeltConveyor','parameters':{'width_m':.3,parameter:value}}]
    with pytest.raises(SceneError,match='must preserve AML '+parameter):
        SceneGenerationAgent._validate_candidate(candidate,summary)
    candidate['model']['geoms'][0][field]='$'+parameter
    SceneGenerationAgent._validate_candidate(candidate,summary)


def test_sensor_marker_cannot_block_material_when_aml_disables_collision():
    candidate=rule('Sensor');summary=[{'id':'low','type':'Sensor','parameters':{'width_m':.1,'collision_enabled':False}}]
    with pytest.raises(SceneError,match='must not introduce blocking colliders'):
        SceneGenerationAgent._validate_candidate(candidate,summary)
    candidate['model']['geoms'][0]['collision']=False
    SceneGenerationAgent._validate_candidate(candidate,summary)


def test_misplaced_provenance_is_relocated_without_losing_metadata(tmp_path):
    candidate=rule('Sensor');metadata={'validation_profile':'plc_executable_device/1','limitations':['外壳为简化几何']}
    candidate['model']['provenance']=metadata;original=copy.deepcopy(candidate)
    revised=SceneGenerationAgent._candidate_envelope({'rules':[candidate]},tmp_path)['rules'][0]
    assert revised['provenance']==metadata and 'provenance' not in revised['model'] and candidate==original
    operation=read_json(tmp_path/'normalization.json')['operations'][0]
    assert operation['op']=='move_rule_metadata' and operation['original_value']==metadata
    candidate['provenance']={'limitations':['不同的原始记录']}
    with pytest.raises(SceneError,match='conflicting'):
        SceneGenerationAgent._candidate_envelope({'rules':[candidate]},tmp_path/'conflict')


def test_default_budget_allows_initial_generation_and_three_corrections(tmp_path):
    belt=rule('BeltConveyor');good=rule('Sensor')
    invalid_bounds=copy.deepcopy(good);invalid_bounds['parameter_bounds']['width_m']=[.3,.4]
    negative=copy.deepcopy(good);negative['model']['geoms'][0]['size'][0]=-1
    wrong_dimension=copy.deepcopy(good);wrong_dimension['model']['geoms'][0]['size']=[.1,.1]
    client=ReplyClient([{'rules':[belt,invalid_bounds]},{'rules':[negative]},{'rules':[wrong_dimension]},{'rules':[good]}])
    result=SceneGenerationAgent(client).draft_rules(device(),'conveyor',{'BeltConveyor','Sensor'},tmp_path)
    assert result==[belt,good] and len(client.calls)==4
    assert read_json(tmp_path/'round_004/validation_progress.json')['validated_in_round']=={'BeltConveyor':1,'Sensor':4}


def test_empty_logical_controller_cannot_acquire_mass_instead_of_its_child():
    candidate=rule('Sensor');candidate['model']['geoms']=[];candidate['model']['physical_parameters']={'mass_kg':1}
    with pytest.raises(SceneError,match='moving child, not this logical body'):
        SceneGenerationAgent._validate_candidate(candidate,[{'id':'controller','type':'Sensor','parameters':{'width_m':.1,'geometry_enabled':False}}])


def test_draft_and_repair_receive_implemented_runtime_binding_contract(tmp_path):
    candidate=rule('Sensor');client=ReplyClient([{'rules':[candidate]},{'rules':[candidate]}])
    agent=SceneGenerationAgent(client)
    agent.draft_rules(device(),'fixture',{'Sensor'},tmp_path/'draft')
    agent.repair_candidate(device(),'repair',candidate,[],tmp_path/'repair')
    for call in client.calls:
        contracts=json.loads(call['messages'][1]['content'])['runtime_kernel_contracts']
        pusher=contracts['binary_prismatic']
        assert pusher['implemented'] and 'component.parameters' in pusher['parameter_source']
        assert 'NOT model.physical_parameters' in pusher['parameter_source']
        assert 'driven_joint_id' in pusher['joint_binding'] and 'actual joint q' in pusher['feedback']


def test_explicit_parameter_reference_objects_are_losslessly_normalized(tmp_path):
    candidate=rule('Sensor');candidate['model']['physical_parameters']={'mass_kg':{'$parameter':'width_m'}}
    original=copy.deepcopy(candidate)
    result=SceneGenerationAgent._candidate_envelope({'rules':[candidate]},tmp_path)['rules'][0]
    assert result['model']['physical_parameters']['mass_kg']=='$width_m'
    assert candidate==original
    operations=read_json(tmp_path/'normalization.json')['operations']
    assert any(o['op']=='normalize_explicit_parameter_reference' and o['original_value']=={'$parameter':'width_m'} for o in operations)
