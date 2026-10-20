"""Run a fixed, resumable, sequential fresh-generation PLC benchmark.

The geometry oracle is specific to this benchmark station. It never generates
PLC source and is deliberately independent of the model's own test plan.
"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from .config import PROJECT_ROOT, MODEL, API_URL
from src.pipeline.trace_query import event_window
from src.pipeline.condition_response import bounded_violations


def dump(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def matrix():
    cases = []
    families = [("triangle", "正三角形", 3), ("square", "正方形", 4),
                ("pentagon", "正五边形", 5), ("hexagon", "正六边形", 6),
                ("heptagon", "正七边形", 7), ("circle", "圆", 36),
                ("rectangle", "长方形", None), ("irregular", "非正凸五边形", None),
                ("concave", "凹六边形", None), ("ellipse", "椭圆", 36)]
    for family, label, n in families:
        for radius in (15, 30):
            if family == "rectangle":
                offsets = [(1, .5), (-1, .5), (-1, -.5), (1, -.5)]
            elif family == "irregular":
                offsets = [(1, 0), (.3, .8), (-.8, .5), (-.6, -.7), (.6, -.4)]
            elif family == "concave":
                offsets = [(1, -1), (1, -.2), (-.2, -.2), (-.2, 1), (-1, 1), (-1, -1)]
            else:
                offsets = [(math.cos(2 * math.pi * k / n),
                            math.sin(2 * math.pi * k / n) * (.5 if family == "ellipse" else 1)) for k in range(n)]
            vertices = [[round(50 + radius * x, 3), round(50 + radius * y, 3)] for x, y in offsets]
            if family in ("circle", "ellipse"):
                geometry = (f"圆心(50,50)，半径{radius}" if family == "circle" else
                            f"中心(50,50)，X半长轴{radius}、Y半短轴{radius / 2}")
                geometry += "；从最右侧开始逆时针，至少36段近似闭合曲线"
            elif family in ("rectangle", "irregular", "concave"):
                geometry = "按以下顶点顺序逐段直线绘制并闭合：" + json.dumps(vertices, ensure_ascii=False)
            else:
                geometry = f"中心(50,50)，外接圆半径{radius}；首顶点({50 + radius},50)，逆时针逐顶点直线闭合"
            request = (f"三轴CSP绘图仪画一个{label}。{geometry}。X/Y使用0..100工程坐标，Z使用毫米。"
                       "run使能且cmd_draw上升沿启动，cmd_reset复位。先抬笔到Z=10后移动到首顶点，"
                       "落笔到Z=0，完成全部绘制后先抬笔到Z=10，再回(50,50,10)，静止后保持plot_done。"
                       "pen_down必须反映真实接触，依据z_fb<=1判定，不得用状态序号或命令值掩盖接触。"
                       "传感器反馈到PLC派生状态的一致性允许最多40ms异步响应，使用显式派生状态响应预算；安全互锁不使用此预算。"
                       "从启动到完整收尾60秒以内；所有到位检查使用设备poswin。未使能时不得运动。")
            cases.append({"id": f"{family}_r{radius}", "family": family, "label": label,
                          "scale": radius, "vertices": vertices, "request": request})
    return cases


def distance(point, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    u = max(0, min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / (dx * dx + dy * dy)))
    return math.hypot(point[0] - a[0] - u * dx, point[1] - a[1] - u * dy)


def request_oracle(case, folder):
    path = folder / "trace.json"
    ids = ("REQUEST_PATH", "REQUEST_CORRIDOR", "REQUEST_FINISH", "CONTACT_TRUTH")
    if not path.exists():
        return [{"id": key, "pass": False, "evaluated": False} for key in ids]
    trace = json.loads(path.read_text(encoding="utf-8"))
    if not trace or any(not {"x_fb", "y_fb", "z_fb", "pen_down"}.issubset(s) for s in trace):
        return [{"id": key, "pass": False, "evaluated": False} for key in ids]
    vertices = case["vertices"] + [case["vertices"][0]]
    cursor = 0
    task_samples = event_window(trace, {"signal": "cmd_draw", "edge": "rising"})
    contact = [s for s in task_samples if s["z_fb"] <= 1]
    for point_number, point in enumerate(vertices):
        for index in range(cursor, len(contact)):
            s = contact[index]
            if math.hypot(s["x_fb"] - point[0], s["y_fb"] - point[1]) <= 2.9:
                cursor = index + 1
                break
        else:
            reached = point_number
            break
    else:
        reached = len(vertices)
    moving_contact = []
    for index, sample in enumerate(task_samples):
        neighbours = task_samples[max(0, index - 1):index] + task_samples[index + 1:index + 2]
        if sample["z_fb"] <= 1 and any(other["z_fb"] <= 1 and
                (sample["x_fb"] != other["x_fb"] or sample["y_fb"] != other["y_fb"]) for other in neighbours):
            moving_contact.append(sample)
    error = max((min(distance((s["x_fb"], s["y_fb"]), a, b) for a, b in zip(vertices, vertices[1:]))
                 for s in moving_contact), default=float("inf"))
    end = trace[-1]
    finish = abs(end["x_fb"] - 50) <= 2 and abs(end["y_fb"] - 50) <= 2 and abs(end["z_fb"] - 10) <= 2
    false_states = sum(bool(s["pen_down"]) != (s["z_fb"] <= 1) for s in trace)
    # One-sample communication skew is possible; report it and require no persistent mismatch.
    longest, streak = 0, 0
    for sample in trace:
        streak = streak + 1 if bool(sample["pen_down"]) != (sample["z_fb"] <= 1) else 0
        longest = max(longest, streak)
    consistency = bounded_violations(trace, [bool(s["pen_down"]) != (s["z_fb"] <= 1) for s in trace], .04,
                                     [s["z_fb"] <= 1 for s in trace])
    return [{"id": ids[0], "pass": reached == len(vertices), "evaluated": True, "reached": reached, "total": len(vertices)},
            {"id": ids[1], "pass": bool(moving_contact) and error <= 2.9, "evaluated": True,
             "max_error": round(error, 4) if math.isfinite(error) else None, "tolerance": 2.9},
            {"id": ids[2], "pass": finish, "evaluated": True, "actual": [end[n] for n in ("x_fb", "y_fb", "z_fb")]},
            {"id": ids[3], "pass": not consistency["confirmed_indices"], "evaluated": True,
             "mismatch_samples": false_states, "longest_mismatch_samples": longest,
             "max_observed_response_s": consistency["max_response_delay_s"], "response_s": .04,
             "unresolved": consistency["unresolved"]}]


def summarize(output, protocol):
    cases, rows = [], []
    for case in protocol["cases"]:
        result_path = output / case["id"] / "result.json"
        if not result_path.exists():
            continue
        result = json.loads(result_path.read_text(encoding="utf-8"))
        cases.append(result)
        for iteration in result.get("iterations", []):
            counts = iteration["acceptance"]
            oracle = iteration.get("request_oracle", [])
            rows.append({"case": case["id"], "iteration": iteration["iter"], "duration_s": iteration["duration_s"],
                         "model_calls": iteration.get("model_calls"), "model_duration_s": iteration.get("model_duration_s"),
                         "passed": counts["passed"], "observed_passed": counts["observed_passed"],
                         "total": counts["total"], "evaluated": counts["evaluated"],
                         "request_passed": sum(r["pass"] for r in oracle), "request_total": len(oracle),
                         "failed_gate": iteration.get("failed_gate", ""), "success": result["success"]})
    executed = [c for c in cases if c.get("executed", True)]
    success = sum(c["success"] for c in executed)
    summary = {"planned": len(protocol["cases"]), "completed": len(cases), "successes": success,
               "executed": len(executed), "success_rate": success / len(protocol["cases"]), "cases": cases,
               "complete": len(executed) == len(protocol["cases"]),
               "completed_case_duration_s": round(sum(c["duration_s"] for c in cases), 3)}
    dump(output / "summary.json", summary)
    with (output / "iterations.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else ["case", "iteration"])
        writer.writeheader()
        writer.writerows(rows)
    with (output / "cases.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        fields = ["case", "status", "success", "iteration_count", "model_calls", "duration_s", "run_dir"]
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(cases)
    lines = ["# PLC fresh-generation benchmark", "", "## Material Passport", "",
             f"- Experiment: {protocol['id']}", "- Type: actual LLM + OpenPLC compilation + MuJoCo; sequential, one fresh run per case",
             "- Status: " + ("COMPLETE" if summary["complete"] else "RUNNING"),
             "- Human repair: none; no cached XML; isolated initial memory shared across cases",
             "", f"Success: {success}/{len(protocol['cases'])} planned cases; {len(cases)} completed.",
             "Success requires pipeline final AND the independent requested-geometry oracle.", "",
             "|Case|Iterations|Model calls|Seconds|Pipeline|Success|", "|---|---:|---:|---:|---|---|"]
    for result in cases:
        lines.append(f"|{result['case']}|{result['iteration_count']}|{result['model_calls']}|{result['duration_s']}|{result['status']}|{result['success']}|")
    lines += ["", "Each size is tested once: this is an observed outcome, not an estimate of production reliability.",
              "No cross-device physical validation is claimed. Native Windows toolchain remains outside this benchmark.",
              "Per-iteration data: iterations.csv; all failures and unevaluated checks are retained."]
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--case-timeout", type=int, default=2400)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    protocol_file = output / "protocol.json"
    sources = {str(p.relative_to(PROJECT_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
               for base in ("src", "runtime", "schemas", "examples/aml")
               for p in (PROJECT_ROOT / base).rglob("*") if p.suffix in (".py", ".md", ".json", ".aml")}
    if protocol_file.exists():
        protocol = json.loads(protocol_file.read_text(encoding="utf-8"))
        if protocol["sources"] != sources:
            raise RuntimeError("Source changed: create a new benchmark instead of mixing versions")
    else:
        protocol = {"id": "bench_" + datetime.now().strftime("%Y%m%d_%H%M%S"), "model": MODEL,
                    "api_url": API_URL, "max_iters": 6, "case_timeout_s": args.case_timeout,
                    "cases": matrix(), "sources": sources, "created_at": datetime.now(timezone.utc).isoformat(),
                    "generation": "fresh requirement + LLM plan + full XML; no manual repair",
                    "oracle": "after observed start edge, requested ordered contact vertices; moving contact samples within Euclidean tolerance 2.9 of requested edges (two axes each poswin=2); final safe pose. All-trace consistency response <=0.04s measured from source predicate change (one initial observation window); stable-source output glitches and unresolved end fail",
                    "model_options": {key: os.environ.get(key) for key in
                                      ("LLM_THINKING", "LLM_REASONING_EFFORT", "LLM_MAX_OUTPUT_TOKENS")}}
        dump(protocol_file, protocol)
        baseline = output / "memory_baseline"
        baseline.mkdir()
        for name, source in (("fixes.json", "workspace/memory/fixes.json"), ("lessons.json", "workspace/experience/lessons.json")):
            path = PROJECT_ROOT / source
            if path.exists():
                shutil.copy2(path, baseline / name)
    for case in protocol["cases"]:
        if (output / "STOP_AFTER_CURRENT").exists():
            print("Stopped between tasks as requested; pending tasks retain their status.", flush=True)
            summarize(output, protocol)
            return 2
        folder = output / case["id"]
        if (folder / "result.json").exists():
            continue
        folder.mkdir(exist_ok=True)
        task = protocol["id"] + "_" + case["id"]
        run = PROJECT_ROOT / "runs" / task
        if run.exists():
            raise RuntimeError(f"Interrupted case {task}; retain its evidence and adjudicate before resume")
        memory = folder / "memory"
        shutil.copytree(output / "memory_baseline", memory, dirs_exist_ok=True)
        command = [sys.executable, "-u", "-m", "src.agent.closed_loop", "--task-id", task,
                   "--request", case["request"], "--max-iters", str(protocol["max_iters"]), "--memory-dir", str(memory)]
        dump(folder / "command.json", command)
        print(f"START {case['id']}", flush=True)
        started, timed_out = time.monotonic(), False
        with (folder / "console.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(command, cwd=PROJECT_ROOT, stdout=log, stderr=subprocess.STDOUT,
                                       env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1"))
            try:
                code = process.wait(timeout=protocol["case_timeout_s"])
            except subprocess.TimeoutExpired:
                print(f"TIMEOUT {case['id']}: stopping process tree at protocol deadline", flush=True)
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                code, timed_out = -1, True
        manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8")) if (run / "manifest.json").exists() else {}
        metrics = json.loads((run / "iteration_metrics.json").read_text(encoding="utf-8")) if (run / "iteration_metrics.json").exists() else {}
        iterations = metrics.get("iterations", [])
        for iteration in iterations:
            iteration["request_oracle"] = request_oracle(case, run / ("iter_%03d" % iteration["iter"]))
        final_iter = manifest.get("iter")
        oracle = request_oracle(case, run / ("iter_%03d" % final_iter)) if final_iter else []
        success = manifest.get("status") == "final" and bool(oracle) and all(r["pass"] for r in oracle)
        result = {"case": case["id"], "status": "timeout" if timed_out else manifest.get("status", "process_error"),
                  "exit_code": code, "success": success, "duration_s": round(time.monotonic() - started, 3),
                  "iteration_count": len(list(run.glob("iter_*"))), "model_calls": len(list((run / "model_calls").glob("*.timing.json"))),
                  "iterations": iterations, "request_oracle": oracle, "run_dir": str(run), "human_repair": False}
        dump(folder / "result.json", result)
        summary = summarize(output, protocol)
        print(f"END {case['id']} {result['status']} success={success} {result['duration_s']}s; aggregate={summary['successes']}/{summary['completed']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
