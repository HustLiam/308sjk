#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Modbus 冒烟验证：读 %QW0 (cnt, DINT, 占保持寄存器 0~1)，确认每秒 +1。

用法:
    python src/pipeline/verify_modbus.py [--host 127.0.0.1] [--port 502]
退出码 0 = 验证通过；1 = 失败（含未连接）。
"""

import argparse
import struct
import sys
import time


def read_counter(client, reg=0):
    """读 2 个保持寄存器并按小端字序拼成 int32（OpenPLC 的 %QW DINT 布局）。"""
    try:  # pymodbus >= 3.x
        rr = client.read_holding_registers(reg, 2)
    except TypeError:  # pymodbus 2.x
        rr = client.read_holding_registers(address=reg, count=2)
    if rr.isError():
        raise RuntimeError("Modbus 读失败: %s" % rr)
    lo, hi = rr.registers[0], rr.registers[1]
    return struct.unpack("<i", struct.pack("<HH", lo, hi))[0]


def main():
    parser = argparse.ArgumentParser(description="Verify counter via Modbus TCP")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=502)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    try:
        from pymodbus.client import ModbusTcpClient
    except ImportError:
        print("[verify] 缺少 pymodbus：pip install pymodbus")
        return 1

    client = ModbusTcpClient(args.host, port=args.port)
    if not client.connect():
        print("[verify] 无法连接 %s:%d —— OpenPLC 运行时在跑吗？" % (args.host, args.port))
        return 1

    try:
        values = []
        for i in range(args.samples):
            v = read_counter(client)
            values.append(v)
            print("[verify] sample %d: cnt = %d" % (i + 1, v))
            if i < args.samples - 1:
                time.sleep(args.interval)
    except Exception as e:
        print("[verify] 读取异常: %s" % e)
        return 1
    finally:
        client.close()

    delta = values[-1] - values[0]
    expect_min = max(1, int(round((args.samples - 1) * args.interval)) - 1)
    if delta >= expect_min:
        print("[verify] 通过：cnt 在 %.1f 秒内 +%d，PLC 在运行 ✅"
              % ((args.samples - 1) * args.interval, delta))
        return 0
    print("[verify] 失败：cnt 变化量 %d 不符合每秒+1 的预期（程序没在跑或地址不对）" % delta)
    return 1


if __name__ == "__main__":
    sys.exit(main())
