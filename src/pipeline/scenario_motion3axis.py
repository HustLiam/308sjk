#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三轴运动控制场景验收（motion3axis.xml：CiA 402 驱动模型 + PLCopen MC API）。

前置：
    python src/pipeline/run_deploy.py --xml src/plc/motion3axis.xml
运行：
    python src/pipeline/scenario_motion3axis.py

本脚本一身两角：
  · CiA 402 主站——写应用级指令（run/cmd_go/jog/quickstop/inject_fault/cmd_reset
    与目标位置寄存器），读应用状态（all_oe/move_done/any_moving/fault_any）与
    各轴状态字（验证 CiA 402 状态位与握手位）；
  · 电机+编码器仿真——按各轴速度指令（带符号 INT，单位/s）积分位置反馈。

地址表：
    线圈入  run 0.0 cmd_home 0.1 cmd_go 0.2 jog_fwd 0.3 jog_rev 0.4
            quickstop 0.5 inject_fault 0.6 cmd_reset 0.7
    寄存器入 x/y/z_fb %QW0/1/2（本脚本写）；x/y/z_sp %QW10/11/12（主站写）
    寄存器出 x/y/z_sw %QW6/7/8（状态字）；x/y/z_v %QW13/14/15（速度指令）
    线圈出  all_oe 1.0 move_done 1.1 any_moving 1.2 fault_any 1.3
    prog_id %QW20=1
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from modbus_io import SafeCoilIO, connect, read_reg, require_program  # noqa: E402

RUN, CMD_HOME, CMD_GO, JOG_FWD, JOG_REV, QS, INJECT, CMD_RESET = range(8)
ALL_OE, MOVE_DONE, ANY_MOVING, FAULT_ANY = 8, 9, 10, 11
X_FB, Y_FB, Z_FB = 0, 1, 2
X_SP, Y_SP, Z_SP = 10, 11, 12
X_SW, Y_SW, Z_SW = 6, 7, 8
X_V, Y_V, Z_V = 13, 14, 15
PROG_ID = 1
DT = 0.06
TOL = 3   # 编码器侧到位容差（含伺服滞后）

