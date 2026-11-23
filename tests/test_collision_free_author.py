"""New non-robot devices require real geometry-aware, collision-free programs."""
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from runtime.device_scene import build_device_scene
from src.agent.scene_agent.author import SceneGenerationAgent
from src.agent.scene_agent.common import SceneError,read_json
from src.agent.scene_agent.device_ir import parse_device_ir
from src.agent.scene_agent.rules import ComponentLibrary
from src.agent.scene_agent.simulation_tests import generate_test_plan,run_test_plan,validate_test_plan
from test_simulation_tests import physical
from test_device_robot import robot
from src.agent.scene_agent.test_author import validate_response_shape


def device_fixture(folder,kind='gantry'):
    ir=parse_device_ir(Path(__file__).resolve().parents[1]/'examples/aml/factoryforge_sorting_reference.aml')
    ir.update(station='Generated_'+kind,components=[],joints=[],interfaces=[],connections=[],assemblies=[],couplings=[],io=[],pending=[])
    ir['simulation_requirements']['checks']=[{'id':'mechanics','kind':'mechanical_joint','target':'*','description':'test full declared travel'}]
    def component(ident,parent,position,typ):
        ir['components'].append({'id':ident,'name':ident,'type':typ,'parent_id':parent,'parameters':{},
            'pose':{'position_m':position,'rpy_rad':[0,0,0]},'interfaces':[],'source':{}})
    component('base',None,[0,0,1],'Base');component('carrier','base',[0,0,0],'Carrier')
    component('tool','carrier',[0,0,0],'Tool')
    component('obstacle','base',[.2,0,0] if kind=='gantry' else [.15*__import__('math').cos(.4),.15*__import__('math').sin(.4),0],'Obstacle')
    for ident,parent,child,typ,axis,limits in [('clearance','base','carrier','slide',[0,1,0] if kind=='gantry' else [0,0,1],[0,.3]),
        ('target','carrier','tool','slide' if kind=='gantry' else 'hinge',[1,0,0] if kind=='gantry' else [0,0,1],[0,.4] if kind=='gantry' else [-.8,.8])]:
        ir['joints'].append({'id':ident,'name':ident,'type':typ,'parent_body':parent,'child_body':child,'axis':axis,'axis_frame':'child',
            'range':limits,'initial':0,'parent_anchor_m':[0,0,0],'child_anchor_m':[0,0,0]})
    library=ComponentLibrary(folder/'library');rules=[];entries={}
    for typ,size,pos in [('Base',[.02,.02,.02],[0,-.1,0]),('Carrier',[.02,.02,.02],[0,-.1,0]),
        ('Tool',[.015,.015,.015],[0,0,0] if kind=='gantry' else [.15,0,0]),('Obstacle',[.025,.05,.035] if kind=='gantry' else [.015,.015,.025],[0,0,0])]:
        rule={'format':'308sjk.component_rule/0.1-draft','component_type':typ,'version':'0.1.0','parameter_bounds':{},'assumptions':['synthetic non-robot obstacle fixture'],
            'unresolved':[],'model':{'behavior':'none','physical_parameters':{'mass_kg':1,'position_kp':1000,'damping':50},
            'geoms':[{'name':'body','type':'box','size':size,'pos':pos}]}}
        path=library.stage(rule);entries[typ]={'rule':rule,'directory':str(path)};rules.append(rule)
    meta,xml=build_device_scene(ir,library,folder/'scene',candidates=entries)
    return ir,meta,xml,rules


def reply(plan,kind='gantry',safe=True):
    programs=[]
    for module in plan['modules']:
        if module['target']=='clearance':path=[{'clearance':.3,'target':0},{'clearance':0,'target':0},{'clearance':0,'target':0}]
        elif kind=='gantry':
            path=([{'clearance':.15,'target':0},{'clearance':.15,'target':.4},{'clearance':.15,'target':0},{'clearance':0,'target':0}]
                if safe else [{'clearance':0,'target':.4},{'clearance':0,'target':0},{'clearance':0,'target':0}])
        else:
            path=([{'clearance':.15,'target':0},{'clearance':.15,'target':-.8},{'clearance':.15,'target':.8},{'clearance':.15,'target':0},{'clearance':0,'target':0}]
                if safe else [{'clearance':0,'target':-.8},{'clearance':0,'target':.8},{'clearance':0,'target':0}])
        programs.append({'test_id':module['id'],'waypoints':path})
    return {'joint_paths':programs,'io_scripts':[]}


