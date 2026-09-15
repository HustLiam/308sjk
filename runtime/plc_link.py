"""PLC 回环链路（2026-09-15 裁决定稿实现，lx 文档 §6.2 / changelog「桥接裁决 v1.0」）。

拓扑：仿真侧桥作 Modbus 客户端轮询 OpenPLC(:502) 的 %Q 区——复用
lx 文档 §5.1 已实测的"外部直接写 %Q"通道（验收脚本同一机制），不经
OpenPLC 自带的 modbus_client 轮询（其 FC03 读固定落 %IW，与契约②冲突）。

每周期（默认 15ms，与 PLC 扫描 8.3ms 错峰）：
- FC03 读输出 %QW（工程量定点 INT16）→ 换算成米 → 写仿真桥指令区；
- FC16 写输入反馈 %QW（仿真位置米 → 工程量定点 INT16）；
- 线圈一律整组读写（FC01 读 / FC15 写完整 16 位跨度——窄范围线圈写
  会破坏相邻位，SafeCoilIO 纪律，lx 文档 §5.2 实测红线）。

换算（归桥裁决）：PLC 工程量 <-> SI 米的线性映射，两端 range 分别取自
PLC spec io_list（工程量）与 scene io_map（米制）；spec 未提供或通道
不在 io_list 时按 1:1（寄存器值即米）。

语义边界：本链路按 io_map 的位置指令通道接线（plotter_cell 形态：
cmd/pos 直接对应）；CSP 速度指令型程序（如 motion3axis 的 x_v 输出）
需要驱动器模型积分速度→位置，属 stage_link 语义（csk 域），不在本类。
"""

import json
import os
import threading
import time

from pymodbus.client import ModbusTcpClient


def _range_of(rst, key):
    r = rst.get(key) if rst else None
    return (float(r[0]), float(r[1])) if r else None


def _axis_of(entry):
    """轴名推导，兼容两种绑定风格：gantry（quantity=x_cmd/pos）与
    linear_axis 契约 v1.1（asset=x_axis、quantity 通用 cmd/pos）。"""
    bind = entry.get("bind", {})
    q = bind.get("quantity", "")
    if q[:1].upper() in ("X", "Y", "Z"):
        return q[:1].upper()
    a = bind.get("asset", "")
    if a.endswith("_axis") and a[:1].upper() in ("X", "Y", "Z"):
        return a[:1].upper()
    return None


def make_channel(entry, plc_ranges=None):
    """io_map 条目 → 换算通道描述。

    entry: io_map 条目（含 modbus.plc_addr 与 bind.range）；
    plc_ranges: {plc_var: [lo, hi]}（取自 PLC spec io_list 的 INT range），
    缺失时按 1:1（寄存器值即米）。"""
    m = entry["modbus"]
    if m.get("plc_encoding") != "int16_scaled":
        return None
    qw = int(m["plc_addr"][3:])
    scene = _range_of(entry.get("bind", {}), "range") or (0.0, 1.0)
    plc = _range_of(plc_ranges, entry["plc_var"]) or scene
    span_plc = (plc[1] - plc[0]) or 1.0
    span_scene = (scene[1] - scene[0]) or 1.0
    return {
        "plc_var": entry["plc_var"], "dir": entry["dir"], "qw": qw,
        "axis": _axis_of(entry),
        "lo_plc": plc[0], "hi_plc": plc[1],
        "lo_scene": scene[0], "hi_scene": scene[1],
        "m_per_unit": span_scene / span_plc,
    }


def int_to_m(value, ch):
    """PLC 工程量定点 INT → 仿真 SI 米（钳位到场景 range）。"""
    v = max(ch["lo_plc"], min(float(value), ch["hi_plc"]))
    m = ch["lo_scene"] + (v - ch["lo_plc"]) * ch["m_per_unit"]
    return max(ch["lo_scene"], min(m, ch["hi_scene"]))


def m_to_int(meters, ch):
    """仿真 SI 米 → PLC 工程量定点 INT（钳位到 PLC range 与 INT16 域）。"""
    v = ch["lo_plc"] + (float(meters) - ch["lo_scene"]) / ch["m_per_unit"]
    v = max(ch["lo_plc"], min(v, ch["hi_plc"]))
    return int(round(max(-32768.0, min(v, 32767.0))))


class PlcLink:
    """桥 ↔ OpenPLC 的 %Q 区轮询回环（数据面挂到 GantryBridge）。"""

    def __init__(self, bridge, io_map, spec=None, plc_ranges=None, host="127.0.0.1",
                 port=502, period_s=0.015):
        if plc_ranges is None and spec:
            with open(spec, encoding="utf-8") as f:
                plc_ranges = {v["name"]: v.get("range")
                              for v in json.load(f).get("io_list", [])}
        self.channels = [c for c in (make_channel(e, plc_ranges) for e in io_map)
                         if c is not None]
        self.out_chs = [c for c in self.channels if c["dir"] == "output"]
        self.in_chs = [c for c in self.channels if c["dir"] == "input"]
        self.coil_bits = sum(1 for e in io_map if e.get("type") == "bool")
        self.bridge = bridge
        self.host, self.port, self.period_s = host, port, period_s
        self.client = ModbusTcpClient(host, port=port)
        self._stop = threading.Event()
        self._thread = None

    # ---------- 单周期（测试可直接调用；client 可注入桩） ----------

    def pump_once(self, client=None):
        client = client or self.client
        out_regs, in_regs = [], []
        if self.out_chs:
            start = min(c["qw"] for c in self.out_chs)
            count = max(c["qw"] for c in self.out_chs) - start + 1
            rr = client.read_holding_registers(address=start, count=count, slave=1)
            if rr.isError():
                return False
            regs = {start + i: v for i, v in enumerate(rr.registers)}
            for c in self.out_chs:
                if c["axis"] and c["axis"] in self.bridge.layout:
                    meters = int_to_m(regs[c["qw"]], c)
                    with self.bridge._lock:
                        from gantry_bridge import pack_f32  # 同目录模块
                        self.bridge.store.setValues(
                            3, self.bridge.layout[c["axis"]]["cmd_reg"], pack_f32(meters))
        if self.in_chs:
            positions = self.bridge.read_positions()
            for c in self.in_chs:
                if c["axis"] and c["axis"] in positions:
                    out_regs.append(m_to_int(positions[c["axis"]], c))
                    in_regs.append(c["qw"])
        if in_regs:
            wr = client.write_registers(address=min(in_regs), values=out_regs, slave=1)
            if wr.isError():
                return False
        return True

    # ---------- 线圈（数字量反馈）：FC15 整组写纪律 ----------

    def write_coil_group(self, values, client=None):
        """整组写线圈（values: {bit_index: 0/1}），跨度取齐 16 位倍数。"""
        client = client or self.client
        if not values:
            return True
        lo, hi = min(values), max(values)
        span = ((hi - lo) // 16 + 1) * 16          # 完整 16 位跨度
        group = [0] * span
        for bit, v in values.items():
            group[bit - lo] = 1 if v else 0
        rr = client.write_coils(address=lo, values=group, slave=1)
        return not rr.isError()

    # ---------- 后台轮询 ----------

    def start(self):
        if not self.client.connect():
            raise ConnectionError(f"OpenPLC {self.host}:{self.port} 不可达（先起容器/运行时）")

        def _loop():
            while not self._stop.is_set():
                try:
                    self.pump_once()
                except Exception:
                    pass                     # 单周期失败不终止回环（下周期重试）
                time.sleep(self.period_s)

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        try:
            self.client.close()
        except Exception:
            pass


def load_io_map(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None
