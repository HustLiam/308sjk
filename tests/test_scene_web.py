"""Frontend scene lifecycle: actual MuJoCo fixtures, isolated synthetic approvals."""
import base64
import copy
from pathlib import Path
import sys
import threading
import time

import pytest
import requests

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'src'))
pytest.importorskip('mujoco')
from agent import web
from agent.scene_agent.web import SceneWorkbench, SceneWebError, DEFAULT_REQUEST
from agent.scene_agent.common import read_json,write_json
from agent.scene_agent.device_ir import apply_patch, facts_digest


def wait(manager,ident):
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        result=manager.state(ident)
        if not result['busy']:return result
        time.sleep(.02)
    raise AssertionError('Scene job did not finish')


class FixtureAuthor:
    """Offline test recipes injected at the author boundary; never a web mode."""
    def draft_rules(self,ir,request,missing,evidence):
        from examples.scene_agent.reference_candidates import make_reference_candidates
        return [r for r in make_reference_candidates(ir) if r['component_type'] in missing]
    def repair_candidate(self,ir,request,rule,reports,evidence):return rule


def fixture_manager(root):return SceneWorkbench(root,author_factory=lambda control:FixtureAuthor())


def fixture_input(**extra):
    return {'aml':{'name':'factoryforge_sorting_reference.aml','data':base64.b64encode(
        (REPO/'examples/aml/factoryforge_sorting_reference.aml').read_bytes()).decode()},
        'method':'agent','request':DEFAULT_REQUEST,**extra}


@pytest.fixture(scope='module')
def scene(tmp_path_factory):
    manager=fixture_manager(tmp_path_factory.mktemp('scene_frontend_test_only'))
    result=manager.create(fixture_input())
    state=wait(manager,result['id'])
    assert state['error'] is None,state['error']
    yield manager,state['id']
    manager.close()


def test_reference_page_uses_new_scene_and_retains_aml_io(scene):
    manager,ident=scene;state=manager.state(ident)
    assert state['scene_ready'] and state['method']=='agent'
    assert len(state['device_ir']['components'])==18 and len(state['device_ir']['io'])==19
    assert len(state['candidates'])==14 and all(c['validation_status']=='passed' for c in state['candidates'])
    assert state['reports']['behavior']['status']=='passed'
    assert len(state['reports']['behavior']['checks'])==20
    assert not state['all_approved'] and not state['model_frozen'] and state['plc_acceptance']=='not_run'


def test_generated_modules_are_selectable_with_independent_replays(scene):
    manager,ident=scene;state=manager.state(ident)
    assert len(state['test_modules'])==20
    assert all(m['available'] and m['status']=='passed' for m in state['test_modules'])
    selected=next(m for m in state['test_modules'] if m['kind']=='mechanical_joint')
    plan_before=manager.file(ident,'simulation_tests.json').read_bytes()
    with pytest.raises(SceneWebError):manager.run_tests({'task':ident,'revision':-1,'test_ids':[selected['id']]})
    with pytest.raises(SceneWebError):manager.run_tests({'task':ident,'revision':state['revision'],'test_ids':['unknown_module']})
    started=manager.run_tests({'task':ident,'revision':state['revision'],'test_ids':[selected['id']]})
    result=wait(manager,started['id']);assert result['error'] is None
    report=read_json(manager.file(ident,'candidate_scene/selected_tests/simulation_test_results.json'))
    assert report['status']=='passed' and not report['complete'] and len(report['modules'])==1
    chosen=next(m for m in result['test_modules'] if m['id']==selected['id'])
    assert chosen['replay'].startswith('selected_tests/')
    replay=read_json(manager.file(ident,'candidate_scene/'+chosen['replay']))
    assert replay['test_id']==chosen['id'] and replay['device_ir_sha256']==result['device_ir_sha256']
    assert manager.file(ident,'simulation_tests.json').read_bytes()==plan_before
    assert not result['model_frozen'] and result['plc_acceptance']=='not_run'


def test_new_aml_replacement_never_reuses_previous_test_plan(scene):
    manager,ident=scene;original=manager.state(ident)
    replacement=(REPO/'examples/aml/factoryforge_sorting_reference.aml').read_text(encoding='utf-8').replace(
        '<Value>0.002</Value></Attribute>','<Value>0.003</Value></Attribute>',1)
    state=wait(manager,manager.upload({'aml':{'name':'same-name.aml','data':base64.b64encode(replacement.encode()).decode()}})['id'])
    assert state['source_sha256']!=original['source_sha256']
    assert state['test_plan']['device_ir_sha256']!=original['test_plan']['device_ir_sha256']
    assert state['test_modules'] and all(m['status']=='not_run' and not m['available'] for m in state['test_modules'])
    assert not state['scene_ready'] and not state['has_replay']
    assert manager.state(ident)['source_sha256']==original['source_sha256']


