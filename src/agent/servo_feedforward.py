"""Position servo feedforward from actual PLC samples and the loaded MuJoCo model."""
import numpy as np
import mujoco


class PositionFeedforward:
    def __init__(self, model, data, qids, dids, aids):
        self.model,self.data=model,data
        self.qids,self.dids,self.aids=qids,dids,aids
        self.previous={a:float(data.qpos[i]) for a,i in qids.items()}
        self.velocity=dict.fromkeys(qids,0.0)
        self.mass=np.empty((model.nv,model.nv))

    def apply(self, targets, dt):
        model,data=self.model,self.data
        velocity={a:(targets[a]-self.previous[a])/dt for a in targets}
        acceleration=np.zeros(model.nv)
        for a,dof in self.dids.items():
            acceleration[dof]=(velocity[a]-self.velocity[a])/dt
        mujoco.mj_fullM(model,self.mass,data.qM)
        inertia=self.mass@acceleration
        for a,dof in self.dids.items():
            actuator=self.aids[a]
            data.ctrl[actuator]=targets[a]
            # ctrl is the next position sample, whereas mj_step evaluates P at
            # the current qpos. Compensate that sample offset once, alongside
            # inertial, damping and bias feedforward. Never assign qpos/qvel.
            delta=targets[a]-self.previous[a]
            data.qfrc_applied[dof]=(inertia[dof]+model.dof_damping[dof]*velocity[a]
                +data.qfrc_bias[dof]-model.actuator_gainprm[actuator,0]*delta)
        self.previous=dict(targets);self.velocity=velocity
