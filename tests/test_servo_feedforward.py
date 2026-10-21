"""Physical tracking must improve through forces, without resetting simulated state."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import mujoco
from agent.servo_feedforward import PositionFeedforward


def test_physical_servo_tracks_acceleration_and_stop_without_corner_overshoot():
    xml='''<mujoco><option timestep="0.008333333333333333" gravity="0 0 0"/>
      <worldbody><body><joint name="x" type="slide" axis="1 0 0" damping="250"/>
      <geom type="sphere" size=".01" mass="7.4" contype="0" conaffinity="0"/></body></worldbody>
      <actuator><position joint="x" kp="6000"/></actuator></mujoco>'''
    errors=[]
    for enabled in (False,True):
        model=mujoco.MjModel.from_xml_string(xml);data=mujoco.MjData(model)
        mujoco.mj_forward(model,data)
        servo=PositionFeedforward(model,data,{'X':0},{'X':0},{'X':0})
        worst=0;previous=0
        for tick in range(360):
            t=(tick+1)/120
            # Accelerate, cruise, decelerate, then hold at the corner.
            target=.4*t*t if t<.5 else .1+.4*(t-.5) if t<1 else .3+.4*(t-1)-.4*(t-1)**2 if t<1.5 else .4
            if enabled:servo.apply({'X':target},1/120)
            else:
                data.ctrl[0]=target
                data.qfrc_applied[0]=250*(target-previous)*120
            mujoco.mj_step(model,data)
            worst=max(worst,abs(float(data.qpos[0])-target));previous=target
        errors.append(worst)
    assert errors[0]>.0001 and errors[1]<.00001,errors
