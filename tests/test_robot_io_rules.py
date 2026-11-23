"""IO binding verdicts must not conflate bad excitation with bad mechanics."""
import copy
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from test_device_robot import robot
from src.agent.scene_agent.author import SceneGenerationAgent
from src.agent.scene_agent.common import SceneError
from src.agent.scene_agent.robot_io import contracts, rules_document
from src.agent.scene_agent.simulation_tests import generate_test_plan, validate_test_plan, run_test_plan


def pick(plan, name):
    return next(m for m in plan['modules'] if m['kind']=='io_binding' and m['target']==name)


def changed_xml(meta, root):
    meta=copy.deepcopy(meta);xml=ET.tostring(root,encoding='unicode')
    meta['model_xml_sha256']=hashlib.sha256(xml.encode()).hexdigest()
    return meta,xml


def test_rules_use_local_motion_and_separate_rejected_boundaries(robot):
    ir,meta,xml=robot;before=copy.deepcopy(ir);plan=generate_test_plan(ir)
    contract=contracts(ir)['cmd1'];program=pick(plan,'cmd1')
    assert contract['raw_window'][0]>-500 and contract['raw_window'][1]<500
    assert all(contract['raw_window'][0]<=s['value']<=contract['raw_window'][1] for s in program['steps'] if s['op']=='drive')
    assert {s['value'] for s in program['steps'] if s['op']=='reject'}=={-501,501}
    report=run_test_plan(ir,meta,xml,plan,[m['id'] for m in plan['modules'] if m.get('ruleset')])
    assert report['io_binding_summary']['failed']==0 and report['io_binding_summary']['inconclusive']==0
    assert report['io_binding_summary']['passed']==len([m for m in plan['modules'] if m.get('ruleset')])
    assert all(m['mechanical_relationship_status']=='not_assessed' for m in report['modules'])
    assert all(c['passed'] for m in report['modules'] for c in m['checks'] if c['name'].startswith('invalid_command_rejected'))
    assert before==ir and rules_document(ir)['format']=='308sjk.robot_io_rules/1'


def test_agent_authors_complete_programs_and_repairs_invalid_stimuli(robot,tmp_path):
    ir,meta,xml=robot;plan=generate_test_plan(ir);calls=[]
    programs=[{'test_id':m['id'],'steps':copy.deepcopy(m['steps'])} for m in plan['modules'] if m.get('ruleset')]
    # Different legal excitation is selected by the author, not overwritten by
    # the host after generation. Keep >2x positional tolerance on both signs.
    command=next(p for p in programs if p['test_id']==pick(plan,'cmd1')['id'])
    for step in command['steps']:
        if step['op']=='drive':step['value']=round(step['value']*.75)
    class Client:
        def chat_completions(self,payload,**kwargs):
            calls.append(payload);context=json.loads(payload['messages'][1]['content'])
            assert context['rules']['format']=='308sjk.robot_io_rules/1'
            response=copy.deepcopy(programs)
            if len(calls)==1:
                next(p for p in response if p['test_id']==pick(plan,'reset')['id'])['steps'].pop(3)
                next(p for p in response if p['test_id']==pick(plan,'estop')['id'])['steps'].append({'op':'contact','value':True})
            else:
                feedback=json.loads(payload['messages'][-1]['content'])['validation_error']
                assert 'latch' in feedback and 'reset' in feedback and 'estop' in feedback
            return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'io_scripts':response})}}]}
    authored=SceneGenerationAgent(Client()).draft_test_sequences(ir,plan,tmp_path)
    assert len(calls)==2 and (tmp_path/'attempt_001/script_validation_error.json').exists()
    assert all(m['script_origin']=='agent' for m in authored['modules'] if m.get('ruleset'))
    assert pick(authored,'cmd1')['steps']==command['steps'] and pick(plan,'cmd1')['script_origin']=='baseline'
    report=run_test_plan(ir,meta,xml,authored,[pick(authored,'cmd1')['id']])
    assert report['modules'][0]['binding_status']=='passed'