@pytest.mark.parametrize('broken_geometry',[False,True])
def test_unresolved_behavior_can_preview_only_valid_geometry(tmp_path,monkeypatch,broken_geometry):
    from examples.scene_agent import reference_candidates
    original=reference_candidates.make_reference_candidates
    def candidates(ir):
        rules=original(ir)
        kind='KinematicBody' if broken_geometry else 'SingleCommandPusher'
        rule=next(r for r in rules if r['component_type']==kind)
        rule['unresolved']=['Unsupported revolute servo example; behavior is not approved']
        if broken_geometry:rule['model']['geoms'][0]['size'][0]=-1
        return rules
    monkeypatch.setattr(reference_candidates,'make_reference_candidates',candidates)
    manager=fixture_manager(tmp_path/'isolated_geometry_preview')
    try:
        state=wait(manager,manager.create(fixture_input(request='Candidate preview only'))['id'])
        assert state['error'] is None,state['error']
        assert not state['all_approved'] and not state['model_frozen'] and state['plc_acceptance']=='not_run'
        assert state['scene_ready'] is not broken_geometry
        if broken_geometry:
            assert not state['can_build']
            return
        assert state['geometry_only'] and state['can_build'] and not state['has_replay']
        assert 'behavior' not in state['reports']
        checks=state['reports']['assembly']['checks']
        assert not any(c['name']=='physics_smoke' for c in checks)
        assert next(c for c in checks if c['name']=='static_geometry_preview')['simulation_steps']==0
        assert not any(c['name']=='io_coverage' for c in checks)
        ir=read_json(manager.directory(state['id'])/'device_ir.json')
        meta=read_json(manager.file(state['id'],'candidate_scene/scene.physical.json'))
        assert meta['io']==ir['io'] and meta['device_ir_sha256']==ir['facts_sha256']
        assert all(p['binding_status']=='declaration_only_not_executable' for p in meta['io_map'])
        candidate=next(c for c in state['candidates'] if c['type']=='SingleCommandPusher')
        assert candidate['geometry_ready'] and not candidate['can_approve'] and candidate['validation_status']=='failed'
        assert manager.geometry(state['id'])['candidate'] is True
        with pytest.raises(SceneWebError,match='候选几何预览'):manager.operate(state['id'],state['revision'],'check')
        with pytest.raises(SceneWebError,match='逐项审批'):manager.operate(state['id'],state['revision'],'freeze')
        with pytest.raises(ValueError):manager.approve({'task':state['id'],'revision':state['revision'],
            'component_type':candidate['type'],'review_sha256':candidate['review_sha256'],'confirmed':True,
            'reviewer':'isolated_test_only','note':'This unresolved candidate must not be approved'})
        assert not list((manager.library.root/'approved').glob('**/approval.json'))
    finally:manager.close()


def test_scene_preview_exports_actual_geometry_and_matching_replay(scene):
    manager,ident=scene;state=manager.state(ident);geometry=manager.geometry(ident)
    assert geometry['source']=='mujoco_compiled_device_model'
    assert geometry['candidate'] is True and len(geometry['geoms'])==len(geometry['transforms'])
    assert len(geometry['mechanism']['joints'])==1 and len(geometry['mechanism']['frames'])>=2
    assert len(geometry['body_transforms'])>len(geometry['mechanism']['joints'])
    components=state['device_ir']['components'];ids={c['id'] for c in components}
    physical_ids={g['component_id'] for g in geometry['geoms'] if g['component_id']}
    assert physical_ids<=ids
    assert all(g['component_id'] is None for g in geometry['geoms'] if g['type']==0)
    assert all(g['component_id'] in ids for g in geometry['geoms'] if g['type']!=0)
    assert {c['id'] for c in components if c['type'] in ('BeltConveyor','RigidCarton','FixedBody','KinematicBody')}<=physical_ids
    replay=read_json(manager.file(ident,'candidate_scene/behavior_replay.json'))
    assert replay['model_xml_sha256']==state['model_xml_sha256']
    assert len(replay['frames'])>200 and replay['source']=='independent_mujoco_model_fixture'
    assert all(len(f['transforms'])==len(geometry['geoms']) for f in replay['frames'])
    counters=[f['signals']['counter_short']+f['signals']['counter_tall'] for f in replay['frames']]
    assert min(counters)==0 and max(counters)==2
    assert replay['frames'][0]['transforms']!=replay['frames'][-1]['transforms']