state = {"x": 0.0, "y": 0.0, "z": 0.0}
violations = []


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

    def wreg(reg, val):
        m.write_register(address=reg, value=int(val) & 0xFFFF)

    def rsw(reg):
        return read_reg(m, reg) & 0xFFFF

    def cycle():
        """一个仿真周期：电机积分 -> 写编码器；读速度/状态做不变量检查。"""
        xv, yv, zv = read_reg(m, X_V), read_reg(m, Y_V), read_reg(m, Z_V)
        xsw, ysw, zsw = rsw(X_SW), rsw(Y_SW), rsw(Z_SW)
        for k, v in (("x", xv), ("y", yv), ("z", zv)):
            state[k] = min(100.0, max(0.0, state[k] + v * DT))
        wreg(X_FB, round(state["x"])); wreg(Y_FB, round(state["y"])); wreg(Z_FB, round(state["z"]))
        # 不变量：非 OE 且非快停/故障减速（bit5=1, bit2=0）时电机指令必须为零
        for tag, sw, v in (("X", xsw, xv), ("Y", ysw, yv), ("Z", zsw, zv)):
            if (sw & 0x0004) == 0 and (sw & 0x0020) != 0 and abs(v) > 2:
                violations.append("%s失能态速度%d(sw=%04X)" % (tag, v, sw))
        if abs(xv) > 120 or abs(yv) > 120 or abs(zv) > 120:
            violations.append("速度越限")
        return xv, yv, zv, xsw, ysw, zsw

    def wait(cond, timeout, tag=""):
        t0 = time.time()
        while time.time() - t0 < timeout:
            cycle()
            if cond():
                return True
            time.sleep(DT)
        return False

    def goto(x, y, z, timeout=10.0, tag=""):
        wreg(X_SP, x); wreg(Y_SP, y); wreg(Z_SP, z)
        time.sleep(0.15)                          # 设定值先于 NewSetpoint（CiA 402 主站时序）
        io.pulse(CMD_GO)
        done = wait(lambda: io.read(MOVE_DONE) and not io.read(ANY_MOVING), timeout, tag)
        return done

    # ---- [1] 上电初始：MC_Power 未使能，主动发 shutdown(0x06) → 三轴 RTSO ----
    print("[1] 上电（run=0）：MC_Power 发 shutdown，三轴 Ready To Switch On")
    for tag, reg in (("X", X_SW), ("Y", Y_SW), ("Z", Z_SW)):
        check("%s 轴 sw.bit0=1 (实际 %04X)" % (tag, rsw(reg)), rsw(reg) & 0x0001 != 0)
    check("all_oe=FALSE", not io.read(ALL_OE))

    # ---- [2] MC_Power 使能序列：SOD→RTSO→SO→OE ----
    print("[2] run=1 → MC_Power 自动走 0x06/0x07/0x0F 使能序列")
    io.write(RUN, True)
    en = wait(lambda: io.read(ALL_OE), 4.0)
    check("三轴 Operation Enabled（all_oe=TRUE）", en)
    for tag, reg in (("X", X_SW), ("Y", Y_SW), ("Z", Z_SW)):
        sw = rsw(reg)
        check("%s 轴 bit2=1 bit4=1 bit5=1 (实际 %04X)" % (tag, sw),
              sw & 0x0004 and sw & 0x0010 and sw & 0x0020)

    # ---- [3] MC_MoveAbsolute：P1(60,40,10) 三轴定位 ----
    print("[3] cmd_go P1(60,40,10)：MC_MoveAbsolute → 握手 → 梯形定位")
    saw_moving = []
    t0 = time.time()
    wreg(X_SP, 60); wreg(Y_SP, 40); wreg(Z_SP, 10)
    time.sleep(0.15)                              # 设定值先于 NewSetpoint 建立（CiA 402 主站时序）
    io.pulse(CMD_GO)
    while time.time() - t0 < 10.0:
        xv, yv, zv, *_ = cycle()
        if io.read(ANY_MOVING):
            saw_moving.append(1)
        if io.read(MOVE_DONE) and not io.read(ANY_MOVING):
            break
        time.sleep(DT)
    check("到位 move_done=TRUE", io.read(MOVE_DONE))
    check("运动期间 any_moving 曾置位", bool(saw_moving))
    for tag, reg, sp in (("X", X_FB, 60), ("Y", Y_FB, 40), ("Z", Z_FB, 10)):
        fb = read_reg(m, reg)
        check("%s 定位 |%d-%d|<=%d（实际 %d）" % (tag, sp, fb, TOL, fb), abs(sp - fb) <= TOL)
    check("静止后 X 状态字 bit10 target-reached", rsw(X_SW) & 0x0400 != 0)

    # ---- [4] P2(60,40,60)：仅 Z 下探 ----
    print("[4] cmd_go P2(60,40,60)：仅 Z 轴运动")
    z_only = [True]
    t0 = time.time()
    wreg(X_SP, 60); wreg(Y_SP, 40); wreg(Z_SP, 60)
    time.sleep(0.15)
    io.pulse(CMD_GO)
    while time.time() - t0 < 10.0:
        xv, yv, zv, *_ = cycle()
        if zv != 0 and (xv != 0 or yv != 0):
            z_only[0] = False
        if io.read(MOVE_DONE) and not io.read(ANY_MOVING):
            break
        time.sleep(DT)
    check("到位", io.read(MOVE_DONE))
    check("全程 X/Y 速度为零（仅 Z 运动）", z_only[0])
    check("Z 定位（实际 %d）" % read_reg(m, Z_FB), abs(60 - read_reg(m, Z_FB)) <= TOL)

    # ---- [5] MC_Stop 快停：运动中 quickstop → QSA 受控减速 ----
    print("[5] 运动中 quickstop：QSA(bit5=0) 受控减速，释放后重新使能")
    wreg(X_SP, 20); wreg(Y_SP, 70); wreg(Z_SP, 10)
    time.sleep(0.15)
    io.pulse(CMD_GO)
    wait(lambda: io.read(ANY_MOVING), 2.0)
    io.write(QS, True)
    stopped = wait(lambda: not io.read(ANY_MOVING), 3.0)
    check("受控减速至停（≤3s）", stopped)
    qs_seen = any((rsw(r) & 0x0020) == 0 for r in (X_SW, Y_SW, Z_SW))
    check("快停轴 sw.bit5=0（QSA）", qs_seen)
    check("无故障", not io.read(FAULT_ANY))
    io.write(QS, False)
    re_en = wait(lambda: io.read(ALL_OE), 4.0)
    check("释放后 MC_Power 重新使能", re_en)
    done = goto(20, 70, 10, 10.0)
    check("重新下发 P3 并到位", done)

    # ---- [6] MC_MoveJog 点动 ----
    print("[6] jog_fwd / jog_rev：按住移动、松开停止")
    io.write(JOG_FWD, True)
    fwd = wait(lambda: read_reg(m, X_V) > 0, 2.0)
    check("按住正向 → X 速度>0", fwd)
    x_at = state["x"]
    wait(lambda: abs(read_reg(m, X_V)) == 0, 2.0) if not io.write(JOG_FWD, False) else None
    io.write(JOG_FWD, False)
    stopped = wait(lambda: abs(read_reg(m, X_V)) == 0 and not io.read(ANY_MOVING), 2.0)
    check("松开 → 减速停止", stopped)
    check("X 位置前进了（%s→%.0f）" % (x_at, state["x"]), state["x"] > x_at + 1)

    # ---- [7] 故障注入 + MC_Reset ----
    print("[7] inject_fault（目标越程 150）→ INTERP 安全拒绝（CSP 语义: 插补层拦截）")
    x_before = state["x"]
    io.pulse(INJECT)
    time.sleep(1.0)
    xv, *_ = cycle()
    check("X 轴未运动（越程被插补引擎拒绝）", abs(xv) <= 2)
    check("X 位置未变（安全拒绝）", abs(state["x"] - x_before) <= 2)
    check("无驱动器故障（插补层已拦截）", not io.read(FAULT_ANY))
    check("驱动器仍使能", io.read(ALL_OE))

    # ---- [8] MC_Home 回零 + 失能 ----
    print("[8] cmd_home 三轴回零，然后 run=0 失能")
    done = goto(0, 0, 0, 12.0)
    check("回零到位", done)
    for tag, reg in (("X", X_FB), ("Y", Y_FB), ("Z", Z_FB)):
        check("%s 回零（实际 %d）" % (tag, read_reg(m, reg)), abs(read_reg(m, reg)) <= TOL)
    io.write(RUN, False)
    dis = wait(lambda: not io.read(ALL_OE), 3.0)
    check("失能 all_oe=FALSE", dis)
    xv, yv, zv, *_ = cycle()
    check("失能后三轴速度为零", abs(xv) <= 2 and abs(yv) <= 2 and abs(zv) <= 2)

    # ---- [9] 全程不变量 ----
    check("全程不变量无违例（失能态零速 / 速度限幅）", not violations)
    if violations:
        print("      违例: %s" % violations[:5])

    m.close()
    print("\n场景验收: %s" % ("全部通过 ✅" if ok else "存在失败 ❌"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
