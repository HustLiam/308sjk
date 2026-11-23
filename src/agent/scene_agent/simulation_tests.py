"""Agent test templates and fixed MuJoCo oracles, independent of model recipes.

Generated JSON scripts contain stimuli only. The authoritative DeviceIR supplies
all expected bindings, kinematics and tolerances at execution time.
"""
import hashlib
import math
from pathlib import Path

import numpy as np

from .common import SceneError, digest, pose_matrix, write_json
from .device_ir import facts_digest, values

FORMAT = '308sjk.simulation_tests/1'


def checker_digest():
    return digest({name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
        for name in ('simulation_tests.py', 'mechanics.py', 'robot_io.py', 'test_safety.py','verification.py','fixed_tests.py','plc_test.py')})


def generate_test_plan(ir):
    """Compile mandatory coverage from facts; every upload gets a fresh plan."""
    from .verification import enabled
    if enabled(ir):
        from .fixed_tests import plan
        return plan(ir)
    modules = []
    req = ir.get('simulation_requirements') or {}
    def add(kind, target, label, steps):
        requirements = [c['id'] for c in req.get('checks', []) if c['kind']==kind and c['target'] in (target,'*')]
        modules.append({'id': 'test_'+digest([kind, target])[:20], 'kind': kind, 'target': target,
                        'label': label, 'requirements': requirements, 'steps': steps})
    for j in ir['joints']:
        add('mechanical_joint', j['id'], '机械联动 · '+j['name'],
            [{'op': 'joint_pose', 'fraction': f} for f in (0, .25, .5, .75, 1)])
    for a in ir.get('assemblies', []):
        add('fixed_mount', a['id'], '固定连接 · '+a['id'], [{'op': 'mount_probe'}])
    for c in ir.get('couplings', []):
        add('joint_coupling', c['id'], '关节耦合 · '+c['id'], [{'op': 'coupling_probe'}])
    from .robot_io import contracts, default_steps, rules_document
    robot_contracts = contracts(ir)
    rules_sha = digest(rules_document(ir)) if robot_contracts else None
    for p in ir['io']:
        # Boundary values cannot be removed by the author or browser selection.
        samples = [False, True, False] if p['type'] == 'BOOL' else sorted(set([p.get('range', [0, 32767])[0], p['initial'], p.get('range', [0, 32767])[1]]))
        add('io_binding', p['name'], ('指令 IO' if p['dir'] == 'output' else '反馈 IO' if p['writer'] == 'simulation' else '输入 IO')+' · '+p['name'],
            [{'op': 'io_sample', 'value': v} for v in samples])
        if p['name'] in robot_contracts:
            modules[-1].update(ruleset='308sjk.robot_io_rules/1', rules_sha256=rules_sha,
                script_origin='baseline', steps=default_steps(robot_contracts[p['name']], p))
    covered={r for m in modules for r in m['requirements']}
    for c in req.get('checks',[]):
        if c['id'] not in covered:
            modules.append({'id':'test_'+digest(['missing',c['id']])[:20],'kind':'missing_requirement',
                'target':c['target'],'label':'缺少验证对象 · '+c['description'],'requirements':[c['id']],'steps':[]})
    return {'format': FORMAT, 'device_ir_sha256': facts_digest(ir), 'generator': 'scene_agent_fixed_templates/1',
            'requirements_source': 'aml' if req else 'inferred_legacy_facts',
            'assumptions': [] if req else ['旧 AML 未声明仿真验证范围与容差；使用模板默认值，不能代替人工确认。'],
            'modules': modules}


def validate_test_plan(ir, plan):
    """Validate Agent IO programs/extra poses without losing targets or oracles."""
    baseline = generate_test_plan(ir)
    from .verification import enabled
    if enabled(ir):
        if digest(plan)!=digest(baseline):raise SceneError('固定校验计划不可由 Agent 或前端修改','simulation_test')
        return plan
    if set(plan) != set(baseline) or any(plan[k] != baseline[k] for k in baseline if k != 'modules'):
        raise SceneError('Test plan identity/requirements changed', 'simulation_test')
    original = {m['id']: m for m in baseline['modules']}
    if len(plan['modules']) != len(original) or {m.get('id') for m in plan['modules']} != set(original):
        raise SceneError('Test plan lost mandatory coverage', 'simulation_test')
    from .robot_io import contracts, validate_steps
    robot_contracts = contracts(ir); points = {p['name']: p for p in ir['io']}
    for module in plan['modules']:
        expected = original[module['id']]
        if set(module) != set(expected) or any(module[k] != expected[k] for k in expected if k not in ('steps', 'script_origin')):
            raise SceneError('Test script cannot change checker or target', 'simulation_test')
        steps = module['steps']
        if expected.get('ruleset'):
            if module['script_origin'] not in ('baseline', 'agent'):
                raise SceneError('Invalid IO script provenance', 'simulation_test')
            validate_steps(robot_contracts[module['target']], points[module['target']], steps)
        elif module['kind'] != 'mechanical_joint':
            if steps != expected['steps']:
                raise SceneError('Mandatory IO/boundary probes changed', 'simulation_test')
        else:
            if isinstance(steps,list) and steps and isinstance(steps[0],dict) and steps[0].get('op')=='joint_configuration':
                from .test_safety import validate_joint_program
                validate_joint_program(ir,module)
                continue
            if not isinstance(steps, list) or not 5 <= len(steps) <= 25:
                raise SceneError('Expected 5..25 legal joint poses', 'simulation_test')
            for s in steps:
                if set(s) != {'op', 'fraction'} or s['op'] != 'joint_pose' or type(s['fraction']) not in (int, float) or not math.isfinite(s['fraction']) or not 0 <= s['fraction'] <= 1:
                    raise SceneError('Illegal joint pose', 'simulation_test')
            if not {0, .25, .5, .75, 1} <= {s['fraction'] for s in steps}:
                raise SceneError('Mandatory joint boundary poses missing', 'simulation_test')
    return plan


