"""A new AML is parsed and resolved before generating/assembling its own model."""
import base64
import copy
import hashlib
from pathlib import Path
import sys
import time

import pytest

pytest.importorskip('mujoco')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from src.agent.scene_agent.common import read_json,write_json
from src.agent.scene_agent.rules import ComponentLibrary,RULE_FORMAT
from src.agent.scene_agent.web import SceneWorkbench,SceneWebError


def source(width=.2,kind='NewFixture'):
    text=f'''<CAEXFile SchemaVersion="3.0"><InstanceHierarchy Name="DeviceCell">
    <InternalElement Name="fixture" ID="fixture-1" RefBaseSystemUnitPath="Parts/{kind}">
      <Attribute Name="component_kind" AttributeDataType="xs:string"><Value>{kind}</Value></Attribute>
      <Attribute Name="width_m" Unit="m"><Value>{width}</Value></Attribute>
    </InternalElement></InstanceHierarchy></CAEXFile>'''
    return {'name':'device.aml','data':base64.b64encode(text.encode()).decode()}


def rule(kind='NewFixture'):
    return {'format':RULE_FORMAT,'component_type':kind,'version':'0.1.0',
        'parameter_bounds':{'width_m':[.1,.4]},'assumptions':['测试用静态组件'],'unresolved':[],
        'model':{'behavior':'none','geoms':[{'name':'box','type':'box','size':['=width_m/2',.1,.1]}]}}


class FixtureAuthor:
    def __init__(self):self.calls=[]
    def draft_rules(self,ir,request,missing,evidence):
        self.calls.append(copy.deepcopy(ir))
        rules=[rule(kind) for kind in missing]
        for r in rules:r['parameter_bounds']['width_m']=[.1,.6]
        return rules


def wait(manager,ident):
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        state=manager.state(ident)
        if not state['busy']:return state
        time.sleep(.02)
    raise AssertionError('Scene task did not complete')


def test_new_upload_parses_queries_library_without_model_calls(tmp_path):
    author=FixtureAuthor();manager=SceneWorkbench(tmp_path,author_factory=lambda control:author)
    try:
        upload=source();state=wait(manager,manager.upload({'aml':upload})['id'])
        assert not author.calls and not state['error'] and state['parse_only']
        assert state['source_sha256']==hashlib.sha256(base64.b64decode(upload['data'])).hexdigest()
        assert state['device_ir']['components'][0]['parameters']['width_m']['value']==.2
        assert state['missing_types']==['NewFixture'] and state['component_resolution'][0]['origin']=='author_required'
        assert not state['scene_ready'] and not state['candidates'] and not state['all_approved']
    finally:manager.close()


def test_same_filename_replacement_authors_and_builds_only_new_device(tmp_path):
    author=FixtureAuthor();manager=SceneWorkbench(tmp_path,author_factory=lambda control:author)
    try:
        parsed=wait(manager,manager.upload({'aml':source()})['id'])
        old=wait(manager,manager.create({'source_task':parsed['id'],'request':'生成当前设备'})['id'])
        assert old['scene_ready'] and not old['error'] and len(author.calls)==1
        new=wait(manager,manager.upload({'aml':source(.45,'DifferentFixture')})['id'])
        assert new['source_sha256']!=old['source_sha256'] and not new['scene_ready'] and not new['candidates']
        result=wait(manager,manager.create({'source_task':new['id'],'request':'生成新设备'})['id'])
        assert result['scene_ready'] and not result['error'] and len(author.calls)==2
        assert [c['type'] for c in result['candidates']]==['DifferentFixture']
        assert author.calls[-1]['components'][0]['parameters']['width_m']['value']==.45
        boxes=[g for g in manager.geometry(result['id'])['geoms'] if g['type']==6]
        assert boxes[0]['size'][0]==pytest.approx(.225)
        assert manager.state(old['id'])['source_sha256']==old['source_sha256']
        # No fixture for this new device: a visible model is not a frozen or PLC-validated model.
        assert not result['model_frozen'] and result['plc_acceptance']=='not_run'
    finally:manager.close()


def test_frontend_upload_marks_all_types_for_regeneration_even_when_approved(tmp_path):
    author=FixtureAuthor();manager=SceneWorkbench(tmp_path,author_factory=lambda control:author)
    try:
        candidate=manager.library.stage(rule())
        token=manager.library.submit_validation(candidate,{'status':'passed','checks':[{'name':'synthetic_test_fixture','passed':True}]})
        manager.library.approve(candidate,reviewer='unit_test_only',note='Disposable synthetic fixture',expected_review_sha256=token)
        first=wait(manager,manager.upload({'aml':source(.2)})['id'])
        assert first['missing_types']==['NewFixture'] and first['component_resolution'][0]['origin']=='author_required'
        assert first['generation_policy']=='full_regeneration_no_reuse'
        second=wait(manager,manager.upload({'aml':source(.45)})['id'])
        assert second['missing_types']==['NewFixture'] and second['component_resolution'][0]['origin']=='author_required'
        assert not author.calls
    finally:manager.close()


