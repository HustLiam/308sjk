#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三轴运动控制场景验收（motion3axis.xml）。

前置：
    python src/pipeline/run_deploy.py --xml src/plc/motion3axis.xml
运行：
    python src/pipeline/scenario_motion3axis.py

本脚本扮演三轴伺服 + Isaac 桥接角色：按 PLC 输出的方向信号积分位置反馈
（每轴速度 30 单位/s，0~100 行程；Z 轴 0=上死点），形成位置闭环。

地址表：start %QX0.0 stop 0.1；x/y/z_fb %QW0/1/2，x/y/z_sp %QW10/11/12；
        x_fwd/rev %QX1.0/1.1，y_fwd/rev 1.2/1.3，z_fwd/rev 1.4/1.5，
        in_pos 1.6，moving 1.7；prog_id %QW20=1。
安全不变量：任一轴 fwd/rev 不同时为 TRUE（双驱互斥）；
        Z 不在上部安全区（z_fb>30）期间 X/Y 驱动必须为 FALSE（Z 安全区互锁）。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from modbus_io import SafeCoilIO, connect, read_reg, require_program  # noqa: E402

START, STOP = 0, 1
X_FB, Y_FB, Z_FB, X_SP, Y_SP, Z_SP = 0, 1, 2, 10, 11, 12
X_FWD, X_REV, Y_FWD, Y_REV, Z_FWD, Z_REV, IN_POS, MOVING = 8, 9, 10, 11, 12, 13, 14, 15
PROG_ID = 1
DEADBAND = 2
Z_SAFE = 30
AXIS_SPEED = 30.0   # 单位/s
DT = 0.06           # 伺服步进周期


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    m = connect()
    io = SafeCoilIO(m)
    require_program(m, PROG_ID, "motion3axis")
    ok = True

    def check(name, cond):
        nonlocal ok
        print(("  PASS " if cond else "  FAIL ") + name)
        ok = ok and cond

    def write_reg(reg, val):
        m.write_register(address=reg, value=int(val))

    def read_fbs():
        return read_reg(m, X_FB), read_reg(m, Y_FB), read_reg(m, Z_FB)

    state = {"x": 0.0, "y": 0.0, "z": 0.0}
    invariant_violation = []

    def invariant(tag):
        xf, xr = io.read(X_FWD), io.read(X_REV)
        yf, yr = io.read(Y_FWD), io.read(Y_REV)
        zf, zr = io.read(Z_FWD), io.read(Z_REV)
        if (xf and xr) or (yf and yr) or (zf and zr):
            invariant_violation.append("%s:双驱同通" % tag)
        if state["z"] > Z_SAFE + DEADBAND and (xf or xr or yf or yr):
            invariant_violation.append("%s:Z低位期间XY动作" % tag)

    def servo(target, tag, settle_timeout=12.0):
        """闭环推进到目标并等待到位：返回到位时各轴反馈。

        到位判定三重条件（防启动竞态——换目标瞬间 PLC 可能仍报旧的在位状态）：
        等一个扫描周期后，须 in_pos 标志、输出全静、且实际位置进入目标死区三者同时成立。"""
        write_reg(X_SP, target[0]); write_reg(Y_SP, target[1]); write_reg(Z_SP, target[2])
        time.sleep(0.15)   # 至少让 PLC 完成一个扫描周期吃到新目标
        t0 = time.time()
        while time.time() - t0 < settle_timeout:
            xf, xr = io.read(X_FWD), io.read(X_REV)
            yf, yr = io.read(Y_FWD), io.read(Y_REV)
            zf, zr = io.read(Z_FWD), io.read(Z_REV)
            invariant(tag)
            state["x"] += ((1 if xf else 0) - (1 if xr else 0)) * AXIS_SPEED * DT
            state["y"] += ((1 if yf else 0) - (1 if yr else 0)) * AXIS_SPEED * DT
            state["z"] += ((1 if zf else 0) - (1 if zr else 0)) * AXIS_SPEED * DT
            for k in "xyz":
                state[k] = min(100.0, max(0.0, state[k]))
            write_reg(X_FB, round(state["x"]))
            write_reg(Y_FB, round(state["y"]))
            write_reg(Z_FB, round(state["z"]))
            quiet = not any((xf, xr, yf, yr, zf, zr))
            on_target = all(abs(round(state[k]) - t) <= DEADBAND for k, t in zip("xyz", target))
            if quiet and on_target and io.read(IN_POS):
                break
            time.sleep(DT)
        return read_fbs()

    # ---- 初始：归零位（0,0,0=三轴原点，Z 上死点），未运行 ----
    io.pulse(STOP)
    write_reg(X_SP, 0); write_reg(Y_SP, 0); write_reg(Z_SP, 0)
    for r, v in ((X_FB, 0), (Y_FB, 0), (Z_FB, 0)):
        write_reg(r, v)
    time.sleep(0.3)

    print("[1] 停机态：六向驱动全关、无到位指示")
    check("六向驱动全 FALSE", not any(io.read(b) for b in (X_FWD, X_REV, Y_FWD, Y_REV, Z_FWD, Z_REV)))
    check("in_pos=FALSE", not io.read(IN_POS))

    print("[2] 启动 → 航点 P1(60,40,10)：三轴并发定位（Z 保持安全区）")
    io.pulse(START)
    x, y, z = servo((60, 40, 10), "P1")
    check("到位 in_pos=TRUE", io.read(IN_POS))
    check("X 定位 |60-x|<=2（实际 %d）" % x, abs(60 - x) <= DEADBAND)
    check("Y 定位 |40-y|<=2（实际 %d）" % y, abs(40 - y) <= DEADBAND)
    check("Z 定位 |10-z|<=2（实际 %d）" % z, abs(10 - z) <= DEADBAND)

    print("[3] 航点 P2(60,40,60)：仅 Z 下探（X/Y 目标不变输出应保持关闭）")
    x, y, z = servo((60, 40, 60), "P2")
    check("in_pos=TRUE", io.read(IN_POS))
    check("Z 下探到 |60-z|<=2（实际 %d）" % z, abs(60 - z) <= DEADBAND)

    print("[4] 互锁负测试：Z 在低位直接给 X/Y 新目标 → X/Y 驱动被封锁")
    write_reg(X_SP, 20); write_reg(Y_SP, 70)   # 不动 Z（目标仍 60）
    time.sleep(0.5)
    check("x_fwd=FALSE（误差存在但被 Z 互锁封锁）", not io.read(X_FWD) and not io.read(X_REV))
    check("y_fwd=FALSE", not io.read(Y_FWD) and not io.read(Y_REV))
    invariant("互锁")

    print("[5] 先抬 Z 再平移：P3(20,70,10) → Z 回升期间 X/Y 禁动，进安全区后平移")
    x, y, z = servo((20, 70, 10), "P3")
    check("in_pos=TRUE", io.read(IN_POS))
    check("X 定位 |20-x|<=2（实际 %d）" % x, abs(20 - x) <= DEADBAND)
    check("Y 定位 |70-y|<=2（实际 %d）" % y, abs(70 - y) <= DEADBAND)
    check("Z 定位 |10-z|<=2（实际 %d）" % z, abs(10 - z) <= DEADBAND)

    print("[6] 死区：目标在 ±2 内微调 → 驱动不动、保持到位")
    write_reg(X_SP, 21)   # 20 -> 21，落在死区内
    time.sleep(0.4)
    check("X 驱动保持关闭", not io.read(X_FWD) and not io.read(X_REV))
    check("in_pos 保持 TRUE", io.read(IN_POS))

    print("[7] 停止 → 安全态（六向驱动全关、到位指示撤销）")
    io.pulse(STOP)
    time.sleep(0.3)
    check("六向驱动全 FALSE", not any(io.read(b) for b in (X_FWD, X_REV, Y_FWD, Y_REV, Z_FWD, Z_REV)))
    check("in_pos=FALSE", not io.read(IN_POS))
    invariant("停机后")

    check("全程安全不变量无违例（双驱互斥 + Z 低位禁 XY）", not invariant_violation)
    if invariant_violation:
        print("      违例记录: %s" % invariant_violation[:5])

    m.close()
    print("\n场景验收: %s" % ("全部通过 ✅" if ok else "存在失败 ❌"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
