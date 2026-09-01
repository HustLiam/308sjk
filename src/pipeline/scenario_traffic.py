#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
十字路口交通灯场景验收（traffic_light.xml）。

前置：
    python src/pipeline/run_deploy.py --xml src/plc/traffic_light.xml
运行：
    python src/pipeline/scenario_traffic.py

相位：0 NS绿5s → 1 NS黄2s → 2 全红1s → 3 EW绿5s → 4 EW黄2s → 5 全红1s → 循环（16s/周期）
地址：run %QX0.0；ns_g/y/r %QX1.0/1/2；ew_g/y/r %QX1.3/4/5；cycle_cnt %QW10。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from modbus_io import SafeCoilIO, connect, read_reg  # noqa: E402

RUN = 0
NS_G, NS_Y, NS_R = 8, 9, 10
EW_G, EW_Y, EW_R = 11, 12, 13
CYCLE = 10


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    m = connect()
    io = SafeCoilIO(m)
    ok = True
    violations = []

    def check(name, cond):
        nonlocal ok
        print(("  PASS " if cond else "  FAIL ") + name)
        ok = ok and cond

    def lamps():
        b = io.read_many(8, 6)
        return {"ns_g": b[0], "ns_y": b[1], "ns_r": b[2],
                "ew_g": b[3], "ew_y": b[4], "ew_r": b[5]}

    def invariant(l):
        """安全不变量：对向绿不同亮；每侧至多一灯亮（绿黄互斥）。"""
        if l["ns_g"] and l["ew_g"]:
            violations.append("双绿")
        if l["ns_g"] and l["ns_y"]:
            violations.append("NS绿黄同亮")
        if l["ew_g"] and l["ew_y"]:
            violations.append("EW绿黄同亮")

    io.write(RUN, False)
    time.sleep(0.3)
    print("[0] 停机态：全部熄灯")
    l = lamps()
    check("六灯全灭", not any(l.values()))

    print("[1] 启动：0.5s 后 NS绿 / EW红")
    io.write(RUN, True)
    time.sleep(0.5)
    l = lamps(); invariant(l)
    check("ns_g=TRUE ew_r=TRUE", l["ns_g"] and l["ew_r"])

    print("[2] 相位推进（采样点验证定时器链）")
    time.sleep(5.3)   # t≈5.8s → state1 NS黄
    l = lamps(); invariant(l)
    check("t≈5.8s: NS黄 / EW红", l["ns_y"] and l["ew_r"] and not l["ns_g"])
    time.sleep(1.7)   # t≈7.5s → state2 全红
    l = lamps(); invariant(l)
    check("t≈7.5s: 全红清空相位", l["ns_r"] and l["ew_r"])
    time.sleep(1.6)   # t≈9.1s → state3 EW绿
    l = lamps(); invariant(l)
    check("t≈9.1s: EW绿 / NS红", l["ew_g"] and l["ns_r"])

    print("[3] 完整周期：t≈16.5s 回到 NS绿，cycle_cnt=1")
    time.sleep(7.4)   # t≈16.5s → state0
    l = lamps(); invariant(l)
    check("回到 NS绿", l["ns_g"])
    cc = read_reg(m, CYCLE)
    check("cycle_cnt==1 (实际 %d)" % cc, cc == 1)

    print("[4] 停机：全部熄灯")
    io.write(RUN, False)
    time.sleep(0.3)
    l = lamps(); invariant(l)
    check("六灯全灭", not any(l.values()))

    m.close()
    check("全程无对向双绿等冲突（采样 %d 次）" % (0 if violations else 1),
          not violations)
    print("\n场景验收: %s" % ("全部通过 ✅" if ok else "存在失败 ❌"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
