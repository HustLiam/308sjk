# -*- coding: utf-8 -*-
"""CSP 闭环画方验收脚本（demo 用，headless）：
触发 run+cmd_draw（FC15 整组写线圈，SafeCoilIO 纪律）→ 轮询 %QW3-5 反馈与
plot_done 线圈 → 记录轨迹 → 判定正方形（四角到位、边落笔、闭合、回中心）。
"""
import json
import sys
import time

from pymodbus.client import ModbusTcpClient

cli = ModbusTcpClient("127.0.0.1", port=502, timeout=2.0)
assert cli.connect()

# FC15 整组写线圈（完整字节跨度，SafeCoilIO 纪律）。位地址与程序 %QX 声明一致，
# 具名常量杜绝手算索引（2026-09-15 实测教训：位索引算错→静默不触发）
RUN_BIT = 0        # %QX0.0 run
CMD_DRAW_BIT = 16  # %QX2.0 cmd_draw
PEN_DOWN_BIT = 17  # %QX2.1 pen_down（读）
PLOT_DONE_BIT = 18 # %QX2.2 plot_done（读）
GROUP = 24         # 3 个完整字节


def write_bits(bits):
    cli.write_coils(address=0, values=[bool(b) for b in bits], slave=1)


def read_state():
    regs = cli.read_holding_registers(address=0, count=3, slave=1)   # x/y/z_fb @ %QW0-2
    coils2 = cli.read_coils(address=16, count=3, slave=1)            # %QX2.0-2.2
    return {
        "x": regs.registers[0], "y": regs.registers[1], "z": regs.registers[2],
        "cmd_draw": bool(coils2.bits[CMD_DRAW_BIT - 16]),
        "pen_down": bool(coils2.bits[PEN_DOWN_BIT - 16]),
        "plot_done": bool(coils2.bits[PLOT_DONE_BIT - 16]),
    }


# 触发：run=1 + cmd_draw=1（电平触发，状态机进序列后自动忽略）
bits = [0] * GROUP
bits[RUN_BIT] = 1
bits[CMD_DRAW_BIT] = 1
write_bits(bits)
print("triggered: run=1, cmd_draw=1")

traj = []
t0 = time.time()
done = False
while time.time() - t0 < 120:
    s = read_state()
    s["t"] = round(time.time() - t0, 2)
    traj.append(s)
    if len(traj) % 20 == 0:
        print(f"  t={s['t']:6.1f}s  x={s['x']:3d} y={s['y']:3d} z={s['z']:2d} "
              f"pen={'DOWN' if s['pen_down'] else 'up'} done={s['plot_done']}")
    if s["plot_done"]:
        done = True
        break
    time.sleep(0.05)

# 复位触发线圈
write_bits([0] * GROUP)

print(f"\nplot_done = {done}  (用时 {round(time.time() - t0, 1)}s)")

# ---- 轨迹判定 ----
def near(p, q, tol=2.0):
    return abs(p["x"] - q[0]) <= tol and abs(p["y"] - q[1]) <= tol


corners = [(20, 20), (80, 20), (80, 80), (20, 80)]
visited = []
for c in corners:
    hit = [p for p in traj if near(p, c)]
    visited.append(bool(hit) and any(p["pen_down"] for p in hit))

pen_down_xy = [p for p in traj if p["pen_down"]]
in_bounds = all(10 <= p["x"] <= 90 and 10 <= p["y"] <= 90 for p in pen_down_xy)
final = traj[-1]
center_ok = near(final, (50, 50), 2) and final["pen_down"] is False

result = {
    "plot_done": done, "corners_visited_with_pen": visited,
    "pen_samples": len(pen_down_xy), "xy_within_bounds": in_bounds,
    "final_center_raised": center_ok,
    "verdict": "PASS" if (done and all(visited) and in_bounds and center_ok) else "FAIL",
}
print(json.dumps(result, ensure_ascii=False, indent=2))
json.dump({"result": result, "trajectory": traj},
          open("../workspace/csp_square_demo.json", "w", encoding="utf-8"))
cli.close()
sys.exit(0 if result["verdict"] == "PASS" else 1)
