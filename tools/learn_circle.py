#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动化学习战役 runner。

目的：验证学习机制（经验库 v2 自动沉淀 + 相似度借鉴）——连跑独立画圆任务
（每任务全新进程 = 重启后汲取前次经验），直到**连续 3 个任务在 ≤2 轮迭代内
final**，判定学习机制更新完成。

判据（负责人口径）：
  fast   = status == final 且 iter <= 2
  达成   = 连续 3 个 fast
  上限   = 12 个任务（防失控），未达成则退出码 1 并输出学习曲线

用法: python tools/learn_circle.py [--max-tasks 12] [--target 3] [--iter-limit 2]
产物: workspace/experience/learning_curve.json + workspace/learn_<task>.log
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCENARIO = "plotter_circle"


def run_task(n):
    task = "plotter_ci_learn%d" % n
    cmd = [sys.executable, "-u", "-m", "src.agent.orchestrator",
           "--aml", "examples/aml/plotter3axis_station.aml",
           "--request", "画一个圆",
           "--deploy", "--acceptance", "--scenario", SCENARIO,
           "--modbus-host", "192.168.12.131",
           "--task-id", task, "--prog-id", "3", "--max-iters", "10"]
    log = REPO / "workspace" / ("learn_%s.log" % task)
    try:
        with open(log, "w", encoding="utf-8") as fh:
            subprocess.run(cmd, cwd=REPO, stdout=fh,
                           stderr=subprocess.STDOUT, timeout=4200)
    except subprocess.TimeoutExpired:
        return task, "timeout", 999
    text = log.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"RESULT: (\w+) \(iter=(\d+)\)", text)
    return (task, m.group(1), int(m.group(2))) if m else (task, "crash", 999)


def main():
    ap = argparse.ArgumentParser(description="画圆自动化学习战役")
    ap.add_argument("--max-tasks", type=int, default=12)
    ap.add_argument("--target", type=int, default=3, help="连续快收敛任务数（判据）")
    ap.add_argument("--iter-limit", type=int, default=2, help="快收敛的轮次上限")
    ap.add_argument("--start-n", type=int, default=1, help="起始任务编号（续跑不覆盖历史）")
    args = ap.parse_args()

    curve, streak = [], 0
    for n in range(args.start_n, args.start_n + args.max_tasks):
        task, status, iters = run_task(n)
        fast = (status == "final" and iters <= args.iter_limit)
        streak = streak + 1 if fast else 0
        curve.append({"task": task, "status": status, "iters": iters,
                      "fast": fast, "streak": streak})
        print("[learn] 任务%-2d %-20s %-10s %3d 轮  fast=%s  连续×%d"
              % (n, task, status, iters, "Y" if fast else "N", streak), flush=True)
        out = REPO / "workspace" / "experience" / "learning_curve.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(curve, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        if streak >= args.target:
            print("[learn] ✅ 判据达成：连续 %d 个任务 ≤%d 轮 final"
                  "（任务 %s 起）——学习机制更新完成"
                  % (args.target, args.iter_limit,
                     curve[-args.target]["task"]), flush=True)
            return 0
    print("[learn] ✗ %d 个任务未达成连续 %d 快收敛——学习曲线见 %s"
          % (args.max_tasks, args.target,
             REPO / "workspace" / "experience" / "learning_curve.json"), flush=True)
    return 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
