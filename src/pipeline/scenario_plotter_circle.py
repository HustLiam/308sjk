#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
画圆场景验收（plotter_circle.xml，LLM 生成：圆心(50,50) 半径25 连续插补）。

【gc 代拟 @lx 复核】prog_id=3（plotter3axis=1、plotter3axis 方形=2 之后顺延）。
对应 spec：画圆需求的 7 条验收准则（AC1 总时限 / AC2-AC4 急停禁止运动 /
AC5 落笔禁手动 / AC6 终态圆心 / AC7 仿真健康）。

前置：
    python src/pipeline/run_deploy.py --xml src/plc/plotter_circle.xml
运行：
    MODBUS_HOST=<运行时IP> python src/pipeline/scenario_plotter_circle.py

地址表 = plotter 站约定（线圈入 run0.0…cmd_draw2.0；出 all_oe1.0…plot_done2.2；
寄存器 fb 0/1/2、sw 6/7/8、sp 10/11/12、v 13/14/15；prog_id %QW20=3）。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from modbus_io import SafeCoilIO, connect, read_reg, require_program  # noqa: E402

RUN, CMD_GO, CMD_DRAW, QS = 0, 2, 16, 5
ALL_OE, ANY_MOVING, PEN_DOWN, PLOT_DONE = 8, 10, 17, 18
X_FB, Y_FB, Z_FB = 0, 1, 2
X_SP, Y_SP, Z_SP = 10, 11, 12
X_V, Y_V, Z_V = 13, 14, 15
X_SW, Y_SW, Z_SW = 6, 7, 8
PROG_ID = 3
DT = 0.06
TOL = 3
ZTOL = 2
CIRCLE_BOX = (20, 80)     # 圆路径 25..75 + 折线转角与伺服滞后容差（同方形 [15,85] 哲学）

