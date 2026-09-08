"""MuJoCo 回环无头测试：MJCF 组装 → 仿真步进 → Modbus 服务端 → 客户端闭环。

覆盖：组装器产出可编译 MJCF 且关节/执行器命名与布局正确、指令写入经速率限制
驱动关节、反馈寄存器跟随真实 qpos、超程钳位、开场 Z 抬笔斜坡。
不需要 Isaac Sim / pxr / GUI；依赖 mujoco（pip 装，见 runtime/requirements.txt）。

运行：
    python runtime/tests/test_mujoco_loop.py             # 独立脚本
    python -m pytest runtime/tests/test_mujoco_loop.py   # pytest
"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gantry_bridge import GantryBridge, derive_layout, load_io_map, pack_f32, unpack_f32  # noqa: E402
from mujoco_build import build_mjcf, load_spec  # noqa: E402

PORT = 15220
_port_seq = iter(range(PORT, PORT + 50))
HERE = os.path.dirname(os.path.abspath(__file__))
REPO_SPEC = os.path.normpath(os.path.join(
    HERE, "..", "..", "scenegen", "out", "gantry", "scene.spec.json"))
REPO_IO_MAP = os.path.normpath(os.path.join(
    HERE, "..", "..", "scenegen", "out", "gantry", "io_map.json"))
AXIS_SPEED = 0.5        # spec 中 gantry 的 speed 参数（与 USD 侧 simio:axisSpeed 同源）


def make_sim(port=None):
    """起一个「MuJoCo 仿真线程 + Modbus 服务端」的最小运行时（同 mujoco_jog_runtime 主循环）。"""
    import mujoco
    import numpy as np

    port = port or next(_port_seq)
    io_map = load_io_map(REPO_IO_MAP if os.path.isfile(REPO_IO_MAP) else None)
    layout, n_regs = derive_layout(io_map)
    model = mujoco.MjModel.from_xml_string(build_mjcf(load_spec(REPO_SPEC)))
    data = mujoco.MjData(model)
    jid = {a: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, f"joint_{a.lower()}")
           for a in "XYZ"}
    aid = {a: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, f"drive_{a.lower()}")
           for a in "XYZ"}
    travel = {a: layout[a]["travel"] for a in "XYZ"}

    bridge = GantryBridge(layout, n_regs, host="127.0.0.1", port=port)
    bridge.set_commands({"X": 0.0, "Y": 0.0, "Z": travel["Z"]})   # 开场：抬笔
    bridge.serve_forever()

    hz = 120
    model.opt.timestep = 1.0 / hz
    state = {"last": {a: 0.0 for a in "XYZ"}, "stop": False}

    def loop():
        frame = 1.0 / hz
        while not state["stop"]:
            for a, target in bridge.read_commands().items():
                step = AXIS_SPEED * frame
                cur = state["last"][a]
                delta = target - cur
                new = target if abs(delta) <= step else \
                    cur + step * (1.0 if delta > 0 else -1.0)
                data.ctrl[aid[a]] = np.clip(new, 0.0, travel[a])
                state["last"][a] = new
            mujoco.mj_step(model, data)
            bridge.write_positions({a: float(data.qpos[jid[a]]) for a in "XYZ"})
            time.sleep(frame)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return bridge, state, data, jid


def read_axis(cli, layout, axis):
    rr = cli.read_holding_registers(address=layout[axis]["pos_reg"], count=2, slave=1)
    assert not rr.isError(), f"读 Axis{axis}_pos 失败: {rr}"
    return unpack_f32(rr.registers)


def write_cmd(cli, layout, axis, value):
    rr = cli.write_registers(address=layout[axis]["cmd_reg"],
                             values=pack_f32(value), slave=1)
    assert not rr.isError(), f"写 Axis{axis}_cmd 失败: {rr}"


# ---------------- pytest 入口 ----------------

def test_mjcf_builds_and_matches_layout():
    import mujoco
    xml = build_mjcf(load_spec(REPO_SPEC))
    model = mujoco.MjModel.from_xml_string(xml)
    for a in "XYZ":
        assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, f"joint_{a.lower()}") >= 0
        assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, f"drive_{a.lower()}") >= 0
    layout, _ = derive_layout(load_io_map(REPO_IO_MAP))
    for i, a in enumerate("XYZ"):
        lo, hi = model.jnt_range[mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_JOINT, f"joint_{a.lower()}")]
        assert abs(lo) < 1e-9 and abs(hi - layout[a]["travel"]) < 1e-6, \
            f"joint_{a.lower()} 限位 {lo}~{hi} 应为 0~{layout[a]['travel']}"


def test_pen_sweep_covers_paper():
    """布局回归：笔尖行程 [0..travel]² 必须铺满纸面（USD 侧曾有纸张偏置在根原点的缺陷，
    导致右上 1/4 出纸、左侧 1/4 不可达——见 devlog 2026-09-07(8)）。"""
    import mujoco
    model = mujoco.MjModel.from_xml_string(build_mjcf(load_spec(REPO_SPEC)))
    data = mujoco.MjData(model)
    jid = {a: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, f"joint_{a.lower()}")
           for a in "XYZ"}
    z_body = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "z_carriage")
    paper = data.geom_xpos[mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "paper")]
    px, py = paper[0], paper[1]
    half_x, half_y = model.geom_size[mujoco.mj_name2id(
        model, mujoco.mjtObj.mjOBJ_GEOM, "paper")][:2]
    for qx, qy in ((0, 0), (0.6, 0), (0, 0.4), (0.6, 0.4)):
        data.qpos[jid["X"]] = qx
        data.qpos[jid["Y"]] = qy
        data.qpos[jid["Z"]] = 0
        mujoco.mj_forward(model, data)
        tx, ty = data.xpos[z_body][:2]
        assert px - half_x - 1e-6 <= tx <= px + half_x + 1e-6, \
            f"笔尖 x={tx:.3f} 出纸（纸 {px - half_x:.3f}..{px + half_x:.3f}）"
        assert py - half_y - 1e-6 <= ty <= py + half_y + 1e-6, \
            f"笔尖 y={ty:.3f} 出纸（纸 {py - half_y:.3f}..{py + half_y:.3f}）"


def test_closed_loop_command_moves_joint():
    """写 X=0.3 → 反馈应经斜坡到达 ≈0.3（真实 qpos，非指令回声）。"""
    from pymodbus.client import ModbusTcpClient
    bridge, state, _, _ = make_sim()
    layout = bridge.layout
    try:
        cli = ModbusTcpClient("127.0.0.1", port=bridge.port, timeout=2.0)
        assert cli.connect()
        write_cmd(cli, layout, "X", 0.3)
        deadline = time.time() + 8.0
        while time.time() < deadline:
            if abs(read_axis(cli, layout, "X") - 0.3) < 0.005:
                break
            time.sleep(0.1)
        assert abs(read_axis(cli, layout, "X") - 0.3) < 0.005, "X 反馈未到达 0.3"
        cli.close()
    finally:
        state["stop"] = True
        bridge.stop()


def test_overrange_command_clamped():
    """超程指令 9.9m → 关节被钳位在行程 0.6。"""
    from pymodbus.client import ModbusTcpClient
    bridge, state, _, _ = make_sim()
    layout = bridge.layout
    try:
        cli = ModbusTcpClient("127.0.0.1", port=bridge.port, timeout=2.0)
        assert cli.connect()
        write_cmd(cli, layout, "X", 9.9)
        deadline = time.time() + 10.0
        while time.time() < deadline:
            if abs(read_axis(cli, layout, "X") - 0.6) < 0.005:
                break
            time.sleep(0.1)
        assert abs(read_axis(cli, layout, "X") - 0.6) < 0.005, "超程未钳位到 0.6"
        cli.close()
    finally:
        state["stop"] = True
        bridge.stop()


def test_opening_z_ramp():
    """开场抬笔：无客户端时 Z 反馈应在 ~travel/speed+整定 内从 0 爬到 0.2。"""
    from pymodbus.client import ModbusTcpClient
    bridge, state, _, _ = make_sim()
    layout = bridge.layout
    try:
        cli = ModbusTcpClient("127.0.0.1", port=bridge.port, timeout=2.0)
        assert cli.connect()
        # 仿真线程已跑：等 Z 到位（0.2m @ 0.5m/s = 0.4s 斜坡 + 伺服整定余量）
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if abs(read_axis(cli, layout, "Z") - 0.2) < 0.005:
                break
            time.sleep(0.05)
        z = read_axis(cli, layout, "Z")
        assert abs(z - 0.2) < 0.005, f"开场 Z 未抬到 0.2（当前 {z:.4f}）"
        cli.close()
    finally:
        state["stop"] = True
        bridge.stop()


def test_rate_limit_step_becomes_ramp():
    """指令阶跃不会瞬移：0.3m 阶跃后 0.2s 内 X 反馈应在斜坡途中（<0.15m）。"""
    from pymodbus.client import ModbusTcpClient
    bridge, state, _, _ = make_sim()
    layout = bridge.layout
    try:
        cli = ModbusTcpClient("127.0.0.1", port=bridge.port, timeout=2.0)
        assert cli.connect()
        # 先等开场 Z 抬笔完成，避免与 X 观测窗口重叠
        deadline = time.time() + 5.0
        while time.time() < deadline and abs(read_axis(cli, layout, "Z") - 0.2) > 0.005:
            time.sleep(0.05)
        write_cmd(cli, layout, "X", 0.3)
        time.sleep(0.2)                    # 斜坡 0.5m/s → 理论 0.1m
        x = read_axis(cli, layout, "X")
        assert 0.02 < x < 0.16, f"阶跃未成斜坡（0.2s 时 X={x:.4f}，期望≈0.1）"
        cli.close()
    finally:
        state["stop"] = True
        bridge.stop()


# ---------------- 独立脚本入口 ----------------

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        passed += 1
        print(f"PASS：{t.__name__}")
    print(f"PASS：{passed} 项 MuJoCo 回环断言全部通过")