@pytest.mark.parametrize('kind',['gantry','rotary_lift'])
def test_agent_repairs_collision_for_different_new_device_structures(tmp_path,kind):
    ir,meta,xml,_=device_fixture(tmp_path,kind);plan=generate_test_plan(ir);calls=[]
    class Client:
        def chat_completions(self,payload,**kwargs):
            calls.append(copy.deepcopy(payload));context=json.loads(payload['messages'][1]['content'])
            assert context['geometry']['checked_geom_pairs'] and not context['geometry']['initial_collisions']
            assert context['geometry']['scope']['device_scope'].startswith('all_compiled')
            if len(calls)==2:
                feedback=json.loads(payload['messages'][-1]['content']);assert 'contacts' in feedback['validation_error']
            return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(reply(plan,kind,safe=len(calls)>1))}}]}
    original=copy.deepcopy(ir);authored=SceneGenerationAgent(Client()).draft_collision_free_tests(ir,meta,xml,plan,tmp_path/'author')
    assert len(calls)==2 and ir==original
    rejected=read_json(tmp_path/'author/attempt_001/safety_preflight.json');assert rejected['issues']
    accepted=read_json(tmp_path/'author/accepted.json');assert accepted['attempt']==2 and accepted['collision_free_preflight']
    report=run_test_plan(ir,meta,xml,authored,output=tmp_path/'results');assert report['complete'],report
    for module in report['modules']:
        frames=read_json(tmp_path/'results'/module['replay'])['frames'];assert all(not f['collisions'] for f in frames)
    assert all(m['steps'][0]['op']=='joint_configuration' for m in authored['modules'])


def test_repeated_unsafe_programs_do_not_fall_back_or_claim_generation_success(tmp_path):
    ir,meta,xml,_=device_fixture(tmp_path);plan=generate_test_plan(ir);calls=[]
    class Client:
        def chat_completions(self,payload,**kwargs):
            calls.append(payload)
            return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(reply(plan,safe=False))}}]}
    with pytest.raises(SceneError,match='preflight rejected'):
        SceneGenerationAgent(Client()).draft_collision_free_tests(ir,meta,xml,plan,tmp_path/'author')
    assert len(calls)==3 and not (tmp_path/'author/accepted.json').exists()
    assert len(list((tmp_path/'author').glob('attempt_*/script_validation_error.json')))==3


@pytest.mark.parametrize('fault',['missing_target','missing_endpoint','limit','coupling','assertion','missing_joint','no_restore'])
def test_paths_cannot_avoid_collisions_by_weakening_mandatory_facts(tmp_path,fault):
    ir,meta,xml,_=device_fixture(tmp_path);plan=generate_test_plan(ir);response=reply(plan)
    for item,module in zip(response['joint_paths'],plan['modules']):module['steps']=[{'op':'joint_configuration','positions':q} for q in item['waypoints']]
    if fault=='missing_target':plan['modules'].pop()
    elif fault=='missing_endpoint':plan['modules'][1]['steps'][1]['positions']['target']=.1
    elif fault=='limit':plan['modules'][1]['steps'][1]['positions']['target']=.5
    elif fault=='coupling':ir['couplings']=[{'id':'coupled','leader':'clearance','follower':'target','ratio':1,'offset':0}];plan=generate_test_plan(ir);plan['modules'][1]['steps']=[{'op':'joint_configuration','positions':q} for q in reply(plan)['joint_paths'][1]['waypoints']]
    elif fault=='assertion':plan['modules'][1]['steps'][0]['expected']=True
    elif fault=='missing_joint':del plan['modules'][1]['steps'][0]['positions']['clearance']
    else:plan['modules'][1]['steps'][-1]['positions']['clearance']=.15
    with pytest.raises(SceneError):validate_test_plan(ir,plan)


def test_new_scene_workflow_passes_assembled_geometry_to_author_and_downloads_safety_evidence(tmp_path):
    from src.agent.scene_agent.workflow import prepare,build_task,check_task
    from src.agent.scene_agent.web import SceneWorkbench
    import uuid
    ir,_,_,rules=device_fixture(tmp_path/'fixture');ident=uuid.uuid4().hex;manager=SceneWorkbench(tmp_path/'workbench')
    folder=manager.directory(ident);folder.mkdir(parents=True);calls=[]
    class Client:
        def chat_completions(self,payload,**kwargs):
            assert (folder/'test_planning_scene/scene.xml').is_file()
            calls.append(payload);context=json.loads(payload['messages'][1]['content'])
            assert context['geometry']['geometries']
            return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(reply(generate_test_plan(ir)))}}]}
    try:
        prepare(ir,'new equipment',folder,manager.library,candidate_rules=rules,author=SceneGenerationAgent(Client()),force_regenerate=True,verification_profile='legacy')
        assert len(calls)==1 and read_json(folder/'test_safety.json')['collision_free_preflight']
        build_task(folder,manager.library,isolated_candidates=True);check_task(folder,isolated_candidates=True)
        manager._save({'id':ident,'revision':1,'created_at':0,'name':'new device','request':'test','busy':False,'phase':'ready','scene_kind':'candidate_scene','logs':[],'preview_revision':1})
        state=manager.state(ident)
        assert state['test_safety']['collision_free_preflight'] and state['test_safety']['origin']=='agent_geometry_aware'
        assert manager.file(ident,'test_safety.json').is_file()
        import zipfile,io
        with zipfile.ZipFile(io.BytesIO(manager.bundle(ident))) as archive:assert 'test_safety.json' in archive.namelist()
    finally:manager.close()


