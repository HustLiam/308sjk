#!/usr/bin/env python
"""MuJoCo 独立运行时：一个进程承载「物理仿真 + Modbus 服务端」（isaac_jog_runtime 的轻量镜像）。

链路与 runtime/README 的 Isaac 工作流 A 完全同构，仅把仿真面从 Isaac/USD 换成
MuJoCo/MJCF：进程加载 mujoco_build.py 组装的 MJCF，逐物理步 mj_step；每帧把指令
寄存器经 axisSpeed 速率限制写入位置执行器 ctrl，把关节 qpos 回写反馈寄存器。
外部照旧用 gantry_jog_gui.py（或 OpenPLC %QW 桥）通过 Modbus TCP 连接。

寄存器布局/钳位/Z 轴语义/开场抬笔全部复用 gantry_bridge 的既有约定，
io_map 契约不变——PLC 侧与示教器零改动即可切换仿真后端。

用法（无需 Isaac、无 GUI 依赖，秒级启动）：
    python mujoco_jog_runtime.py --scene ../scenegen/out/gantry/scene.spec.json
    python mujoco_jog_runtime.py --scene ... --port 5020              # 默认值
    python mujoco_jog_runtime.py --scene ... --watch                 # 观测模式（打印跟随误差）
停止：Ctrl+C。
"""

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from gantry_bridge import AXES, GantryBridge, derive_layout, load_io_map  # noqa: E402
from mujoco_build import build_mjcf, load_spec  # noqa: E402

DEFAULT_SPEC = os.path.normpath(
    os.path.join(HERE, "..", "scenegen", "out", "gantry", "scene.spec.json"))
DEFAULT_IO_MAP = os.path.normpath(
    os.path.join(HERE, "..", "scenegen", "out", "gantry", "io_map.json"))


def parse_args():
    p = argparse.ArgumentParser(description="龙门 MuJoCo 独立运行时（物理仿真 + Modbus 服务端）")
    p.add_argument("--scene", default=DEFAULT_SPEC,
                   help="scene.spec.json 路径（现场组装 MJCF；也可直接传 .xml）")
    p.add_argument("--io-map", default=DEFAULT_IO_MAP,
                   help="io_map.json（推导寄存器布局；缺省用仓库 gantry 场景）")
    p.add_argument("--host", default="0.0.0.0", help="Modbus 绑定地址")
    p.add_argument("--port", type=int, default=5020)
    p.add_argument("--physics-hz", type=int, default=120, help="物理步频（默认 120Hz）")
    p.add_argument("--watch", action="store_true", help="每 2s 打印指令/反馈跟随表")
    p.add_argument("--viewer", action="store_true",
                   help="打开 MuJoCo 3D 交互视窗（需显示服务；无头环境自动回退）")
    return p.parse_args()


