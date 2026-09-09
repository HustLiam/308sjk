# 在 Isaac Sim 的 Script Editor（Window → Script Editor）中整体运行本脚本（先按 Play）。
# 作用：Isaac 进程内启动 Modbus TCP 服务端（:5020，与 io_map 契约一致），
#       读取轴指令寄存器 → 写关节驱动目标；同时把轴位置回读进反馈寄存器。
# 配套 runtime/gantry_jog_gui.py 鼠标示教器（Modbus 客户端）：拖动画笔 → 写轴指令。
#
# 【不想到 Script Editor 里跑？】用独立运行时：runtime/isaac_jog_runtime.py
#   （一个进程同时承载物理仿真 + Modbus 服务端，见其文件头）。
#
# 使用步骤（编辑器工作流）：
#   1. 打开（重生成后的）gantry scene.usda，按 Play（物理运行中关节驱动才生效）
#   2. Script Editor 粘贴运行本脚本；Console 出现 "Modbus server ready" 与寄存器表
#      （首次运行自动 pipapi 安装 pymodbus<3.9；粘贴运行前把下方 HERE 兜底路径
#        改成 runtime 目录——目录里须有 gantry_bridge.py / stage_link.py）
#   3. 运行 gantry_jog_gui.py（同机或局域网均可，--host 填本机 IP），拖动画笔
# 停止：运行 modbus_stop()；或重启 Script Editor。
#
# 说明：开环演示链路为「GUI/PLC → :5020 指令寄存器 → 关节驱动 → 位置反馈寄存器」，
# 与主方案 §3.4.2 的 OpenPLC 闭环同一寄存器语义（传感区块 FC03 → %IW0 轮询照抄
# modbus_summary.json），仅指令来源由 OpenPLC %QW 换成手动写入。

import os
import sys
import threading
import time

import omni.kit.pipapi
import omni.usd

try:
    import pymodbus  # noqa: F401
except ImportError:
    omni.kit.pipapi.install("pymodbus<3.9")     # 服务端从站 API 锁经典 3.x 线
    import pymodbus  # noqa: F401

try:
    HERE = os.path.dirname(os.path.abspath(__file__))
except NameError:
    HERE = r"D:\001xmz\runtime"   # ← Script Editor 粘贴运行时改成 runtime 目录
sys.path.insert(0, HERE)
from gantry_bridge import GantryBridge, derive_layout, load_io_map  # noqa: E402
from stage_link import StageLink  # noqa: E402

stage = omni.usd.get_context().get_stage()

# ---- 布局：优先读仓库 io_map.json，读不到用内置默认 ----
_io_map = load_io_map(os.path.join(HERE, "..", "scenegen", "out", "gantry", "io_map.json"))
layout, n_regs = derive_layout(_io_map)

bridge = GantryBridge(layout, n_regs, host="0.0.0.0", port=5020)
link = StageLink(stage, bridge, _io_map)
# 开场指令与场景初始位一致：X/Y 原点、Z 抬笔（客户端连上后由它接管）
bridge.set_commands({a: (link.travel[a] if a == "Z" else 0.0) for a in layout})

_stop = False


def _apply_loop():
    """20Hz：指令经 axisSpeed 速率限制写驱动目标；位置每帧回写反馈寄存器。"""
    while not _stop:
        try:
            link.apply_once(dt=0.05, verbose=True)
        except Exception as _exc:                     # 单帧异常不终止回环
            print(f"[modbus] loop warn: {_exc}")
        time.sleep(0.05)


bridge.serve_forever()
threading.Thread(target=_apply_loop, daemon=True).start()
print("Modbus server ready (pymodbus " + pymodbus.__version__ + ", Script Editor 工作流)")
print(bridge.describe())
print("  OpenPLC 轮询配置照抄 scenegen/out/gantry/modbus_summary.json")


def modbus_stop():
    global _stop
    _stop = True
    bridge.stop()
    print("Modbus server stopped")