def test_regeneration_reparses_bytes_instead_of_unedited_cached_ir(tmp_path):
    author=FixtureAuthor();manager=SceneWorkbench(tmp_path,author_factory=lambda control:author)
    try:
        parsed=wait(manager,manager.upload({'aml':source(.2)})['id'])
        path=manager.directory(parsed['id'])/'device_ir.json';stale=read_json(path)
        stale['components'][0]['parameters']['width_m']['value']=.35;write_json(path,stale)
        generated=wait(manager,manager.create({'source_task':parsed['id'],'request':'重新生成'})['id'])
        assert generated['device_ir']['components'][0]['parameters']['width_m']['value']==.2
    finally:manager.close()


def test_new_generation_does_not_reuse_parent_candidates_or_scene(tmp_path):
    author=FixtureAuthor();manager=SceneWorkbench(tmp_path,author_factory=lambda control:author)
    try:
        first=wait(manager,manager.create({'aml':source(),'request':'首次生成'})['id'])
        assert first['scene_ready'] and len(author.calls)==1 and first['candidates']
        second=wait(manager,manager.create({'source_task':first['id'],'request':'从AML重新生成'})['id'])
        assert second['scene_ready'] and second['id']!=first['id'] and len(author.calls)==2
        assert second['generation_policy']=='full_regeneration_no_reuse'
        task=read_json(manager.directory(second['id'])/'task.json')
        assert task['candidate_origin']=='llm'
        assert all(c['origin']=='author_required' for c in task['component_resolution'])
        assert (manager.directory(second['id'])/'candidate_history/NewFixture/round_001.json').is_file()
        assert (manager.directory(second['id'])/'candidate_scene/assembly_checks.json').is_file()
    finally:manager.close()


def test_full_regeneration_never_resolves_old_rules_and_uses_new_asset_directories(tmp_path,monkeypatch):
    class IdenticalAuthor(FixtureAuthor):
        def draft_rules(self,ir,request,missing,evidence):
            self.calls.append(copy.deepcopy(ir));return [rule(kind) for kind in missing]
    author=IdenticalAuthor();manager=SceneWorkbench(tmp_path,author_factory=lambda control:author)
    try:
        old=manager.library.stage(rule());token=manager.library.submit_validation(old,{'status':'passed','checks':[{'name':'synthetic','passed':True}]})
        approved=manager.library.approve(old,reviewer='unit_test_only',expected_review_sha256=token,note='Disposable fixture')
        approved_before={p.relative_to(approved).as_posix():p.read_bytes() for p in approved.rglob('*') if p.is_file()}
        def forbidden(*args,**kwargs):raise AssertionError('Full generation must not resolve cached library rules')
        monkeypatch.setattr(manager.library,'resolve',forbidden)
        parsed=wait(manager,manager.upload({'aml':source()})['id'])
        first=wait(manager,manager.create({'source_task':parsed['id'],'request':'重新生成全部组件'})['id'])
        second=wait(manager,manager.create({'source_task':first['id'],'request':'再次完整生成'})['id'])
        assert first['scene_ready'] and second['scene_ready'] and len(author.calls)==2
        left,right=first['candidates'][0],second['candidates'][0]
        assert left['directory']!=right['directory'] and left['directory']!=str(old)
        assert left['version']!=right['version'] and not left['approved'] and not right['approved']
        from src.agent.scene_agent.verification import apply_physics
        assert left['rule']['model']==right['rule']['model']==apply_physics(rule())['model']
        for state in (first,second):
            task=read_json(manager.directory(state['id'])/'task.json')
            assert task['force_regenerate'] and all(c['origin']=='author_required' for c in task['component_resolution'])
            assert (Path(state['candidates'][0]['directory'])/'preview.png').is_file()
            assert task['candidates'][0]['attempts']==1
        assert approved_before=={p.relative_to(approved).as_posix():p.read_bytes() for p in approved.rglob('*') if p.is_file()}
        # A missing fresh candidate must fail, never fall back to an old rule.
        from src.agent.scene_agent.workflow import candidate_entries
        with pytest.raises(ValueError,match='missing a new candidate'):
            candidate_entries(second['device_ir'],manager.library,{'candidates':[],'force_regenerate':True})
    finally:manager.close()


def test_reference_generation_endpoint_removed(tmp_path):
    manager=SceneWorkbench(tmp_path)
    try:
        with pytest.raises(SceneWebError,match='参考场景入口已移除'):
            manager.create({'aml':source(),'request':'新场景','method':'reference'})
        with pytest.raises(SceneWebError,match='参考场景入口已移除'):
            manager.create({'example':True,'request':'新场景'})
        assert not manager.listing()['tasks']
    finally:manager.close()


def test_upload_cannot_silently_reuse_a_different_source_task(tmp_path):
    manager=SceneWorkbench(tmp_path)
    try:
        parsed=wait(manager,manager.upload({'aml':source()})['id'])
        with pytest.raises(SceneWebError,match='不能同时复用旧任务'):
            manager.create({'source_task':parsed['id'],'aml':source(.45),'request':'更换设备'})
        assert len(manager.listing()['tasks'])==1
    finally:manager.close()