def write_test_plan(ir, output):
    plan = generate_test_plan(ir)
    write_json(Path(output)/'simulation_tests.json', plan)
    if any(m.get('ruleset') for m in plan['modules']):
        from .robot_io import rules_document
        write_json(Path(output)/'robot_io_rules.json', rules_document(ir))
    return plan


class UnsupportedProbe(SceneError):
    pass


def _id(model, kind, name):
    import mujoco
    result = mujoco.mj_name2id(model, getattr(mujoco.mjtObj, 'mjOBJ_'+kind), name)
    if result < 0:
        raise SceneError('Missing model '+kind.lower()+': '+str(name), 'simulation_test')
    return result


def _body(runtime, name):
    bid = _id(runtime.model, 'BODY', name)
    matrix = np.eye(4)
    matrix[:3, :3] = runtime.data.xmat[bid].reshape(3, 3)
    matrix[:3, 3] = runtime.data.xpos[bid]
    return matrix


def _frame(runtime, time_s):
    import mujoco
    from .mechanics import body_transforms
    mujoco.mj_forward(runtime.model, runtime.data)
    return {'time_s': time_s, 'simulation_time_s': float(runtime.data.time),
            'transforms': [p.tolist()+r.tolist() for p, r in zip(runtime.data.geom_xpos, runtime.data.geom_xmat)],
            'body_transforms': body_transforms(runtime.data),
            'joint_positions': {jid: float(runtime.data.qpos[runtime.model.jnt_qposadr[_id(runtime.model, 'JOINT', j['mjcf_joint'])]])
                for jid, j in runtime.meta['joints'].items() if j['type'] in ('slide', 'hinge')},
            'signals': dict(runtime.signals), 'properties': copy_properties(runtime), 'colors': runtime.model.geom_rgba.tolist()}


def copy_properties(runtime):
    return {cid:dict(state) for cid,state in runtime.properties.items()}


def _worlds(ir):
    components = {c['id']: c for c in ir['components']}
    assemblies = {a['child_component']: a for a in ir.get('assemblies', [])}
    interfaces = {f['id']: f for f in ir['interfaces']}
    world = {}
    def interface(fid):
        f = interfaces[fid]; p = values(f)
        if not all('position_'+a+'_m' in p for a in 'xyz'):
            raise UnsupportedProbe('AML 缺少安装接口局部坐标，无法独立核对：'+fid)
        angles = []
        for a in 'xyz':
            rec = f['parameters'].get('rotation_'+a+'_deg', {})
            angles.append(rec.get('value', 0) if rec.get('original_unit') else math.radians(rec.get('value', 0)))
        return pose_matrix({'position_m': [p['position_'+a+'_m'] for a in 'xyz'], 'rpy_rad': angles})
    def wt(cid):
        if cid not in world:
            c = components[cid]; mount = assemblies.get(cid)
            if mount and mount['placement'] == 'solve':
                world[cid] = wt(mount['parent_component'])@interface(mount['parent_interface'])@pose_matrix(mount.get('offset', {}))@np.linalg.inv(interface(mount['child_interface']))
            else:
                world[cid] = (wt(c['parent_id']) if c.get('parent_id') else np.eye(4))@pose_matrix(c['pose'])
        return world[cid]
    return wt, interface


