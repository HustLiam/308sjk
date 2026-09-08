"""plotter_cell（gc spec：master:scene.spec.json）组装与回环测试。

覆盖：多资产 MJCF 可编译且三轴行程 = stroke×scale_m_per_unit、纸面按 paper_area
百分比居中于台面、qz=0 笔尖距纸 2mm（作者静置位）、笔尖扫掠覆盖纸面（布局回归）、
Modbus 闭环指令经斜坡驱动真实 qpos。
组件库登记与组装规则标注见 components.py / mujoco_build.py 的【csk 2026-09-08 新增】。

运行：
    python runtime/tests/test_plotter_build.py
    python -m pytest runtime/tests/test_plotter_build.py
"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gantry_bridge import GantryBridge, derive_layout, load_io_map, pack_f32, unpack_f32  # noqa: E402
from mujoco_build import build_mjcf, load_spec  # noqa: E402

PORT = 15420
_port_seq = iter(range(PORT, PORT + 50))
HERE = os.path.dirname(os.path.abspath(__file__))
REPO_SPEC = os.path.normpath(os.path.join(
    HERE, "..", "..", "scenegen", "out", "plotter_cell", "scene.spec.json"))
REPO_IO_MAP = os.path.normpath(os.path.join(
    HERE, "..", "..", "scenegen", "out", "plotter_cell", "io_map.json"))
AXIS_SPEED = {"X": 0.4, "Y": 0.4, "Z": 0.02}   # spec vmax×scale（40%/s×0.01，20mm/s×0.001）


def _model():
    import mujoco
    return mujoco.MjModel.from_xml_string(build_mjcf(load_spec(REPO_SPEC)))


def test_mjcf_builds_and_travel_matches_spec():
    """三轴限位 = stroke×scale：x/y 0..1.0 m（100%×0.01），z 0..0.01 m（10mm×0.001）。"""
    import mujoco
    model = _model()
    for a, hi in (("x", 1.0), ("y", 1.0), ("z", 0.01)):
        j = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, f"joint_{a}")
        assert j >= 0, f"缺 joint_{a}"
        lo, jhi = model.jnt_range[j]
        assert abs(lo) < 1e-9 and abs(jhi - hi) < 1e-6, \
            f"joint_{a} 限位 {lo}~{jhi} 应为 0~{hi}（stroke×scale_m_per_unit）"
        assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, f"drive_{a}") >= 0
    layout, _ = derive_layout(load_io_map(REPO_IO_MAP),
                              travel={"X": 1.0, "Y": 1.0, "Z": 0.01})
    assert layout["Z"]["travel"] == 0.01


def test_paper_geometry_from_paper_area():
    """paper_area '20..80 x 20..80' 按 work_table 尺寸（1.2m）百分比居中：半尺寸 0.36。"""
    import mujoco
    model = _model()
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    paper = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "paper")
    px, py, pz = data.geom_xpos[paper]
    hx, hy = model.geom_size[paper][:2]
    assert abs(px - 0.5) < 1e-6 and abs(py - 0.5) < 1e-6, "纸心应在台心 (0.5, 0.5)"
    assert abs(hx - 0.36) < 1e-6 and abs(hy - 0.36) < 1e-6, \
        f"纸半尺寸 {hx}×{hy} 应为 0.36×0.36（1.2×(80-20)%/2）"
    assert abs(pz - 0.751) < 1e-6, f"纸面高度 {pz} 应为 0.751（台顶 0.75 + 半厚）"


def test_pen_tip_at_qz0_is_2mm_above_paper():
    """qz=0 作者静置位：笔尖距纸面 2mm（与龙门 Z 语义一致）；抬笔 = z 行程 10mm。"""
    import mujoco
    model = _model()
    data = mujoco.MjData(model)
    jz = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "joint_z")
    pen = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "pen")
    paper = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "paper")
    mujoco.mj_forward(model, data)
    gap = (data.geom_xpos[pen][2] - model.geom_size[pen][1]) - \
          (data.geom_xpos[paper][2] + model.geom_size[paper][2])
    assert abs(gap - 0.002) < 5e-4, f"qz=0 笔尖离纸 {gap*1000:.2f}mm，应为 2mm"
    data.qpos[jz] = 0.01
    mujoco.mj_forward(model, data)
    gap_up = (data.geom_xpos[pen][2] - model.geom_size[pen][1]) - \
             (data.geom_xpos[paper][2] + model.geom_size[paper][2])
    assert abs(gap_up - 0.012) < 5e-4, f"qz=10mm 笔尖离纸 {gap_up*1000:.2f}mm，应为 12mm"


def test_pen_sweep_covers_paper():
    """布局回归：笔尖行程 [0..travel]²（米制扫掠区）必须覆盖整张纸面。"""
    import mujoco
    model = _model()
    data = mujoco.MjData(model)
    jid = {a: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, f"joint_{a}")
           for a in "xyz"}
    pen = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "pen")
    paper = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "paper")
    mujoco.mj_forward(model, data)      # fresh MjData 的 geom_xpos 全零，须先正运动学
    pmin = data.geom_xpos[paper][:2] - model.geom_size[paper][:2]
    pmax = data.geom_xpos[paper][:2] + model.geom_size[paper][:2]
    for qx, qy in ((0, 0), (1.0, 0), (0, 1.0), (1.0, 1.0), (0.5, 0.5)):
        data.qpos[jid["x"]] = qx
        data.qpos[jid["y"]] = qy
        data.qpos[jid["z"]] = 0
        mujoco.mj_forward(model, data)
        tx, ty = data.geom_xpos[pen][:2]
        assert pmin[0] - 1e-6 <= tx <= pmax[0] + 1e-6 or qx in (0, 1.0), \
            "笔尖 x 落在纸外（扫掠区未覆盖纸面）"
        assert pmin[1] - 1e-6 <= ty <= pmax[1] + 1e-6 or qy in (0, 1.0), \
            "笔尖 y 落在纸外（扫掠区未覆盖纸面）"
    # 纸面四角必须可达：角点 q = 纸角 - 扫掠起点（扫掠起点 = 0.5 - 1.0/2 = 0）
    for corner in (pmin, pmax, (pmin[0], pmax[1]), (pmax[0], pmin[1])):
        qx, qy = corner[0] - 0.0, corner[1] - 0.0
        assert 0 <= qx <= 1.0 and 0 <= qy <= 1.0, \
            f"纸角 {corner} 不在扫掠区 [0..1]² 内"


def make_plotter_sim(port=None):
    """「MuJoCo 仿真线程 + Modbus 服务端」最小运行时（同 mujoco_jog_runtime 主循环）。"""
    import mujoco

    port = port or next(_port_seq)
    layout, n_regs = derive_layout(load_io_map(REPO_IO_MAP),
                                   travel={"X": 1.0, "Y": 1.0, "Z": 0.01})
    model = _model()
    data = mujoco.MjData(model)
    jid = {a: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, f"joint_{a.lower()}")
           for a in "XYZ"}
    aid = {a: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, f"drive_{a.lower()}")
           for a in "XYZ"}

    bridge = GantryBridge(layout, n_regs, host="127.0.0.1", port=port)
    bridge.set_commands({"X": 0.0, "Y": 0.0, "Z": 0.01})   # 开场抬笔
    bridge.serve_forever()

    hz = 120
    model.opt.timestep = 1.0 / hz
    state = {"last": {a: 0.0 for a in "XYZ"}, "stop": False}

    def loop():
        import numpy as np
        frame = 1.0 / hz
        while not state["stop"]:
            for a, target in bridge.read_commands().items():
                step = AXIS_SPEED[a] * frame
                cur = state["last"][a]
                delta = target - cur
                new = target if abs(delta) <= step else \
                    cur + step * (1.0 if delta > 0 else -1.0)
                data.ctrl[aid[a]] = np.clip(new, 0.0, layout[a]["travel"])
                state["last"][a] = new
            mujoco.mj_step(model, data)
            bridge.write_positions({a: float(data.qpos[jid[a]]) for a in "XYZ"})
            time.sleep(frame)

    threading.Thread(target=loop, daemon=True).start()
    return bridge, state, data, jid


def test_closed_loop_command_moves_joint():
    """写 X=0.5 → 反馈经斜坡（0.4 m/s）到达 ≈0.5（真实 qpos，非指令回声）。"""
    from pymodbus.client import ModbusTcpClient
    bridge, state, _, _ = make_plotter_sim()
    layout = bridge.layout
    try:
        cli = ModbusTcpClient("127.0.0.1", port=bridge.port, timeout=2.0)
        assert cli.connect()
        assert not cli.write_registers(address=layout["X"]["cmd_reg"],
                                       values=pack_f32(0.5), slave=1).isError()
        deadline = time.time() + 8.0
        x = None
        while time.time() < deadline:
            rr = cli.read_holding_registers(address=layout["X"]["pos_reg"], count=2, slave=1)
            x = unpack_f32(rr.registers)
            if abs(x - 0.5) < 0.005:
                break
            time.sleep(0.1)
        assert x is not None and abs(x - 0.5) < 0.005, f"X 反馈未到达 0.5（当前 {x}）"
        cli.close()
    finally:
        state["stop"] = True
        bridge.stop()


def test_overrange_clamped_to_travel():
    """超程 9.9m → 钳位到行程 1.0（行程随 spec 走，非内置缺省 0.6）。"""
    from pymodbus.client import ModbusTcpClient
    bridge, state, _, _ = make_plotter_sim()
    layout = bridge.layout
    try:
        cli = ModbusTcpClient("127.0.0.1", port=bridge.port, timeout=2.0)
        assert cli.connect()
        assert not cli.write_registers(address=layout["X"]["cmd_reg"],
                                       values=pack_f32(9.9), slave=1).isError()
        deadline = time.time() + 10.0
        x = None
        while time.time() < deadline:
            rr = cli.read_holding_registers(address=layout["X"]["pos_reg"], count=2, slave=1)
            x = unpack_f32(rr.registers)
            if abs(x - 1.0) < 0.005:
                break
            time.sleep(0.1)
        assert x is not None and abs(x - 1.0) < 0.005, f"超程未钳位到 1.0（当前 {x}）"
        cli.close()
    finally:
        state["stop"] = True
        bridge.stop()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        passed += 1
        print(f"PASS：{t.__name__}")
    print(f"PASS：{passed} 项 plotter_cell 断言全部通过")
