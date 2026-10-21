"""新产物必须真实部署并有同一版本的物理验收，失败应进入原修复循环。"""
import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from test_workbench_motion import delivery
from agent import workbench_validation as validation
from agent.attribution import AttributionEngine
from agent.task_inputs import load_task_spec
from agent.runtime_control import RuntimeControlError


@pytest.fixture
def execution(delivery,tmp_path,monkeypatch):
    app,ident,_,source=delivery
    spec=load_task_spec(source)
    spec['task_id']='svg_web_'+'d'*32
    directory=tmp_path/'output'/spec['task_id'];directory.mkdir(parents=True)
    shutil.copytree(source/'assets',directory/'assets')
    xml=(source/'final/plcopen.xml').read_text(encoding='utf-8')
    outcomes=[True];motions=[];calls=[]
    class Runtime:
        digest=None;offline=False;mismatch=False;compile_failures=0
        def action(self,action,raw):
            calls.append(action)
            if self.offline:raise RuntimeControlError('connection offline')
            if self.compile_failures:
                self.compile_failures-=1
                raise RuntimeControlError('matiec error',{'status':'FAILED','errors':['matiec error']})
            self.digest=hashlib.sha256(raw).hexdigest()
            return {'status':'OK','deployment':{'sha256':self.digest}}
        def status(self):
            return {'runtime':{'status':'RUNNING'},'prog_id':2,'modbus':{'host':'fake'},
                    'deployment':{'sha256':'wrong' if self.mismatch else self.digest}}
    class Motion:
        def __init__(self,path,*args,**kwargs):
            self.path=path;self.alive=False;self.trace=[{'t':0.0}]
            self.physical_trace=[{'t':0.0,'x_fb':50,'y_fb':50,'z_fb':0,'pen_contact':True}]
            self.verification=None;motions.append(self)
        def start(self):
            outcome=outcomes.pop(0)
            rows=outcome if isinstance(outcome,list) else [{'id':'PATH','pass':outcome,'evidence':'vertex missing' if not outcome else 'reached'}]
            ok=all(row['pass'] for row in rows)
            self.verification={'id':'test','verdict':'PASS' if ok else 'FAIL','status':'completed',
                'physical_samples':1,'details':rows,
                'spec_sha256':hashlib.sha256(json.dumps(load_task_spec(self.path),sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
                'physical_sha256':hashlib.sha256(json.dumps(self.physical_trace,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
                'sha256':{key:hashlib.sha256((self.path/name).read_bytes()).hexdigest() for key,name in
                    [('xml','final/plcopen.xml'),('scene','simulation/scene.spec.json'),('io_map','simulation/io_map.json')]}}
        def status(self):return {'status':'completed','error':None}
        def stop(self):self.alive=False
    class Generator:
        client=object();seed_xml=None;generation_mode='full_xml';feedback=[]
        def generate(self,*args,**kwargs):return {'ok':True,'xml':xml,'rounds':1}
        def repair(self,previous,current,feedback,**kwargs):
            self.feedback.append(feedback)
            # Model a code change; a newline-only response must now be rejected.
            changed=xml.replace('pl_step := pl_step + 1;', 'pl_step := pl_step + 2;',1)
            assert changed!=xml
            return {'ok':True,'xml':changed,'rounds':1}
    runtime=Runtime();generator=Generator()
    monkeypatch.setattr(validation,'Motion',Motion)
    orch=validation.WorkbenchOrchestrator(directory=directory,session=ident,runtime=runtime,
        report=lambda message:None,runs_root=directory.parent,max_iters=2,
        attribution_engine=AttributionEngine())
    monkeypatch.setattr(orch,'_consolidate',lambda *args:None)
    return SimpleNamespace(app=app,spec=spec,directory=directory,orch=orch,runtime=runtime,
        generator=generator,outcomes=outcomes,motions=motions,calls=calls)


def solve(execution):
    from agent.scene_gen import SceneSpecGenerator
    e=execution
    return e.orch.solve(e.spec,e.generator,deploy=True,acceptance='workbench',require_online=True,
        device_model=e.app.model,scene_generator=SceneSpecGenerator())


def test_failed_simulation_repairs_and_only_verified_iteration_is_delivered(execution):
    e=execution;e.outcomes[:]=[False,True]
    result=solve(e)
    assert result['status']=='final' and result['iter']==2
    assert len(e.calls)==len(e.motions)==2
    assert 'vertex missing' in e.generator.feedback[0]
    assert validation.validation_result(e.directory)['status']=='passed'
    xml=e.directory/'final/plcopen.xml'
    xml.write_text(xml.read_text(encoding='utf-8')+'\n',encoding='utf-8')
    assert validation.validation_result(e.directory)['status']=='failed'


def test_physical_proof_is_bound_to_samples_and_frozen_requirements(execution):
    e=execution
    assert solve(e)['status']=='final'
    path=e.directory/'final/physical_trace.json'
    original=path.read_text(encoding='utf-8');path.write_text(original.replace('50','49'),encoding='utf-8')
    assert validation.validation_result(e.directory)['status']=='failed'
    path.write_text(original,encoding='utf-8')
    path=e.directory/'requirements/requirement_spec.json'
    spec=json.loads(path.read_text(encoding='utf-8'));spec['task_goal']+=' changed'
    path.write_text(json.dumps(spec),encoding='utf-8')
    assert validation.validation_result(e.directory)['status']=='failed'


def test_compile_failure_is_repaired_before_starting_simulation(execution):
    e=execution;e.runtime.compile_failures=1
    result=solve(e)
    assert result['status']=='final' and len(e.motions)==1 and len(e.calls)==2
    assert 'matiec error' in e.generator.feedback[0]


@pytest.mark.parametrize('reason',['offline','mismatch','failed'])
def test_environment_or_unresolved_failure_never_freezes_final(execution,reason):
    e=execution;e.runtime.offline=reason=='offline';e.runtime.mismatch=reason=='mismatch'
    if reason=='failed':e.outcomes[:]=[False,False]
    result=solve(e)
    assert result['status']!='final' and not (e.directory/'final').exists()
    if reason!='failed':assert not e.generator.feedback


def test_finalize_without_matching_physical_report_is_rejected(execution):
    e=execution
    with pytest.raises(ValueError,match='物理验收'):
        e.orch._finalize(e.directory,e.directory/'iter_001',1,[])


def test_generator_catches_frozen_axis_errors_before_losing_repair_context():
    from agent.aml_parser import parse_aml
    root=Path(__file__).resolve().parents[1]
    model,errors=parse_aml(root/'examples/aml/motion3axis_station.aml')
    assert not errors
    spec=json.loads((root/'examples/specs/motion3axis.spec.json').read_text(encoding='utf-8'))
    xml=(root/'src/plc/motion3axis.xml').read_text(encoding='utf-8')
    import re
    xml=re.sub(r'(go_x\([\s\S]*?Velocity\s*:=\s*)[\d.]+',r'\g<1>39.0',xml,count=1)
    generator=validation.VerifiedPLCGenerator(None,no_cards=True,device_model=model)
    ok,errors=generator.gate(xml,spec['io_list'])
    assert not ok and any('R8:' in e and 'Velocity' in e for e in errors)
    messages=generator.build_messages(spec)
    assert messages[0]==validation.PLCGenerator(None,no_cards=True).build_messages(spec)[0]


def test_real_argument_to_int_conversion_is_rejected_before_remote_compile():
    import xml.etree.ElementTree as ET
    root=Path(__file__).resolve().parents[1];ns='{http://www.plcopen.org/xml/tc6_0201}'
    spec=json.loads((root/'examples/specs/motion3axis.spec.json').read_text(encoding='utf-8'))
    xml=ET.parse(root/'src/plc/motion3axis.xml').getroot()
    typ=xml.find('.//'+ns+'pou[@name="DRIVE402"]//'+ns+'variable[@name="sp_prev"]/'+ns+'type')
    typ.clear();ET.SubElement(typ,ns+'REAL')
    generator=validation.VerifiedPLCGenerator(None,no_cards=True)
    ok,errors=generator.gate(ET.tostring(xml,encoding='unicode'),spec['io_list'])
    assert not ok and any('[type]' in error and 'sp_prev' in error for error in errors)


def test_compile_feedback_contains_exact_source_lines(tmp_path):
    (tmp_path/'plc.st').write_text('one\ntwo\ninvalid_expression;\nfour',encoding='utf-8')
    result=validation.WorkbenchOrchestrator._compile_context({'errors':['./st_files/42.st:3-1: type mismatch']},tmp_path)
    assert '3: invalid_expression;' in result['errors'][1]


def test_regressed_physical_candidate_does_not_replace_better_repair_source(execution):
    e=execution
    e.outcomes[:]=[[{'id':'PATH','pass':True,'evidence':'reached'},
                   {'id':'GEOMETRY','pass':False,'evidence':'corner error'}],
                  [{'id':'PATH','pass':False,'evidence':'path lost'},
                   {'id':'GEOMETRY','pass':False,'evidence':'corner error'}]]
    assert solve(e)['status']!='final'
    assert e.orch.best_physical[0]==1
    assert e.generator.preferred_candidate[0]==(e.directory/'iter_001/plcopen.xml').read_bytes().decode('utf-8')
    assert 'corner error' in e.generator.preferred_candidate[1]


def test_retry_selects_better_physical_candidate_across_failed_attempts(tmp_path):
    ancestor=tmp_path/('svg_web_'+'a'*32);ancestor.mkdir()
    latest=tmp_path/('svg_web_'+'b'*32);latest.mkdir()
    for directory,index,passes in ((ancestor,1,3),(ancestor,2,1),(latest,1,1)):
        iteration=directory/('iter_%03d'%index);iteration.mkdir()
        (iteration/'plcopen.xml').write_text('<project candidate="%d"/>'%passes,encoding='utf-8')
        (iteration/'gate.json').write_text('{"gate":"acceptance","errors":["physical failure"]}',encoding='utf-8')
        (iteration/'verdict.json').write_text(json.dumps({'details':[{'pass':True}]*passes}),encoding='utf-8')
    (latest/'repair-source.json').write_text(json.dumps({'task':ancestor.name}),encoding='utf-8')
    xml,feedback,_=validation.failed_candidate(latest)
    assert 'candidate="3"' in xml and 'physical failure' in feedback


def test_passed_geometry_focuses_repair_on_physical_waypoint_timing():
    report={'details':[{'id':'SVG_GEOMETRY','pass':True},{'id':'PATH','pass':False}]}
    hint=validation.physical_timing_hint(report)
    assert '未到位时归零' in hint and '保持已通过的插补器' in hint
    assert '边运动边计数' in hint and '至少150ms' in hint
    report['details'][0]['pass']=False
    assert not validation.physical_timing_hint(report)


def test_repeated_candidate_is_stopped_before_second_deploy(execution,monkeypatch):
    e=execution;e.outcomes[:]=[False,True]
    xml=e.generator.generate()['xml']
    monkeypatch.setattr(e.generator,'repair',lambda *args,**kwargs:{'ok':True,'xml':xml+'\n'})
    result=solve(e)
    assert result['stop_reason']=='no_progress' and len(e.calls)==len(e.motions)==1
    assert not (e.directory/'final').exists()


@pytest.mark.parametrize('recover',[True,False])
def test_modbus_timeout_retries_same_source_once_without_model(execution,monkeypatch,recover):
    e=execution
    original=validation.Motion.start
    starts=[]
    def start(motion):
        starts.append(1)
        original(motion)
    monkeypatch.setattr(validation.Motion,'start',start)
    monkeypatch.setattr(validation.Motion,'status',lambda self:{'status':'failed' if len(starts)==1 or not recover else 'completed',
        'error':'Modbus 请求超时' if len(starts)==1 or not recover else None})
    e.outcomes[:]=[False,recover]
    result=solve(e)
    assert len(e.calls)==1 and len(e.motions)==2 and not e.generator.feedback
    assert result['status']==('final' if recover else 'best_effort')
    if not recover:assert result['stop_reason']=='env_down'
    iteration=e.directory/'iter_001'
    assert (iteration/'acceptance-attempts/attempt_001/verdict.json').is_file()
    assert len(json.loads((iteration/'acceptance-attempts.json').read_text(encoding='utf-8')))==2


def test_source_identity_failure_is_not_retried(execution):
    e=execution;e.runtime.mismatch=True
    result=solve(e)
    assert result['stop_reason']=='env_down' and not e.motions
    assert len(json.loads((e.directory/'iter_001/acceptance-attempts.json').read_text(encoding='utf-8')))==1


def test_unchanged_retry_source_is_not_deployed_again(execution,monkeypatch):
    e=execution
    e.generator.initial_candidate=e.generator.generate()['xml']
    result=solve(e)
    assert result['stop_reason']=='no_progress' and not e.calls and not e.motions


def test_static_retry_recovers_original_errors_and_knowledge(tmp_path):
    generation=tmp_path/'iter_001/generation';generation.mkdir(parents=True)
    (generation/'call_001.response.json').write_text(json.dumps({'choices':[{
        'finish_reason':'stop','message':{'content':'<project></project>'}}]}),encoding='utf-8')
    (generation/'call_001.feedback.json').write_text(json.dumps({
        'errors':['cannot AND'],'advice':'P03: declare cw as WORD'}),encoding='utf-8')
    _,feedback,_=validation.failed_candidate(tmp_path)
    assert 'cannot AND' in feedback and 'declare cw as WORD' in feedback


def test_environment_resume_revalidates_exact_source_without_model(monkeypatch):
    generator=validation.VerifiedPLCGenerator(object(),no_cards=True,
        initial_candidate='<project></project>',resume_validation=True)
    monkeypatch.setattr(generator,'gate',lambda *args:(True,[]))
    monkeypatch.setattr(generator,'_call',lambda *args,**kwargs:pytest.fail('resume spent a model call'))
    result=generator.generate({'io_list':[]},trajectory={'shape':'svg'})
    assert result['xml']=='<project></project>' and result['rounds']==0 and result['mode']=='resume'


def test_environment_resume_can_reach_final_with_fresh_proof(execution):
    e=execution
    e.generator.initial_candidate=e.generator.generate()['xml']
    e.generator.resume_validation=True
    assert solve(e)['status']=='final' and len(e.calls)==len(e.motions)==1


def test_old_pymodbus_no_response_report_can_resume_without_rewriting(tmp_path):
    (tmp_path/'gate.json').write_text('{"gate":"acceptance"}',encoding='utf-8')
    error='Modbus Error: [Input/Output] No response received after 0 retries, continue with next request'
    (tmp_path/'verdict.json').write_text(json.dumps({'error':error}),encoding='utf-8')
    assert validation.resumable_environment_failure(tmp_path/'plcopen.xml')
    assert validation.WorkbenchOrchestrator._environment_error(error)
