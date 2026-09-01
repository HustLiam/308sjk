#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
机械臂搬运场景验收（robot_pickplace.xml）。

前置：
    python src/pipeline/run_deploy.py --xml src/plc/robot_pickplace.xml
运行：
    python src/pipeline/scenario_pickplace.py

地址表：start %QX0.0 stop 0.1 x_home 0.2 x_pick 0.3 x_place 0.4 z_up 0.5 z_dn 0.6
        part_det 0.7；x_fwd 1.0 x_rev 1.1 z_down_cmd 1.2 vacuum 1.3 running 1.4；
        step_no %QW0 placed_cnt %QW1。
安全不变量：Z 处于下行/下位期间 X 禁止移动（x_fwd/x_rev 必须为 FALSE）；X 双向不得同驱。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from modbus_io import SafeCoilIO, connect, read_reg  # noqa: E402

START, STOP, X_HOME, X_PICK, X_PLACE, Z_UP, Z_DN, PART = 0, 1, 2, 3, 4, 5, 6, 7
X_FWD, X_REV, Z_CMD, VAC, RUNNING = 8, 9, 10, 11, 12
STEP, PLACED = 0, 1


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

    def invariant(tag):
        z_down = io.read(Z_CMD) or io.read(Z_DN)
        x_move = io.read(X_FWD) or io.read(X_REV)
        check("[%s] 安全不变量: Z 下行/下位期间 X 不移动；X 双向不同驱"
              % tag, (not z_down or not x_move)
              and not (io.read(X_FWD) and io.read(X_REV)))

    def settle():
        time.sleep(0.25)

    def sens(home=0, pick=0, place=0, up=0, dn=0, part=0):
        for bit, val in ((X_HOME, home), (X_PICK, pick), (X_PLACE, place),
                         (Z_UP, up), (Z_DN, dn), (PART, part)):
            io.write(bit, val)
        settle()

    # ---- 初始：原位高位，未运行 ----
    io.pulse(STOP)
    sens(home=1, up=1)

    print("[1] 停机态：未运行、步号 0、输出全安全态")
    check("running=FALSE", not io.read(RUNNING))
    check("step_no==0 (实际 %d)" % read_reg(m, STEP), read_reg(m, STEP) == 0)
    check("x/z/vacuum 全 FALSE", not any(io.read(b) for b in (X_FWD, X_REV, Z_CMD, VAC)))
    invariant("停机")

    print("[2] 启动（原位+高位联锁）→ 步1：X 向取料位")
    io.pulse(START)
    settle()
    check("running=TRUE", io.read(RUNNING))
    check("step_no==1 (实际 %d)" % read_reg(m, STEP), read_reg(m, STEP) == 1)
    check("x_fwd=TRUE", io.read(X_FWD))
    invariant("步1")

    print("[3] 到取料位（Z 高位）→ 步2 下探并吸取 → 步3 抬升")
    sens(pick=1, up=1, part=1)                       # 离开原点，到位取料点，有料
    check("step_no==2 (实际 %d)" % read_reg(m, STEP), read_reg(m, STEP) == 2)
    check("x_fwd=FALSE（到位即停）", not io.read(X_FWD))
    invariant("步2 前行结束")
    sens(pick=1, dn=1, part=1)                       # Z 下探到位
    check("vacuum=TRUE（吸取）", io.read(VAC))
    check("step_no==3 (实际 %d)" % read_reg(m, STEP), read_reg(m, STEP) == 3)
    sens(pick=1, up=1, part=1)                       # Z 抬升
    check("step_no==4 (实际 %d)" % read_reg(m, STEP), read_reg(m, STEP) == 4)
    check("x_fwd=TRUE（携件向放料位）", io.read(X_FWD))
    invariant("步3→4 抬升后移动")

    print("[4] 到放料位 → 步5 下释放并计数 → 步6 抬升")
    sens(place=1, up=1)
    check("step_no==5 (实际 %d)" % read_reg(m, STEP), read_reg(m, STEP) == 5)
    check("x_fwd=FALSE", not io.read(X_FWD))
    sens(place=1, dn=1)
    check("vacuum=FALSE（释放）", not io.read(VAC))
    check("placed_cnt==1 (实际 %d)" % read_reg(m, PLACED), read_reg(m, PLACED) == 1)
    check("step_no==6 (实际 %d)" % read_reg(m, STEP), read_reg(m, STEP) == 6)
    sens(place=1, up=1)
    check("step_no==7 (实际 %d)" % read_reg(m, STEP), read_reg(m, STEP) == 7)
    check("x_rev=TRUE（返程）", io.read(X_REV))
    invariant("步7 返程")

    print("[5] 回原位 → 循环回步1（placed_cnt 保持 1）")
    sens(home=1, up=1)
    check("step_no==1 (实际 %d)" % read_reg(m, STEP), read_reg(m, STEP) == 1)
    check("placed_cnt==1 (实际 %d)" % read_reg(m, PLACED), read_reg(m, PLACED) == 1)
    check("running 仍为 TRUE", io.read(RUNNING))

    print("[6] 无料联锁：第二循环取料位无料 → 步9 等待，不下探吸取")
    sens(pick=1, up=1, part=0)                       # 到取料位但无料
    check("step_no==2 (实际 %d)" % read_reg(m, STEP), read_reg(m, STEP) == 2)
    sens(pick=1, dn=1, part=0)                       # 下探后确认无料
    check("step_no==9 (实际 %d)" % read_reg(m, STEP), read_reg(m, STEP) == 9)
    check("vacuum 保持 FALSE", not io.read(VAC))
    sens(pick=1, up=1, part=0)                       # 步9 先抬升等待
    check("z_down_cmd=FALSE（安全高度等待）", not io.read(Z_CMD))
    invariant("步9 等待")
    sens(pick=1, up=1, part=1)                       # 料到位（Z 仍在高位）→ 步9 放行回步2
    check("step_no==2 (实际 %d)" % read_reg(m, STEP), read_reg(m, STEP) == 2)
    sens(pick=1, dn=1, part=1)                       # 重新下探吸取
    check("vacuum=TRUE（重试成功）", io.read(VAC))
    check("step_no==3 (实际 %d)" % read_reg(m, STEP), read_reg(m, STEP) == 3)

    print("[7] 停止 → 安全态（Z 抬升指令撤、X 停、真空断）")
    io.write(PART, False)
    sens(up=1, home=1)
    io.pulse(STOP)
    settle()
    check("running=FALSE", not io.read(RUNNING))
    check("step_no==0 (实际 %d)" % read_reg(m, STEP), read_reg(m, STEP) == 0)
    check("输出全安全态", not any(io.read(b) for b in (X_FWD, X_REV, Z_CMD, VAC)))
    invariant("停机后")

    m.close()
    print("\n场景验收: %s" % ("全部通过 ✅" if ok else "存在失败 ❌"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
