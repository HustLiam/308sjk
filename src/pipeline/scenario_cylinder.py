#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
双缸顺序控制场景验收（cylinder_seq.xml）。

前置：
    python src/pipeline/run_deploy.py --xml src/plc/cylinder_seq.xml
运行：
    python src/pipeline/scenario_cylinder.py

地址表：start %QX0.0；stop %QX0.1；a_ext/a_ret %QX0.2/0.3；b_ext/b_ret %QX0.4/0.5；
        a_fwd/a_bwd %QX1.0/1.1；b_fwd/b_bwd %QX1.2/1.3；running %QX1.4；step_no %QW0。
安全不变量：任一缸的伸出/缩回输出不得同时为 TRUE（双电磁阀防冲突）。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from modbus_io import SafeCoilIO, connect, read_reg, require_program  # noqa: E402

START, STOP, A_EXT, A_RET, B_EXT, B_RET = 0, 1, 2, 3, 4, 5
A_FWD, A_BWD, B_FWD, B_BWD, RUNNING = 8, 9, 10, 11, 12
STEP = 0
PROG_ID = 5


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    m = connect()
    io = SafeCoilIO(m)
    require_program(m, PROG_ID, "cylinder_seq")
    ok = True

    def check(name, cond):
        nonlocal ok
        print(("  PASS " if cond else "  FAIL ") + name)
        ok = ok and cond

    def invariant(tag):
        check("[%s] 安全不变量: A 双阀不同时 / B 双阀不同时"
              % tag, not (io.read(A_FWD) and io.read(A_BWD))
              and not (io.read(B_FWD) and io.read(B_BWD)))

    def settle():
        time.sleep(0.25)

    # ---- 初始：回原位，未运行 ----
    io.write(STOP, True); settle()
    for bit, val in ((A_EXT, False), (A_RET, True), (B_EXT, False), (B_RET, True), (STOP, False)):
        io.write(bit, val)
    settle()

    print("[1] 停机态：未运行、步号 0、双缸被驱回原位")
    check("running=FALSE", not io.read(RUNNING))
    check("step_no==0 (实际 %d)" % read_reg(m, STEP), read_reg(m, STEP) == 0)
    check("a_fwd=FALSE 且 b_fwd=FALSE", not io.read(A_FWD) and not io.read(B_FWD))
    invariant("停机")

    print("[2] 启动（原位联锁满足）→ 步1：A 伸出")
    io.pulse(START)
    settle()
    check("running=TRUE", io.read(RUNNING))
    check("step_no==1 (实际 %d)" % read_reg(m, STEP), read_reg(m, STEP) == 1)
    check("a_fwd=TRUE", io.read(A_FWD))
    check("b_fwd=FALSE", not io.read(B_FWD))
    invariant("步1")

    print("[3] A 到前位 → 步2：B 伸出（A 保持）")
    io.write(A_EXT, True); io.write(A_RET, False)
    settle()
    check("step_no==2 (实际 %d)" % read_reg(m, STEP), read_reg(m, STEP) == 2)
    check("b_fwd=TRUE", io.read(B_FWD))
    check("a_fwd 仍为 TRUE（保持）", io.read(A_FWD))
    invariant("步2")

    print("[4] B 到前位 → 步3：B 缩回")
    io.write(B_EXT, True); io.write(B_RET, False)
    settle()
    check("step_no==3 (实际 %d)" % read_reg(m, STEP), read_reg(m, STEP) == 3)
    check("b_bwd=TRUE 且 b_fwd=FALSE", io.read(B_BWD) and not io.read(B_FWD))
    invariant("步3")

    print("[5] B 回原位 → 步4：A 缩回")
    io.write(B_EXT, False); io.write(B_RET, True)
    settle()
    check("step_no==4 (实际 %d)" % read_reg(m, STEP), read_reg(m, STEP) == 4)
    check("a_bwd=TRUE 且 a_fwd=FALSE", io.read(A_BWD) and not io.read(A_FWD))
    invariant("步4")

    print("[6] A 回原位 → 循环回到步1（自动循环）")
    io.write(A_EXT, False); io.write(A_RET, True)
    settle()
    check("step_no==1 (实际 %d)" % read_reg(m, STEP), read_reg(m, STEP) == 1)
    check("a_fwd=TRUE（新一轮伸出）", io.read(A_FWD))
    check("running 仍为 TRUE", io.read(RUNNING))
    invariant("循环")

    print("[7] 停止 → 退出循环、双缸驱回原位")
    io.pulse(STOP)
    settle()
    check("running=FALSE", not io.read(RUNNING))
    check("step_no==0 (实际 %d)" % read_reg(m, STEP), read_reg(m, STEP) == 0)
    check("a_fwd=FALSE 且 b_fwd=FALSE", not io.read(A_FWD) and not io.read(B_FWD))
    invariant("停机后")

    m.close()
    print("\n场景验收: %s" % ("全部通过 ✅" if ok else "存在失败 ❌"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
