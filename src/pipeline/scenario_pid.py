#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PID 液位连续调节场景验收（pid_tank.xml）。

前置：
    python src/pipeline/run_deploy.py --xml src/plc/pid_tank.xml
运行：
    python src/pipeline/scenario_pid.py

本脚本扮演被控对象 + Isaac 桥接角色：内嵌一阶水箱模型
    dpv/dt = 0.02*mv - 0.02*pv          （进料 ∝ 开度，出料 ∝ 液位）
每 0.1s 一步：写 pv（INT 定点）→ 读 mv → 推进模型。

地址表：auto %QX0.0；pv %QW0；sp %QW1；mv %QW10；dev_alarm %QX1.0。
验收：稳态误差 ≤4（含 INT 量化）、无过冲越限、抗饱和、设定值阶跃跟踪、退自动归零。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from modbus_io import SafeCoilIO, connect, read_reg  # noqa: E402

AUTO = 0
PV, SP, MV = 0, 1, 10
ALARM = 8
DT = 0.1
A_GAIN, B_LEAK = 0.04, 0.04   # dpv/dt = A_GAIN*mv - B_LEAK*pv（τ=25s，满开度平衡液位 100）


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

    def write_reg(reg, val):
        m.write_register(address=reg, value=int(val))

    def run_closed_loop(setpoint, seconds, pv_r):
        """推进被控对象并返回 (末了液位REAL, 峰值, mv 越界标志, 记录点)。"""
        write_reg(SP, setpoint)
        peak, mv_violation, marks = pv_r, False, []
        n = int(seconds / DT)
        for i in range(n):
            write_reg(PV, round(pv_r))
            mv = read_reg(m, MV)
            if mv < 0 or mv > 100:
                mv_violation = True
            pv_r += (A_GAIN * mv - B_LEAK * pv_r) * DT
            if pv_r > peak:
                peak = pv_r
            if i % int(5 / DT) == 0:
                marks.append((round(i * DT), round(pv_r, 1), mv))
            time.sleep(DT)
        return pv_r, peak, mv_violation, marks

    # ---- 初始：退自动，液位 0，设定 50 ----
    io.write(AUTO, False)
    write_reg(SP, 50)
    write_reg(PV, 0)
    time.sleep(0.3)

    print("[1] 退自动：指令归零、积分清零、报警不触发（auto=FALSE 压制）")
    check("mv==0 (实际 %d)" % read_reg(m, MV), read_reg(m, MV) == 0)
    check("dev_alarm=FALSE", not io.read(ALARM))

    print("[2] 投自动 sp=50：闭环调节 30s")
    io.write(AUTO, True)
    time.sleep(0.3)                                   # 等 PLC 扫描周期生效再读报警
    check("初始偏差报警 dev_alarm=TRUE（|0-50|>20）", io.read(ALARM))
    pv_r, peak, mv_viol, marks = run_closed_loop(50, 30.0, 0.0)
    for t, pv_i, mv_i in marks:
        print("      t=%2ds  pv=%5.1f  mv=%3d" % (t, pv_i, mv_i))
    check("稳态 |pv-50| <= 4（实际 %.1f）" % pv_r, abs(pv_r - 50) <= 4)
    check("无过冲越限 peak <= 55（实际 %.1f）" % peak, peak <= 55.0)
    check("全程 mv ∈ [0,100]（抗饱和有效）", not mv_viol)
    check("稳态后偏差报警解除", not io.read(ALARM))

    print("[3] 设定值阶跃 50→70：跟踪调节 25s")
    pv_r, peak, mv_viol, marks = run_closed_loop(70, 25.0, pv_r)
    for t, pv_i, mv_i in marks:
        print("      t=%2ds  pv=%5.1f  mv=%3d" % (t, pv_i, mv_i))
    check("稳态 |pv-70| <= 4（实际 %.1f）" % pv_r, abs(pv_r - 70) <= 4)
    check("全程 mv ∈ [0,100]", not mv_viol)

    print("[4] 退自动：指令立即归零")
    io.write(AUTO, False)
    write_reg(PV, round(pv_r))
    time.sleep(0.3)
    check("mv==0 (实际 %d)" % read_reg(m, MV), read_reg(m, MV) == 0)

    m.close()
    print("\n场景验收: %s" % ("全部通过 ✅" if ok else "存在失败 ❌"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
