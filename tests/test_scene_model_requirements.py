"""PLC model scope must retain executable behavior and existing device evidence."""
import copy
from pathlib import Path

import pytest

from src.agent.scene_agent.author import SceneGenerationAgent
from src.agent.scene_agent.common import SceneError,read_json,write_json
from src.agent.scene_agent.device_ir import parse_device_ir
from src.agent.scene_agent.rules import ComponentLibrary
from src.agent.scene_agent.validation import validate_candidate
from src.agent.scene_agent.requirements import MODEL_PROFILE
from examples.scene_agent.reference_candidates import make_reference_candidates
from tools.archive_sorting_scene_cache import archive,plan

AML=Path(__file__).resolve().parents[1]/'examples/aml/factoryforge_sorting_reference.aml'


def test_logical_panel_needs_executable_io_not_a_housing(tmp_path):
    ir=parse_device_ir(AML)
    panel=next(c for c in ir['components'] if c['type']=='OperatorPanel')
    rule=next(r for r in make_reference_candidates(ir) if r['component_type']=='OperatorPanel')
    summary=[SceneGenerationAgent._component_summary(panel,ir)]
    assert summary[0]['io'] and all(p['address'].startswith(('%QX','%QW')) for p in summary[0]['io'])
    assert not rule['model']['geoms'] and not rule['unresolved']
    assert rule['provenance']['validation_profile']==MODEL_PROFILE and rule['provenance']['limitations']
    SceneGenerationAgent._validate_candidate(rule,summary)
    library=ComponentLibrary(tmp_path/'library');candidate=library.stage(rule)
    report,_=validate_candidate(library,candidate,summary[0]['parameters'])
    assert report['status']=='passed' and not report['unresolved']
    broken=copy.deepcopy(rule);broken['model']['behavior']='none'
    with pytest.raises(SceneError,match='尚未实现 AML 信号'):
        SceneGenerationAgent._validate_candidate(broken,summary)
    # An explicit blocker can be retained for a non-executable preview, never
    # silently converted into a successful model by a nonblocking limitation.
    broken['unresolved']=['面板触点运行时未实现，无法验证启停输入。']
    SceneGenerationAgent._validate_candidate(broken,summary)
    failed,_=validate_candidate(library,library.stage(broken),summary[0]['parameters'])
    assert failed['status']=='failed'


@pytest.mark.parametrize('use_role',[True,False])
def test_source_workpieces_require_rigid_pool_behavior(use_role):
    ir=parse_device_ir(AML)
    carton=next(c for c in ir['components'] if c['type']=='RigidCarton')
    rule=next(r for r in make_reference_candidates(ir) if r['component_type']=='RigidCarton')
    summary=SceneGenerationAgent._component_summary(carton,ir)
    assert summary['role']=='Product' and summary['spawned_by']
    if not use_role:summary['role']='Equipment'
    SceneGenerationAgent._validate_candidate(rule,[summary])
    broken=copy.deepcopy(rule);broken['model']['behavior']='none'
    with pytest.raises(SceneError,match='必须使用 model.behavior="rigid"'):
        SceneGenerationAgent._validate_candidate(broken,[summary])
    broken['unresolved']=['工件生成内核尚未实现，不能运行料源/输送/分拣验收。']
    SceneGenerationAgent._validate_candidate(broken,[summary])


def seed_task(root,ident,name,types):
    folder=root/'tasks'/ident
    write_json(folder/'web.json',{'id':ident,'name':name,'busy':False})
    write_json(folder/'device_ir.json',{'components':[{'type':kind} for kind in types]})
    return folder


def test_sorting_reset_preserves_shared_components_other_tasks_and_plc(tmp_path):
    root=tmp_path/'device-scenes';sorting='a'*32;robot='b'*32
    seed_task(root,sorting,'factoryforge_sorting_reference.aml',['BeltConveyor','OperatorPanel','PLCInterface','SingleCommandPusher'])
    robot_folder=seed_task(root,robot,'robot.aml',['Robot6R','OperatorPanel','PLCInterface'])
    other_conveyor=seed_task(root,'d'*32,'another_conveyor.aml',['BeltConveyor'])
    before=(robot_folder/'device_ir.json').read_bytes()
    for area in ('candidates','approved'):
        for kind in ('BeltConveyor','SingleCommandPusher','OperatorPanel','PLCInterface','Robot6R'):
            write_json(root/'component_library'/area/kind/'0.1.0'/'digest'/'rule.json',{'type':kind})
    write_json(tmp_path/'sessions'/'old_plc.json',{'final':'preserve'})
    shared=root/'component_library'/'approved'/'OperatorPanel'/'0.1.0'/'digest'/'rule.json'
    shared_before=shared.read_bytes()
    assert plan(root)['shared_types']==['BeltConveyor','OperatorPanel','PLCInterface']
    assert (root/'tasks'/sorting).exists()  # dry run does not mutate
    result=archive(root)
    assert result['retired_tasks']==[sorting] and result['archived_types']==['SingleCommandPusher']
    assert not (root/'tasks'/sorting).exists() and not (root/'component_library'/'approved'/'SingleCommandPusher').exists()
    assert other_conveyor.is_dir() and (root/'component_library'/'approved'/'BeltConveyor').is_dir()
    assert (Path(result['archive'])/'tasks'/sorting/'web.json').is_file()
    assert shared.read_bytes()==shared_before and (robot_folder/'device_ir.json').read_bytes()==before
    assert read_json(tmp_path/'sessions'/'old_plc.json')=={'final':'preserve'}
    assert read_json(root/'cache_reset.json')['retired_tasks']==[sorting]
    assert archive(root)['status']=='nothing_to_archive'


def test_sorting_reset_refuses_busy_work_before_any_move(tmp_path):
    root=tmp_path/'device-scenes';folder=seed_task(root,'c'*32,'sorting.aml',['BeltConveyor'])
    write_json(folder/'web.json',{'busy':True})
    with pytest.raises(ValueError,match='still busy'):archive(root)
    assert folder.is_dir() and not (root/'archive').exists()