def _joint_probe(ir, runtime, module, check, capture, cancelled=None):
    import mujoco
    j = next(j for j in ir['joints'] if j['id'] == module['target'])
    if j['type'] not in ('slide', 'hinge'):
        raise UnsupportedProbe('当前运动学检查器尚未覆盖 '+j['type'])
    m, d = runtime.model, runtime.data
    ji = _id(m, 'JOINT', 'j_'+j['id']); qa = m.jnt_qposadr[ji]
    parent_name = 'c_'+j['parent_body']; child_name = 'c_'+j['child_body']
    axis = np.array(j['axis'], dtype=float); axis /= np.linalg.norm(axis)
    check('joint_axis', np.allclose(m.jnt_axis[ji], axis, atol=1e-9), actual=m.jnt_axis[ji].tolist(), expected=axis.tolist())
    check('joint_type', int(m.jnt_type[ji]) == int(mujoco.mjtJoint.mjJNT_SLIDE if j['type'] == 'slide' else mujoco.mjtJoint.mjJNT_HINGE))
    check('joint_range', bool(m.jnt_limited[ji]) and np.allclose(m.jnt_range[ji], j['range'], atol=1e-9))
    check('joint_initial', abs(m.qpos0[qa]-j['initial']) < 1e-9)
    check('joint_anchor', np.allclose(m.jnt_pos[ji], j['child_anchor_m'], atol=1e-9))
    check('joint_child', m.jnt_bodyid[ji] == _id(m, 'BODY', child_name))
    check('joint_parent',m.body_parentid[_id(m,'BODY',child_name)]==_id(m,'BODY',parent_name))
    wt, _ = _worlds(ir)
    rest = np.linalg.inv(wt(j['parent_body']))@wt(j['child_body'])
    req = ir.get('simulation_requirements') or {}
    ptol = req.get('position_tolerance_m', .001); atol = req.get('angle_tolerance_rad', .001)
    # Dense FK samples preserve the articulated chain. Each segment starts at
    # the last measured pose, rather than teleporting to another extreme.
    params = values(next(c for c in ir['components'] if c['id'] == j['child_body']))
    velocity = params.get('max_velocity_rad_s' if j['type']=='hinge' else 'max_velocity_m_s', .5 if j['type']=='hinge' else .1)
    acceleration = params.get('max_acceleration_rad_s2' if j['type']=='hinge' else 'max_acceleration_m_s2', 1 if j['type']=='hinge' else .2)
    if velocity <= 0 or acceleration <= 0:
        raise UnsupportedProbe('机械回放需要正的速度和加速度限制')
    if module['steps'][0]['op']=='joint_configuration':
        return _configuration_probe(ir,runtime,module,check,capture,cancelled)
    targets = sorted({j['range'][0]+s['fraction']*(j['range'][1]-j['range'][0]) for s in module['steps']})+[j['initial']]
    elapsed = 0.; capture(elapsed)
    from .mechanics import RobotCollisionProbe, preflight_joint_path
    from .robot_io import ScenarioBlocked
    probe = RobotCollisionProbe(ir, runtime); rejected_plan = None
    if probe.scope['enabled']:
        rejected_plan = preflight_joint_path(runtime, j, targets, probe, cancelled)
        if rejected_plan:
            check('motion_plan_preflight', False, domain='scenario', evidence=rejected_plan,
                scope='requested_full_travel', execution='not_executed')
            # Relationship checks do not require driving into joint-space
            # obstacles. Retain the AML full-travel requirement as uncovered.
            span = min(math.radians(5) if j['type']=='hinge' else .01,
                (j['range'][1]-j['range'][0])*.1)
            tolerance = atol if j['type']=='hinge' else ptol
            lo, hi = max(j['range'][0],j['initial']-span), min(j['range'][1],j['initial']+span)
            if min(j['initial']-lo,hi-j['initial']) <= 2*tolerance:
                raise ScenarioBlocked('测试路径需重规划；局部激励不足以独立核对连接关系', 'simulation_test')
            targets = [lo, hi, j['initial']]
            local_rejection = preflight_joint_path(runtime,j,targets,probe,cancelled)
            if local_rejection:
                check('local_motion_preflight',False,domain='scenario',evidence=local_rejection,execution='not_executed')
                raise ScenarioBlocked('测试路径需重规划；局部路径也未通过预检，未执行运动', 'simulation_test')
            check('local_motion_preflight',True,scope='local_relationship_only',targets=targets)
        else:
            check('motion_plan_preflight',True,scope='requested_full_travel')
    for index, target in enumerate(targets):
        start = float(d.qpos[qa]); distance = abs(target-start)
        duration = max(math.pi*distance/(2*velocity), math.pi*math.sqrt(distance/(2*acceleration))) if distance else .1
        count = max(1, math.ceil(duration*30), math.ceil(math.pi*distance/(2*(math.radians(1) if j['type']=='hinge' else .002))))
        if count > 10000:
            raise UnsupportedProbe('声明的行程或速度超出当前连续采样预算')
        for sample in range(1, count+1):
            if cancelled:cancelled()
            d.qpos[qa] = start+(target-start)*(1-math.cos(math.pi*sample/count))/2
            mujoco.mj_forward(m, d)
            capture(elapsed+duration*sample/count)
        elapsed += duration
        q = float(d.qpos[qa])
        motion = np.eye(4); delta = q-j['initial']
        if j['type'] == 'slide':
            motion[:3, 3] = axis*delta
        else:
            cross = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
            rotation = np.eye(3)+math.sin(delta)*cross+(1-math.cos(delta))*(cross@cross)
            motion[:3, :3] = rotation
            anchor = np.asarray(j['child_anchor_m']); motion[:3, 3] = anchor-rotation@anchor
        actual = np.linalg.inv(_body(runtime, parent_name))@_body(runtime, child_name)
        expected = rest@motion
        pe = float(np.linalg.norm(actual[:3, 3]-expected[:3, 3]))
        ae = float(math.acos(np.clip((np.trace(expected[:3, :3].T@actual[:3, :3])-1)/2, -1, 1)))
        check('actual_pose:'+str(index), pe <= ptol and ae <= atol, q=q, position_error_m=pe, angle_error_rad=ae)
    if rejected_plan:
        raise ScenarioBlocked('局部连接关系已核对；全行程测试路径需重规划，未执行有干涉的动作', 'simulation_test')