def test_model_bundle_contains_scene_facts_and_checks(scene):
    import io,zipfile
    manager,ident=scene
    with zipfile.ZipFile(io.BytesIO(manager.bundle(ident))) as archive:
        assert {'input.aml','device_ir.json','candidate_scene/scene.xml','candidate_scene/behavior_checks.json','README.txt'}.issubset(archive.namelist())
        assert not any('..' in Path(name).parts for name in archive.namelist())


def test_stale_revision_and_unapproved_freeze_are_rejected(scene):
    manager,ident=scene;state=manager.state(ident)
    with pytest.raises(SceneWebError,match='版本已更新'):manager.operate(ident,-1,'build')
    with pytest.raises(SceneWebError,match='逐项审批'):manager.operate(ident,state['revision'],'freeze')
    assert not manager.state(ident)['model_frozen']


@pytest.mark.parametrize('relative',['../web.json','../../component_library/rule.json','web.json','model_calls/round_001/request.json','/Windows/win.ini'])
def test_scene_artifacts_cannot_escape_download_allowlist(scene,relative):
    manager,ident=scene
    with pytest.raises(SceneWebError):manager.file(ident,relative)


def test_bad_upload_does_not_create_history(tmp_path):
    manager=SceneWorkbench(tmp_path);before=manager.listing()['tasks']
    try:
        with pytest.raises(ValueError):manager.create({'request':'device','method':'agent','aml':{'name':'bad.aml','data':base64.b64encode(b'not AML').decode()}})
        assert manager.listing()['tasks']==before
    finally:manager.close()


def test_approval_requires_explicit_confirmation_and_current_token(scene):
    manager,ident=scene;state=manager.state(ident);c=state['candidates'][0]
    body={'task':ident,'revision':state['revision'],'component_type':c['type'],'review_sha256':c['review_sha256'],
          'reviewer':'test_fixture_only','note':'Synthetic test record, never real approval'}
    with pytest.raises(SceneWebError,match='核对并确认'):manager.approve(body)
    with pytest.raises(SceneWebError,match='版本已过期'):manager.approve({**body,'confirmed':True,'review_sha256':'stale'})
    assert not list((manager.library.root/'approved').glob('**/approval.json'))


def test_numeric_local_edit_keeps_parent_and_unaffected_components(scene):
    manager,ident=scene;original=manager.state(ident);ir=original['device_ir']
    c=next(c for c in ir['components'] if c['type']=='OperatorPanel')
    pose=copy.deepcopy(c['pose']);pose['position_m'][0]+=.02
    patch={'base_sha256':facts_digest(ir),'editable_ids':[c['id']],'reason':'Move selected panel',
        'operations':[{'op':'set_pose','component_id':c['id'],**pose}]}
    started=manager.edit({'task':ident,'revision':original['revision'],'request':'Move selected panel',
                         'editable_ids':[c['id']],'patch':patch})
    changed=wait(manager,started['id']);assert changed['error'] is None,changed['error']
    assert changed['parent']==ident and changed['device_ir']['io']==ir['io']
    assert [x for x in changed['device_ir']['components'] if x['id']!=c['id']]==[x for x in ir['components'] if x['id']!=c['id']]
    assert manager.state(ident)['device_ir']==ir
    assert changed['reports']['behavior']['status']=='passed'
    assert [x['rule_sha256'] for x in changed['candidates']]==[x['rule_sha256'] for x in original['candidates']]
    assert not changed['model_frozen']
    regenerated=manager.create({'source_task':changed['id'],'method':'agent','request':'Reuse the confirmed device scene'})
    regenerated=wait(manager,regenerated['id'])
    assert regenerated['error'] is None,regenerated['error']
    assert regenerated['device_ir']['components']==changed['device_ir']['components']
    assert regenerated['parent']==changed['id']


