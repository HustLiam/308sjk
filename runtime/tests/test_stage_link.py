"""StageLink 无 Isaac 接线测试：用 usd-core 打开仓库生成的 scene.usda，
配合真 GantryBridge 验证「指令寄存器 → 关节驱动属性 → 位置回读」整条链路。

需要 usd-core（scenegen 同款依赖）；未安装时跳过，不影响其余回环测试。
运行：python runtime/tests/test_stage_link.py 或 pytest。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from pxr import Usd, UsdGeom
    HAVE_PXR = True
except ImportError:                                   # pragma: no cover
    HAVE_PXR = False

from gantry_bridge import unpack_f32  # noqa: E402

if HAVE_PXR:
    from stage_link import StageLink                  # noqa: E402

import test_modbus_loop as mb  # noqa: E402   （复用 make_bridge / first_write 基建）

HERE = os.path.dirname(os.path.abspath(__file__))
SCENE = os.path.normpath(os.path.join(HERE, "..", "..", "scenegen", "out", "gantry", "scene.usda"))

JOINTS = {a: f"/World/gantry_1/joint_{a.lower()}" for a in "XYZ"}
BODIES = {a: f"/World/gantry_1/{n}_carriage"
          for a, n in (("X", "x"), ("Y", "y"), ("Z", "z"))}


def _drive_target(stage, axis):
    return stage.GetPrimAtPath(JOINTS[axis]).GetAttribute(
        f"drive:trans{axis}:physics:targetPosition").Get()


def _set_body_pos(stage, axis, q):
    attr = stage.GetPrimAtPath(BODIES[axis]).GetAttribute("xformOp:translate")
    v = list(attr.Get())
    v["XYZ".index(axis)] += q
    attr.Set(tuple(v))


# ---------------- pytest ----------------

def test_stage_link_command_to_drive():
    if not HAVE_PXR:
        print("SKIP：未安装 usd-core")
        return
    bridge = mb.make_bridge()
    try:
        stage = Usd.Stage.Open(SCENE)
        io_map = mb.load_io_map(mb.REPO_IO_MAP)
        link = StageLink(stage, bridge, io_map)
        dt = 1.0 / 60

        cli = mb.make_client(bridge.port)
        try:
            mb.first_write(cli, bridge.layout["X"]["cmd_reg"], mb.pack_f32(0.25))
            mb.first_write(cli, bridge.layout["Z"]["cmd_reg"], mb.pack_f32(0.05))
            # 首帧：速率限制生效——单帧只走 axis_speed*dt（0.5/60 ≈ 8.3mm），绝不阶跃
            link.apply_once(dt=dt)
            first = _drive_target(stage, "X")
            assert 0.0 < first <= link.axis_speed * dt + 1e-6, \
                f"首帧应按速率限制走 {link.axis_speed * dt:.4f} m，实际 {first}"
            for _ in range(60):                   # 0.25m / (0.5m/s) = 0.5s 内收敛
                link.apply_once(dt=dt)
            assert abs(_drive_target(stage, "X") - 0.25) < 1e-5, "指令未落到关节驱动属性"
            assert abs(_drive_target(stage, "Z") - 0.05) < 1e-5
        finally:
            cli.close()

        # 超程钳位 → 驱动目标 = 行程上限
        cli = mb.make_client(bridge.port)
        try:
            mb.first_write(cli, bridge.layout["Y"]["cmd_reg"], mb.pack_f32(9.0))
            for _ in range(80):
                link.apply_once(dt=dt)
            assert abs(_drive_target(stage, "Y") - bridge.layout["Y"]["travel"]) < 1e-5
        finally:
            cli.close()
    finally:
        bridge.stop()


def test_stage_link_z_raising_ramp():
    """开场落笔位 + 抬笔指令：驱动目标沿 axisSpeed 渐升，绝无饱和弹射阶跃。"""
    if not HAVE_PXR:
        print("SKIP：未安装 usd-core")
        return
    bridge = mb.make_bridge()
    try:
        stage = Usd.Stage.Open(SCENE)
        link = StageLink(stage, bridge)
        dt = 1.0 / 60
        bridge.set_commands({"Z": bridge.layout["Z"]["travel"]})   # 抬笔 0.2m
        prev = 0.0
        frames = 0
        while True:
            link.apply_once(dt=dt)
            cur = _drive_target(stage, "Z")
            assert cur - prev <= link.axis_speed * dt + 1e-6, "单帧增量超轴速（弹射风险）"
            prev = cur
            frames += 1
            if abs(cur - 0.2) < 1e-5:
                break
            assert frames < 120, "0.2m/0.5m/s 应在 24 帧内抬完"
        assert 20 <= frames <= 30, f"抬笔应耗时 ~0.4s（24 帧），实际 {frames} 帧"
    finally:
        bridge.stop()


def test_stage_link_position_feedback():
    if not HAVE_PXR:
        print("SKIP：未安装 usd-core")
        return
    bridge = mb.make_bridge()
    try:
        stage = Usd.Stage.Open(SCENE)
        link = StageLink(stage, bridge)

        # 模拟物理推完一步后的刚体位置：X 移动 0.25、Z 从抬笔位落下 0.2 到 0.08
        _set_body_pos(stage, "X", 0.25)
        _set_body_pos(stage, "Z", 0.08)
        link.apply_once()

        pos = bridge.read_positions()
        assert abs(pos["X"] - 0.25) < 1e-5, "位置回读未进反馈寄存器"
        assert abs(pos["Z"] - 0.08) < 1e-5

        # 客户端按契约地址 FC03 读到的就是同一值
        cli = mb.make_client(bridge.port)
        try:
            rr = cli.read_holding_registers(address=bridge.layout["X"]["pos_reg"],
                                            count=2, slave=1)
            assert abs(unpack_f32(rr.registers) - 0.25) < 1e-5
        finally:
            cli.close()
    finally:
        bridge.stop()


# ---------------- 独立脚本 ----------------

def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS：{t.__name__}")
    print(f"PASS：{len(tests)} 项 StageLink 接线断言全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
