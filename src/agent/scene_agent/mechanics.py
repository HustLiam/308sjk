"""Mechanical frames and independent interference probes for robot previews."""
import math
import numpy as np
import mujoco

from .device_ir import values
from .properties import interface_properties


def describe_mechanism(ir, model):
    """Keep frame ownership and joint endpoints from AML, never from box bounds."""
    properties = interface_properties(ir)
    frames = []
    for frame in ir['interfaces']:
        prop = properties[frame['id']]
        body = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, 'c_'+frame['component_id'])
        if body < 0 or prop['position_m'] is None:
            continue
        frames.append({'id': frame['id'], 'name': frame['name'], 'type': frame['type'],
            'component_id': frame['component_id'], 'body': body,
            'position_m': prop['position_m'], 'rpy_rad': prop['rpy_rad'] or [0, 0, 0],
            'joint_ids': [b['joint_id'] for b in prop['joint_bindings']], 'connected': prop['connected']})
    joints = []
    for joint in ir['joints']:
        item = {k: joint[k] for k in ('id', 'name', 'type', 'axis', 'parent_anchor_m', 'child_anchor_m')}
        item['parent_component'] = joint['parent_body']; item['child_component'] = joint['child_body']
        item['parent_body'] = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, 'c_'+joint['parent_body'])
        item['child_body'] = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, 'c_'+joint['child_body'])
        if min(item['parent_body'], item['child_body']) >= 0:
            joints.append(item)
    return {'frames': frames, 'joints': joints}


def body_transforms(data):
    return [p.tolist()+r.tolist() for p, r in zip(data.xpos, data.xmat)]


def preflight_joint_path(runtime, joint, targets, probe, cancelled=None):
    """Inspect a proposed path on scratch state, before publishing any motion.

    Joint limits alone do not imply collision-free reachability. Restore all
    measured state even on cancellation; rejected poses never enter a replay.
    """
    m, d = runtime.model, runtime.data
    ji = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, 'j_'+joint['id'])
    qa = int(m.jnt_qposadr[ji]); saved = d.qpos.copy()
    resolution = math.radians(.5) if joint['type']=='hinge' else .001
    try:
        for target in targets:
            if not joint['range'][0] <= target <= joint['range'][1]:
                return {'reason':'joint_limit','target':target,'range':joint['range']}
            start = float(d.qpos[qa]); count = max(1, math.ceil(abs(target-start)/resolution))
            if count > 10000:return {'reason':'sampling_budget','target':target}
            for q in np.linspace(start, target, count+1):
                if cancelled:cancelled()
                d.qpos[qa] = q; mujoco.mj_forward(m, d)
                contacts = probe.collisions()
                if contacts:return {'reason':'collision','target':target,'q':float(q),'contacts':contacts}
        return None
    finally:
        d.qpos[:] = saved; mujoco.mj_forward(m, d)


class DeviceCollisionProbe:
    """Signed geometry distances also detect pairs disabled in the solver.

    Only immediate AML joint/mount neighbours and rigidly welded geometry are
    exempt. A disabled self-contact policy must not conceal nonadjacent overlap.
    This is sampled interference checking, not continuous collision detection.
    """
    def __init__(self, ir, runtime):
        self.runtime = runtime
        model = runtime.model
        components = {c['id']: c for c in ir['components']}
        meta = runtime.meta['components']
        roots = {cid for cid, c in meta.items() if c['behavior'] == 'robot_control'}
        owners = {}
        by_body = {c['body']: cid for cid, c in meta.items()}
        for gid in range(model.ngeom):
            body = int(model.geom_bodyid[gid])
            while body:
                cid = by_body.get(mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body))
                if cid:
                    owners[gid] = cid; break
                body = int(model.body_parentid[body])
        neighbours = {frozenset((j['parent_body'], j['child_body'])) for j in ir['joints']}
        neighbours.update(frozenset((a['parent_component'], a['child_component'])) for a in ir.get('assemblies', []))
        product_bodies = {mujoco.mj_name2id(model,mujoco.mjtObj.mjOBJ_BODY,p['body']) for p in runtime.meta.get('products',[])}
        self.pairs = []; self.owners = owners
        floor=mujoco.mj_name2id(model,mujoco.mjtObj.mjOBJ_GEOM,'floor')
        def collision_proxy(gid):return bool(model.geom_contype[gid] or model.geom_conaffinity[gid]) or gid==floor
        for a in range(model.ngeom):
            # Non-colliding visual meshes are never used as collision proxies.
            if not collision_proxy(a):
                continue
            for b in range(a+1, model.ngeom):
                if not collision_proxy(b):
                    continue
                ca, cb = owners.get(a), owners.get(b)
                # Spawned products have normal process contact with the machine.
                # This oracle checks machine parts/environment, not material
                # transport contacts or inactive product pool slots.
                if ca not in meta and cb not in meta:
                    continue
                if int(model.geom_bodyid[a]) in product_bodies or int(model.geom_bodyid[b]) in product_bodies:
                    continue
                ba, bb = model.geom_bodyid[a], model.geom_bodyid[b]
                if model.body_weldid[ba] == model.body_weldid[bb]:
                    continue
                if ca == cb and (model.body_parentid[ba]==bb or model.body_parentid[bb]==ba):
                    continue
                if frozenset((ca, cb)) in neighbours:
                    continue
                self.pairs.append((a, b))
        self.scope = {'enabled': bool(self.pairs), 'pair_count': len(self.pairs),
            'method': 'sampled_signed_geom_distance', 'penetration_epsilon_m': 1e-5,
            'exemptions': 'immediate_AML_joint_or_mount_neighbours_and_rigidly_welded_bodies',
            'device_scope':'all_compiled_device_components_not_robot_type_detection',
            'process_contacts':'spawned_product_contacts_are_outside_machine_interference_scope',
            'environment_floor_checked':floor>=0,
            'solver_policies': {cid: {k: values(components[cid]).get(k) for k in
                ('self_collision_policy', 'environment_contact_policy')} for cid in roots},
            'environment_enabled': any(not owners.get(g) and (model.geom_contype[g] or model.geom_conaffinity[g])
                for g in range(model.ngeom))}
        covered_welds={int(model.body_weldid[model.geom_bodyid[g]]) for g in range(model.ngeom)
            if model.geom_contype[g] or model.geom_conaffinity[g]}
        missing=[mujoco.mj_id2name(model,mujoco.mjtObj.mjOBJ_BODY,int(b)) for b in model.jnt_bodyid
            if int(b) not in product_bodies and int(model.body_weldid[b]) not in covered_welds]
        self.scope.update(coverage_complete=not missing,moving_bodies_without_collision_proxy=missing)

    def collisions(self):
        model, data = self.runtime.model, self.runtime.data
        contacts = []
        for a, b in self.pairs:
            segment = np.zeros(6)
            distance = float(mujoco.mj_geomDistance(model, data, a, b, .01, segment))
            if distance < -self.scope['penetration_epsilon_m']:
                contacts.append({'geom_ids': [a, b], 'component_ids': [self.owners.get(a), self.owners.get(b)],
                    'geom_names': [mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, g) for g in (a, b)],
                    'penetration_m': -distance, 'points_m': segment.reshape(2, 3).tolist()})
        return contacts


# Compatibility for callers and historical rule snapshots. There is no longer
# a robot-controller gate on collision checking.
RobotCollisionProbe = DeviceCollisionProbe