def _configuration_probe(ir,runtime,module,check,capture,cancelled):
    import mujoco
    from .test_safety import scalar_joints,preflight_configurations
    from .mechanics import DeviceCollisionProbe
    from .robot_io import ScenarioBlocked
    joints=scalar_joints(ir);m,d=runtime.model,runtime.data;probe=DeviceCollisionProbe(ir,runtime)
    rejected=preflight_configurations(ir,runtime,module['steps'],probe,cancelled)
    if rejected:
        check('motion_plan_preflight',False,domain='scenario',evidence=rejected,execution='not_executed')
        raise ScenarioBlocked('Agent 测试路径未通过预检，需重规划；未执行有干涉的动作','simulation_test')
    check('motion_plan_preflight',True,scope='agent_multi_joint_path')
    addresses={ident:int(m.jnt_qposadr[_id(m,'JOINT','j_'+ident)]) for ident in joints}
    wt,_=_worlds(ir);j=joints[module['target']];rest=np.linalg.inv(wt(j['parent_body']))@wt(j['child_body'])
    axis=np.array(j['axis'],dtype=float);axis/=np.linalg.norm(axis)
    components={c['id']:c for c in ir['components']};req=ir.get('simulation_requirements') or {};elapsed=0.;capture(0)
    for index,waypoint in enumerate(module['steps']):
        start={ident:float(d.qpos[a]) for ident,a in addresses.items()};target=waypoint['positions'];duration=.1;count=1
        for ident,joint in joints.items():
            p=values(components[joint['child_body']]);angular=joint['type']=='hinge';distance=abs(target[ident]-start[ident])
            v=p.get('max_velocity_rad_s' if angular else 'max_velocity_m_s',.5 if angular else .1)
            a=p.get('max_acceleration_rad_s2' if angular else 'max_acceleration_m_s2',1 if angular else .2)
            if v<=0 or a<=0:raise UnsupportedProbe('路径关节需要正的速度和加速度限制：'+ident)
            duration=max(duration,math.pi*distance/(2*v),math.pi*math.sqrt(distance/(2*a)))
            count=max(count,math.ceil(math.pi*distance/(2*(math.radians(.5) if angular else .001))))
        count=max(count,math.ceil(duration*30))
        if count>10000:raise UnsupportedProbe('Agent 路径超出连续采样预算')
        for sample in range(1,count+1):
            if cancelled:cancelled()
            fraction=(1-math.cos(math.pi*sample/count))/2
            for ident,a in addresses.items():d.qpos[a]=start[ident]+fraction*(target[ident]-start[ident])
            mujoco.mj_forward(m,d);capture(elapsed+duration*sample/count)
        elapsed+=duration;delta=float(d.qpos[addresses[j['id']]])-j['initial'];motion=np.eye(4)
        if j['type']=='slide':motion[:3,3]=axis*delta
        else:
            cross=np.array([[0,-axis[2],axis[1]],[axis[2],0,-axis[0]],[-axis[1],axis[0],0]])
            rotation=np.eye(3)+math.sin(delta)*cross+(1-math.cos(delta))*(cross@cross);motion[:3,:3]=rotation
            anchor=np.asarray(j['child_anchor_m']);motion[:3,3]=anchor-rotation@anchor
        actual=np.linalg.inv(_body(runtime,'c_'+j['parent_body']))@_body(runtime,'c_'+j['child_body']);expected=rest@motion
        pe=float(np.linalg.norm(actual[:3,3]-expected[:3,3]));ae=float(math.acos(np.clip((np.trace(expected[:3,:3].T@actual[:3,:3])-1)/2,-1,1)))
        check('actual_pose:'+str(index),pe<=req.get('position_tolerance_m',.001) and ae<=req.get('angle_tolerance_rad',.001),position_error_m=pe,angle_error_rad=ae)


def _mount_probe(ir, runtime, module, check, capture):
    import mujoco
    a = next(a for a in ir['assemblies'] if a['id'] == module['target'])
    _, interface = _worlds(ir)
    req = ir.get('simulation_requirements') or {}; tolerance = req.get('position_tolerance_m', .001)
    for index in range(4):
        if index:
            # Excite the whole model through simulation; a detached body must
            # not pass merely because its initial transform happens to align.
            runtime.data.xfrc_applied[_id(runtime.model, 'BODY', 'c_'+a['child_component']), 0] = 10
            runtime.test_physics_step(50)
        mujoco.mj_forward(runtime.model, runtime.data)
        expected = _body(runtime, 'c_'+a['parent_component'])@interface(a['parent_interface'])@pose_matrix(a.get('offset', {}))
        actual = _body(runtime, 'c_'+a['child_component'])@interface(a['child_interface'])
        pe = float(np.linalg.norm(expected[:3, 3]-actual[:3, 3]))
        ae = float(math.acos(np.clip((np.trace(expected[:3, :3].T@actual[:3, :3])-1)/2, -1, 1)))
        check('fixed_transform:'+str(index), pe <= tolerance and ae <= req.get('angle_tolerance_rad', .001), position_error_m=pe, angle_error_rad=ae)
        capture(index*.2)


def _coupling_probe(ir, runtime, module, check, capture):
    import mujoco
    c = next(c for c in ir['couplings'] if c['id'] == module['target'])
    eq = _id(runtime.model, 'EQUALITY', c['id'])
    leader = _id(runtime.model, 'JOINT', 'j_'+c['leader']); follower = _id(runtime.model, 'JOINT', 'j_'+c['follower'])
    check('coupling_definition', int(runtime.model.eq_type[eq]) == int(mujoco.mjtEq.mjEQ_JOINT)
          and runtime.model.eq_obj1id[eq] == follower and runtime.model.eq_obj2id[eq] == leader
          and bool(runtime.data.eq_active[eq]) and np.allclose(runtime.model.eq_data[eq, :5], [0, c['ratio'], 0, 0, 0]))
    joints = {j['id']: j for j in ir['joints']}; j = joints[c['leader']]
    # Apply force to the actual follower and let the physical constraint act.
    for index, fraction in enumerate((.25, .75)):
        runtime.data.qpos[runtime.model.jnt_qposadr[leader]] = j['range'][0]+fraction*(j['range'][1]-j['range'][0])
        runtime.data.qpos[runtime.model.jnt_qposadr[follower]] = c['offset']+c['ratio']*runtime.data.qpos[runtime.model.jnt_qposadr[leader]]
        runtime.data.qvel[:] = 0
        runtime.data.qfrc_applied[runtime.model.jnt_dofadr[follower]] = 1
        runtime.test_physics_step(100)
        q1 = float(runtime.data.qpos[runtime.model.jnt_qposadr[leader]]); q2 = float(runtime.data.qpos[runtime.model.jnt_qposadr[follower]])
        error = abs(q2-(c['offset']+c['ratio']*q1))
        req = ir.get('simulation_requirements') or {}
        tol = req.get('angle_tolerance_rad', .001) if joints[c['follower']]['type'] == 'hinge' else req.get('position_tolerance_m', .001)
        check('actual_coupling:'+str(index), error <= tol, error=error, leader=q1, follower=q2)
        capture(index*.5)


