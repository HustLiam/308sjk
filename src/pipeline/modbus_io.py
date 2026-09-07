#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Modbus 安全 IO 层（OpenPLC 怪癖适配）。

实测结论（见 docs/部署手册-OpenPLC.md §6）：OpenPLC 的 Modbus 服务端在窄范围
线圈写入（fc05 单线圈 / fc15 少量线圈）时会破坏同缓冲区的相邻位（例如写
%QX0.3 会把 %QX1.0 的电机自锁打掉）。读-改-写整组线圈（fc15 覆盖完整跨度）
则完全正常。

本模块把这一怪癖封装掉：所有线圈写入一律 RMW 整组；未来的 Isaac Sim 桥接
必须经由此层访问线圈，禁止裸 write_coil/write_coils(窄范围)。
"""

from pymodbus.client import ModbusTcpClient


class SafeCoilIO:
    """读-改-写安全的线圈访问。span 为一次写回覆盖的线圈数（默认 16 的整数倍）。"""

    def __init__(self, client, span=16):
        self.c = client
        self.span = max(16, span)

    def read(self, bit):
        rr = self._read(bit)
        return bool(rr.bits[0])

    def read_many(self, start, count):
        return [bool(b) for b in self._read(start, count).bits[:count]]

    def write(self, bit, value):
        """写单个线圈：读整组 -> 改一位 -> 整组写回。"""
        base = (bit // self.span) * self.span
        cur = [bool(b) for b in self._read(base, self.span).bits[:self.span]]
        cur[bit - base] = bool(value)
        self.c.write_coils(address=base, values=cur)

    def pulse(self, bit, seconds=0.15):
        """打一个脉冲：置位 -> 保持 -> 复位（用于模拟按钮/光电信号）。"""
        import time
        self.write(bit, True)
        time.sleep(seconds)
        self.write(bit, False)
        time.sleep(0.1)

    def _read(self, address, count=1):
        try:
            return self.c.read_coils(address=address, count=count)
        except TypeError:  # pymodbus 2.x
            return self.c.read_coils(address, count)


def read_reg(client, reg):
    """读单个保持寄存器并按 INT16 解释（pymodbus 2.x/3.x 兼容）。"""
    try:
        rr = client.read_holding_registers(address=reg, count=1)
    except TypeError:
        rr = client.read_holding_registers(reg, 1)
    if rr.isError():
        raise RuntimeError("Modbus 读失败: %s" % rr)
    v = rr.registers[0]
    return v - 65536 if v > 32767 else v


PROG_ID_REG = 20  # 场景程序身份寄存器（每个 PLC_PRG 每扫描周期写自己的 prog_id 常量）


def require_program(client, prog_id, name):
    """校验运行时当前加载的是预期场景程序，不匹配直接终止。

    避免对错误程序注入激励得到一堆莫名其妙的 FAIL。"""
    got = read_reg(client, PROG_ID_REG)
    if got != prog_id:
        print("[verify] 程序不匹配：期望 %s (prog_id=%d)，运行时当前 prog_id=%r"
              " —— 请先 python src/pipeline/run_deploy.py --xml src/plc/<对应场景>.xml"
              % (name, prog_id, got))
        raise SystemExit(1)
    print("[verify] 程序身份确认: %s (prog_id=%d)" % (name, prog_id))


def zero_regs(client, *regs):
    """清零保持寄存器（场景幂等性：PLC 内部维护的计数器归零，重复运行可复现）。"""
    for r in regs:
        client.write_register(address=r, value=0)


def connect(host="127.0.0.1", port=502):
    c = ModbusTcpClient(host, port=port)
    if not c.connect():
        raise ConnectionError("无法连接 Modbus %s:%d" % (host, port))
    return c
