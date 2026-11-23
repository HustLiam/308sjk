"""Fixed structure and per-channel IO verification of isolated assembled models."""
import copy
import math
from pathlib import Path

import mujoco
import numpy as np

from .common import SceneError, digest, write_json
from .device_ir import values, facts_digest
from .verification import PROFILE, dimensions, validate_parameters, parameters, interface_frame


def plan(ir):
    from .simulation_tests import generate_test_plan
    legacy=copy.deepcopy(ir);legacy.pop('verification_profile',None)
    result=generate_test_plan(legacy)
    result.update(device_ir_sha256=facts_digest(ir),generator=PROFILE)
    from .robot_io import contracts
    io_contracts=contracts(ir)
    for module in result['modules']:
        if module['kind']!='io_binding':module['steps']=[{'op':'inspect_structure'}]
        elif module.get('ruleset'):
            module['script_origin']='fixed_checker'
            if io_contracts[module['target']]['kind']=='controller':
                quantity=next(p['quantity'] for p in ir['io'] if p['name']==module['target'])
                cases=[(False,False,True),(True,False,True)]
                if quantity=='fault':cases=[(True,False,True),(True,False,False)]
                if quantity=='reset_cmd':cases=[(True,False,False),(True,False,True),(True,True,True)]
                module['steps']=[{'op':'control','enable':e,'reset':r,'estop_ok':s} for e,r,s in cases]
    for c in ir['components']:
        if c['id'] in {j['id'] for j in ir['joints']}:continue
        result['modules'].insert(0,{'id':'test_'+digest(['component_structure',c['id']])[:20],
            'kind':'component_structure','target':c['id'],'label':'尺寸与装配 · '+c['name'],
            'requirements':[],'steps':[{'op':'inspect_structure'}]})
    return result


def _component(ir,runtime,module,check):
    from .simulation_tests import _id,_body,_worlds,UnsupportedProbe
    c=next(c for c in ir['components'] if c['id']==module['target']);m=runtime.model;d=runtime.data
    if any(p['product_id']==c['id'] for p in runtime.meta.get('products',[])):
        raise UnsupportedProbe('动态工件为生成池原型，尚未提供独立的原型尺寸与初始放置检查')
    bid=_id(m,'BODY','c_'+c['id']);world,_=_worlds(ir);expected=world(c['id']);actual=_body(runtime,'c_'+c['id'])
    req=ir['simulation_requirements'];ptol=req['position_tolerance_m'];atol=req['angle_tolerance_rad']
    check('component_position',np.linalg.norm(expected[:3,3]-actual[:3,3])<=ptol,expected=expected[:3,3].tolist(),actual=actual[:3,3].tolist())
    angle=math.acos(np.clip((np.trace(expected[:3,:3].T@actual[:3,:3])-1)/2,-1,1))
    check('component_orientation',angle<=atol,error_rad=float(angle))
    joints={j['child_body']:j for j in ir['joints']};mounts={a['child_component']:a for a in ir.get('assemblies',[])}
    parent=joints[c['id']]['parent_body'] if c['id'] in joints else mounts[c['id']]['parent_component'] if c['id'] in mounts else c['parent_id']
    check('component_parent',int(m.body_parentid[bid])==(_id(m,'BODY','c_'+parent) if parent else 0),expected=parent)
    gids=[g for g in range(m.ngeom) if int(m.geom_bodyid[g])==bid or (mujoco.mj_id2name(m,mujoco.mjtObj.mjOBJ_BODY,int(m.geom_bodyid[g])) or '').startswith('c_'+c['id']+'__')]
    if values(c).get('geometry_enabled') is False:
        check('logical_component_geometry',not gids);return
    size=dimensions(c)
    if size is None:
        if gids:raise UnsupportedProbe('组件未声明可量测的关键 XYZ 尺寸：'+c['name'])
        return
    if not gids:
        check('critical_dimensions',False,expected_m=size,actual_m=None);return
    # Actual compiled geometry bounds in the component frame, not metadata sizes.
    corners=[];inverse=np.linalg.inv(actual)
    for gid in gids:
        center=m.geom_aabb[gid,:3];half=m.geom_aabb[gid,3:]
        for x in (-1,1):
            for y in (-1,1):
                for z in (-1,1):
                    point=d.geom_xpos[gid]+d.geom_xmat[gid].reshape(3,3)@(center+half*np.array([x,y,z]))
                    corners.append((inverse@np.r_[point,1])[:3])
    measured=np.ptp(np.asarray(corners),axis=0)
    check('critical_dimensions',np.allclose(measured,size,atol=ptol,rtol=0),expected_m=size,actual_m=measured.tolist())


