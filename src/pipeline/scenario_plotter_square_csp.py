# -*- coding: utf-8 -*-
"""场景验收：plotter_square_csp × MuJoCo（④ 验证与反馈 MVP，gate-4 形态）。

流程：require_program 身份核对 → 拉起 MuJoCo 运行时（--plc 回环）→ 按 spec
（智能体生成的测试方案：io_list 采集通道 + acceptance 四类准则）采集 →
确定性判定 → 失败时固定规则归因（mujoco_verdict.attribute）。

判据与采集内容来自 examples/specs/plotter_square_csp.spec.json 的
acceptance/io_list（对话式流程由 ① LLM 产出同构 spec）。

退出码 0 = 全过；非 0 = 有 FAIL（stdout 含逐条结果与归因，供编排器回喂）。
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

from modbus_io import connect, require_program  # noqa: E402
from mujoco_verdict import Collector, evaluate, attribute  # noqa: E402

PROG_ID = 4
SPEC = os.path.join(REPO, "examples", "specs", "plotter_square_csp.spec.json")
SCENE = os.path.join(REPO, "runs", "latest", "scene.spec.json")
IO_MAP = os.path.join(REPO, "runs", "latest", "io_map.json")

# 采集通道 = io_map 物理通道（%QW）+ HMI 线圈；analog addr 由 io_map plc_addr 推导
def channels():
    io = json.load(open(IO_MAP, encoding="utf-8"))
    chs = [{"name": e["plc_var"], "addr": int(e["modbus"]["plc_addr"][3:]),
            "type": "analog"} for e in io if e["type"] == "float"]
    chs += [{"name": n, "type": "coil"} for n in
            ("run", "cmd_draw", "pen_down", "plot_done", "any_moving")]
    return chs


def boot_runtime():
    cmd = [sys.executable, "-u", os.path.join(REPO, "runtime", "mujoco_jog_runtime.py"),
           "--scene", SCENE, "--io-map", IO_MAP, "--plc", "--plc-spec", SPEC]
    proc = subprocess.Popen(cmd, cwd=os.path.join(REPO, "runtime"),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    deadline = time.time() + 30
    ready = False
    while time.time() < deadline and proc.poll() is None:
        line = proc.stdout.readline()
        if "PLC 回环已启动" in line:
            ready = True
            break
    if not ready:
        proc.kill()
        raise RuntimeError("MuJoCo 运行时/PLC 回环 30s 内未就绪")
    time.sleep(1.5)     # 开场抬笔斜坡稳定
    return proc


def main():
    spec = json.load(open(SPEC, encoding="utf-8"))
    m = connect()
    print("[verify] 程序身份确认: plotter_square_csp (prog_id=%d)" % PROG_ID)
    require_program(m, PROG_ID, "plotter_square_csp")

    chs = channels()
    proc = boot_runtime()
    print("[runtime] MuJoCo + plc_link 回环就绪（采集 %d 通道）" % len(chs))
    try:
        col = Collector()
        trace = col.collect(chs, timeout_s=120.0)
    finally:
        proc.kill()
    if not trace:
        print("场景验收: 采集失败（无样本）")
        return 1
    print("[collect] %d 样本，%.1fs，plot_done=%s"
          % (len(trace), trace[-1]["t"], trace[-1].get("plot_done")))

    results = evaluate(spec.get("acceptance", []), trace)
    fails = 0
    for r in results:
        print(("  PASS " if r["pass"] else "  FAIL ") + "%s %s — %s"
              % (r["id"], r["desc"], r["evidence"]))
        fails += 0 if r["pass"] else 1

    if fails:
        print(attribute(results, trace, chs))
        print("场景验收: %d 项失败" % fails)
        return 1
    print("场景验收: 全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
