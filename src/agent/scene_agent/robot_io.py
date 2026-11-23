"""Versioned robot IO rules, Agent stimulus programs and independent oracles.

Binding verification uses local legal excitation, not full workspace travel.
Expected values always come from DeviceIR and measured MuJoCo state.
"""
import math

import mujoco
import numpy as np

from .common import SceneError, digest
from .device_ir import facts_digest, values

FORMAT = '308sjk.robot_io_rules/1'
RULES = {
    'declaration': 'Exact AML name/type/direction/address/writer/quantity/component/scale and port ownership',
    'routing': 'Command actuator transmission must reference the AML controlled joint',
    'command': 'Positive/negative local commands must change the intended target and measured joint',
    'isolation': 'Undeclared command targets must remain unchanged; physical drift is response evidence',
    'feedback': 'Encode measured q using AML scale and declared encoder saturation, never command echo',
    'in_position': 'Measured absolute angle error against the command and AML position tolerance',
    'controller': 'Enable/estop/fault latch/reset truth table from fixed rules',
    'contact': 'Externally owned contact transitions preserve type, owner and exact binding',
    'rejection': 'Out-of-range PLC values are rejected without advancing the plant',
    'scenario': 'Illegal/colliding test states are blocked scenarios, not proof of a binding or mechanical defect',
}


class ScenarioBlocked(SceneError):
    pass


def contracts(ir):
    """Recognize the joint-space IO profile solely from authoritative AML facts."""
    ports = {(p['component_id'], p['quantity']): p for p in ir['io']}
    components = {c['id']: c for c in ir['components']}
    roots = {cid for cid, c in components.items() if values(c).get('estop_io_name')
        and (cid, 'enable_cmd') in ports and (cid, 'reset_cmd') in ports}
    result = {}
    for point in ir['io']:
        cid = point['component_id']; quantity = point['quantity']; owner = cid; seen = set()
        while owner and owner not in roots and owner not in seen:
            seen.add(owner); owner = components.get(owner, {}).get('parent_id')
        # Operator panel inputs belong to the robot profile only if that robot
        # actually declares this panel's estop contact.
        if owner not in roots and point['writer'] == 'operator':
            owner = next((root for root in roots if any(p['name'] == values(components[root])['estop_io_name']
                and p['component_id'] == cid for p in ir['io'])), None)
        if owner not in roots:
            continue
        record = {'point': point['name'], 'component_id': cid, 'owner': owner,
            'quantity': quantity, 'required_rules': ['declaration']}
        if quantity in ('joint_position_cmd', 'joint_position_fb', 'in_position'):
            params = values(components[cid]); joint = next((j for j in ir['joints'] if j['id'] == params.get('driven_joint_id')), None)
            command = ports.get((cid, 'joint_position_cmd')); feedback = ports.get((cid, 'joint_position_fb'))
            if not joint or joint['type'] != 'hinge' or not command or not feedback:
                continue
            scale = command['physical_per_plc_unit']; tolerance = params['position_tolerance_rad']
            if not math.isfinite(scale) or scale <= 0:
                raise SceneError('Robot IO rules require positive angular command scaling', 'simulation_test')
            # A diagnostic envelope, not a new mechanical limit or safety claim.
            span = min(math.radians(5), (joint['range'][1]-joint['range'][0])*.1)
            lo = max(command['range'][0], math.ceil(max(joint['range'][0], joint['initial']-span)/scale))
            hi = min(command['range'][1], math.floor(min(joint['range'][1], joint['initial']+span)/scale))
            home = round(joint['initial']/scale)
            record.update(kind='command' if quantity == 'joint_position_cmd' else 'feedback',
                joint_id=joint['id'], command=command['name'], feedback=feedback['name'],
                in_position=ports[(cid, 'in_position')]['name'], initial_rad=joint['initial'],
                command_scale=scale, position_tolerance_rad=tolerance,
                raw_window=[lo, hi], home_raw=home, position_window_rad=[lo*scale, hi*scale],
                feedback_scale=feedback['physical_per_plc_unit'], feedback_range=feedback['range'])
            record['required_rules'] += ['routing', 'command', 'isolation', 'rejection'] if record['kind'] == 'command' else ['feedback', 'in_position']
        elif quantity in ('enable_cmd', 'reset_cmd', 'ready', 'fault') and cid in roots:
            record.update(kind='controller'); record['required_rules'].append('controller')
        elif point['writer'] == 'operator' and point['type'] == 'BOOL':
            record.update(kind='contact'); record['required_rules'].append('contact')
        else:
            continue
        result[point['name']] = record
    return result


