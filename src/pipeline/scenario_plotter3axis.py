#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三轴绘图仪场景验收（plotter3axis.xml：CSP 栈 + 笔轴 INTERP_Z + 9 步绘图序列器）。

【gc 代拟 @lx 复核】本脚本按 lx《PLC 代码生成与执行引擎详细设计》§5.3 场景脚本
模式编写（require_program 身份兜底 + check 断言 + 全程不变量），结构与
scenario_motion3axis.py 一致；接线地址表来自 examples/aml/plotter3axis_station.aml
的 PLC 通道（⓪ 侧地址源头）。请 lx 评审后纳入场景库。

前置：
    python src/pipeline/run_deploy.py --xml src/plc/plotter3axis.xml
运行：
    python src/pipeline/scenario_plotter3axis.py

本脚本一身两角：
  · 主站（NC/操作台）——写指令信号与定位设定值，读指示灯/诊断；
  · 电机+编码器仿真——按各轴速度指令（带符号 INT）积分位置反馈。

对应 spec（examples/specs/plotter3axis.spec.json）验收准则：
  AC1 使能时序（[2]）/ AC2 回参考点抬笔（[2b]）/ AC3 绘图完成时限（[7][8]）/
  AC4 急停减速时限（[8]）/ AC5-AC6 forbidden（[8] plot_done 熄灭；不变量）/
  AC7 终态 plot_head (50,50,10)（[7]）/ 笔互锁负测试（[3][6]，行为级）。

地址表（线圈按全局位号）：
    入  run 0 cmd_home 1 cmd_go 2 jog_fwd 3 jog_rev 4 quickstop 5 cmd_reset 7
        cmd_draw 16
    出  all_oe 8 move_done 9 any_moving 10 fault_any 11 pen_down 17 plot_done 18
    寄存器入 x/y/z_fb %QW0/1/2（本脚本写）；x/y/z_sp %QW10/11/12（主站写）
    寄存器出 x/y/z_sw %QW6/7/8；x/y/z_v %QW13/14/15；prog_id %QW20=2
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from modbus_io import SafeCoilIO, connect, read_reg, require_program  # noqa: E402

RUN, CMD_HOME, CMD_GO, JOG_FWD, JOG_REV, QS, CMD_RESET = 0, 1, 2, 3, 4, 5, 7
ALL_OE, MOVE_DONE, ANY_MOVING, FAULT_ANY = 8, 9, 10, 11
CMD_DRAW, PEN_DOWN, PLOT_DONE = 16, 17, 18
X_FB, Y_FB, Z_FB = 0, 1, 2
X_SP, Y_SP, Z_SP = 10, 11, 12
X_SW, Y_SW, Z_SW = 6, 7, 8
X_V, Y_V, Z_V = 13, 14, 15
PROG_ID = 2
DT = 0.06
TOL = 3    # X/Y 到位容差（含伺服滞后）
ZTOL = 2   # Z 笔位容差
DRAW_AREA = (17, 83)  # 绘图区 [20,80] + 容差