def main():
    import mujoco
    import numpy as np

    args = parse_args()
    io_map = load_io_map(args.io_map if os.path.isfile(args.io_map) else None)
    layout, n_regs = derive_layout(io_map)

    if args.scene.endswith(".xml"):
        model = mujoco.MjModel.from_xml_path(args.scene)
    else:
        model = mujoco.MjModel.from_xml_string(build_mjcf(load_spec(args.scene)))
    data = mujoco.MjData(model)
    jid = {a: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, f"joint_{a.lower()}")
           for a in AXES}
    aid = {a: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, f"drive_{a.lower()}")
           for a in AXES}
    travel = {a: layout[a]["travel"] for a in AXES}

    bridge = GantryBridge(layout, n_regs, host=args.host, port=args.port)
    # 开场指令与场景初始位一致：X/Y 原点、Z 抬笔（客户端连上后由它接管）
    bridge.set_commands({a: (travel[a] if a == "Z" else 0.0) for a in AXES})
    bridge.serve_forever()
    print(f"Modbus server ready (mujoco {mujoco.__version__}, in-process)")
    print(bridge.describe())
    print(f"physics: {args.physics_hz}Hz, scene: {args.scene}")

    # axisSpeed 与龙门 pose 从 spec 的 gantry 参数取（与 USD 侧 simio:axisSpeed 同源）
    axis_speed = 0.5
    gx = gy = 0.0
    if not args.scene.endswith(".xml"):
        for a in load_spec(args.scene).get("assets", []):
            if a.get("type") == "gantry_xyz":
                axis_speed = float(a.get("params", {}).get("speed", 0.5))
                gx, gy = (float(v) for v in
                          a.get("pose", {}).get("position", (0.0, 0.0))[:2])

    model.opt.timestep = 1.0 / args.physics_hz
    last_cmd = {a: 0.0 for a in AXES}       # 跟踪起点 = 0（作者位姿），阶跃必成斜坡
    frame = 1.0 / args.physics_hz
    n_watch = 0

    viewer_ctx = None
    if args.viewer:
        try:
            import mujoco.viewer
            viewer_ctx = mujoco.viewer.launch_passive(model, data)
            print("MuJoCo 3D 视窗已打开（拖动旋转 / 滚轮缩放；关闭视窗即结束仿真）")
        except Exception as exc:
            print(f"视窗不可用（{type(exc).__name__}: {exc}），回退无头模式")

    # 落笔墨迹：笔尖接近纸面（≤1.2cm，兼容伺服静差）且离开上个墨点 ≥4mm 时，
    # 在 viewer 场景里留一颗墨点（user_scn 环形缓冲，写满覆盖最旧——保留最近笔迹）
    INK_MAX = 2000
    ink_next = 0
    ink_last = None
    PAPER_TOP = 0.042

    def drop_ink():
        nonlocal ink_next, ink_last
        if viewer_ctx is None:
            return
        zb = data.xpos[mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "z_carriage")]
        tip = (zb[0], zb[1], zb[2] - 0.135)          # 笔尖 = z 滑块下方 0.135m
        if tip[2] > PAPER_TOP + 0.012:               # 未落笔（离纸 >1.2cm 不留痕）
            return
        if ink_last is not None and \
                (tip[0] - ink_last[0]) ** 2 + (tip[1] - ink_last[1]) ** 2 < 0.004 ** 2:
            return                                   # 与上个墨点太近，不重复留痕
        g = viewer_ctx.user_scn.geoms[ink_next % INK_MAX]
        mujoco.mjv_initGeom(
            g, mujoco.mjtGeom.mjGEOM_SPHERE,
            np.array([0.004, 0.004, 0.004]),
            np.array([tip[0], tip[1], PAPER_TOP + 0.0015]),
            np.eye(3).flatten(),
            np.array([0.1, 0.1, 0.55, 1.0], dtype=np.float32))
        ink_next += 1
        viewer_ctx.user_scn.ngeom = min(ink_next, INK_MAX)
        ink_last = tip

    try:
        while viewer_ctx is None or viewer_ctx.is_running():
            t0 = time.perf_counter()
            n_watch += 1
            for a, target in bridge.read_commands().items():
                step = axis_speed * frame
                cur = last_cmd[a]
                delta = target - cur
                new = target if abs(delta) <= step else \
                    cur + step * (1.0 if delta > 0 else -1.0)
                data.ctrl[aid[a]] = np.clip(new, 0.0, travel[a])
                last_cmd[a] = new
            mujoco.mj_step(model, data)
            bridge.write_positions({a: float(data.qpos[jid[a]]) for a in AXES})
            if viewer_ctx is not None:
                drop_ink()
                if n_watch % 2 == 0:
                    viewer_ctx.sync()                # 60Hz 同步足够流畅
                if n_watch == 30:
                    # 初始机位（启动自动对焦之后再设才不被覆盖）：3/4 俯视工作区
                    # 中心——默认平视角度下纸面几乎侧对镜头，落笔墨迹不可见
                    viewer_ctx.cam.azimuth = 135.0
                    viewer_ctx.cam.elevation = -42.0
                    viewer_ctx.cam.distance = 1.9
                    viewer_ctx.cam.lookat[:] = np.array([gx + travel["X"] / 2,
                                                         gy + travel["Y"] / 2, 0.06])
                    viewer_ctx.sync()
            if args.watch and n_watch % int(args.physics_hz * 2) == 0:
                fb = bridge.read_positions()
                print(" ".join(f"Axis{a}: cmd={last_cmd[a]:.3f} fb={fb[a]:.3f}"
                               for a in AXES), flush=True)
            remain = frame - (time.perf_counter() - t0)
            if remain > 0:
                time.sleep(remain)
    except KeyboardInterrupt:
        print("\nCtrl+C，停止中…")
    finally:
        if viewer_ctx is not None:
            viewer_ctx.close()
        bridge.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