def _mechanical(ir,runtime,module,check):
    from .simulation_tests import _id,_body,_worlds,UnsupportedProbe
    m=runtime.model;req=ir['simulation_requirements'];kind=module['kind']
    if kind=='mechanical_joint':
        j=next(j for j in ir['joints'] if j['id']==module['target'])
        if j['type'] not in ('hinge','slide'):raise UnsupportedProbe('固定结构检查尚不支持该运动副类型')
        ji=_id(m,'JOINT','j_'+j['id']);child=_id(m,'BODY','c_'+j['child_body'])
        check('joint_type',int(m.jnt_type[ji])==int(mujoco.mjtJoint.mjJNT_HINGE if j['type']=='hinge' else mujoco.mjtJoint.mjJNT_SLIDE))
        check('joint_child',int(m.jnt_bodyid[ji])==child)
        check('joint_parent',int(m.body_parentid[child])==_id(m,'BODY','c_'+j['parent_body']))
        axis=np.asarray(j['axis']);axis=axis/np.linalg.norm(axis)
        check('joint_axis',np.allclose(m.jnt_axis[ji],axis,atol=1e-9),expected=axis.tolist(),actual=m.jnt_axis[ji].tolist())
        check('joint_anchor',np.allclose(m.jnt_pos[ji],j['child_anchor_m'],atol=req['position_tolerance_m']))
        check('joint_range',bool(m.jnt_limited[ji]) and np.allclose(m.jnt_range[ji],j['range'],atol=1e-9))
        check('joint_initial',abs(float(m.qpos0[m.jnt_qposadr[ji]])-j['initial'])<=1e-9)
        world,_=_worlds(ir)
        pa=world(j['parent_body'])@np.r_[j['parent_anchor_m'],1]
        ca=_body(runtime,'c_'+j['child_body'])@np.r_[j['child_anchor_m'],1]
        check('joint_interface_alignment',np.linalg.norm(pa-ca)<=req['position_tolerance_m'])
        if j.get('definition_source')=='interfaces':
            f=next(f for f in ir['interfaces'] if f['id']==j['parent_interface'])
            parent_axis=(world(j['parent_body'])@interface_frame(f))[:3,2]
            check('joint_interface_axis',np.allclose(parent_axis,world(j['child_body'])[:3,:3]@axis,atol=req['angle_tolerance_rad']))
        c=next(c for c in ir['components'] if c['id']==j['child_body']);p=values(c)
        for key in ('max_velocity_rad_s','max_acceleration_rad_s2','max_velocity_m_s','max_acceleration_m_s2','nominal_speed_m_s'):
            if key in p:check('motion_parameter:'+key,runtime.meta['components'][c['id']]['parameters'].get(key)==p[key],expected=p[key])
    elif kind=='fixed_mount':
        a=next(a for a in ir['assemblies'] if a['id']==module['target']);_,frame=_worlds(ir)
        from .common import pose_matrix
        expected=_body(runtime,'c_'+a['parent_component'])@frame(a['parent_interface'])@pose_matrix(a.get('offset',{}))
        actual=_body(runtime,'c_'+a['child_component'])@frame(a['child_interface'])
        check('fixed_interface_position',np.linalg.norm(actual[:3,3]-expected[:3,3])<=req['position_tolerance_m'])
        check('fixed_interface_orientation',math.acos(np.clip((np.trace(expected[:3,:3].T@actual[:3,:3])-1)/2,-1,1))<=req['angle_tolerance_rad'])
    elif kind=='joint_coupling':
        c=next(c for c in ir['couplings'] if c['id']==module['target']);ei=_id(m,'EQUALITY',c['id'])
        check('coupling_definition',bool(runtime.data.eq_active[ei]) and int(m.eq_obj1id[ei])==_id(m,'JOINT','j_'+c['follower'])
              and int(m.eq_obj2id[ei])==_id(m,'JOINT','j_'+c['leader']) and np.allclose(m.eq_data[ei,:5],[0,c['ratio'],0,0,0]))
    else:raise UnsupportedProbe('AML 声明的检查对象不存在')


