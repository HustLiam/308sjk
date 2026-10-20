"""物理开场位与桥反馈一致；第一步不会把抬笔初态拖回零位。"""
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mujoco_jog_runtime import initialize_bridge_pose


def test_initial_pose_is_real_joint_state_before_feedback_publishing():
    mujoco = pytest.importorskip('mujoco')
    model = mujoco.MjModel.from_xml_string('''<mujoco><option gravity="0 0 0"/>
      <worldbody><body><joint name="joint_z" type="slide" axis="0 0 1"/>
      <geom size=".01" mass="1"/></body></worldbody>
      <actuator><position joint="joint_z" kp="1000"/></actuator></mujoco>''')
    data = mujoco.MjData(model)
    class Bridge:
        def read_commands(self): return {'Z': .01}
        def write_positions(self, values): self.feedback = values
    bridge = Bridge()
    initial = initialize_bridge_pose(model, data, bridge, {'Z': 0}, {'Z': 0})
    assert initial == bridge.feedback == {'Z': .01}
    assert data.qpos[0] == data.ctrl[0] == .01
    mujoco.mj_step(model, data)
    assert abs(data.qpos[0] - .01) < 1e-9
