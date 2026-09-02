# 在 Isaac Sim 的 Script Editor（Window → Script Editor）中整体运行本脚本。
# 作用：在 Isaac 进程内启动一个 OPC UA 服务端（Objects/Gantry 下暴露轴指令节点）。
# 配套 runtime/gantry_jog_gui.py 鼠标示教器：拖动画笔 → 写 AxisX/Y_cmd → 轴运动。
#
# 使用步骤：
#   1. 打开 gantry scene.usda，先按 Play（物理运行中驱动才生效）
#   2. Script Editor 粘贴运行本脚本，Console 出现 "OPC UA server ready"
#      （首次运行自动 pipapi 安装 asyncua；失败则用 Isaac 的 python.sh -m pip install asyncua）
#   3. 运行 gantry_jog_gui.py（同机或异机均可，端点填本机 IP），拖动画笔
# 停止：运行 srv_stop() ；或重启 Script Editor。

import threading
import time

import omni.kit.pipapi
import omni.usd

try:
    from asyncua import sync
except ImportError:
    omni.kit.pipapi.install("asyncua")          # 首次运行自动装入 Kit Python 环境
    from asyncua import sync

stage = omni.usd.get_context().get_stage()

# var 名: (关节 prim 路径, 驱动 token, 量程上限 m) —— 与 io_map.json 的 usd_prim 一致
AXES = {
    "AxisX_cmd": ("/World/gantry_1/joint_x", "transX", 0.6),
    "AxisY_cmd": ("/World/gantry_1/joint_y", "transY", 0.4),
    "AxisZ_cmd": ("/World/gantry_1/joint_z", "transZ", 0.2),
}

_srv = sync.Server()
_srv.set_endpoint("opc.tcp://0.0.0.0:4840/isaac/gantry")
_srv.set_server_name("IsaacSim-Gantry-Demo")
_idx = _srv.register_namespace("gantry")
_gantry_obj = _srv.nodes.objects.add_object(_idx, "Gantry")   # Objects/Gantry/...
_nodes = {}
for _name, (_path, _tok, _travel) in AXES.items():
    _n = _gantry_obj.add_variable(_idx, _name, 0.0)
    _n.set_writable()
    _nodes[_name] = (_n, _path, _tok, _travel)

_stop = False


def _apply_loop():
    """轮询读节点值并写入关节驱动目标（100ms 级，足够演示；正式闭环走 runtime 桥）。"""
    _last = {k: None for k in _nodes}
    while not _stop:
        for _name, (_n, _path, _tok, _travel) in _nodes.items():
            try:
                _v = float(_n.read_value())
            except Exception:
                continue
            if _v != _last[_name]:
                _last[_name] = _v
                _clamped = max(0.0, min(_v, _travel))
                _attr = stage.GetPrimAtPath(_path).GetAttribute(
                    f"drive:{_tok}:physics:targetPosition")
                _attr.Set(_clamped)
                print(f"[opcua] {_name} -> {_clamped:.3f} m")
        time.sleep(0.1)


threading.Thread(target=_apply_loop, daemon=True).start()
_srv.start()
print("OPC UA server ready: opc.tcp://<this-host>:4840/isaac/gantry")
print("  nodes: Objects/Gantry/{AxisX_cmd, AxisY_cmd, AxisZ_cmd}  (namespace uri: gantry)")


def srv_stop():
    global _stop
    _stop = True
    _srv.stop()
    print("OPC UA server stopped")
