"""Device-independent path contracts and immutable collision evidence."""
import math

import mujoco
import numpy as np

from .common import SceneError
from .mechanics import DeviceCollisionProbe


def scalar_joints(ir):
    return {j['id']:j for j in ir['joints'] if j['type'] in ('hinge','slide')}


def validate_joint_program(ir, module):
    joints=scalar_joints(ir);target=joints.get(module['target']);steps=module['steps']
    def invalid(message):raise SceneError('Invalid device motion program '+module['target']+': '+message,'simulation_test')
    if target is None or not isinstance(steps,list) or not 3<=len(steps)<=40:invalid('expected 3..40 configurations of scalar joints')
    for step in steps:
        if not isinstance(step,dict) or set(step)!={'op','positions'} or step['op']!='joint_configuration':invalid('only joint_configuration stimuli are allowed')
        positions=step['positions']
        if not isinstance(positions,dict) or set(positions)!=set(joints):invalid('each waypoint must contain exactly all declared scalar joints')
        for ident,q in positions.items():
            if type(q) not in (int,float) or not math.isfinite(q) or not joints[ident]['range'][0]<=q<=joints[ident]['range'][1]:invalid('joint outside physical limits: '+ident)
        for coupling in ir.get('couplings',[]):
            if coupling['leader'] in positions and coupling['follower'] in positions:
                expected=coupling['offset']+coupling['ratio']*positions[coupling['leader']]
                if abs(positions[coupling['follower']]-expected)>1e-8:invalid('coupling violated: '+coupling['id'])
    if any(abs(steps[-1]['positions'][ident]-j['initial'])>1e-9 for ident,j in joints.items()):invalid('restore all joints to their declared initial state')
    # Endpoints imply coverage of intermediate values under continuous paths;
    # the author cannot remove the AML travel requirement to avoid an obstacle.
    values=[s['positions'][target['id']] for s in steps]+[target['initial']]
    if abs(min(values)-target['range'][0])>1e-9 or abs(max(values)-target['range'][1])>1e-9:invalid('mandatory target-joint travel endpoints missing')
    return steps


def preflight_configurations(ir,runtime,steps,probe,cancelled=None):
    joints=scalar_joints(ir);m,d=runtime.model,runtime.data;saved=d.qpos.copy()
    addresses={}
    for ident in joints:
        ji=mujoco.mj_name2id(m,mujoco.mjtObj.mjOBJ_JOINT,'j_'+ident)
        if ji<0:raise SceneError('Compiled model lost declared joint: '+ident,'simulation_test')
        addresses[ident]=int(m.jnt_qposadr[ji])
    try:
        for index,step in enumerate(steps):
            positions=step['positions'];start={ident:float(d.qpos[a]) for ident,a in addresses.items()}
            count=max([1]+[math.ceil(abs(positions[ident]-start[ident])/(math.radians(.5) if j['type']=='hinge' else .001)) for ident,j in joints.items()])
            if count>10000:return {'reason':'sampling_budget','waypoint':index}
            for fraction in np.linspace(0,1,count+1):
                if cancelled:cancelled()
                for ident,a in addresses.items():d.qpos[a]=start[ident]+fraction*(positions[ident]-start[ident])
                mujoco.mj_forward(m,d);contacts=probe.collisions()
                if contacts:return {'reason':'collision','waypoint':index,'fraction':float(fraction),
                    'positions':{ident:float(d.qpos[a]) for ident,a in addresses.items()},'contacts':contacts}
        return None
    finally:d.qpos[:]=saved;mujoco.mj_forward(m,d)


def geometry_context(ir,metadata,xml):
    from runtime.device_scene import DeviceRuntime
    r=DeviceRuntime(metadata,xml);m,d=r.model,r.data;mujoco.mj_forward(m,d);probe=DeviceCollisionProbe(ir,r)
    geoms=[]
    for gid in range(m.ngeom):
        if not (m.geom_contype[gid] or m.geom_conaffinity[gid]):continue
        if hasattr(m,'geom_aabb'):
            local=m.geom_aabb[gid];rotation=d.geom_xmat[gid].reshape(3,3)
            center=d.geom_xpos[gid]+rotation@local[:3];half=np.abs(rotation)@local[3:]
        else:center=d.geom_xpos[gid];half=np.repeat(m.geom_rbound[gid],3)
        geoms.append({'id':gid,'component':probe.owners.get(gid),'name':mujoco.mj_id2name(m,mujoco.mjtObj.mjOBJ_GEOM,gid),
            'body':int(m.geom_bodyid[gid]),'type':int(m.geom_type[gid]),'local_pos':m.geom_pos[gid].tolist(),
            'size':m.geom_size[gid].tolist(),'initial_world_aabb':[center.tolist(),half.tolist()]})
    return {'scope':probe.scope,'geometries':geoms,'checked_geom_pairs':probe.pairs,
        'initial_collisions':probe.collisions(),'joints':ir['joints'],'couplings':ir.get('couplings',[]),
        'assemblies':ir.get('assemblies',[]),'model_xml_sha256':metadata['model_xml_sha256'],
        'note':'AABBs are planning hints only; the host checks actual compiled collision geometry and interpolated paths.'}


def safety_issues(report):
    issues=[]
    for module in report['modules']:
        rejected=[c for c in module['checks'] if not c['passed'] and c.get('domain')=='scenario']
        if rejected or module['status']=='blocked':issues.append({'test_id':module['id'],'target':module['target'],'evidence':rejected,'uncovered':module.get('uncovered')})
    return issues