def rules_document(ir):
    return {'format': FORMAT, 'device_ir_sha256': facts_digest(ir), 'rules': RULES,
        'diagnostic_window': 'initial +/- min(5 degrees, 10 percent of joint travel), intersect PLC and physical ranges',
        'scope': 'IO declaration, binding and local response; not mechanical workspace or full-stroke acceptance',
        'contracts': contracts(ir)}


def default_steps(contract, point):
    """Explicit offline baseline; the live author replaces this entire program."""
    kind = contract['kind']
    if kind == 'command':
        lo, hi = contract['raw_window']
        return [{'op': 'drive', 'value': v} for v in (hi, lo, contract['home_raw'])]+[
            {'op': 'reject', 'value': point['range'][0]-1}, {'op': 'reject', 'value': point['range'][1]+1}]
    if kind == 'feedback':
        lo, hi = contract['position_window_rad']; home = contract['home_raw']*contract['command_scale']
        tol = contract['position_tolerance_rad']
        return [{'op': 'observe', 'position_rad': q, 'reference_value': contract['home_raw']}
            for q in (lo, hi, home, home+tol*.5, home+tol*1.5)]
    if kind == 'controller':
        return [{'op': 'control', 'enable': e, 'reset': r, 'estop_ok': s}
            for e, r, s in ((False, False, True), (True, False, True), (True, True, False),
                (True, False, True), (True, True, True), (False, False, True))]
    return [{'op': 'contact', 'value': v} for v in (False, True, False)]


def validate_steps(contract, point, steps):
    """Check type, scope and meaningful stimulus coverage before execution."""
    def invalid(message):raise SceneError('Invalid robot IO script '+point['name']+': '+message, 'simulation_test', {'failure_domain': 'script'})
    if not isinstance(steps, list) or not 3 <= len(steps) <= 20:
        invalid('expected 3..20 stimulus cases')
    kind = contract['kind']; driven = []; observed = []; outcomes = set(); transitions = set(); latched = False
    rejected = set()
    for step in steps:
        if not isinstance(step, dict):invalid('case must be an object')
        op = step.get('op')
        if kind == 'command' and op in ('drive', 'reject'):
            if set(step) != {'op', 'value'} or type(step['value']) is not int:invalid('integer drive/reject value required')
            value = step['value']
            if op == 'reject':
                if point['range'][0] <= value <= point['range'][1]:invalid('rejection case must be outside PLC range')
                rejected.add('low' if value < point['range'][0] else 'high')
            else:
                if not contract['raw_window'][0] <= value <= contract['raw_window'][1]:invalid('normal motion exceeds local diagnostic window; use reject for invalid inputs')
                driven.append(value)
        elif kind == 'feedback' and op == 'observe':
            if set(step) != {'op', 'position_rad', 'reference_value'}:invalid('observe fields differ from rules')
            q, raw = step['position_rad'], step['reference_value']
            if type(q) not in (int, float) or not math.isfinite(q) or type(raw) is not int:invalid('finite measured-state stimulus and integer reference required')
            if not contract['position_window_rad'][0] <= q <= contract['position_window_rad'][1] or not contract['raw_window'][0] <= raw <= contract['raw_window'][1]:invalid('feedback state outside diagnostic window')
            observed.append(q); error = abs(q-raw*contract['command_scale']); tol = contract['position_tolerance_rad']
            if error <= tol*.75:outcomes.add('inside')
            if tol*1.25 <= error <= tol*2:outcomes.add('outside_near_threshold')
        elif kind == 'controller' and op == 'control':
            if set(step) != {'op', 'enable', 'reset', 'estop_ok'} or any(type(step[k]) is not bool for k in ('enable', 'reset', 'estop_ok')):invalid('control needs three boolean inputs')
            was_latched = latched
            if not step['estop_ok']:latched = True
            elif step['reset']:latched = False
            if not step['enable'] and not latched:transitions.add('disabled')
            if step['enable'] and step['estop_ok'] and not latched:transitions.add('enabled')
            if not step['estop_ok'] and step['reset']:transitions.add('active_cause_cannot_reset')
            if was_latched and step['estop_ok'] and not step['reset']:transitions.add('latch')
            if was_latched and step['estop_ok'] and step['reset']:transitions.add('reset_after_clear')
        elif kind == 'contact' and op == 'contact':
            if set(step) != {'op', 'value'} or type(step['value']) is not bool:invalid('contact needs boolean value')
            outcomes.add(step['value'])
        else:invalid('operation is not permitted for this channel')
    if kind == 'command':
        delta = 2*contract['position_tolerance_rad']/contract['command_scale']; home = contract['home_raw']
        if not driven or min(driven) > home-delta or max(driven) < home+delta or driven[-1] != home:invalid('need distinct positive/negative excitation beyond 2x tolerance and final home restoration')
        if rejected != {'low', 'high'}:invalid('both invalid range boundaries must be exercised')
    elif kind == 'feedback':
        home = contract['initial_rad']; tol = contract['position_tolerance_rad']
        if len(set(observed)) < 3 or min(observed) >= home-tol or max(observed) <= home+tol or outcomes != {'inside', 'outside_near_threshold'}:invalid('need signed measured states and both sides of in-position threshold')
    elif kind == 'controller' and not {'disabled', 'enabled', 'active_cause_cannot_reset', 'latch', 'reset_after_clear'} <= transitions:
        missing = {'disabled', 'enabled', 'active_cause_cannot_reset', 'latch', 'reset_after_clear'} - transitions
        invalid('controller truth-table transitions missing: '+', '.join(sorted(missing)))
    elif kind == 'contact' and (outcomes != {False, True} or steps[0]['value'] is not False or steps[-1]['value'] is not False):invalid('contact must exercise false/true/false and restore its initial false state')
    return steps


