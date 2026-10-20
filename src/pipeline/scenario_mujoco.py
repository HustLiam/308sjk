"""本轮 spec/SceneSpec/io_map 的无头 MuJoCo 验收；不读取 runs/latest。"""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time

from modbus_io import connect, require_program
from mujoco_verdict import Collector, build_channels, evaluate, attribute, validate_collection_spec
from mujoco_verdict import AcceptanceConfigurationError
from test_plan_runtime import execute_test_plan, evaluate_path_checks

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.agent.test_plan import validate_test_plan, TestPlanError


def verify_trajectory(trace, trajectory, spec):
    """按物理反馈验证全部路点及落笔状态，防止仅置完成灯的假阳性。"""
    axes = {a["axis"].removesuffix("_axis"): a for a in spec["axes"]}
    cursor, reached = 0, []
    for step in trajectory["steps"]:
        target = step["to"]
        down = target[2] == trajectory["pen_z"]["down"]
        found = None
        for idx in range(cursor, len(trace)):
            sample = trace[idx]
            if all(sample.get(axes[a]["io"]["fb"]) is not None
                   and abs(sample[axes[a]["io"]["fb"]] - target[i]) <= axes[a].get("poswin", 1)
                   for i, a in enumerate("xyz")) and (not down or sample.get("pen_down") is True):
                found = idx
                break
        if found is None:
            return {"id": "PATH", "desc": "轨迹路点与笔态", "pass": False,
                    "evidence": "未按顺序实际到达步%d %s（此前已到达 %s）" % (step["no"], target, reached)}
        cursor = found + 1
        reached.append({"step": step["no"], "t": trace[found]["t"]})
    return {"id": "PATH", "desc": "轨迹路点与笔态", "pass": True,
            "evidence": "全部 %d 个路点按顺序实际到达（落笔点要求 pen_down=TRUE）" % len(reached),
            "reached": reached}


def main():
    spec_path = Path(os.environ["AGENT_REQUIREMENT_SPEC"])
    scene_path = Path(os.environ["AGENT_SCENE"])
    io_path = Path(os.environ["AGENT_IO_MAP"])
    out = Path(os.environ["AGENT_EVIDENCE_DIR"])
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    iomap = json.loads(io_path.read_text(encoding="utf-8"))
    plan_path = Path(os.environ["AGENT_TEST_PLAN"]) if os.environ.get("AGENT_TEST_PLAN") else None
    plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path else None
    if plan is not None:
        try:
            validate_test_plan(plan, spec)
        except (TestPlanError, KeyError, TypeError) as exc:
            raise AcceptanceConfigurationError(str(exc)) from exc
    else:
        validate_collection_spec(spec)
    cli = connect()
    try:
        require_program(cli, int(os.environ["AGENT_PROG_ID"]), spec["task_id"])
    finally:
        cli.close()
    channels, triggers, done = build_channels(spec, iomap)
    if plan is None and (not triggers or not done):
        raise ValueError("严格验收缺少触发输入或完成信号")
    log_path = out / "runtime.log"
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    cmd = [sys.executable, "-u", str(ROOT / "runtime/mujoco_jog_runtime.py"),
           "--scene", str(scene_path), "--io-map", str(io_path), "--plc", "--plc-spec", str(spec_path),
           "--plc-host", os.environ.get("MODBUS_HOST", "127.0.0.1"),
           "--plc-port", os.environ.get("MODBUS_PORT", "502")]
    if os.environ.get("AGENT_FRAME_OUTPUT"):
        cmd += ["--frame-output", os.environ["AGENT_FRAME_OUTPUT"]]
    trace, execution = [], None
    proc = None
    with log_path.open("w", encoding="utf-8") as log:
        def start_simulation():
            nonlocal proc
            proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT, env=env)
            deadline = time.monotonic() + (plan["runtime"]["startup_timeout_s"] if plan else 30)
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    raise ConnectionError("MuJoCo 退出: " + log_path.read_text(encoding="utf-8", errors="replace")[-4000:])
                ready_marker = "PLC 物理反馈已就绪" if plan else "PLC 回环已启动"
                if ready_marker in log_path.read_text(encoding="utf-8", errors="replace"):
                    break
                time.sleep(0.1)
            else:
                raise ConnectionError("MuJoCo/PLC 回环启动超时")
            print("[runtime] 本轮 MuJoCo 回环就绪: %s" % scene_path, flush=True)
        try:
            if plan is not None:
                col = Collector(poll_s=plan["sampling"]["period_ms"] / 1000)
                trace, execution = execute_test_plan(plan, col, channels, start_simulation,
                    runtime_alive=lambda: proc is not None and proc.poll() is None,
                    report=lambda s: print(s, flush=True))
                (out / "execution.json").write_text(json.dumps(execution, ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                start_simulation()
                time.sleep(0.5)
                col = Collector(poll_s=0.02)
                limit = max(a.get("value", 0) for a in spec["acceptance"]) + 5
                trace = col.collect(channels, triggers, done, timeout_s=max(10, min(limit, 180)))
                if proc.poll() is not None:
                    raise RuntimeError("采集中 MuJoCo 退出")
        finally:
            if proc is not None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
    (out / "trace.json").write_text(json.dumps(trace, ensure_ascii=False), encoding="utf-8")
    results = evaluate(spec["acceptance"], trace, channels)
    trajectory_path = os.environ.get("AGENT_TRAJECTORY")
    if plan is not None:
        results.append({k: v for k, v in execution.items() if k != "events"})
        results += evaluate_path_checks(plan, trace)
    elif trajectory_path:
        results.append(verify_trajectory(trace, json.loads(Path(trajectory_path).read_text(encoding="utf-8")), spec))
    ok = bool(results) and all(r["pass"] for r in results)
    verdict = {"verdict": "PASS" if ok else "FAIL", "samples": len(trace),
               "duration_s": trace[-1]["t"] if trace else 0, "details": results,
               "spec": str(spec_path), "scene": str(scene_path), "io_map": str(io_path),
               "sha256": {name: hashlib.sha256(path.read_bytes()).hexdigest()
                          for name, path in {"spec": spec_path, "scene": scene_path,
                                             "io_map": io_path, "xml": out / "plcopen.xml"}.items()}}
    if plan_path:
        verdict["test_plan"] = str(plan_path)
        verdict["sha256"]["test_plan"] = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    (out / "verdict.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
    for r in results:
        print("  %s %s %s — %s" % ("PASS" if r["pass"] else "FAIL", r["id"], r["desc"], r["evidence"]), flush=True)
    if not ok:
        report = attribute(results, trace, channels, done)
        (out / "report.md").write_text(report, encoding="utf-8")
        print(report, flush=True)
    print("场景验收: " + ("全部通过" if ok else "存在失败"), flush=True)
    if execution and execution["status"] == "environment_error":
        print("ConnectionError: " + execution["evidence"], flush=True)
        return 3
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