def _io_probe(ir, runtime, module, check, capture, cancelled):
    import mujoco
    p = next(p for p in ir['io'] if p['name'] == module['target'])
    actual = runtime.points.get(p['name'])
    fields = ('dir', 'type', 'address', 'component_id', 'quantity', 'writer', 'physical_per_plc_unit')
    check('io_declaration', actual is not None and all(actual.get(k) == p.get(k) for k in fields), expected={k: p.get(k) for k in fields}, actual=actual)
    bindings = [b for b in runtime.meta['io_map'] if b['plc_var'] == p['name']]
    check('io_map', len(bindings) == 1 and all(bindings[0].get(k) == p.get(k) for k in ('dir', 'type', 'address', 'writer', 'physical_per_plc_unit'))
          and bindings[0]['bind'] == {'asset': p['component_id'], 'quantity': p['quantity']})
    if actual is None:
        return
    wrong_value = 1 if p['type'] == 'BOOL' else True
    try:runtime._check_value(actual, wrong_value)
    except ValueError:check('io_type_rejection', True)
    else:check('io_type_rejection', False)
    if p['type'] == 'INT' and p.get('range'):
        for invalid in (p['range'][0]-1, p['range'][1]+1):
            try:runtime._check_value(actual, invalid)
            except ValueError:check('io_range_rejection:'+str(invalid), True)
            else:check('io_range_rejection:'+str(invalid), False)
    try:
        if p['writer'] == 'simulation':runtime.set_inputs({p['name']: p['initial']})
        elif p['dir'] == 'input':runtime.step({p['name']: p['initial']})
        else:runtime.set_inputs({p['name']: p['initial']})
    except ValueError:check('io_writer_ownership', True)
    else:check('io_writer_ownership', False)
    cid = p['component_id']; component = next(c for c in ir['components'] if c['id'] == cid)
    params = values(component); quantity = p['quantity']
    if module.get('ruleset'):
        from .robot_io import execute
        return execute(ir,runtime,module,p,check,capture,cancelled)
    # Reference kernels have no encoded unit conversion. Never claim to have
    # tested a nontrivial scale by merely echoing the declared scale back.
    if p.get('physical_per_plc_unit', 1) != 1 and quantity!='delay_setpoint':
        raise UnsupportedProbe('此物理量尚无单位编码检查器：'+p['name'])
    counter = 0
    def step(outputs=None, inputs=None, count=1):
        nonlocal counter
        for _ in range(count):
            if cancelled:cancelled()
            runtime.step(outputs, inputs); counter += 1
            if counter % 25 == 0:capture(float(runtime.data.time))
    def physical_point(q):
        return next((x for x in ir['io'] if x['component_id'] == cid and x['quantity'] == q), None)
    def commanded(q, value):
        port = physical_point(q)
        if port is None:raise UnsupportedProbe('AML 缺少测试所需指令 '+q)
        return {port['name']: value}
    capture(0)
    if p['writer'] in ('operator', 'fault_harness'):
        for sample in module['steps']:
            # Apply the externally owned contact through the runtime API.
            step(inputs={p['name']: sample['value']}, count=200)
            check('input_boundary:'+str(sample['value']), runtime.signals[p['name']] == sample['value'])
            if quantity=='delay_setpoint':
                actual_delay=runtime.properties.get(cid,{}).get('delay_s')
                check('encoded_physical_property:'+str(sample['value']), actual_delay==sample['value']*p['physical_per_plc_unit'],
                    expected_s=sample['value']*p['physical_per_plc_unit'],actual_s=actual_delay)
        # These points are externally owned input contacts, not sensors.
        if quantity=='fault' and params.get('driven_joint_id'):
            ji=_id(runtime.model,'JOINT','j_'+params['driven_joint_id']);qa=runtime.model.jnt_qposadr[ji]
            step(commanded('extend_cmd',True),{p['name']:False},count=50)
            before=float(runtime.data.qpos[qa])
            step(commanded('extend_cmd',True),{p['name']:True},count=100)
            drift=abs(float(runtime.data.qpos[qa])-before)
            check('fault_holds_actual_joint',drift <= (ir.get('simulation_requirements') or {}).get('position_tolerance_m',.002),drift_m=drift)
        elif quantity=='fault' and runtime.meta['components'][cid]['behavior']=='conveyor':
            sources=[edge['from'] for edge in runtime.meta['material_flow'] if edge['to']==cid]
            source=next((x for x in ir['io'] if x['component_id'] in sources and x['quantity']=='emit_cmd'),None)
            if source is None:raise UnsupportedProbe('故障输送测试需要连接的实际料源')
            step({source['name']:True},count=150)
            step({source['name']:False,**commanded('run_cmd',True)},{p['name']:False},count=150)
            item=next((x for x in runtime.products if x['active']),None)
            if item is None:raise SceneError('故障测试缺少实际工件')
            step(commanded('run_cmd',True),{p['name']:True},count=250)
            speed=float(np.linalg.norm(runtime.data.qvel[item['v']:item['v']+3]))
            check('fault_stops_actual_transport',speed<.05,speed_m_s=speed)
    elif quantity in ('extend_cmd', 'extended', 'retracted'):
        joint = next(j for j in ir['joints'] if j['id'] == params.get('driven_joint_id'))
        ji = _id(runtime.model, 'JOINT', 'j_'+joint['id']); qa = runtime.model.jnt_qposadr[ji]
        if quantity == 'extend_cmd':
            duration = (ir.get('simulation_requirements') or {}).get('response_timeout_s', 3)
            for sample in module['steps']:
                value = sample['value']; before = runtime.data.qpos.copy()
                step(commanded('extend_cmd', value), count=math.ceil(duration/runtime.model.opt.timestep))
                q = float(runtime.data.qpos[qa]); target = params['command_true_target_m' if value else 'command_false_target_m']
                tolerance = (ir.get('simulation_requirements') or {}).get('position_tolerance_m', .002)
                check('command_actual_position:'+str(value), abs(q-target) <= tolerance, expected=target, actual=q)
                for other in ir['joints']:
                    if other['id'] == joint['id'] or other['id'] in {c['follower'] for c in ir.get('couplings', [])}:continue
                    oi = _id(runtime.model, 'JOINT', 'j_'+other['id']); oq = runtime.model.jnt_qposadr[oi]
                    check('channel_isolation:'+other['id'], abs(float(runtime.data.qpos[oq]-before[oq])) <= tolerance)
        else:
            threshold = params['extended_threshold_m' if quantity == 'extended' else 'retracted_threshold_m']
            eps = min(.0001, (joint['range'][1]-joint['range'][0])/1000)
            for index, q in enumerate((joint['range'][0], threshold-eps, threshold, threshold+eps, joint['range'][1])):
                runtime.data.qpos[qa] = float(np.clip(q, *joint['range'])); runtime.data.qvel[:] = 0
                # Observe the actual post-step q; the servo can move at an edge.
                step(count=1); measured = float(runtime.data.qpos[qa])
                expected = measured >= threshold if quantity == 'extended' else measured <= threshold
                check('feedback_threshold:'+str(index), runtime.signals[p['name']] == expected, actual_q=measured, expected=expected)
                capture(index*.25)
    elif quantity in ('green_cmd', 'red_cmd'):
        bid = _id(runtime.model, 'BODY', 'c_'+cid)
        gids = list(range(runtime.model.body_geomadr[bid], runtime.model.body_geomadr[bid]+runtime.model.body_geomnum[bid]))
        if not gids and runtime.meta['components'][cid]['behavior']!='panel':raise UnsupportedProbe('指示灯没有可观察几何')
        for index, sample in enumerate(module['steps']):
            other = physical_point('red_cmd' if quantity == 'green_cmd' else 'green_cmd')
            output = {p['name']: sample['value']}
            if other:output[other['name']] = False
            step(output, count=20)
            channel = 1 if quantity == 'green_cmd' else 0
            state=runtime.properties.get(cid,{})
            check('indicator_property:'+str(index), state.get('green' if channel==1 else 'red')==sample['value']
                  and state.get('red' if channel==1 else 'green') is False)
            if gids:check('indicator_color:'+str(index), all(abs(runtime.model.geom_rgba[g, channel]-(.9 if sample['value'] else .12)) < 1e-5 for g in gids))
            capture(float(runtime.data.time))
    elif quantity == 'emit_cmd':
        for index, sample in enumerate(module['steps']):
            before = len([e for e in runtime.events if e['event'] == 'spawn' and e['source_id'] == cid])
            step({p['name']: sample['value']}, count=50)
            after = len([e for e in runtime.events if e['event'] == 'spawn' and e['source_id'] == cid])
            check('source_edge:'+str(index), after-before == int(sample['value']))
    elif quantity == 'run_cmd':
        sources = [edge['from'] for edge in runtime.meta['material_flow'] if edge['to'] == cid]
        source = next((x for x in ir['io'] if x['component_id'] in sources and x['quantity'] == 'emit_cmd'), None)
        if source is None:raise UnsupportedProbe('输送测试需要 AML 连接的料源')
        step({source['name']: True}, count=150)
        item = next((x for x in runtime.products if x['active']), None)
        if item is None:raise SceneError('料源未生成实际工件')
        direction = _body(runtime, 'c_'+cid)[:3, :3]@np.array([params['conveying_direction_'+a] for a in 'xyz'])
        direction /= np.linalg.norm(direction)
        for index, sample in enumerate(module['steps']):
            start = runtime.data.xpos[item['bid']].copy()
            step({source['name']: False, p['name']: sample['value']}, count=300)
            displacement = float((runtime.data.xpos[item['bid']]-start)@direction)
            speed = float(runtime.data.qvel[item['v']:item['v']+3]@direction)
            check('conveyor_response:'+str(index), displacement > .02 if sample['value'] else abs(speed) < .05, displacement_m=displacement, speed_m_s=speed)
    elif quantity == 'detect':
        sensor = pose_matrix(component['pose'])
        wt, _ = _worlds(ir); sensor = wt(cid)
        origin = sensor[:3, 3]+sensor[:3, :3]@np.array([params['beam_origin_local_'+a+'_m'] for a in 'xyz'])
        direction = sensor[:3, :3]@np.array([params['beam_direction_'+a] for a in 'xyz']); direction /= np.linalg.norm(direction)
        if not runtime.products:raise UnsupportedProbe('射线反馈测试需要可放置的实际工件')
        item = runtime.products[0]
        for index, offset in enumerate((0, 10)):
            position = origin+direction*min(params['range_m']*.5, .1)+np.array([offset, 0, 0])
            runtime.data.qpos[item['q']:item['q']+7] = np.r_[position, [1, 0, 0, 0]]
            runtime.data.qvel[:] = 0; item['active'] = True
            for g in item['geoms']:runtime.model.geom_contype[g] = 1; runtime.model.geom_conaffinity[g] = 1
            step(); mujoco.mj_forward(runtime.model, runtime.data)
            gid = np.zeros(1, dtype=np.int32)
            distance = mujoco.mj_ray(runtime.model, runtime.data, origin, direction, np.array([1, 1, 1, 1, 1, 0], dtype=np.uint8), 1, -1, gid)
            expected = bool(0 <= distance <= params['range_m'])
            check('ray_actual_geometry:'+str(index), runtime.signals[p['name']] == expected, expected=expected)
            capture(index*.5)
    elif quantity == 'count':
        if not runtime.products:raise UnsupportedProbe('计数反馈测试需要实际工件')
        wt, _ = _worlds(ir); item = runtime.products[0]
        runtime.data.qpos[item['q']:item['q']+7] = np.r_[wt(cid)[:3, 3], [1, 0, 0, 0]]
        runtime.data.qvel[:] = 0; item['active'] = True
        for g in item['geoms']:runtime.model.geom_contype[g] = 1; runtime.model.geom_conaffinity[g] = 1
        initial = runtime.signals[p['name']]
        step(count=1)
        check('counter_actual_entry', runtime.signals[p['name']] == initial+1)
        step(count=25)
        check('counter_no_duplicate', runtime.signals[p['name']] == initial+1)
        # No direct writes to feedback or fabricated maximum counts.
    else:
        raise UnsupportedProbe('尚无物理响应检查器：'+str(quantity))
    capture(max(float(runtime.data.time), .01))


