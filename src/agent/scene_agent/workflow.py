"""Task artifacts and fail-closed candidate/formal scene lifecycle."""
from datetime import datetime
from pathlib import Path

from .common import SceneError, digest, read_json, write_json
from .device_ir import parse_device_ir, values, facts_digest, validate_device_ir
from .rules import ComponentLibrary, ApprovalRequired
from .validation import validate_candidate, check_assembly
from .requirements import MODEL_REQUIREMENTS,for_device
from .approval_state import candidate_version, TaskApprovedLibrary


def component_report(reports):
    return {'status':'passed' if all(r['status']=='passed' for r in reports) else 'failed',
            'model_requirements':MODEL_REQUIREMENTS,
            'checks':[c for r in reports for c in r['checks']],'instance_reports':reports,
            'scope':'isolated component checks; scene behavior checks required separately',
            'plc_acceptance':'not_run'}


def default_output(task):
    return Path('E:/chagpt')/(datetime.now().strftime('%Y-%m-%d')+'-场景生成Agent')/task


def prepare(aml,request,output,library, *, candidate_rules=None,author=None,blender='E:/Blender/blender.exe',
            reusable_candidates=None,progress=None,force_regenerate=False,verification_profile='fixed_model_io/1'):
    output=Path(output)
    if (output/'task.json').exists():raise SceneError('Task already exists; use a new output directory')
    output.mkdir(parents=True,exist_ok=True)
    from .rules import implementation_hashes
    import shutil
    repository=Path(__file__).resolve().parents[3]
    for name in implementation_hashes():
        target=output/'implementation_snapshot'/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(repository/name,target)
    ir=validate_device_ir(aml) if isinstance(aml,dict) else parse_device_ir(aml)
    from .verification import PROFILE,configure,enabled,parameters,validate_parameters,apply_physics
    if verification_profile not in (PROFILE,'legacy'):raise SceneError('未知设备校验配置')
    if verification_profile==PROFILE:ir=configure(ir)
    write_json(output/'device_ir.json',ir)
    if enabled(ir):
        producer=getattr(author,'verification_parameters',None)
        contract=producer(ir) if producer else parameters(ir)
        validate_parameters(ir,contract)
        write_json(output/'verification_parameters.json',contract)
        from .plc_test import save_harness
        save_harness(ir,output)
    from .simulation_tests import write_test_plan
    write_test_plan(ir,output)
    (output/'request.txt').write_text(request,encoding='utf-8')
    progress=progress or (lambda message:None)
    progress('设备描述已解析；完整重生成全部规则、几何和场景，不使用已有模型' if force_regenerate else '设备描述已解析，正在查询组件库')
    excluded={j['id'] for j in ir['joints']};missing=set();reused={};resolution=[]
    from .rules import validate_rule,review_digest,implementation_hashes
    for item in ([] if force_regenerate else reusable_candidates or []):
        candidate=Path(item['directory'])
        if not candidate.resolve().is_relative_to(library.root/'candidates'):continue
        try:
            rule=read_json(candidate/'rule.json');report=read_json(candidate/'validation.json')
            samples=[values(c) for c in ir['components'] if c['type']==item['type']]
            if (not samples or digest(rule)!=item['rule_sha256'] or report['status']!='passed'
                    or review_digest(candidate)!=item['review_sha256']
                    or report['implementation_sha256']!=implementation_hashes()):continue
            for sample in samples:validate_rule(rule,sample)
            from .author import SceneGenerationAgent
            SceneGenerationAgent._validate_candidate(rule,[SceneGenerationAgent._component_summary(c,ir) for c in ir['components'] if c['type']==item['type']])
            reused[item['type']]=item
        except (SceneError,ValueError,KeyError,OSError):continue
    for c in ir['components']:
        if c['id'] in excluded:continue
        if force_regenerate:
            missing.add(c['type'])
            resolution.append({'component_id':c['id'],'type':c['type'],'origin':'author_required',
                'reason':'full_regeneration_no_reuse'})
            continue
        entry=None;reason=None
        try:entry=library.resolve(c['type'],values(c))
        except ApprovalRequired as exc:reason=str(exc)
        if entry is not None:
            # A type/domain match alone must not reuse tuning that contradicts
            # the new AML's external joint and explicitly supplied parameters.
            from .author import SceneGenerationAgent
            try:SceneGenerationAgent._validate_candidate(entry['rule'],[SceneGenerationAgent._component_summary(c,ir)])
            except SceneError as exc:reason=str(exc)
        if reason is None:
            resolution.append({'component_id':c['id'],'type':c['type'],'origin':'approved_library',
                'version':entry['rule']['version'],'rule_sha256':digest(entry['rule'])})
        else:
            if c['type'] not in reused:missing.add(c['type'])
            resolution.append({'component_id':c['id'],'type':c['type'],
                'origin':'reused_candidate' if c['type'] in reused else 'author_required','reason':reason})
    task={'status':'device_ir_generated','plc_generation':'unchanged','plc_acceptance':'not_run',
          'model_requirements':for_device(ir),
          'device_ir_sha256':facts_digest(ir),'missing_types':sorted(missing),'pending':ir['pending'],
          'candidate_origin':'supplied_candidate_rules' if candidate_rules is not None else 'llm',
          'candidates':list(reused.values()),'request':request,'library':str(library.root),
          'source_sha256':ir['source']['sha256'],'component_resolution':resolution}
    task['force_regenerate']=force_regenerate
    task['generation_policy']='full_regeneration_no_reuse' if force_regenerate else 'library_resolution'
    write_json(output/'task.json',task)
    if ir['pending']:
        task['status']='device_description_pending';write_json(output/'task.json',task);return task
    if missing:
        if candidate_rules is None:
            if author is None:
                task['status']='component_authoring_required';write_json(output/'task.json',task);return task
            versions={kind:sorted({p.parent.parent.name for p in (library.root/'approved'/kind).glob('*/*/rule.json')}) for kind in missing}
            progress('Agent 正在重新生成全部 '+str(len(missing))+' 类组件规则' if force_regenerate else 'Agent 正在编写 '+str(len(missing))+' 类缺失组件的候选规则')
            instruction='\nFull regeneration: author ALL requested types from AML. No previous rules or assets are supplied. Existing version identifiers are naming metadata only.' if force_regenerate else ''
            candidate_rules=author.draft_rules(ir,request+instruction+'\nExisting approved versions (new rules must increment these): '+str(versions),missing,output/'model_calls')
        available={r['component_type']:r for r in candidate_rules}
        if enabled(ir):available={kind:apply_physics(rule) for kind,rule in available.items()}
        if not missing.issubset(available):raise SceneError('Supplied rules do not cover missing component types')
        for kind in sorted(missing):
            progress('正在构建和检查候选组件：'+kind)
            rule=available[kind]
            components=[c for c in ir['components'] if c['type']==kind]
            samples=[values(c) for c in components]
            history=[]
            for attempt in range(1,4):
                if enabled(ir):rule=apply_physics(rule)
                rule,allocation=candidate_version(library,rule,force_new=force_regenerate)
                if allocation:write_json(output/'candidate_history'/kind/('version_%03d.json'%attempt),allocation)
                if force_regenerate and (library.root/'candidates'/kind/rule['version']/digest(rule)).exists():
                    raise SceneError('Fresh candidate directory already exists; refuse cached assets')
                candidate=library.stage(rule,origin=task['candidate_origin']);reports=[]
                # All instances of a shared type are checked, not just the first.
                for component,sample in zip(components,samples):
                    try:
                        from .author import SceneGenerationAgent
                        SceneGenerationAgent._validate_candidate(rule,[SceneGenerationAgent._component_summary(component,ir)])
                        report,token=validate_candidate(library,candidate,sample,blender=blender)
                    except (SceneError,ValueError,KeyError,OSError) as exc:
                        report={'status':'failed','checks':[{'name':'candidate_build','passed':False,'detail':str(exc)}],
                                'parameters':sample,'layer':getattr(exc,'layer','component_model'),'unresolved':[str(exc)],
                                'error_details':getattr(exc,'details',{})}
                    reports.append(report)
                history.append({'directory':str(candidate),'rule_sha256':digest(rule),'instance_reports':reports})
                write_json(output/'candidate_history'/kind/('round_%03d.json'%attempt),history[-1])
                build_errors=any(not c.get('passed') for r in reports for c in r['checks'])
                if (all(r['status']=='passed' for r in reports) or author is None or attempt==3
                        or (rule['unresolved'] and not build_errors)):break
                rule=author.repair_candidate(ir,request,rule,reports,output/'model_calls'/kind/('repair_%03d'%attempt))
            combined=component_report(reports)
            token=library.submit_validation(candidate,combined)
            task['candidates'].append({'type':kind,'directory':str(candidate),'rule_sha256':digest(rule),
                                       'review_sha256':token,'validation_status':combined['status'],'approval':'pending','attempts':len(history)})
            write_json(output/'task.json',task)
        task['status']='awaiting_component_approval'
    else:task['status']='awaiting_component_approval' if reused else 'approved_components_available'
    if enabled(ir):
        task['verification_profile']=PROFILE
        progress('模型参数已与 AML 核对；使用固定结构与 OpenPLC IO 校验，不生成 Agent 测试脚本')
    elif author is not None and hasattr(author,'draft_collision_free_tests') and not any(c['validation_status']!='passed' for c in task['candidates']):
        from runtime.device_scene import build_device_scene
        from .simulation_tests import validate_test_plan,run_test_plan
        from .test_safety import safety_issues
        progress('正在装配实际模型，为通用测试脚本提供碰撞几何与机械关系')
        metadata,xml=build_device_scene(ir,library,output/'test_planning_scene',candidates=candidate_entries(ir,library,task),blender=blender)
        progress('Agent 正在编排无干涉测试路径；固定检查器预检失败将反馈重写')
        plan=author.draft_collision_free_tests(ir,metadata,xml,read_json(output/'simulation_tests.json'),output/'model_calls'/'test_planning')
        validate_test_plan(ir,plan)
        report=run_test_plan(ir,metadata,xml,plan,cancelled=getattr(author,'check_cancelled',None));issues=safety_issues(report)
        if issues:raise SceneError('生成的设备测试未通过通用碰撞预检','simulation_test',{'issues':issues})
        write_json(output/'simulation_tests.json',plan)
        write_json(output/'test_safety.json',{'format':'308sjk.collision_free_tests/1','collision_free_preflight':True,
            'model_xml_sha256':metadata['model_xml_sha256'],'plan_sha256':report['plan_sha256'],
            'checker_sha256':report['checker_sha256'],'scope':'device_machine_interference_not_product_process_contacts',
            'evidence':'model_calls/test_planning','plc_acceptance':'not_run'})
    elif author is not None and hasattr(author,'draft_test_sequences'):
        from .simulation_tests import validate_test_plan
        plan=author.draft_test_sequences(ir,read_json(output/'simulation_tests.json'),output/'model_calls'/'simulation_tests')
        write_json(output/'simulation_tests.json',validate_test_plan(ir,plan))
    write_json(output/'task.json',task)
    return task