def execute(ir, runtime, module, point, check, capture, cancelled):
    """Fixed oracles interpret the Agent's program; no assertions from the Agent."""
    r = runtime; contract = contracts(ir)[point['name']]; ports = {(p['component_id'], p['quantity']): p for p in ir['io']}
    components = {c['id']: c for c in ir['components']}; cid = contract['component_id']; owner = contract['owner']
    control = {q: ports[(owner, q)]['name'] for q in ('enable_cmd', 'reset_cmd', 'ready', 'fault')}
    estop = values(components[owner])['estop_io_name']; frame_count = 0
    def record(domain, name, passed, **details):check(name, passed, domain=domain, **details)
    def step(outputs=None, inputs=None, count=1):
        nonlocal frame_count
        for _ in range(count):
            if cancelled:cancelled()
            r.step(outputs, inputs); frame_count += 1
            if frame_count % 20 == 0:capture(float(r.data.time))
    def preflight(qa, target):
        from .mechanics import RobotCollisionProbe
        saved = r.data.qpos.copy(); probe = RobotCollisionProbe(ir, r)
        start = float(saved[qa]); count = max(2, math.ceil(abs(target-start)/math.radians(.5)))
        try:
            for q in np.linspace(start, target, count+1):
                if cancelled:cancelled()
                r.data.qpos[qa] = q; mujoco.mj_forward(r.model, r.data)
                contacts = probe.collisions()
                if contacts:raise ScenarioBlocked('IO 测试工况存在干涉，绑定结论未覆盖', 'simulation_test', {'contacts': contacts, 'failure_domain': 'scenario'})
        finally:
            r.data.qpos[:] = saved; mujoco.mj_forward(r.model, r.data)
    capture(0)
    record('binding', 'io_port_target', r.ports.get((cid, point['quantity'])) == point['name'])
    kind = contract['kind']; expected_fault = False
    if kind in ('command', 'feedback'):
        joint = next(j for j in ir['joints'] if j['id'] == contract['joint_id'])
        ji = mujoco.mj_name2id(r.model, mujoco.mjtObj.mjOBJ_JOINT, 'j_'+joint['id'])
        ai = mujoco.mj_name2id(r.model, mujoco.mjtObj.mjOBJ_ACTUATOR, 'a_'+joint['id'])
        if ji < 0 or ai < 0:
            record('binding', 'actuator_route', False); return
        qa = int(r.model.jnt_qposadr[ji])
        record('binding', 'actuator_route', int(r.model.actuator_trnid[ai, 0]) == ji
            and r.meta['components'][cid].get('parameters', {}).get('driven_joint_id') == joint['id'])
        record('binding', 'controller_owner', r.meta['components'][cid].get('robot_owner') == owner)
        params = values(components[cid]); timeout = (ir.get('simulation_requirements') or {}).get('response_timeout_s', 20)
    for index, case in enumerate(module['steps']):
        op = case['op']
        if op == 'reject':
            before_q = r.data.qpos.copy(); before_time = float(r.data.time)
            try:r.step({point['name']: case['value']})
            except ValueError:rejected = True
            else:rejected = False
            record('binding', 'invalid_command_rejected:'+str(index), rejected and np.array_equal(before_q, r.data.qpos) and before_time == float(r.data.time), value=case['value'])
        elif op == 'drive':
            target = case['value']*contract['command_scale']; preflight(qa, target)
            initial_q = r.data.qpos.copy(); previous_targets = {lid: s.get('target', s['joint']['initial']) for lid, s in r.robot.states.items()}
            step({control['enable_cmd']: True, control['reset_cmd']: False, contract['command']: case['value']}, {estop: True})
            target_state = r.robot.states[cid]
            record('binding', 'command_property:'+str(index), abs(target_state['target']-target) < 1e-12, expected_rad=target, actual_rad=target_state['target'])
            for lid, state in r.robot.states.items():
                if lid != cid:record('binding', 'command_target_isolation:'+str(index)+':'+lid, abs(state['target']-previous_targets[lid]) < 1e-12)
            for _ in range(math.ceil(timeout/r.model.opt.timestep)):
                step()
                if abs(float(r.data.qpos[qa])-target) <= params['position_tolerance_rad'] and abs(float(r.data.qvel[r.model.jnt_dofadr[ji]])) < .01 and abs(target_state['velocity']) < .001:break
            actual_q = float(r.data.qpos[qa]); error = abs(actual_q-target)
            record('response', 'local_joint_response:'+str(index), error <= params['position_tolerance_rad'], expected_rad=target, actual_rad=actual_q, error_rad=error)
            for other in ir['joints']:
                if other['id'] == joint['id'] or other['id'] in {c['follower'] for c in ir.get('couplings', [])}:continue
                oi = mujoco.mj_name2id(r.model, mujoco.mjtObj.mjOBJ_JOINT, 'j_'+other['id']); oq = r.model.jnt_qposadr[oi]
                record('response', 'measured_channel_isolation:'+str(index)+':'+other['id'], abs(float(r.data.qpos[oq]-initial_q[oq])) <= params['position_tolerance_rad'])
            encoded = int(np.clip(round(actual_q/contract['feedback_scale']), *contract['feedback_range']))
            record('binding', 'measured_encoder:'+str(index), r.signals[contract['feedback']] == encoded, actual_q_rad=actual_q, expected=encoded, actual=r.signals[contract['feedback']])
        elif op == 'observe':
            preflight(qa, case['position_rad'])
            step({control['enable_cmd']: False, control['reset_cmd']: False, contract['command']: case['reference_value']}, {estop: True})
            r.data.qpos[qa] = case['position_rad']; r.data.qvel[:] = 0; mujoco.mj_forward(r.model, r.data)
            # State injection tests sensing only. The normal runtime feedback
            # kernel reads q; feedback signals are never assigned by the test.
            r.robot.post_step(); measured = float(r.data.qpos[qa]); target = case['reference_value']*contract['command_scale']
            expected = int(np.clip(round(measured/contract['feedback_scale']), *contract['feedback_range']))
            record('binding', 'measured_encoder:'+str(index), r.signals[contract['feedback']] == expected, actual_q_rad=measured, expected=expected, actual=r.signals[contract['feedback']], stimulus_mode='state_injection')
            record('binding', 'measured_in_position:'+str(index), r.signals[contract['in_position']] == (abs(measured-target) <= params['position_tolerance_rad']), actual_q_rad=measured, target_rad=target, stimulus_mode='state_injection')
        elif op == 'control':
            if not case['estop_ok']:expected_fault = True
            elif case['reset']:expected_fault = False
            expected_ready = case['enable'] and case['estop_ok'] and not expected_fault
            step({control['enable_cmd']: case['enable'], control['reset_cmd']: case['reset']}, {estop: case['estop_ok']}, count=10)
            record('binding', 'controller_truth_table:'+str(index), r.signals[control['ready']] == expected_ready and r.signals[control['fault']] == expected_fault, expected_ready=expected_ready, expected_fault=expected_fault, actual_ready=r.signals[control['ready']], actual_fault=r.signals[control['fault']])
            record('binding', 'controller_drive_gate:'+str(index), all(state['enabled']==expected_ready for state in r.robot.states.values() if state['owner']==owner), expected_enabled=expected_ready)
        elif op == 'contact':
            step(inputs={point['name']: case['value']}, count=10)
            record('binding', 'contact_transition:'+str(index), r.signals[point['name']] == case['value'], expected=case['value'], actual=r.signals[point['name']])
        capture(max(float(r.data.time), index*.1))
