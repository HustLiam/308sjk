"""Human approval survives read/recheck; changed evidence cannot inherit it."""
import copy
import hashlib
from pathlib import Path

import pytest

from src.agent.scene_agent.approval_state import existing_approval,candidate_version,TaskApprovedLibrary
from src.agent.scene_agent.common import SceneError,digest,read_json,write_json
from src.agent.scene_agent.inventory import component_inventory
from src.agent.scene_agent.rules import ApprovalRequired,ComponentLibrary
from src.agent.scene_agent.device_ir import parse_device_ir
from src.agent.scene_agent.web import SceneWorkbench
from test_scene_source import FixtureAuthor,source,rule,wait


def files(root):
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


def reviewed(manager,ident):
    state=manager.state(ident);candidate=state['candidates'][0]
    return manager.approve({'task':ident,'revision':state['revision'],'component_type':candidate['type'],
        'confirmed':True,'reviewer':'isolated_test_only','review_sha256':candidate['review_sha256']})


def test_approval_survives_recheck_refresh_restart_and_duplicate_submit(tmp_path):
    author=FixtureAuthor();manager=SceneWorkbench(tmp_path,author_factory=lambda control:author)
    try:
        state=wait(manager,manager.create({'aml':source(),'request':'fixture'})['id']);ident=state['id']
        approved=reviewed(manager,ident);token=approved['candidates'][0]['review_sha256']
        snapshots=files(manager.library.root/'approved')
        for _ in range(2):
            current=manager.state(ident)
            state=wait(manager,manager.operate(ident,current['revision'],'revalidate')['id'])
            assert state['error'] is None and state['candidates'][0]['approved'] and state['all_approved']
            assert state['candidates'][0]['review_sha256']==token
        assert files(manager.library.root/'approved')==snapshots
        again=reviewed(manager,ident)
        assert again['all_approved'] and files(manager.library.root/'approved')==snapshots
    finally:manager.close()
    reloaded=SceneWorkbench(tmp_path)
    try:
        assert reloaded.state(ident)['all_approved']
        assert reloaded.state(ident)['candidates'][0]['review_sha256']==token
    finally:reloaded.close()


def test_legacy_report_wording_drift_recovers_existing_approval_only(tmp_path):
    manager=SceneWorkbench(tmp_path,author_factory=lambda control:FixtureAuthor())
    try:
        state=wait(manager,manager.create({'aml':source(),'request':'fixture'})['id']);ident=state['id']
        original=read_json(manager.directory(ident)/'task.json')['candidates'][0]
        approved=reviewed(manager,ident);token=approved['candidates'][0]['review_sha256']
        snapshots=files(manager.library.root/'approved')
        path=Path(original['directory']);report=read_json(path/'validation.json')
        report.pop('model_requirements',None);report['scope']='component geometry/internal-joint smoke only'
        changed=manager.library.submit_validation(path,report)
        assert changed!=token
        task=read_json(manager.directory(ident)/'task.json');task['candidates'][0]['review_sha256']=changed
        write_json(manager.directory(ident)/'task.json',task)
        restored=manager.state(ident)['candidates'][0]
        assert restored['approved'] and restored['review_sha256']==token
        assert restored['directory']!=str(path) and files(manager.library.root/'approved')==snapshots
        # A real failed check cannot be hidden by the old approval.
        report['checks'][0]['passed']=False;report['status']='failed'
        manager.library.submit_validation(path,report)
        assert existing_approval(manager.library,path) is None
        assert not manager.state(ident)['candidates'][0]['approved']
    finally:manager.close()


def test_approved_recheck_failure_blocks_freeze_without_rewriting_approval(tmp_path,monkeypatch):
    manager=SceneWorkbench(tmp_path,author_factory=lambda control:FixtureAuthor())
    try:
        state=wait(manager,manager.create({'aml':source(),'request':'fixture'})['id']);ident=state['id']
        approved=reviewed(manager,ident);snapshots=files(manager.library.root/'approved')
        def fail(*args):raise ValueError('actual geometry failure')
        from src.agent.scene_agent.author import SceneGenerationAgent
        monkeypatch.setattr(SceneGenerationAgent,'_validate_candidate',staticmethod(fail))
        state=wait(manager,manager.operate(ident,approved['revision'],'revalidate')['id'])
        assert state['candidates'][0]['approved'] and not state['candidates'][0]['geometry_ready']
        assert not state['can_build'] and files(manager.library.root/'approved')==snapshots
        with pytest.raises(ValueError,match='当前组件检查未通过'):
            manager.operate(ident,state['revision'],'freeze')
    finally:manager.close()