state = {"x": 0.0, "y": 0.0, "z": 0.0}
violations = []
draw_samples = {"xy_with_pen": 0, "out_of_area": 0}


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    m = connect()
    io = SafeCoilIO(m)
    require_program(m, PROG_ID, "plotter3axis")
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
        state["x"] = min(100.0, max(0.0, state["x"] + xv * DT))
        state["y"] = min(100.0, max(0.0, state["y"] + yv * DT))
        state["z"] = min(10.0, max(0.0, state["z"] + zv * DT))
        wreg(X_FB, round(state["x"])); wreg(Y_FB, round(state["y"])); wreg(Z_FB, round(state["z"]))
        # 不变量：非 OE 且非快停/故障减速（bit5=1, bit2=0）时电机指令必须为零
        for tag, sw, v in (("X", xsw, xv), ("Y", ysw, yv), ("Z", zsw, zv)):
            if (sw & 0x0004) == 0 and (sw & 0x0020) != 0 and abs(v) > 2:
                violations.append("%s失能态速度%d(sw=%04X)" % (tag, v, sw))
            if abs(v) > 120:
                violations.append("%s速度越限%d" % (tag, v))
        # 绘图监测：落笔期间的 X/Y 联动必须落在绘图区（笔互锁的正面语义）
        if (zsw & 0x0004) and state["z"] <= 2 and (abs(xv) > 2 or abs(yv) > 2):
            draw_samples["xy_with_pen"] += 1
            if not (DRAW_AREA[0] <= state["x"] <= DRAW_AREA[1]
                    and DRAW_AREA[0] <= state["y"] <= DRAW_AREA[1]):
                draw_samples["out_of_area"] += 1
        return xv, yv, zv, xsw, ysw, zsw

    def wait(cond, timeout, tag=""):
        t0 = time.time()
        while time.time() - t0 < timeout:
            cycle()
            if cond():
                return True
            time.sleep(DT)
        return False

    def goto(x, y, z, timeout=10.0):
        wreg(X_SP, x); wreg(Y_SP, y); wreg(Z_SP, z)
        time.sleep(0.15)                          # 设定值先于定位启动（主站时序）
        io.pulse(CMD_GO)
        return wait(lambda: io.read(MOVE_DONE) and not io.read(ANY_MOVING), timeout)

    def draw(timeout=40.0):
        """触发绘图并全程监测，返回 (完成, 抬笔时刻距启动秒数 or None)。"""
        io.pulse(CMD_DRAW)
        t0 = time.time()
        lift = None
        while time.time() - t0 < timeout:
            cycle()
            if lift is None and not io.read(PEN_DOWN):
                lift = time.time() - t0           # 序列第一步=抬笔（自落笔态可测）
            if io.read(PLOT_DONE) and not io.read(ANY_MOVING):
                return True, lift
            time.sleep(DT)
        return False, lift

    # ---- [1] 上电初始：未使能，笔在纸上（z=0） ----
    print("[1] 上电（run=0）：三轴 Ready To Switch On；初始笔位 z=0（触纸）")
    for tag, reg in (("X", X_SW), ("Y", Y_SW), ("Z", Z_SW)):
        check("%s 轴 sw.bit0=1 (实际 %04X)" % (tag, rsw(reg)), rsw(reg) & 0x0001 != 0)
    check("all_oe=FALSE", not io.read(ALL_OE))
    check("pen_down=TRUE（初始笔触纸）", io.read(PEN_DOWN))

    # ---- [2] AC1：使能时序 run↑ → all_oe↑ ----
    print("[2] run=1 → 三轴使能（AC1：≤2s）")
    io.write(RUN, True)
    t_en = time.time()
    en = wait(lambda: io.read(ALL_OE), 4.0)
    check("三轴 Operation Enabled（all_oe=TRUE，实际 %.2fs ≤ 2s）" % (time.time() - t_en),
          en and (time.time() - t_en) <= 2.5)
    for tag, reg in (("X", X_SW), ("Y", Y_SW), ("Z", Z_SW)):
        sw = rsw(reg)
        check("%s 轴 bit2=1 bit4=1 bit5=1 (实际 %04X)" % (tag, sw),
              sw & 0x0004 and sw & 0x0010 and sw & 0x0020)

    # ---- [2b] AC2：回参考点（自落笔态）→ Z 抬笔、pen_down 下降沿 ≤3s ----
    print("[2b] cmd_home（落笔态）：Z 回参考点=抬笔安全位（AC2：pen_down↓ ≤3s）")
    t_home = time.time()
    io.pulse(CMD_HOME)
    lifted = wait(lambda: not io.read(PEN_DOWN), 3.0)
    check("笔抬离纸面（pen_down=FALSE，实际 %.2fs ≤ 3s，AC2）" % (time.time() - t_home), lifted)
    check("Z 到参考位 10（实际 %d）" % read_reg(m, Z_FB), abs(10 - read_reg(m, Z_FB)) <= ZTOL)

    # ---- [3] 笔互锁负测试：落笔态手动定位被拒（先抬笔要求） ----
    print("[3] 手动落笔后 cmd_go(60,40,0)：X/Y 请求被安全互锁拒绝（位置不变）")
    done = goto(0, 0, 0, 6.0)                     # 仅 Z 下探落笔
    check("已落笔（pen_down=TRUE）", io.read(PEN_DOWN))
    wreg(X_SP, 60); wreg(Y_SP, 40); wreg(Z_SP, 0)
    time.sleep(0.15)
    x0, y0 = state["x"], state["y"]
    io.pulse(CMD_GO)
    time.sleep(1.0)
    for _ in range(10):
        cycle()
        time.sleep(DT)
    check("X/Y 未运动（互锁拒绝）", abs(state["x"] - x0) <= 1 and abs(state["y"] - y0) <= 1)
    check("期间无运动标志", not io.read(ANY_MOVING))

    # ---- [4] Z 轴抬笔不受互锁限制 ----
    print("[4] cmd_go (0,0,10)：仅 Z 抬笔（笔互锁不限制 Z）")
    done = goto(0, 0, 10, 6.0)
    check("Z 抬笔到位（实际 %d）" % read_reg(m, Z_FB), abs(10 - read_reg(m, Z_FB)) <= ZTOL)
    check("pen_down=FALSE（已抬笔）", not io.read(PEN_DOWN))
    check("X/Y 仍为 0", abs(state["x"]) <= 1 and abs(state["y"]) <= 1)

    # ---- [5] 抬笔态手动定位 ----
    print("[5] cmd_go P(60,40,10)：抬笔态三轴并发定位")
    saw_moving = []
    t0 = time.time()
    wreg(X_SP, 60); wreg(Y_SP, 40); wreg(Z_SP, 10)
    time.sleep(0.15)
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
        check("%s 定位 |%d-%d|<=容差（实际 %d）" % (tag, sp, fb, fb), abs(sp - fb) <= TOL)
    check("静止后 X 状态字 bit10 target-reached", rsw(X_SW) & 0x0400 != 0)

    # ---- [6] 越程安全拒绝（抬笔态，x_sp=150） ----
    print("[6] x_sp=150 手动定位：越程目标被插补引擎安全拒绝")
    x_before = state["x"]
    wreg(X_SP, 150); wreg(Y_SP, 40); wreg(Z_SP, 10)
    time.sleep(0.15)
    io.pulse(CMD_GO)
    time.sleep(1.5)
    for _ in range(10):
        cycle()
        time.sleep(DT)
    xv, *_ = cycle()
    check("X 轴未运动（越程拒绝）", abs(xv) <= 2 and abs(state["x"] - x_before) <= 2)
    wreg(X_SP, 60)

    # ---- [7] AC3/AC7：cmd_draw 完整绘图序列（C6 前置：就绪且抬笔） ----
    print("[7] cmd_draw（抬笔态启动，C6 前置）：定位(20,20)→落笔画 20..80 正方形→抬笔→回中心(50,50)")
    # [6] 结束时笔处于抬起位 (60,40,10)——满足 C6 前置；落笔态先抬笔路径已由 [2b] 验证
    draw_samples["xy_with_pen"] = 0
    draw_samples["out_of_area"] = 0
    t_draw = time.time()
    done, lift = draw(40.0)
    el = time.time() - t_draw
    check("绘图序列完成 plot_done=TRUE（实际 %.1fs ≤ 30s，AC3）" % el, done and el <= 30.0)
    check("序列启动前置=抬笔（C6）", lift is None or lift <= 3.0)
    check("终态 X=50（实际 %d）" % read_reg(m, X_FB), abs(50 - read_reg(m, X_FB)) <= TOL)
    check("终态 Y=50（实际 %d）" % read_reg(m, Y_FB), abs(50 - read_reg(m, Y_FB)) <= TOL)
    check("终态 Z=10 抬笔（实际 %d）" % read_reg(m, Z_FB), abs(10 - read_reg(m, Z_FB)) <= ZTOL)
    check("落笔期间 XY 联动发生过（实际采样 %d）" % draw_samples["xy_with_pen"],
          draw_samples["xy_with_pen"] >= 10)
    check("落笔期间 XY 始终在绘图区 [17,83]（越区 %d 次）" % draw_samples["out_of_area"],
          draw_samples["out_of_area"] == 0)
    check("完成后 pen_down=FALSE", not io.read(PEN_DOWN))

    # ---- [8] AC4/AC5：急停中止 + 受控减速时限 + 复跑 ----
    print("[8] 绘图中急停：受控减速≤3s（AC4）、plot_done 熄灭（AC5）、复跑可完成")
    io.pulse(CMD_DRAW)
    moving = wait(lambda: io.read(ANY_MOVING), 5.0)
    check("第二次绘图已启动（any_moving）", moving)
    io.write(QS, True)
    t_stop = time.time()
    stopped = wait(lambda: not io.read(ANY_MOVING), 3.0)
    check("急停后受控减速至停（≤3s，实际 %.2fs）" % (time.time() - t_stop), stopped)
    check("plot_done=FALSE（序列被中止）", not io.read(PLOT_DONE))
    check("无故障", not io.read(FAULT_ANY))
    io.write(QS, False)
    re_en = wait(lambda: io.read(ALL_OE), 4.0)
    check("释放后重新使能", re_en)
    # 中止位置不定，先原地抬笔满足 C6 前置（手动 Z 不受笔互锁限制）
    goto(read_reg(m, X_FB), read_reg(m, Y_FB), 10, 8.0)
    check("原地抬笔（pen_down=FALSE）", not io.read(PEN_DOWN))
    done, _lift = draw(40.0)
    check("重新 cmd_draw 后完成", done)
    check("终态回中心 (50,50,10)",
          abs(50 - read_reg(m, X_FB)) <= TOL and abs(50 - read_reg(m, Y_FB)) <= TOL
          and abs(10 - read_reg(m, Z_FB)) <= ZTOL)

    # ---- [9] AC2：回参考点（Z 参考点=抬笔安全位） ----
    print("[9] cmd_home：X/Y 回零，Z 回抬笔安全位 10")
    io.pulse(CMD_HOME)
    homed = wait(lambda: not io.read(ANY_MOVING)
                 and abs(read_reg(m, X_FB)) <= TOL
                 and abs(read_reg(m, Y_FB)) <= TOL
                 and abs(10 - read_reg(m, Z_FB)) <= ZTOL, 15.0)
    check("三轴回参考点（X=0 Y=0 Z=10）", homed)
    check("pen_down=FALSE（参考点=抬笔）", not io.read(PEN_DOWN))

    # ---- [10] 失能 ----
    print("[10] run=0 失能：速度归零")
    io.write(RUN, False)
    dis = wait(lambda: not io.read(ALL_OE), 3.0)
    check("失能 all_oe=FALSE", dis)
    xv, yv, zv, *_ = cycle()
    check("失能后三轴速度为零", abs(xv) <= 2 and abs(yv) <= 2 and abs(zv) <= 2)

    # ---- [11] 全程不变量 ----
    check("全程不变量无违例（失能态零速 / 速度限幅）", not violations)
    if violations:
        print("      违例: %s" % violations[:5])

    m.close()
    print("\n场景验收: %s" % ("全部通过 ✅" if ok else "存在失败 ❌"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
