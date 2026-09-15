"""PLC 回环链路无头测试（plc_link，2026-09-15 裁决定稿实现）。

覆盖：
- 换算（归桥）：PLC 工程量定点 INT ↔ SI 米 的线性映射、钳位、1:1 缺省；
- %Q 面地址：iomap.assign_modbus 输入通道落 %QW（不再 %IW）、输出 %QW 步长 1；
- 回环：Modbus 服务端充当 OpenPLC 桩（%QW 写工程量指令）→ PlcLink 换算
  → 仿真桥指令区收到米；仿真桥位置回写 → PlcLink → 桩的反馈寄存器。

运行：
    python runtime/tests/test_plc_link.py            # 独立脚本
    python -m pytest runtime/tests/test_plc_link.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymodbus.client import ModbusTcpClient  # noqa: E402

from gantry_bridge import GantryBridge, derive_layout  # noqa: E402
from plc_link import PlcLink, int_to_m, m_to_int, make_channel  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))

PORT = 15070
_port_seq = iter(range(PORT, PORT + 50))


def _io_map():
    """plotter_cell 的 io_map（6 通道：3 输出指令 + 3 输入反馈）。"""
    import json
    with open(os.path.join(REPO, "scenegen", "out", "plotter_cell", "io_map.json"),
              encoding="utf-8") as f:
        return json.load(f)


def _plc_stand_in():
    """充当 OpenPLC 的裸 Modbus 服务端（%QW 即保持寄存器）。"""
    port = next(_port_seq)
    bridge = GantryBridge({a: {"pos_reg": 0, "cmd_reg": 2 * i, "travel": 1.0}
                           for i, a in enumerate(("X", "Y", "Z"))},
                          6, host="127.0.0.1", port=port)
    bridge.serve_forever()
    return bridge, port


def test_scale_roundtrip_percent_to_meter():
    ch = make_channel({"plc_var": "x_cmd", "dir": "output", "type": "float",
                       "bind": {"quantity": "x_cmd", "range": [0.0, 1.0]},
                       "modbus": {"plc_addr": "%QW0", "plc_encoding": "int16_scaled"}},
                      {"x_cmd": [0, 100]})
    assert abs(ch["m_per_unit"] - 0.01) < 1e-12
    assert abs(int_to_m(50, ch) - 0.5) < 1e-9
    assert m_to_int(0.5, ch) == 50
    assert m_to_int(0.753, ch) == 75            # 四舍五入到工程量
    assert int_to_m(120, ch) == 1.0             # PLC 超量程 → 钳到场景 range
    assert m_to_int(1.5, ch) == 100             # 场景超行程 → 钳到 PLC range


def test_scale_signed_and_default_identity():
    ch = make_channel({"plc_var": "x_v", "dir": "output", "type": "float",
                       "bind": {"quantity": "x_cmd", "range": [-0.2, 0.2]},
                       "modbus": {"plc_addr": "%QW1", "plc_encoding": "int16_scaled"}},
                      {"x_v": [-120, 120]})
    assert abs(int_to_m(-120, ch) + 0.2) < 1e-9
    assert m_to_int(0.1, ch) == 60
    ident = make_channel({"plc_var": "q", "dir": "input", "type": "float",
                          "bind": {"quantity": "z_pos", "range": [0.0, 1.0]},
                          "modbus": {"plc_addr": "%QW2", "plc_encoding": "int16_scaled"}})
    assert ident["m_per_unit"] == 1.0           # 无 spec range → 1:1（寄存器值即米）


def test_iomap_plc_face_all_q_area():
    import importlib.util
    path = os.path.join(REPO, "scenegen", "scenegen", "iomap.py")
    spec = importlib.util.spec_from_file_location("iomap_direct", path)
    iomap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(iomap)
    io = iomap.assign_modbus(_io_map())
    by_var = {e["plc_var"]: e for e in io}
    assert by_var["AxisX_cmd"]["modbus"]["plc_addr"] == "%QW0"     # 输出步长 1
    assert by_var["AxisY_cmd"]["modbus"]["plc_addr"] == "%QW1"
    assert by_var["AxisZ_cmd"]["modbus"]["plc_addr"] == "%QW2"
    assert by_var["AxisX_pos"]["modbus"]["plc_addr"] == "%QW3"     # 输入也落 %QW
    assert by_var["AxisZ_pos"]["modbus"]["plc_addr"] == "%QW5"
    st = iomap.st_io_declaration(io)
    assert "AT %QW0 : INT" in st and "AT %QW3 : INT" in st         # PLC 面 INT 定点
    summary = iomap.modbus_summary(io)
    assert summary["plc_loop"]["read_commands"]["length"] == 3
    assert summary["plc_loop"]["write_feedback"]["start"] == 3


def test_closed_loop_via_plc_stand_in():
    stand_in, plc_port = _plc_stand_in()
    sim = GantryBridge(derive_layout(None)[0], derive_layout(None)[1],
                       host="127.0.0.1", port=next(_port_seq))
    sim.serve_forever()
    try:
        io = _io_map()
        link = PlcLink(sim, io, plc_ranges={"AxisX_cmd": [0, 100], "AxisX_pos": [0, 100]},
                       host="127.0.0.1", port=plc_port)
        # AxisX_cmd：PLC 工程量 [0,100] ↔ 场景 [0,1] 米（scale 0.01）
        cli = ModbusTcpClient("127.0.0.1", port=plc_port, timeout=2.0)
        assert cli.connect()
        cli.write_register(address=0, value=40, slave=1)   # %QW0 ← PLC 写指令（工程量/寄存器值）
        sim.write_positions({"X": 0.7})           # 仿真当前位置 0.7m
        ok = link.pump_once(client=cli)
        assert ok
        cmds = sim.read_commands()
        assert abs(cmds["X"] - 0.4) < 1e-6        # 指令换算进仿真桥
        fb = cli.read_holding_registers(address=3, count=1, slave=1)  # %QW3 反馈寄存器
        assert fb.registers[0] == 70              # 位置换算回工程量
        cli.close()
    finally:
        stand_in.stop()
        sim.stop()


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted({k: v for k, v in globals().items()
                            if k.startswith("test_") and callable(v)}.items()):
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as exc:
            fails += 1
            print(f"FAIL {name}: {exc}")
    raise SystemExit(1 if fails else 0)
