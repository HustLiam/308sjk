"""Plan boundaries, qualification invalidation and PLC-generated control semantics."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from agent.scene_agent.common import SceneError, write_json
from agent.scene_agent.motion_agent import validate_plan, resolve_device, BoundMotionAgent
from agent.scene_agent.binding_registry import certify, qualified, profiles
from agent.scene_agent.robotcell_io import load_config, component_id
from agent.pipeline import PLCGenerator
from test_plc_preview import make_preview, CLIENT
from test_robotcell_io import ScanClient


@pytest.fixture
def config():
    return load_config()


def capability(config):
    return {'component_id':component_id(config),'component_name':'010RB_100_KR240R2700prime_',
        'max_speed_deg_s':30,'joints':[{'variable':p['name'],'current_deg':0,
        'range_deg':[-180,180],'deg_per_count':p['deg_per_count'],'joint_id':i,'source_id':p['source_id']}
        for i,p in enumerate(p for p in config['io_list'] if p['role']=='target')]}


def plan(cap):
    return {'component_id':cap['component_id'],'repeat':1,'question':'','steps':[
        {'duration_s':1,'hold_s':0,'targets':[{'variable':cap['joints'][0]['variable'],
        'mode':'relative','value_deg':3}]}]}


def test_relative_repeat_and_unmentioned_axes(config):
    cap=capability(config);p=plan(cap);p['repeat']=3
    steps=validate_plan(p,cap)
    assert [s['to_deg']['rb100_j1_target'] for s in steps]==[3,6,9]
    assert all(s['to_deg']['rb100_j2_target']==0 for s in steps)


@pytest.mark.parametrize('change',[
    lambda p:p.update(component_id='other'),
    lambda p:p.update(repeat=True),lambda p:p.update(repeat=21),
    lambda p:p['steps'][0].update(duration_s=0),
    lambda p:p['steps'][0].update(duration_s=float('nan')),
    lambda p:p['steps'][0].update(hold_s=-1),
    lambda p:p['steps'][0]['targets'][0].update(variable='unbound'),
    lambda p:p['steps'][0]['targets'][0].update(mode='cartesian'),
    lambda p:p['steps'][0]['targets'][0].update(value_deg=181),
    lambda p:p['steps'][0]['targets'][0].update(value_deg=40),
    lambda p:p['steps'][0]['targets'][0].update(value_deg=float('inf')),
    lambda p:p['steps'][0]['targets'].append(deepcopy(p['steps'][0]['targets'][0])),
])
def test_bad_plan_rejected(config,change):
    cap=capability(config);p=plan(cap);change(p)
    with pytest.raises(ValueError):validate_plan(p,cap)


def test_repeat_rechecks_limits(config):
    cap=capability(config);cap['joints'][0]['current_deg']=175
    p=plan(cap);p['repeat']=3
    with pytest.raises(ValueError,match='限位'):validate_plan(p,cap)


def test_device_must_be_named_and_unique():
    cs={str(i):{'id':str(i),'name':name} for i,name in enumerate(['010RB_100','010RB_100_KR240_','030RB_100_KR240_'])}
    assert resolve_device('010RB_100_KR240 的第一轴转3度',cs)=='1'
    assert resolve_device('030RB 第一轴转3度',cs)=='2'
    for request in ('所选机械臂转3度','010RB转3度','010RB_100_KR240和030RB_100_KR240都转3度'):
        with pytest.raises(SceneError):resolve_device(request,cs)


def test_compiler_uses_plc_feedback_timers_and_same_mapping(config):
    cap=capability(config);p=plan(cap);p['steps'][0]['hold_s']=.2
    result=PLCGenerator(None).generate_bound_motion(config,cap,p)
    xml=result['xml']
    assert 'TIME_TO_REAL' in xml and 'TIME_TO_DINT' not in xml and 'rb100_j1_actual' in xml and 'agent_motion_dwell0.Q' in xml
    assert 'agent_motion_state := 1' in xml
    assert result['io_list'][:14]==config['io_list']
    p['steps'][0]['targets'][0]['value_deg']=4
    assert PLCGenerator(None).generate_bound_motion(config,cap,p)['program_hash']!=result['program_hash']


def test_generalized_device_names_axis_count_and_addresses(config):
    c=deepcopy(config)
    c['io_list']=[p for p in c['io_list'] if p['role'] not in ('target','actual') or '_j1_' in p['name'] or '_j2_' in p['name']]
    for i,p in enumerate(c['io_list']):
        p.update(component_id='other-cell',name='cellB_'+p['name'],address='%QW'+str(200+i))
    cap=capability(c);p=plan(cap)
    r=PLCGenerator(None).generate_bound_motion(c,cap,p)
    assert '%QW200' in r['xml'] and 'cellB_rb100_j1_target' in r['xml']
    assert 'rb100_j6_target' not in r['xml']


def qualification(tmp_path,config):
    folder=tmp_path/'scene';folder.mkdir()
    for name in ('scene.xml','body_mapping.json','assembly_checks.json'):(folder/name).write_text('{}')
    config['adapter']['scene_dir']=str(folder)
    profile=tmp_path/'test.io.json';write_json(profile,{'io_list':config['io_list']})
    write_json(tmp_path/'test.adapter.json',config['adapter']);write_json(tmp_path/'test.motion.json',config['motion'])
    report=tmp_path/'report.json'
    write_json(report,{'status':'passed','unbound_qpos_unchanged':True,
        'channels':{p['name']:{} for p in config['io_list'] if p['role']=='target'},
        'events':[{'feedback':{'v':1},'plc_observed_inputs':{'v':1}}]})
    certify(profile,config,report)
    return profile,folder,report


@pytest.mark.parametrize('changed',['mapping','motion','model','proof'])
def test_qualification_invalidates_on_change(tmp_path,config,changed):
    profile,folder,report=qualification(tmp_path,config)
    assert qualified(profile,config)
    if changed=='mapping':config['io_list'][0]['address']='%QW300'
    if changed=='motion':config['motion']['max_speed_deg_s']=40
    if changed=='model':(folder/'scene.xml').write_text('changed')
    if changed=='proof':report.write_text('{}')
    assert not qualified(profile,config)


def test_registry_scene_isolation_and_duplicates(tmp_path,config):
    profile,folder,_=qualification(tmp_path,config)
    assert component_id(config) in profiles(folder,[tmp_path])
    assert not profiles(tmp_path/'other',[tmp_path])
    for suffix in ('io','adapter','motion','verified'):
        (tmp_path/f'duplicate.{suffix}.json').write_bytes((tmp_path/f'test.{suffix}.json').read_bytes())
    assert not profiles(folder,[tmp_path])


def test_unverified_request_never_reaches_agent_or_plc(config,tmp_path):
    from agent.scene_agent.agent_control import AgentControl
    scan=ScanClient(config);preview=make_preview(config,lambda:scan)
    controller=AgentControl(preview,tmp_path,{},[],lambda _:pytest.fail('must not invoke model'))
    with pytest.raises(SceneError,match='检验'):controller.start(CLIENT,'Robot 转3度')
    assert not scan.writes


def test_planner_clarification_cannot_deploy(config,tmp_path):
    client=SimpleNamespace(chat_completions=lambda _: {'choices':[{'message':{'content':json.dumps({'question':'请明确目标关节'})}}]})
    with pytest.raises(SceneError,match='明确目标关节'):
        BoundMotionAgent(client).plan('Robot去抓东西',capability(config),tmp_path)


def test_ui_control_labels_and_no_old_pose_button():
    ui=Path(__file__).resolve().parents[1]/'src/agent/web_ui'
    text=(ui/'index.html').read_text(encoding='utf8')
    assert '<summary>agent控制</summary>' in text and '>下发命令</button>' in text
    assert 'id="ds-pose"' not in text and '让 Agent 调整所选组件' not in text
    js=(ui/'device_scene.js').read_text(encoding='utf8')
    assert '/api/device-scene/agent-command' in js
