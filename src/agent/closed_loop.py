"""真实 LLM 完整 XML → OpenPLC → 本轮 MuJoCo → 确定性验收的严格入口。"""
import argparse
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sys
import time
import subprocess
import hashlib
import re

from .aml_parser import parse_aml
from .attribution import AttributionEngine
from .client import BigModelClient
from .config import API_URL, MODEL, PROJECT_ROOT, get_api_key
from .orchestrator import Orchestrator, narrate
from .pipeline import PLCGenerator
from .requirement import RequirementUnderstander
from .scene_gen import SceneSpecGenerator
from .test_plan import TestPlanGenerator, PlanFeasibilityError
from .memory import MemoryStore


DEFAULT_REQUEST = (
    "三轴 CSP 绘图仪：run 使能，cmd_draw 上升沿启动。先抬笔到10mm，再定位到(20,20)，"
    "落笔到0mm，依次经(80,20)、(80,80)、(20,80)、(20,20)闭合正方形，"
    "最后抬笔到10mm再回中心(50,50)，保持 plot_done。run=FALSE 或 cmd_reset 复位。"
    "x/y 为0..100工程百分比，z为0..10毫米，IO与轴参数严格锚定设备模型。"
    "task_goal 明确上述路线，constraints 记载运动顺序和失能复位。")


def bind_square_acceptance_protocol(spec):
    """旧画方协议兼容函数；新 CLI/GUI 只采用 LLM 测试计划。"""
    from mujoco_verdict import AcceptanceConfigurationError, validate_collection_spec
    points = {p["name"]: p for p in spec.get("io_list", [])}
    names = ("run", "cmd_draw", "cmd_reset")
    for name in names:
        point = points.get(name, {})
        if point.get("dir") != "input" or point.get("type") != "BOOL" or not point.get("address"):
            raise AcceptanceConfigurationError("标准画方验收缺少带地址的 input BOOL：%s" % name)
    extras = [p["name"] for p in points.values() if p.get("trigger") and p["name"] not in names]
    if extras:
        raise AcceptanceConfigurationError("标准画方未定义这些额外激励：%s" % extras)
    bindings = []
    for name in names:
        point = points[name]
        desired = name != "cmd_reset"
        bindings.append({"signal": name, "address": point["address"],
                         "model_trigger": point.get("trigger"), "effective_trigger": desired})
        point["trigger"] = desired
    validate_collection_spec(spec)
    return {"protocol": "standard_square", "source": "explicit entry-point stimulus contract",
            "bindings": bindings}


