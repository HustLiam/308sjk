#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分拣线场景验收：src/plc/sorting.xml 的集成冒烟测试（扮演 Isaac Sim 桥接角色）。

前置：OpenPLC 运行时已启动，且已部署 sorting.xml
    python src/pipeline/run_deploy.py --xml src/plc/sorting.xml
运行：
    python src/pipeline/scenario_sorting.py

地址表（sorting.xml 契约）：
    线圈输入侧  %QX0.0 启动  0.1 停止  0.2 急停  0.3 入料光电  0.4 废品光电
    线圈输出侧  %QX1.0 电机  1.1 推料  1.2 报警
    保持寄存器  %QW10 总数   %QW11 次品数
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from modbus_io import SafeCoilIO, connect, read_reg  # noqa: E402

START, STOP, ESTOP, PIN, PREJ = 0, 1, 2, 3, 4
MOTOR, PUSHER, ALARM = 8, 9, 10
TOTAL, REJ = 10, 11


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    m = connect()
    io = SafeCoilIO(m)
    ok = True

    def check(name, cond):
        nonlocal ok
        print(("  PASS " if cond else "  FAIL ") + name)
        ok = ok and cond

    # 复位所有输入
    for bit in (START, STOP, ESTOP, PIN, PREJ):
        io.write(bit, False)
    time.sleep(0.3)

    print("[1] 启保停：启动 → 电机运行并自锁")
    io.pulse(START)
    time.sleep(0.2)
    check("motor=TRUE", io.read(MOTOR))
    check("自锁保持（启动已松开）", io.read(MOTOR))

    print("[2] 入料计数：两次光电脉冲 → total_cnt=2")
    io.pulse(PIN)
    io.pulse(PIN)
    time.sleep(0.2)
    t = read_reg(m, TOTAL)
    check("total_cnt==2 (实际 %d)" % t, t == 2)

    print("[3] 次品分拣：废品光电 → 300ms后推料500ms → 自动收回")
    io.pulse(PREJ)
    time.sleep(0.45)
    check("推料气缸伸出（300ms 延时后）", io.read(PUSHER))
    time.sleep(0.7)
    check("气缸自动收回（500ms 后）", not io.read(PUSHER))
    r = read_reg(m, REJ)
    check("reject_cnt==1 (实际 %d)" % r, r == 1)

    print("[4] 急停：电机立即停 + 报警灯，复位后报警解除")
    io.write(ESTOP, True)
    time.sleep(0.2)
    check("motor=FALSE", not io.read(MOTOR))
    check("alarm=TRUE", io.read(ALARM))
    io.write(ESTOP, False)
    time.sleep(0.2)
    check("报警随急停复位", not io.read(ALARM))

    print("[5] 停止按钮优先于自锁")
    io.pulse(START)
    time.sleep(0.2)
    check("重新启动", io.read(MOTOR))
    io.pulse(STOP)
    time.sleep(0.2)
    check("停止后 motor=FALSE", not io.read(MOTOR))

    m.close()
    print("\n场景验收: %s" % ("全部通过 ✅" if ok else "存在失败 ❌"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