@pytest.mark.parametrize('mutation',['normal_limit','drop_negative','drop_reject','assertion','feedback_limit','missing_latch','contact_type','rule_hash'])
def test_invalid_or_weakened_scripts_are_rejected_before_running(robot,mutation):
    ir,_,_=robot;plan=generate_test_plan(ir);cmd=pick(plan,'cmd1')
    if mutation=='normal_limit':cmd['steps'][0]['value']=500
    elif mutation=='drop_negative':cmd['steps']=[s for s in cmd['steps'] if s['op']!='drive' or s['value']>=0]
    elif mutation=='drop_reject':cmd['steps']=[s for s in cmd['steps'] if s['op']!='reject']
    elif mutation=='assertion':cmd['steps'][0]['expected']=True
    elif mutation=='feedback_limit':pick(plan,'fb1')['steps'][0]['position_rad']=.5
    elif mutation=='missing_latch':pick(plan,'reset')['steps'].pop(3)
    elif mutation=='contact_type':pick(plan,'estop')['steps'][0]['value']=1
    else:cmd['rules_sha256']='0'*64
    with pytest.raises(SceneError):validate_test_plan(ir,plan)


@pytest.mark.parametrize('mutation',['swap_address','wrong_scale','actuator_route','wrong_in_position'])
def test_binding_faults_have_binding_verdict_not_scenario_verdict(robot,mutation):
    ir,original,xml=robot;meta=copy.deepcopy(original);point='cmd1'
    if mutation=='swap_address':
        next(p for p in meta['io'] if p['name']=='cmd1')['address']='%QW2'
    elif mutation=='wrong_scale':
        next(p for p in meta['io'] if p['name']=='cmd1')['physical_per_plc_unit']=.002
    elif mutation=='actuator_route':
        root=ET.fromstring(xml);root.find('./actuator/position[@name="a_joint1"]').set('joint','j_joint2')
        meta,xml=changed_xml(meta,root)
    else:
        meta['components']['link1']['parameters']['position_tolerance_rad']=.8;point='inpos1'
    plan=generate_test_plan(ir);report=run_test_plan(ir,meta,xml,plan,[pick(plan,point)['id']]);module=report['modules'][0]
    assert module['binding_status']=='failed' and module['failure_category']=='io_binding_error'
    assert module['mechanical_relationship_status']=='not_assessed'


def test_collision_blocks_io_scenario_without_claiming_binding_error(robot):
    ir,meta,xml=robot;root=ET.fromstring(xml)
    root.find('.//body[@name="c_link2"]/geom').set('pos','0 -.1 0');meta,xml=changed_xml(meta,root)
    plan=generate_test_plan(ir);report=run_test_plan(ir,meta,xml,plan,[pick(plan,'cmd1')['id']]);module=report['modules'][0]
    assert module['status']=='blocked' and module['binding_status']=='inconclusive'
    assert module['failure_category']=='scenario_blocked' and report['io_binding_summary']['failed']==0


def test_unresponsive_drive_is_not_misreported_as_binding_or_mechanical_error(robot):
    ir,meta,xml=robot;root=ET.fromstring(xml)
    root.find('./actuator/position[@name="a_joint1"]').set('kp','0');meta,xml=changed_xml(meta,root)
    plan=generate_test_plan(ir);report=run_test_plan(ir,meta,xml,plan,[pick(plan,'cmd1')['id']]);module=report['modules'][0]
    assert module['status']=='failed' and module['failure_category']=='response_failed'
    assert module['binding_status']=='inconclusive' and module['response_status']=='failed'
    assert not any(not c['passed'] for c in module['checks'] if c['domain']=='binding')


def test_command_echo_cannot_pass_feedback_state_injection(robot,monkeypatch):
    from runtime.device_robot import RobotDriver
    original=RobotDriver.post_step
    def echo(driver):
        original(driver)
        driver.r.signals['fb1']=driver.r.signals['cmd1']
    monkeypatch.setattr(RobotDriver,'post_step',echo)
    ir,meta,xml=robot;plan=generate_test_plan(ir)
    report=run_test_plan(ir,meta,xml,plan,[pick(plan,'fb1')['id']])
    assert report['modules'][0]['binding_status']=='failed'
    assert any(not c['passed'] for c in report['modules'][0]['checks'] if c['name'].startswith('measured_encoder'))