class RecordingClient(BigModelClient):
    """只记录模型请求体/回复，不记录 HTTP 头或密钥。"""
    def __init__(self, key, evidence):
        super().__init__(key)
        self.evidence, self.calls = evidence, 0
        self.stage = "requirement"

    def chat_completions(self, payload, **kwargs):
        payload = self.prepare_payload(payload)
        self.calls += 1
        started = time.monotonic()
        stem = self.evidence / ("call_%03d" % self.calls)
        stem.parent.mkdir(parents=True, exist_ok=True)
        stem.with_suffix(".request.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            result = super().chat_completions(payload, **kwargs)
        except RuntimeError as exc:
            stem.with_suffix(".error.json").write_text(
                json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2), encoding="utf-8")
            raise
        finally:
            stem.with_suffix(".timing.json").write_text(json.dumps({
                "call": self.calls, "duration_s": round(time.monotonic() - started, 3),
                "stage": self.stage, "started_at": datetime.now(timezone.utc).timestamp() - (time.monotonic() - started),
                "model": payload.get("model"), "max_tokens": payload.get("max_tokens")},
                ensure_ascii=False, indent=2), encoding="utf-8")
        stem.with_suffix(".response.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result


def record_api_blocked(exc, run, client, stage):
    """Record model service failures without turning them into PLC repair tasks."""
    status = getattr(client, "blocked_status", None)
    if status not in ("api_blocked", "api_unavailable") and not re.search(r"HTTP\s+(?:401|402)\b", str(exc)):
        return False
    status = status or "api_blocked"
    data = {"status": status, "stage": stage, "error": str(exc), "model": MODEL,
            "model_calls": client.calls, "generation_mode": "full_xml", "seed_used": False,
            "require_online": True, "run_dir": str(run)}
    for name in ("api_error.json", "manifest.json"):
        (Path(run) / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    narrate(status, data)
    print("RESULT: %s -> %s" % (status, run), flush=True)
    return True


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aml", default=str(PROJECT_ROOT / "examples/aml/plotter_square_csp_station.aml"))
    parser.add_argument("--request", default=DEFAULT_REQUEST)
    parser.add_argument("--task-id")
    parser.add_argument("--max-iters", type=int, default=6)
    parser.add_argument("--spec", help="复现已冻结规格；省略则由 LLM 从 AML/自然语言理解需求")
    parser.add_argument("--frame-output", help="可选：将实际验收仿真画面发布到该 npy 文件（供 Qt 界面读取）")
    parser.add_argument("--memory-dir", help="可选：隔离本次运行的经验文件，供可重复批量测试使用")
    args = parser.parse_args()
    if not get_api_key():
        raise RuntimeError("未配置模型密钥")
    task = args.task_id or "full_xml_square_" + datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S")
    run = PROJECT_ROOT / "runs" / task
    if run.exists():
        raise ValueError("任务目录已存在，换新 task-id，避免覆盖实验记录")
    run.mkdir(parents=True)
    (run / "request.txt").write_text(args.request, encoding="utf-8")
    client = RecordingClient(get_api_key(), run / "model_calls")
    start = time.monotonic()
    print("[model] %s | %s | full_xml（不使用种子或确定性运动栈）" % (API_URL, MODEL), flush=True)
    if args.spec:
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    else:
        model, problems = parse_aml(args.aml)
        if problems:
            raise ValueError("AML 不可用于严格闭环: %s" % problems)
        print("[requirement] 正在由真实模型理解自然语言需求…", flush=True)
        try:
            result = RequirementUnderstander(client=client).understand(args.request, model, task_id=task)
        except RuntimeError as exc:
            if record_api_blocked(exc, run, client, "requirement"):
                return 2
            raise
        (run / "requirement_report.json").write_text(json.dumps(result["report"], ensure_ascii=False, indent=2), encoding="utf-8")
        if result["spec"] is None:
            raise ValueError("需求理解未通过: %s" % result["report"])
        spec = result["spec"]
    spec["task_id"], spec["prog_id"] = task, 4
    (run / "requirement_spec.model.json").write_text(
        json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        client.stage = "test_plan"
        test_plan = TestPlanGenerator(client).generate(args.request, spec, run / "test_plan_generation")
    except PlanFeasibilityError as exc:
        data = {"status": "configuration_error", "stage": "test_plan", "error": str(exc),
                "model": MODEL, "model_calls": client.calls, "seed_used": False, "generation_mode": "full_xml"}
        (run / "manifest.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        narrate("configuration_error", data)
        print(str(exc), flush=True)
        print("RESULT: configuration_error -> %s" % run, flush=True)
        return 2
    except RuntimeError as exc:
        if record_api_blocked(exc, run, client, "test_plan"):
            return 2
        raise
    print("[preflight] LLM 测试计划已校验；将按计划启动仿真并执行激励。", flush=True)
    generator = PLCGenerator(client=client, no_cards=True, generation_mode="full_xml", report=lambda s: print(s, flush=True))
    client.stage = "plc_generation_and_diagnosis"
    memory = (MemoryStore(episodic_path=Path(args.memory_dir) / "fixes.json",
                          lessons_path=Path(args.memory_dir) / "lessons.json") if args.memory_dir else None)
    orch = Orchestrator(max_iters=args.max_iters,
                        attribution_engine=AttributionEngine(client=client, model=MODEL, memory=memory))
    result = orch.solve(spec, generator, deploy=True, acceptance="mujoco", echo=narrate,
                        scene_generator=SceneSpecGenerator(), test_plan=test_plan,
                        aml_path=args.aml, require_online=True, frame_output=args.frame_output)
    manifest = {"status": result["status"], "iter": result["iter"], "model": MODEL,
                "stop_reason": result.get("stop_reason"),
                "api_url": API_URL, "generation_mode": "full_xml", "model_calls": client.calls,
                "seed_used": False, "require_online": True, "duration_s": round(time.monotonic() - start, 2),
                "test_plan_source": "llm", "test_plan_sha256": hashlib.sha256(
                    (run / "test_plan.json").read_bytes()).hexdigest(),
                "command": sys.argv, "run_dir": str(run),
                "thinking": __import__("os").environ.get("LLM_THINKING", "disabled"),
                "reasoning_effort": __import__("os").environ.get("LLM_REASONING_EFFORT"),
                "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT).decode().strip(),
                "source_sha256": {str(p.relative_to(PROJECT_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in (PROJECT_ROOT / "src").rglob("*.py")}}
    (run / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("RESULT: %s -> %s" % (result["status"], run), flush=True)
    return 0 if result["status"] == "final" else 1


if __name__ == "__main__":
    raise SystemExit(main())