def run_test_plan(ir, metadata, xml, plan, selected=None, *, output=None, cancelled=None, transport=None):
    from .verification import enabled
    if enabled(ir):
        from .fixed_tests import run
        return run(ir,metadata,xml,plan,selected,output=output,cancelled=cancelled,transport=transport)
    from runtime.device_scene import DeviceRuntime
    from .mechanics import RobotCollisionProbe
    from .robot_io import ScenarioBlocked
    validate_test_plan(ir, plan)
    if metadata.get('geometry_only'):
        raise SceneError('静态几何模型不能运行仿真测试', 'simulation_test')
    if metadata['device_ir_sha256'] != facts_digest(ir):
        raise SceneError('测试要求与模型来源版本不一致', 'simulation_test')
    modules = {m['id']: m for m in plan['modules']}
    full_request=selected is None
    selected = list(modules) if full_request else selected
    if not isinstance(selected, list) or (not selected and not full_request) or any(not isinstance(s,str) or s not in modules for s in selected) or len(selected) != len(set(selected)):
        raise SceneError('请选择当前模型中有效且不重复的测试模块', 'simulation_test')
    results = []
    for ident in selected:
        if cancelled:cancelled()
        module = modules[ident]; checks = []; frames = []
        def check(name, passed, **detail):
            detail.setdefault('domain', 'binding' if module['kind']=='io_binding' else 'mechanical')
            checks.append({'name': name, 'passed': bool(passed), **detail})
        runtime = None; collision_probe = None
        class Interference(Exception):
            pass
        def capture(time_s):
            # Playback time is monotonic even for isolated kinematic poses.
            frame = _frame(runtime, max(time_s, frames[-1]['time_s']+.001 if frames else 0))
            contacts = collision_probe.collisions() if collision_probe else []
            frame['collisions'] = contacts; frames.append(frame)
            if contacts:
                check('collision_free_path', False, domain='scenario', time_s=frame['time_s'], contacts=contacts)
                if module.get('ruleset'):
                    raise ScenarioBlocked('IO 工况出现干涉，不能据此判定绑定错误', 'simulation_test', {'contacts':contacts})
                raise Interference()
        blocked = None
        try:
            runtime=DeviceRuntime(metadata,xml)
            collision_probe = RobotCollisionProbe(ir, runtime)
            import mujoco
            normal_step=runtime.step
            def guarded(call):
                if cancelled:cancelled()
                contacts=collision_probe.collisions()
                if contacts:
                    check('collision_free_path',False,domain='scenario',contacts=contacts,execution='not_executed')
                    raise ScenarioBlocked('测试动作开始前存在部件干涉，需修订工况或模型','simulation_test',{'contacts':contacts})
                saved_q=runtime.data.qpos.copy();saved_v=runtime.data.qvel.copy();saved_time=float(runtime.data.time)
                result=call();mujoco.mj_forward(runtime.model,runtime.data);contacts=collision_probe.collisions()
                if contacts:
                    runtime.data.qpos[:]=saved_q;runtime.data.qvel[:]=saved_v;runtime.data.time=saved_time
                    mujoco.mj_forward(runtime.model,runtime.data)
                    check('collision_free_path',False,domain='scenario',contacts=contacts,execution='sandbox_step_rejected')
                    raise ScenarioBlocked('测试动作产生部件干涉，已拦截且不生成该动作的回放帧','simulation_test',{'contacts':contacts})
                return result
            def guarded_step(*args,**kwargs):return guarded(lambda:normal_step(*args,**kwargs))
            def physics_steps(count):
                for _ in range(count):guarded(lambda:mujoco.mj_step(runtime.model,runtime.data))
            runtime.step=guarded_step;runtime.test_physics_step=physics_steps
            if module['kind'] == 'mechanical_joint':_joint_probe(ir, runtime, module, check, capture, cancelled)
            elif module['kind'] == 'fixed_mount':_mount_probe(ir, runtime, module, check, capture)
            elif module['kind'] == 'joint_coupling':_coupling_probe(ir, runtime, module, check, capture)
            elif module['kind']=='io_binding':_io_probe(ir, runtime, module, check, capture, cancelled)
            else:raise UnsupportedProbe('AML 要求的验证对象不存在，不能完成此验证范围')
            if collision_probe.scope['enabled']:
                check('collision_free_path', True, sampled_frames=len(frames), scope=collision_probe.scope)
        except ScenarioBlocked as exc:
            blocked = str(exc)
            if not any(c['domain']=='scenario' and not c['passed'] for c in checks):
                check('io_scenario_precondition' if module.get('ruleset') else 'motion_scenario_precondition', False, domain='scenario', detail=str(exc), evidence=getattr(exc,'details',{}))
        except Interference:
            pass
        except UnsupportedProbe as exc:
            blocked = str(exc)
        except (ValueError, KeyError, StopIteration, OSError) as exc:
            check('execution', False, detail=str(exc))
        if runtime is not None:
            check('physics_state',np.isfinite(runtime.data.qpos).all() and np.isfinite(runtime.data.qvel).all()
                  and not any(w.number for w in runtime.data.warning), domain='response' if module.get('ruleset') else 'mechanical')
        rejected_motion = module['kind']=='mechanical_joint' and blocked and any(c['name']=='motion_plan_preflight' and not c['passed'] for c in checks)
        failed = any(not c['passed'] and (not blocked or c['domain']!='scenario') for c in checks)
        status = 'failed' if failed else 'blocked' if blocked or not checks else 'passed'
        replay_name = 'test_replays/'+ident+'.json'
        result = {**{k: v for k, v in module.items() if k != 'steps'}, 'status': status,
                  'checks': checks, 'uncovered': blocked, 'frame_count': len(frames), 'replay': replay_name if frames else None}
        if collision_probe and collision_probe.scope['enabled']:result['collision_scope'] = collision_probe.scope
        if module.get('ruleset'):
            binding_errors = any(not c['passed'] and c['domain']=='binding' for c in checks)
            response_errors = any(not c['passed'] and c['domain']=='response' for c in checks)
            result.update(ruleset=module['ruleset'], script_origin=module['script_origin'],
                binding_status='failed' if binding_errors else 'inconclusive' if blocked or response_errors else 'passed',
                response_status='failed' if response_errors else 'incomplete' if blocked else 'passed',
                scenario_status='blocked' if blocked else 'clear', mechanical_relationship_status='not_assessed',
                failure_category='io_binding_error' if binding_errors else 'scenario_blocked' if blocked else 'response_failed' if response_errors else None)
        elif module['kind']=='mechanical_joint':
            # FK/definition evidence, path validity and numerical health answer
            # different questions. A rejected stimulus is not a broken joint.
            relationship_checks = [c for c in checks if c['domain']=='mechanical'
                                   and c['name'] not in ('physics_state','execution','collision_free_path')]
            posed = any(c['name'].startswith('actual_pose:') for c in checks)
            relationship_failed = any(not c['passed'] for c in relationship_checks)
            scenario_failed = bool(blocked) or any(c['domain']=='scenario' and not c['passed'] for c in checks)
            numerical_failed = any(c['name']=='physics_state' and not c['passed'] for c in checks)
            result.update(failure_category='mechanical_relationship_error' if relationship_failed else
                'motion_plan_rejected' if scenario_failed else 'numerical_error' if numerical_failed else
                'execution_error' if status=='failed' else None,
                full_travel_status='passed' if status=='passed' else 'not_covered',
                mechanical_relationship_status='failed' if relationship_failed else 'passed' if posed else 'inconclusive',
                scenario_status='blocked' if scenario_failed else 'clear',
                response_status='not_assessed')
            if rejected_motion:result['motion_scope']='local_relationship_only'
        elif any(not c['passed'] and c['domain']=='scenario' for c in checks):
            result['failure_category']='scenario_blocked'
        if module['kind']=='mechanical_joint' and module['steps'][0]['op']=='joint_configuration':
            result.update(motion_scope='agent_joint_configuration_path',
                full_travel_status='passed' if status=='passed' else 'not_covered')
        results.append(result)
        if output and frames:
            write_json(Path(output)/replay_name, {'source': 'fixed_mujoco_test_checker', 'test_id': ident,
                'device_ir_sha256': facts_digest(ir), 'model_xml_sha256': metadata['model_xml_sha256'],
                'definition_sha256': digest(metadata), 'plan_sha256': digest(plan), 'plc_acceptance': 'not_run', 'frames': frames})
    all_run = bool(modules) and set(selected) == set(modules)
    status = 'failed' if any(r['status'] == 'failed' for r in results) else 'blocked' if not results or any(r['status'] == 'blocked' for r in results) else 'passed'
    report = {'format': FORMAT, 'status': status, 'complete': all_run and status == 'passed',
              'scope': 'independent_device_tests', 'plc_acceptance': 'not_run', 'modules': results,
              'device_ir_sha256': facts_digest(ir), 'scene_sha256': metadata['model_xml_sha256'],
              'definition_sha256': digest(metadata), 'plan_sha256': digest(plan),
              'checker_sha256': checker_digest()}
    io_results = [r for r in results if r.get('ruleset')]
    if io_results:
        report['io_binding_summary']={k:sum(r['binding_status']==k for r in io_results) for k in ('passed','failed','inconclusive')}
        report['io_binding_summary'].update(total=len(io_results), scope='local_robot_io_binding_not_mechanical_relationship_acceptance')
    if output:write_json(Path(output)/'simulation_test_results.json', report)
    return report
