#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Modbus 冒烟验证：读 %QW0（cnt, INT16, 保持寄存器 0），确认每秒 +1。

用法:
    python src/pipeline/verify_modbus.py [--host 127.0.0.1] [--port 502]
退出码 0 = 验证通过；1 = 失败（含未连接）。
"""

import argparse
import sys
import time


def read_register(client, reg=0):
    """读单个保持寄存器（pymodbus 2.x/3.x 兼容）。"""
    try:
        rr = client.read_holding_registers(address=reg, count=1)
    except TypeError:
        rr = client.read_holding_registers(reg, 1)
    if rr.isError():
        raise RuntimeError("Modbus 读失败: %s" % rr)
    return rr.registers[0]


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

    # 程序身份校验：counter 的 prog_id=1（%QW20）
    got_id = read_register(client, reg=20)
    if got_id != 1:
        print("[verify] 程序不匹配：期望 counter (prog_id=1)，当前 prog_id=%r ——"
              " 请先 python src/pipeline/run_deploy.py --xml src/plc/counter.xml" % got_id)
        client.close()
        return 1
    print("[verify] 程序身份确认: counter (prog_id=1)")

    try:
        values = []
        for i in range(args.samples):
            v = read_register(client)
            # INT16 有符号解释
            if v > 32767:
                v -= 65536
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