def test_non_robot_io_collision_is_intercepted_between_capture_frames(physical,tmp_path):
    import hashlib
    import mujoco
    import xml.etree.ElementTree as ET
    from runtime.device_scene import DeviceRuntime
    ir,original,xml=physical;meta=copy.deepcopy(original);r=DeviceRuntime(meta,xml)
    point=next(p for p in ir['io'] if p['quantity']=='extend_cmd');cid=point['component_id']
    joint=next(j for j in ir['joints'] if j['id']==meta['components'][cid]['parameters']['driven_joint_id']);ji=mujoco.mj_name2id(r.model,mujoco.mjtObj.mjOBJ_JOINT,'j_'+joint['id'])
    r.data.qpos[r.model.jnt_qposadr[ji]]=sum(joint['range'])/2;mujoco.mj_forward(r.model,r.data)
    body=mujoco.mj_name2id(r.model,mujoco.mjtObj.mjOBJ_BODY,'c_'+joint['child_body']);gid=int(r.model.body_geomadr[body]);position=r.data.geom_xpos[gid].tolist()
    root=ET.fromstring(xml);ET.SubElement(root.find('worldbody'),'geom',name='test_barrier',type='box',size='.002 .002 .002',pos=' '.join(map(str,position)))
    xml=ET.tostring(root,encoding='unicode');meta['model_xml_sha256']=hashlib.sha256(xml.encode()).hexdigest()
    plan=generate_test_plan(ir);module=next(m for m in plan['modules'] if m['target']==point['name'])
    result=run_test_plan(ir,meta,xml,plan,[module['id']],output=tmp_path)['modules'][0]
    assert result['status']=='blocked' and result['failure_category']=='scenario_blocked'
    assert any(c.get('execution')=='sandbox_step_rejected' for c in result['checks'])
    if result['replay']:assert all(not f['collisions'] for f in read_json(tmp_path/result['replay'])['frames'])


@pytest.mark.parametrize('mutation,expected',[
    ('cases','missing_fields'),('empty_io','missing_test_ids'),('duplicate','duplicate_test_id'),
    ('unknown','unknown_test_id'),('non_string_id','unknown_test_id'),('steps_object','io_scripts[0].steps'),
    ('extra_field','unexpected_fields'),('missing_collection','required_test_ids'),
])
def test_response_reports_exact_structural_and_coverage_errors(mutation,expected):
    response={'joint_paths':[],'io_scripts':[{'test_id':'io_a','steps':[]}]}
    entry=response['io_scripts'][0]
    if mutation=='cases':entry['cases']=entry.pop('steps')
    elif mutation=='empty_io':response['io_scripts']=[]
    elif mutation=='duplicate':response['io_scripts'].append(copy.deepcopy(entry))
    elif mutation=='unknown':entry['test_id']='missing'
    elif mutation=='non_string_id':entry['test_id']=[]
    elif mutation=='steps_object':entry['steps']={}
    elif mutation=='extra_field':entry['passed']=True
    else:del response['io_scripts']
    original=copy.deepcopy(response)
    with pytest.raises(SceneError,match=__import__('re').escape(expected)):
        validate_response_shape(response,{}, {'io_a':{}})
    assert response==original


def test_geometry_author_repairs_cases_and_missing_io_without_losing_coverage(robot,tmp_path):
    ir,meta,xml=robot;plan=generate_test_plan(ir);calls=[]
    home={j['id']:j['initial'] for j in ir['joints']}
    joints={j['id']:j for j in ir['joints']}
    response={'joint_paths':[], 'io_scripts':[]}
    for module in plan['modules']:
        if module['kind']=='mechanical_joint':
            jid=module['target'];lo,hi=joints[jid]['range']
            response['joint_paths'].append({'test_id':module['id'],
                'waypoints':[{**home,jid:lo},{**home,jid:hi},dict(home)]})
        elif module.get('ruleset'):
            response['io_scripts'].append({'test_id':module['id'],'steps':copy.deepcopy(module['steps'])})
    class Client:
        def chat_completions(self,payload,**kwargs):
            calls.append(copy.deepcopy(payload));value=copy.deepcopy(response)
            assert '"steps"' in payload['messages'][0]['content'] and 'never cases' in payload['messages'][0]['content']
            if len(calls)==1:
                for entry in value['io_scripts']:entry['cases']=entry.pop('steps')
            elif len(calls)==2:
                feedback=json.loads(payload['messages'][-1]['content'])['validation_error']
                assert 'missing_fields' in feedback and 'steps' in feedback and 'cases' in feedback
                value['io_scripts']=[]
            else:
                feedback=json.loads(payload['messages'][-1]['content'])['validation_error']
                assert 'missing_test_ids' in feedback
                assert all(entry['test_id'] in feedback for entry in response['io_scripts'])
            return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}]}
    authored=SceneGenerationAgent(Client()).draft_collision_free_tests(ir,meta,xml,plan,tmp_path/'author')
    assert len(calls)==3 and len(authored['modules'])==len(plan['modules'])
    assert read_json(tmp_path/'author/accepted.json')['io_programs']==len(response['io_scripts'])
    assert run_test_plan(ir,meta,xml,authored)['complete']