def candidate_entries(ir,library,task):
    result={}
    for record in task.get('candidates',[]):
        path=Path(record['directory']);rule=read_json(path/'rule.json')
        if digest(rule)!=record['rule_sha256']:raise SceneError('Candidate changed since task preparation')
        result[record['type']]={'rule':rule,'directory':str(path)}
    for c in ir['components']:
        if c['id'] in {j['id'] for j in ir['joints']} or c['type'] in result:continue
        if task.get('force_regenerate'):raise SceneError('Full regeneration is missing a new candidate: '+c['type'])
        result[c['type']]=library.resolve(c['type'],values(c))
    return result


def build_task(output,library, *, isolated_candidates=False,geometry_only=False,blender='E:/Blender/blender.exe'):
    from runtime.device_scene import build_device_scene, DeviceRuntime
    output=Path(output);task=read_json(output/'task.json');ir=read_json(output/'device_ir.json')
    if geometry_only and not isolated_candidates:raise SceneError('Geometry-only preview requires isolated candidates')
    folder=output/('candidate_scene' if isolated_candidates else 'scene')
    entries=candidate_entries(ir,library,task) if isolated_candidates else None
    if not isolated_candidates:library=TaskApprovedLibrary(library,task)
    if geometry_only:
        # The existing runtime builder requires implemented IO kernels. Its
        # geometry projection is separate from the authoritative DeviceIR and
        # cannot become an executable model. Preserve the full source IO below.
        import copy
        projection=copy.deepcopy(ir);projection['io']=[]
        metadata,xml=build_device_scene(projection,library,folder,candidates=entries,blender=blender)
        declared=[]
        for p in ir['io']:
            if p['component_id'] not in metadata['components']:raise SceneError('Declared IO has no modeled component: '+p['name'],'assembly')
            declared.append({'plc_var':p['name'],'dir':p['dir'],'type':p['type'],
                'bind':{'asset':p['component_id'],'quantity':p['quantity']},'address':p['address'],
                'writer':p['writer'],'physical_per_plc_unit':p['physical_per_plc_unit'],
                'binding_status':'declaration_only_not_executable'})
        metadata.update(device_ir_sha256=facts_digest(ir),io=copy.deepcopy(ir['io']),io_map=declared,
            geometry_only=True,io_execution_status='not_implemented_or_not_validated')
        write_json(folder/'device_ir.json',ir);write_json(folder/'scene.physical.json',metadata);write_json(folder/'io_map.json',declared)
    else:metadata,xml=build_device_scene(ir,library,folder,candidates=entries,blender=blender)
    from .verification import enabled
    report=check_assembly(metadata,xml,steps=0 if geometry_only or enabled(ir) else 500,preview=folder/'preview.png')
    if geometry_only:
        report['checks']=[check for check in report['checks'] if check['name']!='physics_smoke']
        for check in report['checks']:
            if check['name']=='io_coverage':check.update(name='io_declarations_preserved',binding_status='declaration_only_not_executable')
        report['checks'].append({'name':'static_geometry_preview','passed':True,'simulation_steps':0})
        report.update(scope='initial geometry and assembly only; physics and behavior not validated',validation_level='geometry_only')
        for name in ('behavior_checks.json','behavior_replay.json','validated_preview.png','model.freeze.json','simulation_test_results.json'):
            (folder/name).unlink(missing_ok=True)
    write_json(folder/'assembly_checks.json',report)
    task['scene']={'directory':str(folder),'status':'candidate_geometry_only' if geometry_only else 'candidate_built' if isolated_candidates else 'built',
                   'geometry_only':geometry_only,
                   'assembly_checks':report['status'],'model_frozen':False,'plc_acceptance':'not_run'}
    write_json(output/'task.json',task)
    return task


