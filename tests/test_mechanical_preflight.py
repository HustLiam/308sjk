"""Unsafe scripted sweeps must never become displayed robot motion."""
import copy
import hashlib
import math
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from test_device_robot import robot
from runtime.device_scene import DeviceRuntime
from src.agent.scene_agent.common import read_json
from src.agent.scene_agent.mechanics import RobotCollisionProbe, preflight_joint_path
from src.agent.scene_agent.simulation_tests import generate_test_plan, run_test_plan


def obstructed(robot, *, near_home=False, reverse_axis=False):
    ir, original, xml = robot; meta=copy.deepcopy(original); root=ET.fromstring(xml)
    angle, radius, size = (.03,.3,.001) if near_home else (.4,.07,.004)
    geom=root.find('.//body[@name="c_link1"]/geom');geom.set('pos',f'{radius} 0 0')
    if near_home:geom.set('size','.002 .002 .002')
    if reverse_axis:root.find('.//joint[@name="j_joint1"]').set('axis','0 0 -1')
    ET.SubElement(root.find('worldbody'),'geom',name='path_obstacle',type='box',
        size=f'{size} {size} {size}',pos=f'{radius*math.cos(angle)} {.1+radius*math.sin(angle)} .5')
    xml=ET.tostring(root,encoding='unicode');meta['model_xml_sha256']=hashlib.sha256(xml.encode()).hexdigest()
    return ir,meta,xml


def test_rejected_sweep_only_replays_safe_local_relationship_motion(robot,tmp_path):
    ir,meta,xml=obstructed(robot);plan=generate_test_plan(ir)
    report=run_test_plan(ir,meta,xml,plan,[plan['modules'][0]['id']],output=tmp_path)
    module=report['modules'][0]
    assert report['status']=='blocked' and not report['complete']
    assert module['failure_category']=='motion_plan_rejected'
    assert module['mechanical_relationship_status']=='passed' and module['full_travel_status']=='not_covered'
    rejected=next(c for c in module['checks'] if c['name']=='motion_plan_preflight')
    assert rejected['evidence']['reason']=='collision' and rejected['execution']=='not_executed'
    frames=read_json(tmp_path/module['replay'])['frames'];q=[f['joint_positions']['joint1'] for f in frames]
    assert max(q)<=math.radians(5)+1e-12 and min(q)>=-math.radians(5)-1e-12
    assert min(q)<0<max(q) and q[-1]==0 and all(not f['collisions'] for f in frames)


def test_path_rejection_does_not_hide_wrong_mechanical_axis(robot):
    ir,meta,xml=obstructed(robot,reverse_axis=True);plan=generate_test_plan(ir)
    module=run_test_plan(ir,meta,xml,plan,[plan['modules'][0]['id']])['modules'][0]
    assert module['status']=='failed' and module['mechanical_relationship_status']=='failed'
    assert module['failure_category']=='mechanical_relationship_error'
    assert any(c['name']=='joint_axis' and not c['passed'] for c in module['checks'])


def test_no_legal_local_path_means_no_motion_and_no_relationship_pass(robot,tmp_path):
    ir,meta,xml=obstructed(robot,near_home=True);plan=generate_test_plan(ir)
    module=run_test_plan(ir,meta,xml,plan,[plan['modules'][0]['id']],output=tmp_path)['modules'][0]
    assert module['status']=='blocked' and module['mechanical_relationship_status']=='inconclusive'
    frames=read_json(tmp_path/module['replay'])['frames']
    assert len(frames)==1 and frames[0]['joint_positions']['joint1']==0 and not frames[0]['collisions']


def test_preflight_restores_measured_state_on_rejection_and_cancellation(robot):
    ir,meta,xml=obstructed(robot);runtime=DeviceRuntime(meta,xml);probe=RobotCollisionProbe(ir,runtime)
    saved=runtime.data.qpos.copy();time=float(runtime.data.time)
    assert preflight_joint_path(runtime,ir['joints'][0],[-.5,.5,0],probe)['reason']=='collision'
    assert np.array_equal(runtime.data.qpos,saved) and float(runtime.data.time)==time
    calls=0
    def cancel():
        nonlocal calls
        calls+=1
        if calls==3:raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError,match='cancelled'):
        preflight_joint_path(runtime,ir['joints'][0],[-.5],probe,cancel)
    assert np.array_equal(runtime.data.qpos,saved) and float(runtime.data.time)==time


def test_successful_fk_does_not_claim_actuator_response_validation(robot):
    ir,meta,xml=robot;plan=generate_test_plan(ir)
    module=run_test_plan(ir,meta,xml,plan,[plan['modules'][0]['id']])['modules'][0]
    assert module['mechanical_relationship_status']=='passed'
    assert module['full_travel_status']=='passed' and module['scenario_status']=='clear'
    assert module['response_status']=='not_assessed'


def test_agent_path_rejection_separates_definition_errors_from_unsafe_stimuli(robot):
    for reverse_axis in (False,True):
        ir,meta,xml=obstructed(robot,reverse_axis=reverse_axis);plan=generate_test_plan(ir)
        module=plan['modules'][0];home={j['id']:j['initial'] for j in ir['joints']}
        module['steps']=[{'op':'joint_configuration','positions':{**home,'joint1':q}} for q in (-.5,.5,0)]
        result=run_test_plan(ir,meta,xml,plan,[module['id']])['modules'][0]
        assert result['full_travel_status']=='not_covered' and result['scenario_status']=='blocked'
        assert result['mechanical_relationship_status']==('failed' if reverse_axis else 'inconclusive')
        assert result['failure_category']==('mechanical_relationship_error' if reverse_axis else 'motion_plan_rejected')
        assert result['response_status']=='not_assessed' and result['frame_count']==0
