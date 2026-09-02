"""Modbus 回环无头测试：起 GantryBridge 服务端（与 isaac_modbus_server.py 同一核心），
用 gantry_jog_gui.GantryModbusClient 按协议地址写指令 / 读反馈，验证整条数据链路
与地址推导。不打开 GUI、不需要 Isaac Sim、不需要 pxr。

运行：
    python runtime/tests/test_modbus_loop.py            # 独立脚本
    python -m pytest runtime/tests/test_modbus_loop.py  # pytest

服务端从站 API 锁 pymodbus>=3.7,<3.9（见 runtime/requirements.txt）。
"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymodbus.client import ModbusTcpClient  # noqa: E402

from gantry_bridge import GantryBridge, derive_layout, load_io_map, pack_f32, unpack_f32  # noqa: E402

PORT = 15020
_port_seq = iter(range(PORT, PORT + 50))
HERE = os.path.dirname(os.path.abspath(__file__))
REPO_IO_MAP = os.path.normpath(os.path.join(
    HERE, "..", "..", "scenegen", "out", "gantry", "io_map.json"))


def make_bridge(port=None, io_map_path=REPO_IO_MAP):
    port = port or next(_port_seq)      # 每用例独立端口：旧 listener 释放不及时不影响后续用例
    io_map = load_io_map(io_map_path if os.path.isfile(io_map_path) else None)
    layout, n_regs = derive_layout(io_map)
    bridge = GantryBridge(layout, n_regs, host="127.0.0.1", port=port)
    bridge.serve_forever()          # 返回即端口可连
    return bridge


def make_client(port):
    cli = ModbusTcpClient("127.0.0.1", port=port, timeout=2.0)
    assert cli.connect(), "客户端连接失败"
    return cli


def first_write(cli, address, values, tries=10):
    """连接后首个事务可能早于服务端应用层就绪，短暂重试。"""
    for _ in range(tries):
        rr = cli.write_registers(address=address, values=values, slave=1)
        if not rr.isError():
            return rr
        time.sleep(0.2)
    return rr


def wait_for(getter, expect, timeout=5.0, tol=1e-6):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if abs(getter() - expect) < tol:
                return True
        except Exception:
            pass
        time.sleep(0.05)
    return False


# ---------------- pytest 入口 ----------------

def test_derive_layout_from_repo_io_map():
    io_map = load_io_map(REPO_IO_MAP)
    assert io_map, f"找不到仓库 io_map: {REPO_IO_MAP}"
    layout, n_regs = derive_layout(io_map)
    assert n_regs == 12                      # 传感区块 6 + 指令区 6
    for axis, pos, cmd in (("X", 0, 6), ("Y", 2, 8), ("Z", 4, 10)):
        assert layout[axis]["pos_reg"] == pos
        assert layout[axis]["cmd_reg"] == cmd
    assert abs(layout["Z"]["travel"] - 0.2) < 1e-6


def test_client_write_reaches_bridge():
    bridge = make_bridge()
    try:
        cli = make_client(bridge.port)
        try:
            cfg = bridge.layout["X"]
            assert first_write(cli, cfg["cmd_reg"], pack_f32(0.3)).isError() is False
            assert wait_for(lambda: bridge.read_commands()["X"], 0.3), "FC16 写入未到桥"
        finally:
            cli.close()
    finally:
        bridge.stop()


def test_overrange_command_clamped():
    bridge = make_bridge()
    try:
        cli = make_client(bridge.port)
        try:
            cfg = bridge.layout["Y"]
            first_write(cli, cfg["cmd_reg"], pack_f32(5.0))
            assert wait_for(lambda: bridge.read_commands()["Y"],
                            bridge.layout["Y"]["travel"]), "超程指令应钳位到行程上限"
        finally:
            cli.close()
    finally:
        bridge.stop()


def test_positions_readable_via_fc03():
    bridge = make_bridge()
    try:
        cli = make_client(bridge.port)
        try:
            bridge.write_positions({"X": 0.123, "Y": 0.05, "Z": 0.2})
            cfg = bridge.layout["X"]
            rr = cli.read_holding_registers(address=cfg["pos_reg"], count=2, slave=1)
            assert rr.isError() is False
            assert abs(unpack_f32(rr.registers) - 0.123) < 1e-5, "FC03 读回的反馈值不符"
        finally:
            cli.close()
    finally:
        bridge.stop()


def test_client_reconnect():
    """asyncua 时代的痛点回归：断开后重连、重写必须无残留会话问题。"""
    bridge = make_bridge()
    try:
        for _ in range(2):
            cli = make_client(bridge.port)
            try:
                cfg = bridge.layout["Z"]
                first_write(cli, cfg["cmd_reg"], pack_f32(0.1))
                assert wait_for(lambda: bridge.read_commands()["Z"], 0.1)
            finally:
                cli.close()
    finally:
        bridge.stop()


def test_closed_loop_mini():
    """迷你闭环：指令 →（模拟 Isaac 侧跟随）→ 反馈，客户端两侧读写一致。"""
    bridge = make_bridge()
    try:
        cli = make_client(bridge.port)
        stop = threading.Event()

        def fake_isaac():
            while not stop.is_set():
                cmds = bridge.read_commands()
                bridge.write_positions({a: v * 0.5 for a, v in cmds.items()})  # 半程跟随
                time.sleep(0.01)

        worker = threading.Thread(target=fake_isaac, daemon=True)
        worker.start()
        try:
            for axis, target in (("X", 0.4), ("Z", 0.16)):
                cfg = bridge.layout[axis]
                first_write(cli, cfg["cmd_reg"], pack_f32(target))
                deadline = time.time() + 5
                while time.time() < deadline:
                    act = cli.read_holding_registers(
                        address=cfg["pos_reg"], count=2, slave=1).registers
                    if abs(unpack_f32(act) - target * 0.5) < 1e-5:
                        break
                    time.sleep(0.05)
                assert abs(unpack_f32(act) - target * 0.5) < 1e-5, f"Axis{axis} 反馈未跟随"
        finally:
            stop.set()
            cli.close()
    finally:
        bridge.stop()


# ---------------- 独立脚本入口 ----------------

def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS：{t.__name__}")
    print(f"PASS：{len(tests)} 项回环断言全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