def test_candidate_version_collision_allocates_new_version_without_changing_physics(tmp_path):
    library=ComponentLibrary(tmp_path);original=rule();path=library.stage(original)
    token=library.submit_validation(path,{'status':'passed','checks':[{'name':'synthetic','passed':True}]})
    library.approve(path,reviewer='isolated_test_only',expected_review_sha256=token,note='unit fixture only')
    snapshot=files(library.root/'approved')
    identical,allocation=candidate_version(library,original)
    assert identical==original and allocation is None
    changed=copy.deepcopy(original);changed['model']['geoms'][0]['size'][1]=.2
    reserved=copy.deepcopy(changed);reserved['version']='0.1.2';library.stage(reserved)
    allocated,allocation=candidate_version(library,changed)
    assert allocated['version']=='0.1.3' and allocation['from_version']=='0.1.0'
    assert {k:v for k,v in allocated.items() if k!='version'}=={k:v for k,v in changed.items() if k!='version'}
    library.stage(allocated)
    assert files(library.root/'approved')==snapshot and changed['version']=='0.1.0'


def test_force_new_version_uses_only_version_names_not_old_rule_content(tmp_path,monkeypatch):
    from src.agent.scene_agent import approval_state
    library=ComponentLibrary(tmp_path);original=rule();library.stage(original)
    def forbidden(*args,**kwargs):raise AssertionError('Do not read cached rule content for full regeneration')
    monkeypatch.setattr(approval_state,'read_json',forbidden)
    revised,allocation=candidate_version(library,original,force_new=True)
    assert revised['version']=='0.1.1' and revised['model']==original['model']
    assert allocation['reason']=='full regeneration requires a new candidate directory'
    assert original['version']=='0.1.0'


def test_formal_build_pins_task_approval_even_when_newer_rule_exists(tmp_path):
    from src.agent.scene_agent.workflow import build_task
    manager=SceneWorkbench(tmp_path,author_factory=lambda control:FixtureAuthor())
    try:
        state=wait(manager,manager.create({'aml':source(),'request':'fixture'})['id']);ident=state['id']
        approved=reviewed(manager,ident)
        task=read_json(manager.directory(ident)/'task.json');original=task['candidates'][0]
        newer=copy.deepcopy(approved['candidates'][0]['rule']);newer['version']='9.0.0'
        newer['model']['geoms'][0]['size'][0]=.25
        path=manager.library.stage(newer)
        token=manager.library.submit_validation(path,{'status':'passed','checks':[{'name':'synthetic','passed':True}]})
        manager.library.approve(path,reviewer='isolated_test_only',expected_review_sha256=token,note='fixture only')
        kind=newer['component_type']
        assert manager.library.resolve(kind)['rule']['version']=='9.0.0'
        assert TaskApprovedLibrary(manager.library,task).resolve(kind)['rule']['version']!='9.0.0'
        assert manager.state(ident)['all_approved']
        build_task(manager.directory(ident),manager.library)
        meta=read_json(manager.directory(ident)/'scene/scene.physical.json')
        assert {r['rule_sha256'] for r in meta['rules']}=={original['rule_sha256']}
        assert all(r['approval'] is not None for r in meta['rules']) and not meta['candidate']
        # A different approval elsewhere cannot approve a task's pending rule.
        pending=copy.deepcopy(newer);pending['version']='10.0.0'
        staged=manager.library.stage(pending)
        unapproved={'force_regenerate':True,'candidates':[{'type':kind,'directory':str(staged),
            'rule_sha256':digest(pending),'review_sha256':'not-reviewed'}]}
        with pytest.raises(ApprovalRequired):TaskApprovedLibrary(manager.library,unapproved).resolve(kind)
        # The expected rule hash remains authoritative even for approved paths.
        broken=copy.deepcopy(task);broken['candidates'][0]['rule_sha256']='f'*64
        with pytest.raises(ApprovalRequired):TaskApprovedLibrary(manager.library,broken).resolve(kind)
    finally:manager.close()


def test_component_inventory_counts_ownership_leaves_and_excludes_joints():
    ir=parse_device_ir(Path(__file__).resolve().parents[1]/'examples/aml/factoryforge_sorting_reference.aml')
    counts=component_inventory(ir)
    assert counts['leaf']==14 and counts['composite']==3 and counts['total']==17 and counts['joints']==1
    byid={c['id']:c['name'] for c in ir['components']}
    assert not {'sorting_line','pusher','workpieces','pusher_slide'}&{byid[key] for key in counts['leaf_ids']}
    # An endpoint nested under a legacy joint remains a child of its containing assembly.
    nested={'components':[{'id':'assembly'},{'id':'joint','parent_id':'assembly'},
                          {'id':'part','parent_id':'joint'}],'joints':[{'id':'joint'}]}
    assert component_inventory(nested)=={'leaf':1,'composite':1,'total':2,'joints':1,'leaf_ids':['part']}