def run(ir,metadata,xml,test_plan,selected=None,*,output=None,cancelled=None,transport=None):
    from runtime.device_scene import DeviceRuntime
    from .simulation_tests import FORMAT,checker_digest,_frame,_io_probe,UnsupportedProbe
    from .mechanics import DeviceCollisionProbe
    validate_parameters(ir,parameters(ir))
    if digest(test_plan)!=digest(plan(ir)):raise SceneError('固定检查计划不能被修改','simulation_test')
    if metadata.get('device_ir_sha256')!=facts_digest(ir):raise SceneError('模型与校验参数版本不一致','simulation_test')
    modules={m['id']:m for m in test_plan['modules']};selected=list(modules) if selected is None else selected
    if not isinstance(selected,list) or not selected or any(not isinstance(s,str) or s not in modules for s in selected) or len(selected)!=len(set(selected)):
        raise SceneError('请选择有效且不重复的检查模块','simulation_test')
    results=[];cancelled=cancelled or (lambda:None)
    for ident in selected:
        cancelled();module=modules[ident];checks=[];frames=[];blocked=None;category=None;trace_start=len(transport.trace) if transport else 0
        def check(name,passed,**detail):checks.append({'name':name,'passed':bool(passed),**detail})
        runtime=DeviceRuntime(metadata,xml);probe=DeviceCollisionProbe(ir,runtime)
        last_publish=-math.inf
        def publish():
            nonlocal last_publish
            if transport and module['kind']=='io_binding':
                transport.exchange(inputs={p['name']:runtime.signals[p['name']] for p in ir['io'] if p['dir']=='input'})
                last_publish=float(runtime.data.time)
        def capture(time_s):
            cancelled();collisions=probe.collisions()
            if collisions:raise SceneError('测试动作存在禁止干涉，未发布该回放帧','test_scenario',{'contacts':collisions})
            # Feedback state injection may advance no simulation time. Publish
            # every observation, including both sides of a response threshold.
            publish()
            frame=_frame(runtime,max(time_s,frames[-1]['time_s']+.001 if frames else 0));frame['collisions']=[];frames.append(frame)
        normal_step=runtime.step
        def step(outputs=None,external_inputs=None):
            cancelled()
            # Local validation happens before touching the PLC, including rejection probes.
            for name,value in (outputs or {}).items():
                p=runtime.points.get(name)
                if not p or p['dir']!='output':raise SceneError('非法输出通道','device_description')
                runtime._check_value(p,value)
            if probe.collisions():raise SceneError('模型初态存在禁止干涉','test_scenario')
            # Preflight each new joint target against the actual collision geometry.
            if outputs:
                from .mechanics import preflight_joint_path
                components={c['id']:c for c in ir['components']}
                for name,value in outputs.items():
                    p=runtime.points[name];params=values(components[p['component_id']]);jid=params.get('driven_joint_id')
                    if not jid or p['quantity'] not in ('joint_position_cmd','extend_cmd'):continue
                    joint=next(j for j in ir['joints'] if j['id']==jid)
                    target=value*p['physical_per_plc_unit'] if p['quantity']=='joint_position_cmd' else params['command_true_target_m' if value else 'command_false_target_m']
                    if not joint['range'][0]<=target<=joint['range'][1]:raise SceneError('测试目标超出关节限位','test_scenario')
                    evidence=preflight_joint_path(runtime,joint,[target],probe,cancelled)
                    if evidence:raise SceneError('固定 IO 激励路径未通过预检','test_scenario',evidence)
            actual=None
            if outputs:
                if transport is None:raise SceneError('尚未连接隔离 OpenPLC 测试 Runtime','plc_io')
                actual=transport.exchange(outputs=outputs)
            elif transport:actual=dict(transport.outputs)
            saved_q=runtime.data.qpos.copy();saved_v=runtime.data.qvel.copy();saved_time=float(runtime.data.time)
            result=normal_step(actual,external_inputs)
            if probe.collisions():
                runtime.data.qpos[:]=saved_q;runtime.data.qvel[:]=saved_v;runtime.data.time=saved_time;mujoco.mj_forward(runtime.model,runtime.data)
                raise SceneError('运行步产生禁止干涉，已拦截','test_scenario')
            return result
        runtime.step=step
        try:
            if not ir.get('simulation_requirements'):raise UnsupportedProbe('AML 尚未声明检查目标、容差和响应超时；请补全后重新生成')
            if not probe.scope['coverage_complete']:raise UnsupportedProbe('模型缺少碰撞检查所需几何')
            if probe.collisions():raise SceneError('模型初态存在禁止干涉','test_scenario',{'contacts':probe.collisions()})
            if module['kind']=='component_structure':_component(ir,runtime,module,check)
            elif module['kind']=='io_binding':
                from .robot_io import contracts
                contract=contracts(ir).get(module['target'])
                if contract and contract['kind']=='feedback':
                    joint=next(j for j in ir['joints'] if j['id']==contract['joint_id'])
                    if any(not joint['range'][0]<=s['position_rad']<=joint['range'][1] for s in module['steps'] if s['op']=='observe'):
                        raise SceneError('反馈测试状态超出关节范围，未注入模型','test_scenario')
                if transport is None:raise SceneError('尚未连接隔离 OpenPLC 测试 Runtime；不能把本地 IO 检查当成双向链路通过','plc_io')
                transport.exchange(outputs={p['name']:p['initial'] for p in ir['io'] if p['dir']=='output'},
                                   inputs={p['name']:p['initial'] for p in ir['io'] if p['dir']=='input'})
                _io_probe(ir,runtime,module,check,capture,cancelled)
                publish()
                check('plc_scan_acknowledged',len(transport.trace)>trace_start,identity_sha256=transport.contract['identity_sha256'])
            else:_mechanical(ir,runtime,module,check)
            capture(float(runtime.data.time))
            check('physics_state',np.isfinite(runtime.data.qpos).all() and np.isfinite(runtime.data.qvel).all() and not any(w.number for w in runtime.data.warning))
        except UnsupportedProbe as exc:blocked=str(exc);category='unsupported_probe'
        except SceneError as exc:
            from .robot_io import ScenarioBlocked
            if exc.layer in ('plc_io','test_scenario') or isinstance(exc,ScenarioBlocked):
                blocked=str(exc);category='plc_environment' if exc.layer=='plc_io' else 'scenario_blocked'
            else:check('execution',False,detail=str(exc));category='model_or_binding_error'
        except (ConnectionError,RuntimeError) as exc:blocked=str(exc);category='plc_environment' if transport else 'execution_error'
        except (ValueError,KeyError,StopIteration,OSError) as exc:check('execution',False,detail=str(exc));category='execution_error'
        status='failed' if any(not c['passed'] for c in checks) else 'blocked' if blocked else 'passed' if checks else 'blocked'
        replay='test_replays/'+ident+'.json' if frames else None
        result={**{k:v for k,v in module.items() if k!='steps'},'status':status,'checks':checks,'uncovered':blocked,
                'failure_category':category,'frame_count':len(frames),'replay':replay,'scope':PROFILE}
        if module['kind']=='io_binding':
            result.update(binding_status='passed' if status=='passed' else 'failed' if status=='failed' else 'inconclusive',
                plc_io_status=status,mechanical_relationship_status='not_assessed')
        else:result.update(mechanical_relationship_status=status,full_travel_status='not_requested',response_status='not_assessed')
        results.append(result)
        if output:
            if frames:write_json(Path(output)/replay,{'source':'fixed_model_io_checker','test_id':ident,'model_xml_sha256':metadata['model_xml_sha256'],
                'device_ir_sha256':facts_digest(ir),'definition_sha256':digest(metadata),'plan_sha256':digest(test_plan),'frames':frames})
            if transport and module['kind']=='io_binding':write_json(Path(output)/'plc_traces'/(ident+'.json'),transport.trace[trace_start:])
    status='failed' if any(m['status']=='failed' for m in results) else 'blocked' if any(m['status']=='blocked' for m in results) else 'passed'
    report={'format':FORMAT,'scope':PROFILE,'status':status,'complete':set(selected)==set(modules) and status=='passed',
            'modules':results,'device_ir_sha256':facts_digest(ir),'scene_sha256':metadata['model_xml_sha256'],
            'definition_sha256':digest(metadata),'plan_sha256':digest(test_plan),'checker_sha256':checker_digest(),
            'plc_acceptance':'not_run','plc_io_validation':'connected' if transport else 'not_run',
            'whole_machine_functional_tests':'not_requested'}
    if output:write_json(Path(output)/'simulation_test_results.json',report)
    return report