def _reset_fixed_results(output,folder):
    """A failed new connection/check cannot keep an old passed badge or freeze."""
    for name in ('simulation_test_results.json','selected_tests/simulation_test_results.json','behavior_checks.json','model.freeze.json'):
        (folder/name).unlink(missing_ok=True)
    if (output/'task.json').exists():
        task=read_json(output/'task.json');task.setdefault('scene',{}).update(model_frozen=False,behavior_checks='not_run')
        write_json(output/'task.json',task)


def check_task(output, *, isolated_candidates=False,freeze=False,capture_replay=False,plc_config=None,cancelled=None):
    from runtime.device_scene import DeviceRuntime
    from .validation import check_device_behaviors
    output=Path(output);task=read_json(output/'task.json')
    if task.get('scene',{}).get('geometry_only'):
        raise SceneError('候选仅完成几何预览；请先解决组件行为问题并重新检查，不能运行设备验收或冻结')
    folder=output/('candidate_scene' if isolated_candidates else 'scene')
    metadata=read_json(folder/'scene.physical.json');xml=(folder/'scene.xml').read_text(encoding='utf-8')
    from .verification import enabled,validate_parameters
    ir=read_json(output/'device_ir.json') if (output/'device_ir.json').exists() else {}
    if enabled(ir):
        _reset_fixed_results(output,folder)
        task=read_json(output/'task.json')
        validate_parameters(ir,read_json(output/'verification_parameters.json'))
        from .simulation_tests import run_test_plan
        transport=None
        if plc_config is None and (output/'plc_test_connection.json').exists():plc_config=read_json(output/'plc_test_connection.json')
        try:
            if plc_config:
                from .plc_test import connect
                transport=connect(ir,plc_config,cancelled)
            tests=run_test_plan(ir,metadata,xml,read_json(output/'simulation_tests.json'),output=folder,transport=transport,cancelled=cancelled)
        finally:
            if transport:transport.close()
        report={'status':tests['status'],'scope':tests['scope'],'behavior_checks_complete':tests['complete'],
            'scene_sha256':metadata['model_xml_sha256'],'definition_sha256':digest(metadata),'plc_acceptance':'not_run',
            'plc_io_validation':tests['plc_io_validation'],'whole_machine_functional_tests':'not_requested',
            'simulation_tests':{k:tests[k] for k in ('status','complete','plan_sha256','checker_sha256')},
            'checks':[{'name':m['label'],'passed':m['status']=='passed','status':m['status'],'uncovered':m['uncovered']} for m in tests['modules']]}
        write_json(folder/'behavior_checks.json',report)
        task.setdefault('scene',{})['behavior_checks']=report['status']
        if freeze:
            DeviceRuntime(metadata,xml).freeze(folder,report);task['scene'].update(model_frozen=True,status='model_frozen')
        write_json(output/'task.json',task)
        return report
    # An approved legacy rule can still have the wrong material semantics.
    # Diagnose source references before a fixture tries to dereference an absent item.
    pools={p['product_id'] for p in metadata['products']};missing=[]
    for cid,component in metadata['components'].items():
        if component['behavior']!='source':continue
        for parameter in ('short_product_id','tall_product_id'):
            product_id=component['parameters'].get(parameter)
            if product_id in pools:continue
            product=metadata['components'].get(product_id,{})
            missing.append({'source_id':cid,'source_name':component['name'],'parameter':parameter,
                'product_id':product_id,'product_name':product.get('name',str(product_id)),
                'actual_behavior':product.get('behavior'),'required_behavior':'rigid'})
    if missing:
        detail='；'.join(m['source_name']+' 引用的工件 '+m['product_name']+
            ' 没有可运动的实例（当前行为 '+str(m['actual_behavior'])+'，需要 rigid）' for m in missing)
        report={'status':'failed','checks':[{'name':'material_product_pool:'+str(m['product_id']),
            'passed':False,'detail':m} for m in missing],'behavior_checks_complete':False,
            'scope':'material blueprint preflight; dynamic checks not run',
            'scene_sha256':metadata['model_xml_sha256'],'plc_acceptance':'not_run'}
        write_json(folder/'behavior_checks.json',report)
        for name in ('behavior_replay.json','validated_preview.png','model.freeze.json'):
            (folder/name).unlink(missing_ok=True)
        task.setdefault('scene',{}).update(behavior_checks='failed',model_frozen=False)
        write_json(output/'task.json',task)
        raise SceneError(detail+'。请修正工件规则并审批新版本后重新构建。',details={'missing_products':missing})
    from .simulation_tests import write_test_plan
    ir=read_json(output/'device_ir.json') if (output/'device_ir.json').exists() else read_json(folder/'device_ir.json')
    plan=read_json(output/'simulation_tests.json') if (output/'simulation_tests.json').exists() else write_test_plan(ir,output)
    frames=[]
    report=check_device_behaviors(metadata,xml,preview=folder/'validated_preview.png',record=frames.append if capture_replay else None,
        verification_ir=ir,test_plan=plan,test_output=folder)
    from .test_safety import safety_issues
    from .mechanics import DeviceCollisionProbe
    from .simulation_tests import checker_digest
    tests=read_json(folder/'simulation_test_results.json');issues=safety_issues(tests)
    accepted=output/'model_calls/test_planning/accepted.json'
    from_agent=accepted.exists() and read_json(accepted).get('plan_sha256')==digest(plan)
    scope=DeviceCollisionProbe(ir,DeviceRuntime(metadata,xml)).scope
    write_json(output/'test_safety.json',{'format':'308sjk.collision_free_tests/1',
        'collision_free_preflight':not issues and scope['coverage_complete'],'scope':scope,'issues':issues,
        'model_xml_sha256':metadata['model_xml_sha256'],'plan_sha256':digest(plan),'checker_sha256':checker_digest(),
        'origin':'agent_geometry_aware' if from_agent else 'fixed_or_existing_program','plc_acceptance':'not_run'})
    if capture_replay and frames:
        write_json(folder/'behavior_replay.json',{'source':'independent_mujoco_model_fixture','plc_acceptance':'not_run',
                  'model_xml_sha256':metadata['model_xml_sha256'],'frames':frames})
    elif capture_replay:(folder/'behavior_replay.json').unlink(missing_ok=True)
    write_json(folder/'behavior_checks.json',report)
    task.setdefault('scene',{})['behavior_checks']=report['status']
    if freeze:
        DeviceRuntime(metadata,xml).freeze(folder,report)
        task['scene'].update(model_frozen=True,status='model_frozen')
    write_json(output/'task.json',task)
    return report


