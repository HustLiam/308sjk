# 在 Isaac Sim 的 Script Editor（Window → Script Editor）中整体运行本脚本（先按 Play）。
# 作用：Isaac 进程内启动 Modbus TCP 服务端（:5020，与 io_map 契约一致），
#       读取轴指令寄存器 → 写关节驱动目标；同时把轴位置回读进反馈寄存器。
# 配套 runtime/gantry_jog_gui.py 鼠标示教器（Modbus 客户端）：拖动画笔 → 写轴指令。
#
# 使用步骤：
#   1. 打开（重生成后的）gantry scene.usda，按 Play（物理运行中关节驱动才生效）
#   2. Script Editor 粘贴运行本脚本；Console 出现 "Modbus server ready" 与寄存器表
#      （首次运行自动 pipapi 安装 pymodbus<3.9；注意本脚本所在目录须包含
#        gantry_bridge.py——粘贴运行前把下方 HERE 兜底路径改成 runtime 目录）
#   3. 运行 gantry_jog_gui.py（同机或局域网均可，--host 填本机 IP），拖动画笔
# 停止：运行 modbus_stop()；或重启 Script Editor。
#
# 说明：开环演示链路为「GUI/PLC → :5020 指令寄存器 → 关节驱动 → 位置反馈寄存器」，
# 与主方案 §3.4.2 的 OpenPLC 闭环同一寄存器语义（传感区块 FC03 → %IW0 轮询照抄
# modbus_summary.json），仅指令来源由 OpenPLC %QW 换成手动写入。

import os
import sys
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
    HERE = r"D:\001xmz\runtime"   # ← Script Editor 粘贴运行时改成 gantry_bridge.py 所在目录
sys.path.insert(0, HERE)
from gantry_bridge import AXES, GantryBridge, derive_layout, load_io_map  # noqa: E402

stage = omni.usd.get_context().get_stage()

# ---- 定位 gantry 根 prim（simio:assetType == "gantry_xyz"）并读构建期标记 ----
_gantry = None
for _prim in stage.Traverse():
    if _prim.GetAttribute("simio:assetType").Get() == "gantry_xyz":
        _gantry = _prim
        break
assert _gantry is not None, "场景中找不到 simio:assetType == gantry_xyz 的组件"

_travel = {a: float(_gantry.GetAttribute(f"simio:travel{a}").Get()) for a in AXES}
_pos_body = list(_gantry.GetAttribute("simio:posBody").Get())      # [x/y/z 回读刚体路径]
_pos_rest = list(_gantry.GetAttribute("simio:posRest").Get())      # 关节零位坐标分量

# ---- 布局：优先读仓库 io_map.json，读不到用内置默认 ----
_io_map = load_io_map(os.path.join(HERE, "..", "scenegen", "out", "gantry", "io_map.json"))
layout, n_regs = derive_layout(_io_map, _travel)

bridge = GantryBridge(layout, n_regs, host="0.0.0.0", port=5020)

# 指令 → 关节驱动属性（io_map 的 usd_prim 即关节 prim；token 按轴名）
_cmd_attr = {}
if _io_map:
    for _e in _io_map:
        _q = _e["bind"]["quantity"]
        if _q.endswith("_cmd"):
            _a = _q[0].upper()
            _cmd_attr[_a] = (_e["usd_prim"], f"drive:trans{_a}:physics:targetPosition")
else:
    _root_path = str(_gantry.GetPath())
    for _a in AXES:
        _cmd_attr[_a] = (f"{_root_path}/joint_{_a.lower()}",
                         f"drive:trans{_a}:physics:targetPosition")

_body_prim = {a: stage.GetPrimAtPath(p) for a, p in zip(AXES, _pos_body)}


def _read_pos(axis: str) -> float:
    """刚体 translate 分量 − 关节零位坐标 = 关节坐标 q（米），钳位到行程。"""
    t = _body_prim[axis].GetAttribute("xformOp:translate").Get()
    i = AXES.index(axis)
    q = float(t[i]) - float(_pos_rest[i])
    return max(0.0, min(q, _travel[axis]))


_stop = False


def _apply_loop():
    """20Hz：指令变化才写驱动目标；位置每帧回写反馈寄存器。"""
    _last = {a: None for a in AXES}
    while not _stop:
        try:
            cmds = bridge.read_commands()
            for a, v in cmds.items():
                if _last[a] is None or abs(v - _last[a]) > 1e-5:
                    _last[a] = v
                    _path, _attr = _cmd_attr[a]
                    stage.GetPrimAtPath(_path).GetAttribute(_attr).Set(v)
                    print(f"[modbus] Axis{a}_cmd -> {v:.3f} m")
            bridge.write_positions({a: _read_pos(a) for a in AXES})
        except Exception as _exc:                     # 单帧异常不终止回环
            print(f"[modbus] loop warn: {_exc}")
        time.sleep(0.05)


import threading  # noqa: E402

bridge.serve_forever()
threading.Thread(target=_apply_loop, daemon=True).start()
print("Modbus server ready (pymodbus " + pymodbus.__version__ + ")")
print(bridge.describe())
print("  OpenPLC 轮询配置照抄 scenegen/out/gantry/modbus_summary.json")


def modbus_stop():
    global _stop
    _stop = True
    bridge.stop()
    print("Modbus server stopped")