def test_scene_worker_cancellation_and_restart_records_interruption(tmp_path,monkeypatch):
    manager=SceneWorkbench(tmp_path);entered=threading.Event();release=threading.Event()
    def blocked(*args):entered.set();release.wait(5);manager.control.check()
    monkeypatch.setattr(manager,'_generate',blocked)
    try:
        state=manager.create(fixture_input(request='test only'))
        assert entered.wait(2)
        with pytest.raises(SceneWebError,match='另一个'):manager.create(fixture_input(request='test only'))
        stopped=manager.stop(state['id']);assert stopped['phase']=='stopping'
        release.set();state=wait(manager,state['id']);assert state['phase']=='cancelled' and not state['model_frozen']
    finally:release.set();manager.close()
    record=read_json(manager.directory(state['id'])/'web.json');record['busy']=True;write_json(manager.directory(state['id'])/'web.json',record)
    reopened=SceneWorkbench(tmp_path)
    try:assert reopened.state(state['id'])['phase']=='interrupted' and not reopened.state(state['id'])['busy']
    finally:reopened.close()


def test_frontend_routes_and_csrf(tmp_path,monkeypatch):
    monkeypatch.setattr(web,'get_api_key',lambda:'')
    app=web.SVGWorkbench(tmp_path/'app');server=web.make_server(app,0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    client=requests.Session();client.trust_env=False;url='http://127.0.0.1:'+str(server.server_port)
    try:
        config=client.get(url+'/api/config').json()
        page=client.get(url+'/');assert 'id="workflow-panel-3"' in page.text and 'device_scene.js' in page.text
        for path in ('/device_scene.js','/device_scene_i18n.js','/device_scene.css','/device_tree.js','/api/device-scenes'):
            assert client.get(url+path).status_code==200
        assert client.post(url+'/api/device-scene/create',json={'example':True}).status_code==403
        response=client.post(url+'/api/device-scene/create',headers={'X-Agent-Token':config['token']},json={})
        assert response.status_code==400
        assert client.get(url+'/device-scene-file?task=../&path=input.aml').status_code==404
        assert app._device_scenes.listing()['tasks']==[]
    finally:client.close();server.shutdown();server.server_close();thread.join(2);app.close()


def test_human_approval_adapter_can_freeze_only_after_all_components_pass(tmp_path):
    manager=fixture_manager(tmp_path/'synthetic_review_test_only')
    try:
        started=manager.create(fixture_input())
        state=wait(manager,started['id']);assert state['error'] is None
        for candidate in list(state['candidates']):
            state=manager.approve({'task':state['id'],'revision':state['revision'],'component_type':candidate['type'],
                'review_sha256':candidate['review_sha256'],'confirmed':True,'reviewer':'automated_test_fixture_only',
                'note':'Synthetic unit-test review, confined to disposable pytest library'})
        assert state['all_approved'] and not state['model_frozen']
        manager.operate(state['id'],state['revision'],'freeze');state=wait(manager,state['id'])
        assert state['error'] is None,state['error']
        assert state['model_frozen'] and state['scene_kind']=='scene'
        assert state['plc_acceptance']=='not_run'
        assert read_json(manager.file(state['id'],'scene/model.freeze.json'))['status']=='model_frozen'
        frozen_id=state['id'];frozen_ir=copy.deepcopy(state['device_ir']);frozen_xml=manager.file(frozen_id,'scene/scene.xml').read_bytes()
        cid=state['device_ir']['components'][0]['id']
        edit={'base_sha256':state['device_ir']['facts_sha256'],'editable_ids':[cid],
              'reason':'Define a local mount without altering reviewed rule code',
              'operations':[{'op':'add_interface','component_id':cid,'interface_id':'reviewed_mount',
                             'name':'reviewed_mount','position_m':[.1,.2,.3],'rpy_rad':[0,0,0]}]}
        child=manager.edit({'task':frozen_id,'revision':state['revision'],'editable_ids':[cid],'request':edit['reason'],'patch':edit})
        child=wait(manager,child['id']);assert child['error'] is None,child['error']
        assert child['parent']==frozen_id and child['all_approved'] and not child['model_frozen']
        assert child['device_ir']['io']==frozen_ir['io'] and child['reports']['behavior']['status']=='passed'
        assert manager.state(frozen_id)['model_frozen'] and manager.state(frozen_id)['device_ir']==frozen_ir
        assert manager.file(frozen_id,'scene/scene.xml').read_bytes()==frozen_xml
    finally:manager.close()


def test_blank_approval_note_still_requires_human_confirmation(tmp_path):
    from agent.scene_agent.web import DEFAULT_APPROVAL_NOTE
    manager=fixture_manager(tmp_path/'optional_note_test_only')
    try:
        state=wait(manager,manager.create(fixture_input())['id'])
        candidate=state['candidates'][0]
        data={'task':state['id'],'revision':state['revision'],'component_type':candidate['type'],
            'review_sha256':candidate['review_sha256'],'reviewer':'synthetic_test_reviewer','note':''}
        with pytest.raises(SceneWebError,match='核对并确认'):manager.approve(data)
        assert not list((manager.library.root/'approved').rglob('approval.json'))
        state=manager.approve({**data,'confirmed':True})
        approval=next((manager.library.root/'approved'/candidate['type']).rglob('approval.json'))
        record=read_json(approval)
        assert record['reviewer']=='synthetic_test_reviewer' and record['note']==DEFAULT_APPROVAL_NOTE
        assert state['model_requirements']['purpose']=='面向 PLC 验证的可执行设备模型'
        assert not state['model_frozen'] and state['plc_acceptance']=='not_run'
    finally:manager.close()


def test_misaligned_interface_revision_fails_without_changing_parent(scene):
    manager,ident=scene;original=manager.state(ident);joint=original['device_ir']['joints'][0]
    frame=next(f for f in original['device_ir']['interfaces'] if f['type']=='JointAttachment' and f['component_id']==joint['parent_body'])
    scope=[joint['id'],joint['parent_body'],joint['child_body']]
    patch={'base_sha256':original['device_ir']['facts_sha256'],'editable_ids':scope,'reason':'Move one anchor while keeping the other fixed',
           'operations':[{'op':'set_interface_pose','component_id':frame['component_id'],'interface_id':frame['id'],'position_m':[0,0,.25]}]}
    child=manager.edit({'task':ident,'revision':original['revision'],'editable_ids':scope,'request':patch['reason'],'patch':patch})
    child=wait(manager,child['id'])
    assert child['error']['layer']=='assembly' and 'align' in child['error']['message']
    assert not child['model_frozen'] and manager.state(ident)['device_ir']==original['device_ir']
    assert child['device_ir']['io']==original['device_ir']['io']


def test_mesh_preview_uses_compiled_obj_triangles(tmp_path):
    from runtime.device_scene import build_device_scene
    from agent.scene_agent.device_ir import parse_device_ir
    manager=SceneWorkbench(tmp_path/'mesh_preview_test_only')
    try:
        ir=parse_device_ir(REPO/'examples/aml/factoryforge_sorting_reference.aml')
        component=copy.deepcopy(ir['components'][0]);component.update(id='mesh_fixture',type='MeshFixture',parent_id=None,parameters={},interfaces=[])
        component['pose']={'position_m':[0,0,.3],'rpy_rad':[0,0,0]}
        ir.update(components=[component],interfaces=[],connections=[],assemblies=[],joints=[],io=[],pending=[])
        rule={'format':'308sjk.component_rule/0.1-draft','component_type':'MeshFixture','version':'0.1.0',
            'parameter_bounds':{},'assumptions':['static mesh for preview test'],'unresolved':[],
            'model':{'behavior':'none','bodies':[{'name':'part','parent':'root'}],
                     'blender_bodies':[{'name':'visual','body':'part','type':'mesh','mesh':'geometry/root.obj','collision':False}]}}
        candidate=manager.library.stage(rule)
        (candidate/'geometry').mkdir();(candidate/'geometry/root.obj').write_text('v 0 0 0\nv .3 0 0\nv 0 .2 0\nv 0 0 .1\nf 1 2 3\nf 1 4 2\nf 1 3 4\nf 2 4 3\n')
        ident='b'*32;folder=manager.directory(ident);folder.mkdir(parents=True)
        write_json(folder/'web.json',{'id':ident,'scene_kind':'candidate_scene'})
        build_device_scene(ir,manager.library,folder/'candidate_scene',candidates={'MeshFixture':{'rule':rule,'directory':str(candidate)}})
        geometry=manager.geometry(ident);mesh=next(g for g in geometry['geoms'] if g['type']==7)
        assert len(mesh['vertices'])==4*9 and geometry['source']=='mujoco_compiled_device_model'
        assert mesh['component_id']=='mesh_fixture'
        assert len(geometry['geoms'])==len(geometry['transforms'])
    finally:manager.close()
