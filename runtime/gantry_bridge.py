"""龙门三轴 Modbus TCP 回环桥核心（Isaac 服务端语义，与 scenegen io_map 契约一致）。

寄存器布局（全部保持寄存器，float32 大端，与 iomap.assign_modbus 的 encoding 一致）：

  [0, sensor_regs)            轴位置反馈（x/y/z_pos）：Isaac 每帧写入；
                              OpenPLC 主站轮询（FC03 → %IW0）/ jog GUI 读取
  [sensor_regs, +6)           轴指令（x/y/z_cmd）：jog GUI（FC06/FC16 写入）或
                              OpenPLC %QW 桥写入；Isaac 读取后驱动关节目标

布局由 io_map.json 推导（derive_layout）：位置区地址 = 契约中的 server_register，
指令区紧随传感区块之后。io_map 缺省时使用 out/gantry 场景的内置布局。

版本约束：服务端从站 API 锁 pymodbus>=3.7,<3.9（3.13+ 移除了
ModbusSlaveContext.getValues/setValues，见 runtime/requirements.txt）；
客户端（GUI）只用 ModbusTcpClient，兼容任意 3.x。
"""

import json
import os
import struct
import threading

from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusServerContext,
    ModbusSlaveContext,
)
from pymodbus.server import StartAsyncTcpServer

AXES = ("X", "Y", "Z")                       # 固定轴序：布局推导与寄存器排布都依赖它
DEFAULT_TRAVEL = {"X": 0.6, "Y": 0.4, "Z": 0.2}   # out/gantry 场景的行程（米）


def derive_layout(io_map=None, travel=None):
    """从 io_map.json 推导寄存器布局。返回 (layout, n_regs)。

    layout: {axis: {"pos_reg", "cmd_reg", "travel"}}，位置区按契约 server_register，
    指令区 = 传感区块总长 + 2*轴序号。io_map 为 None 时用内置默认布局。"""
    travel = {**DEFAULT_TRAVEL, **(travel or {})}
    pos_reg = {a: 2 * i for i, a in enumerate(AXES)}   # 缺省：0/2/4
    sensor_end = 6
    for entry in io_map or []:
        m = entry.get("modbus", {})
        if m.get("area") != "sensor_block":
            continue
        quantity = entry.get("bind", {}).get("quantity", "")
        axis = quantity[0].upper() if quantity[:1] else None
        if axis in AXES:
            pos_reg[axis] = int(m["server_register"])
            sensor_end = max(sensor_end, int(m["server_register"]) + int(m.get("length", 2)))
    layout = {a: {"pos_reg": pos_reg[a], "cmd_reg": sensor_end + 2 * i,
                  "travel": float(travel[a])}
              for i, a in enumerate(AXES)}
    return layout, sensor_end + 2 * len(AXES)


def load_io_map(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


import os  # noqa: E402  （load_io_map 使用；置于版本约束说明之后）


def pack_f32(value: float):
    """float32 → 2 个保持寄存器（大端字序，与 %QW REAL 约定一致）。"""
    return list(struct.unpack(">2H", struct.pack(">f", float(value))))


def unpack_f32(registers) -> float:
    return struct.unpack(">f", struct.pack(">2H", int(registers[0]), int(registers[1])))[0]


class GantryBridge:
    """Modbus TCP 服务端 + 仿真侧数据面。

    - serve_forever(host, port)：后台线程起 FC03/06/16 服务（默认 :5020，
      与 modbus_summary.openplc_polling.server 一致）；
    - 仿真主循环每帧调用 read_commands() 取指令、write_positions() 回写反馈；
    - datastore 同时就是契约中的"Isaac 传感区块"，OpenPLC 轮询配置照抄
      modbus_summary.json 即可。"""

    def __init__(self, layout, n_regs, host="0.0.0.0", port=5020):
        self.layout = layout
        self.n_regs = n_regs
        self.host, self.port = host, port
        self._lock = threading.Lock()
        # 从站上下文的 get/set 内部统一 +1（服务端请求与本类数据面走同一方法，
        # 协议地址与 io_map 寄存器号保持一致）；块从 0 起多留 1 字覆盖偏移。
        block = ModbusSequentialDataBlock(0, [0] * (n_regs + 1))
        self.store = ModbusSlaveContext(hr=block)
        self.context = ModbusServerContext(slaves=self.store, single=True)
        self._loop = None
        self._thread = None
        self.ready = threading.Event()

    # ---------- 数据面（仿真主循环 / 测试调用） ----------

    def read_commands(self) -> dict:
        """读三轴指令（米），按各自行程钳位。"""
        out = {}
        with self._lock:
            for axis, cfg in self.layout.items():
                raw = self.store.getValues(3, cfg["cmd_reg"], 2)
                v = unpack_f32(raw)
                out[axis] = max(0.0, min(v, cfg["travel"]))
        return out

    def set_commands(self, values: dict) -> None:
        """直接写 datastore 的指令区（等效客户端 FC16；测试/无客户端场景用）。"""
        with self._lock:
            for axis, v in values.items():
                cfg = self.layout[axis]
                self.store.setValues(3, cfg["cmd_reg"], pack_f32(v))

    def write_positions(self, values: dict) -> None:
        """仿真回写轴位置反馈（米）。"""
        with self._lock:
            for axis, v in values.items():
                cfg = self.layout[axis]
                self.store.setValues(3, cfg["pos_reg"], pack_f32(v))

    def read_positions(self) -> dict:
        with self._lock:
            return {a: unpack_f32(self.store.getValues(3, c["pos_reg"], 2))
                    for a, c in self.layout.items()}

    # ---------- 服务面 ----------

    def serve_forever(self):
        """后台线程启动 Modbus TCP 服务，端口可连（就绪）后才返回。"""
        import asyncio
        import socket
        import time

        started = threading.Event()

        def _run():
            # Windows 默认 Proactor 循环关停噪声大，用 Selector 循环更干净
            loop = asyncio.SelectorEventLoop() if hasattr(asyncio, "SelectorEventLoop") \
                else asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            started.set()
            loop.run_forever()            # StartAsyncTcpServer 在此循环上常驻服务

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        started.wait(timeout=5)
        self._serve = asyncio.run_coroutine_threadsafe(
            StartAsyncTcpServer(self.context, address=(self.host, self.port)), self._loop)
        # 就绪 = 端口可建立 TCP 连接（pymodbus 3.x 的 Start* 协程常驻，无完成回调）
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                with socket.create_connection((self._bind_host(), self.port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            raise RuntimeError(f"Modbus 服务端 {self.host}:{self.port} 未能在 10s 内就绪")
        time.sleep(0.25)   # TCP 背板队列可先于应用层就绪，稍作稳定再返回

    def _bind_host(self):
        return "127.0.0.1" if self.host in ("0.0.0.0", "") else self.host

    def stop(self):
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def stop(self):
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def describe(self) -> str:
        lines = [f"Modbus TCP {self.host}:{self.port}  (float32 大端, 1 寄存器 = 16bit)"]
        for axis, cfg in sorted(self.layout.items()):
            lines.append(f"  Axis{axis}: pos @{cfg['pos_reg']}-{cfg['pos_reg'] + 1}"
                         f"  cmd @{cfg['cmd_reg']}-{cfg['cmd_reg'] + 1}"
                         f"  travel 0..{cfg['travel']} m")
        return "\n".join(lines)
