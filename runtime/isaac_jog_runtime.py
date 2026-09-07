#!/usr/bin/env python
"""独立运行时：不开 Script Editor，一个进程同时承载「Isaac 物理仿真 + Modbus 服务端」。

这是闭环的正式形态（csk 文档 §3.3 run_sim 骨架的 jog 版）：进程自己启动
SimulationApp、加载 scene.usda、逐物理步推 World；每帧把指令寄存器写入关节驱动
目标、把轴位置回写反馈寄存器。外部照旧用 gantry_jog_gui.py（或未来的 OpenPLC 桥）
通过 Modbus TCP 连接，无需编辑器、无需粘贴脚本。

用法（Isaac Sim 6.0 的 Python 环境；pip 元包装法直接用 python，包装版用 python.sh）：
    python isaac_jog_runtime.py --scene ../scenegen/out/gantry/scene.usda            # 无头
    python isaac_jog_runtime.py --scene ... --window                                # 带窗口观察
    python isaac_jog_runtime.py --scene ... --host 0.0.0.0 --port 5020              # 默认值
停止：Ctrl+C（或 kill）。
"""

import argparse
import os
import sys
import time

os.environ.setdefault("OMNI_KIT_ACCEPT_EULA", "YES")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from gantry_bridge import GantryBridge, derive_layout, load_io_map  # noqa: E402
from stage_link import StageLink  # noqa: E402

DEFAULT_IO_MAP = os.path.normpath(
    os.path.join(HERE, "..", "scenegen", "out", "gantry", "io_map.json"))


def parse_args():
    p = argparse.ArgumentParser(description="龙门独立运行时（物理仿真 + Modbus 服务端）")
    p.add_argument("--scene", required=True, help="scene.usda 路径")
    p.add_argument("--io-map", default=DEFAULT_IO_MAP,
                   help="io_map.json（推导寄存器布局；缺省用仓库 gantry 场景）")
    p.add_argument("--host", default="0.0.0.0", help="Modbus 绑定地址")
    p.add_argument("--port", type=int, default=5020)
    p.add_argument("--physics-hz", type=int, default=60, help="物理步频（默认 60Hz）")
    p.add_argument("--window", action="store_true",
                   help="带 Isaac 窗口渲染（便于人工观察）；缺省 headless 最快")
    return p.parse_args()


def main():
    args = parse_args()
    io_map = load_io_map(args.io_map if os.path.isfile(args.io_map) else None)
    layout, n_regs = derive_layout(io_map)

    # SimulationApp 必须最先创建，其后才能 import 其它 isaacsim 模块
    from isaacsim.simulation_app import SimulationApp
    app = SimulationApp({"headless": not args.window})
    try:
        import omni.usd
        from isaacsim.core.api import World

        if not omni.usd.get_context().open_stage(args.scene):
            raise FileNotFoundError(f"无法打开场景: {args.scene}")
        world = World(physics_dt=1.0 / args.physics_hz, rendering_dt=1 / 30)
        world.reset()
        stage = omni.usd.get_context().get_stage()

        bridge = GantryBridge(layout, n_regs, host=args.host, port=args.port)
        link = StageLink(stage, bridge, io_map)
        # 开场指令与场景初始位一致：X/Y 原点、Z 抬笔（客户端连上后由它接管）
        bridge.set_commands({a: (link.travel[a] if a == "Z" else 0.0) for a in layout})
        bridge.serve_forever()
        print("Modbus server ready (in-process, no Script Editor)")
        print(bridge.describe())
        print(f"physics: {args.physics_hz}Hz, scene: {args.scene}")

        frame = 1.0 / args.physics_hz
        try:
            while True:
                t0 = time.perf_counter()
                link.apply_once(verbose=True)
                world.step(render=bool(args.window))
                remain = frame - (time.perf_counter() - t0)
                if remain > 0:
                    time.sleep(remain)
        except KeyboardInterrupt:
            print("\nCtrl+C，停止中…")
        finally:
            bridge.stop()
    finally:
        app.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
