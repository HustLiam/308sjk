"""Joint-space servo approximation with measured, encoded MuJoCo feedback."""
import math

import numpy as np
import mujoco

from src.agent.scene_agent.common import SceneError


class RobotDriver:
    def __init__(self,runtime):
        self.r=runtime;self.states={};self.faults={}
        for cid,c in runtime.meta['components'].items():
            if c['behavior']=='robot_control':self.faults[cid]=False
            if c['behavior']!='joint_position':continue
            p=c['parameters'];jid=p.get('driven_joint_id');j=runtime.meta['joints'].get(jid)
            owner=c.get('robot_owner')
            if not j or j['type'] not in ('hinge','slide') or j['child_body']!=cid or not owner:
                raise SceneError('Joint-space driver requires its own joint and robot controller: '+cid)
            for key in ('max_velocity_rad_s','max_acceleration_rad_s2','position_tolerance_rad'):
                if type(p.get(key)) not in (int,float) or not math.isfinite(p[key]) or p[key]<=0:raise SceneError('Invalid robot parameter '+key)
            ji=mujoco.mj_name2id(runtime.model,mujoco.mjtObj.mjOBJ_JOINT,j['mjcf_joint'])
            ai=mujoco.mj_name2id(runtime.model,mujoco.mjtObj.mjOBJ_ACTUATOR,j['actuator'])
            if ji<0 or ai<0:raise SceneError('Robot actuator/joint missing')
            for quantity in ('joint_position_cmd','joint_position_fb','in_position'):
                if (cid,quantity) not in runtime.ports:raise SceneError('Robot IO missing: '+quantity)
            scale=runtime.points[runtime.ports[(cid,'joint_position_cmd')]]['physical_per_plc_unit']
            if not math.isfinite(scale) or scale<=0:raise SceneError('Robot angular encoding must have a positive finite scale')
            self.states[cid]={'joint':j,'ji':ji,'ai':ai,'qa':int(runtime.model.jnt_qposadr[ji]),
                'va':int(runtime.model.jnt_dofadr[ji]),'position':j['initial'],'velocity':0.,'owner':owner,'enabled':False}
            bid=mujoco.mj_name2id(runtime.model,mujoco.mjtObj.mjOBJ_BODY,c['body'])
            runtime.model.body_gravcomp[bid]=p.get('gravity_compensation',0)
            runtime.data.ctrl[ai]=j['initial']

    def pre_step(self):
        r=self.r;dt=float(r.model.opt.timestep)
        for owner in self.faults:
            root=r.meta['components'][owner];estop_name=root['parameters'].get('estop_io_name')
            if not estop_name or estop_name not in r.points or r.points[estop_name]['quantity']!='estop_ok':
                raise SceneError('Robot estop input binding is missing')
            estop=bool(r.signals[estop_name])
            if not estop:self.faults[owner]=True
            elif r._value(owner,'reset_cmd'):self.faults[owner]=False
            enabled=bool(estop and r._value(owner,'enable_cmd') and not self.faults[owner])
            r._feedback(owner,'fault',self.faults[owner]);r._feedback(owner,'ready',enabled)
            for cid,s in self.states.items():
                if s['owner']!=owner:continue
                p=r.meta['components'][cid]['parameters'];j=s['joint']
                point=r.points[r.ports[(cid,'joint_position_cmd')]]
                target=r.signals[point['name']]*point['physical_per_plc_unit']
                if not j['range'][0]-1e-12<=target<=j['range'][1]+1e-12:raise SceneError('Robot command exceeds AML joint range: '+point['name'])
                velocity=s['velocity'];accel=p['max_acceleration_rad_s2']
                distance=target-s['position']
                desired=math.copysign(min(p['max_velocity_rad_s'],math.sqrt(2*accel*abs(distance))),distance) if enabled and abs(distance)>1e-12 else 0.
                new_velocity=velocity+float(np.clip(desired-velocity,-accel*dt,accel*dt))
                movement=(new_velocity+velocity)*dt/2
                if enabled and abs(movement)>abs(distance):movement=distance;new_velocity=0.
                s['position']=float(np.clip(s['position']+movement,*j['range']))
                s['velocity']=new_velocity;s['enabled']=enabled;s['target']=target
                r.data.ctrl[s['ai']]=s['position']

    def post_step(self):
        r=self.r
        for cid,s in self.states.items():
            p=r.meta['components'][cid]['parameters'];q=float(r.data.qpos[s['qa']]);velocity=float(r.data.qvel[s['va']])
            point=r.points[r.ports[(cid,'joint_position_fb')]]
            encoded=round(q/point['physical_per_plc_unit'])
            # Encoder saturation is explicit in the simulation profile. Actual
            # q remains available, so saturation cannot hide mechanical error.
            encoded=int(np.clip(encoded,*point['range']))
            r._feedback(cid,'joint_position_fb',encoded)
            r._feedback(cid,'in_position',abs(q-s['target'])<=p['position_tolerance_rad'])
            r.properties[cid]={'position_rad':q,'velocity_rad_s':velocity,'target_rad':s['target'],
                'trajectory_rad':s['position'],'trajectory_velocity_rad_s':s['velocity'],'enabled':s['enabled']}
