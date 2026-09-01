#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
双泵交替液位控制场景验收（pump_alternation.xml）。

前置：
    python src/pipeline/run_deploy.py --xml src/plc/pump_alternation.xml
运行：
    python src/pipeline/scenario_pump.py

地址表：auto %QX0.0；level %QW0(0~100)；pump1/2 %QX1.0/1.1；low_alarm %QX1.2；
        cycles %QW10。滞后带：启动<30，停止>80；最小运行 3s；每轮换主泵。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from modbus_io import SafeCoilIO, connect, read_reg  # noqa: E402

AUTO = 0
PUMP1, PUMP2, ALARM = 8, 9, 10
LEVEL, CYCLES = 0, 10


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

    def set_level(v):
        m.write_register(address=LEVEL, value=v)
        time.sleep(0.15)

    io.write(AUTO, False)
    set_level(50)
    time.sleep(0.3)

    print("[1] 低液位启动：level=25 → 泵1运行（首轮 lead=pump1），cycles=1")
    io.write(AUTO, True)
    set_level(25)
    time.sleep(0.3)
    check("pump1=TRUE", io.read(PUMP1))
    check("pump2=FALSE", not io.read(PUMP2))
    check("cycles==1 (实际 %d)" % read_reg(m, CYCLES), read_reg(m, CYCLES) == 1)

    print("[2] 最小运行时间：level=90 后 1s 内泵不停（防频繁启停）")
    set_level(90)
    time.sleep(1.0)
    check("3s 内 pump1 仍在运行", io.read(PUMP1))

    print("[3] 滞后停止：3s 后高液位停泵")
    time.sleep(3.0)
    check("pump1=FALSE", not io.read(PUMP1))

    print("[4] 滞后带：level=50/70 不启动（30~80 死区）")
    set_level(50)
    time.sleep(0.4)
    check("level=50 不启动", not io.read(PUMP1))
    set_level(70)
    time.sleep(0.4)
    check("level=70 不启动", not io.read(PUMP1))

    print("[5] 泵轮换：再次低液位 → 泵2接棒，cycles=2")
    set_level(25)
    time.sleep(0.3)
    check("pump2=TRUE", io.read(PUMP2))
    check("pump1=FALSE", not io.read(PUMP1))
    check("cycles==2 (实际 %d)" % read_reg(m, CYCLES), read_reg(m, CYCLES) == 2)

    print("[6] 低液位报警与手动退出自动")
    set_level(5)
    time.sleep(0.3)
    check("low_alarm=TRUE", io.read(ALARM))
    io.write(AUTO, False)
    time.sleep(0.3)
    check("退出自动后泵停", not io.read(PUMP2) and not io.read(PUMP1))

    m.close()
    print("\n场景验收: %s" % ("全部通过 ✅" if ok else "存在失败 ❌"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