def review_pack(output):
    """Export reviewable facts/approximations and current tokens; never approve."""
    output=Path(output);task=read_json(output/'task.json');rows=[]
    from .rules import review_digest,implementation_hashes
    for item in task.get('candidates',[]):
        folder=Path(item['directory']);rule=read_json(folder/'rule.json');report=read_json(folder/'validation.json')
        current=review_digest(folder)
        if current!=item['review_sha256'] or report['implementation_sha256']!=implementation_hashes():
            raise SceneError('Task review token is stale; revalidate candidate: '+item['type'])
        rows.append({**item,'version':rule['version'],'assumptions':rule['assumptions'],
            'unresolved':rule['unresolved'],'parameter_bounds':rule['parameter_bounds'],
            'limitations':rule['provenance'].get('limitations',[]) if isinstance(rule.get('provenance'),dict) else [],
            'validation_report':str(folder/'validation.json'),
            'preview':str(folder/'geometry/preview.png') if (folder/'geometry/preview.png').exists()
                      else str(folder/'preview.png') if (folder/'preview.png').exists() else None})
    pack={'status':'awaiting_human_review','plc_acceptance':'not_run','components':rows,'model_requirements':MODEL_REQUIREMENTS,
          'device_ir':str(output/'device_ir.json'),'scene':task.get('scene')}
    write_json(output/'review_pack.json',pack)
    lines=['# 场景组件审批材料','','所有条目仍未审批。检查通过不代表原设备等效或 PLC 验收通过。',
           '','[完整审批清单](review_pack.json) · [设备事实](device_ir.json)','','| 类型 | 版本 | 检查 | 审批 |','|---|---|---|---|']
    for r in rows:lines.append('| '+r['type']+' | '+r['version']+' | '+r['validation_status']+' | pending |')
    lines+=['','## 场景预览','','![装配预览](candidate_scene/validated_preview.png)',
            '','[独立行为检查](candidate_scene/behavior_checks.json)；此夹具不执行 PLC 策略。']
    for r in rows:
        lines+=['','## '+r['type']+' '+r['version'],'','规则：'+r['directory'],
                '','审批 SHA：`'+r['review_sha256']+'`','','建模近似：']
        lines += ['- '+s for s in r['assumptions']] or ['- 无额外近似声明']
        lines+=['','未解决问题（阻断 PLC 验证）：'+str(r['unresolved']),
                '','简化与适用范围（不阻断当前验证）：'+str(r['limitations'])]
    (output/'REVIEW.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return pack


def test_task(output,*,isolated_candidates=False,selected=None,plc_config=None,cancelled=None):
    """Public selected-test entry; parameter contract is checked before any connection."""
    from .simulation_tests import run_test_plan
    from .verification import enabled,validate_parameters
    output=Path(output);folder=output/('candidate_scene' if isolated_candidates else 'scene')
    ir=read_json(output/'device_ir.json');transport=None
    if enabled(ir):
        _reset_fixed_results(output,folder)
        validate_parameters(ir,read_json(output/'verification_parameters.json'))
        if plc_config is None and (output/'plc_test_connection.json').exists():plc_config=read_json(output/'plc_test_connection.json')
    try:
        if enabled(ir) and plc_config:
            from .plc_test import connect
            transport=connect(ir,plc_config,cancelled)
        return run_test_plan(ir,read_json(folder/'scene.physical.json'),(folder/'scene.xml').read_text(encoding='utf-8'),
            read_json(output/'simulation_tests.json'),selected,output=folder/'selected_tests',cancelled=cancelled,transport=transport)
    finally:
        if transport:transport.close()