state = {"x": 0.0, "y": 0.0, "z": 0.0}
violations = []
phase = ["preamble"]
seat_dwell = [0]   # 笔落座连续周期数（区分真绘图段与升降过渡窗）
draw_samples = {"pen_moving": 0, "out_of_box": 0,
               "min_x": 99, "max_x": 0, "min_y": 99, "max_y": 0}


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    m = connect()
    io = SafeCoilIO(m)
    require_program(m, PROG_ID, "plotter_circle")
    ok = True

    def check(name, cond):
        nonlocal ok
        print(("  PASS " if cond else "  FAIL ") + name)
        ok = ok and cond

    def wreg(reg, val):
        m.write_register(address=reg, value=int(val) & 0xFFFF)

    def cycle():
        xv, yv, zv = read_reg(m, X_V), read_reg(m, Y_V), read_reg(m, Z_V)
        state["x"] = min(100.0, max(0.0, state["x"] + xv * DT))
        state["y"] = min(100.0, max(0.0, state["y"] + yv * DT))
        state["z"] = min(10.0, max(0.0, state["z"] + zv * DT))
        wreg(X_FB, round(state["x"])); wreg(Y_FB, round(state["y"])); wreg(Z_FB, round(state["z"]))
        for tag, v in (("X", xv), ("Y", yv), ("Z", zv)):
            if abs(v) > 120:
                violations.append("%s速度越限%d" % (tag, v))
        # 轨迹统计仅计"真绘图段"：笔落座且驻留≥8 周期（排除升降过渡窗内的
        # 插补器/电机位置重同步追赶位移——AC7 的对象是圆轨迹本身）
        if io.read(PEN_DOWN) and read_reg(m, Z_FB) <= 1:
            seat_dwell[0] += 1
        else:
            seat_dwell[0] = 0
        if seat_dwell[0] >= 8 and (abs(xv) > 2 or abs(yv) > 2):
            draw_samples["pen_moving"] += 1
            draw_samples["min_x"] = min(draw_samples["min_x"], state["x"])
            draw_samples["max_x"] = max(draw_samples["max_x"], state["x"])
            draw_samples["min_y"] = min(draw_samples["min_y"], state["y"])
            draw_samples["max_y"] = max(draw_samples["max_y"], state["y"])
            if not (CIRCLE_BOX[0] <= state["x"] <= CIRCLE_BOX[1]
                    and CIRCLE_BOX[0] <= state["y"] <= CIRCLE_BOX[1]):
                draw_samples["out_of_box"] += 1
                if len(violations) < 12:
                    violations.append("[dbg %s] pos=(%.1f,%.1f,%.1f) v=(%d,%d,%d)"
                                      % (phase[0], state["x"], state["y"], state["z"], xv, yv, zv))
        return xv, yv, zv

    def wait(cond, timeout):
        t0 = time.time()
        while time.time() - t0 < timeout:
            cycle()
            if cond():
                return True
            time.sleep(DT)
        return False

    def manual_goto(x, y, z, timeout=10.0, attempts=3):
        """带到位校验的手动定位：脉冲若撞上插补器残留 Busy 会被吃掉
        （旧弦完成即清 exe，新目标未接受）——校验失败重发，最多 attempts 次。"""
        for _ in range(attempts):
            settle(4)
            wreg(X_SP, x); wreg(Y_SP, y); wreg(Z_SP, z)
            time.sleep(0.15)
            io.write(CMD_GO, True); time.sleep(0.2); io.write(CMD_GO, False)
            wait(lambda: not io.read(ANY_MOVING), timeout)
            settle(4)
            if (abs(read_reg(m, X_FB) - x) <= 2 and abs(read_reg(m, Y_FB) - y) <= 2
                    and abs(read_reg(m, Z_FB) - z) <= 2):
                return True
        return False

    def back_to_initial():
        """幂等前奏：三段重建一致初态——①原位抬笔（Z 不受笔互锁，解锁 X/Y）
        ②XY 归零（笔上位）③落笔归 (0,0,0)。同时重同步插补器与电机位置。"""
        if read_reg(m, Z_FB) > ZTOL or read_reg(m, X_FB) > TOL or read_reg(m, Y_FB) > TOL:
            io.write(RUN, True)
            t0 = time.time()
            while time.time() - t0 < 4.0:
                cycle()
                if io.read(ALL_OE):
                    break
                time.sleep(DT)
            manual_goto(read_reg(m, X_FB), read_reg(m, Y_FB), 10, 6.0)   # 抬笔解锁
            manual_goto(0, 0, 10, 10.0)                                   # XY 归零
            manual_goto(0, 0, 0, 6.0)                                     # 落笔复位
            settle()
            io.write(RUN, False)
            wait(lambda: not io.read(ALL_OE), 3.0)

    def settle(samples=6):
        still = 0
        for _ in range(150):
            cycle()
            if all(abs(read_reg(m, r)) <= 2 for r in (X_V, Y_V, Z_V)):
                still += 1
                if still >= samples:
                    return True
            else:
                still = 0
            time.sleep(DT)
        return False

    def draw(timeout=40.0):
        """触发绘图并监测。每 2s 打印运行时时间线（pos/v/pen/done）——
        失败时这些行进入闸门反馈包，是归因与修复的运行时证据。"""
        io.write(CMD_DRAW, True); time.sleep(0.2); io.write(CMD_DRAW, False)
        t0 = time.time()
        next_mark = 2.0
        while time.time() - t0 < timeout:
            xv, yv, zv = cycle()
            el = time.time() - t0
            if el >= next_mark:
                print("    [trace t=%.0fs] pos=(%d,%d,%d) v=(%d,%d,%d) pen=%d done=%d moving=%d"
                      % (el, read_reg(m, X_FB), read_reg(m, Y_FB), read_reg(m, Z_FB),
                         xv, yv, zv, io.read(PEN_DOWN), io.read(PLOT_DONE), io.read(ANY_MOVING)))
                next_mark += 2.0
            if io.read(PLOT_DONE) and not io.read(ANY_MOVING):
                return time.time() - t0
            time.sleep(DT)
        print("    [trace 超时] pos=(%d,%d,%d) pen=%d done=%d"
              % (read_reg(m, X_FB), read_reg(m, Y_FB), read_reg(m, Z_FB),
                 io.read(PEN_DOWN), io.read(PLOT_DONE)))
        return None

    back_to_initial()

    # ---- [1] 上电：未使能，初始笔触纸 ----
    print("[1] 上电（run=0）：三轴就绪；初始笔位 z=0")
    for tag, reg in (("X", X_SW), ("Y", Y_SW), ("Z", Z_SW)):
        sw = read_reg(m, reg) & 0xFFFF
        check("%s 轴 sw.bit0=1（%04X）" % (tag, sw), sw & 0x0001 != 0)
    check("all_oe=FALSE", not io.read(ALL_OE))
    check("pen_down=TRUE（初始触纸）", io.read(PEN_DOWN))

    # ---- [2] AC 使能时序 ----
    print("[2] run=1 → 三轴使能")
    io.write(RUN, True)
    t0 = time.time()
    en = wait(lambda: io.read(ALL_OE), 4.0)
    check("all_oe=TRUE（%.2fs）" % (time.time() - t0), en)

    phase[0] = "s3"
    # ---- [3] AC5：落笔态手动定位被拒（负测试） ----
    print("[3] 落笔态 cmd_go(60,40,0)：X/Y 请求被笔互锁拒绝")
    wreg(X_SP, 60); wreg(Y_SP, 40); wreg(Z_SP, 0)
    time.sleep(0.15)
    x0, y0 = state["x"], state["y"]
    io.write(CMD_GO, True); time.sleep(0.15); io.write(CMD_GO, False)
    for _ in range(30):
        cycle(); time.sleep(DT)
    check("X/Y 未运动（互锁拒绝，AC5）",
          abs(state["x"] - x0) <= 1 and abs(state["y"] - y0) <= 1)

    phase[0] = "s4"
    # ---- [4] AC1/AC6：cmd_draw 完整画圆 ----
    print("[4] cmd_draw：抬笔→(50,25)→落笔画整圆→回圆心(50,50,10)")
    el = draw(40.0)
    settle()
    check("画圆序列完成 plot_done=TRUE（%.1fs ≤ 30s，AC1）" % (el or 999),
          el is not None and el <= 30.0)
    fx, fy, fz = read_reg(m, X_FB), read_reg(m, Y_FB), read_reg(m, Z_FB)
    check("终态回圆心 (50,50,10)（实际 (%d,%d,%d)，AC6）" % (fx, fy, fz),
          abs(50 - fx) <= TOL and abs(50 - fy) <= TOL and abs(10 - fz) <= ZTOL)
    check("落笔期间 XY 联动发生过（采样 %d）" % draw_samples["pen_moving"],
          draw_samples["pen_moving"] >= 10)
    check("落笔轨迹在圆包围盒（x %0.f..%0.f, y %0.f..%0.f，越界 %d 次，AC7）"
          % (draw_samples["min_x"], draw_samples["max_x"],
             draw_samples["min_y"], draw_samples["max_y"], draw_samples["out_of_box"]),
          draw_samples["out_of_box"] == 0)
    check("完成后抬笔", not io.read(PEN_DOWN))

    phase[0] = "s5"
    # ---- [5] AC2-AC4：急停中止 + 复跑 ----
    print("[5] 画圆中急停：受控减速≤3s、序列中止、复跑可完成")
    io.write(CMD_DRAW, True); time.sleep(0.2); io.write(CMD_DRAW, False)
    moving = wait(lambda: io.read(ANY_MOVING), 5.0)
    check("第二次画圆已启动", moving)
    io.write(QS, True)
    t_stop = time.time()
    stopped = wait(lambda: not io.read(ANY_MOVING), 3.0)
    check("急停后三轴静止≤3s（%.2fs，AC2-4）" % (time.time() - t_stop), stopped)
    check("plot_done=FALSE（序列中止）", not io.read(PLOT_DONE))
    io.write(QS, False)
    re_en = wait(lambda: io.read(ALL_OE), 4.0)
    check("释放后重新使能", re_en)
    el2 = draw(40.0)
    settle()
    check("重新 cmd_draw 后完成（%.1fs）" % (el2 or 999), el2 is not None)

    # ---- [6] 失能 ----
    print("[6] run=0 失能")
    io.write(RUN, False)
    dis = wait(lambda: not io.read(ALL_OE), 3.0)
    check("失能 all_oe=FALSE", dis)
    xv, yv, zv = cycle()
    check("失能后速度为零", abs(xv) <= 2 and abs(yv) <= 2 and abs(zv) <= 2)

    # ---- [7] 全程不变量（AC7） ----
    check("全程无越界/超速违例", not [v for v in violations if not v.startswith("[dbg")])
    dbg = [v for v in violations if v.startswith("[dbg")]
    if dbg:
        print("      越界样本:")
        for d in dbg: print("       ", d)

    m.close()
    print("\n场景验收: %s" % ("全部通过 ✅" if ok else "存在失败 ❌"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
