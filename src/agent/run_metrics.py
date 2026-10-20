"""Persist iteration timing and acceptance counts, including interrupted runs."""
from functools import wraps
import inspect
import json
from pathlib import Path
import time


def acceptance_counts(iter_dir, expected_ids):
    path = Path(iter_dir) / "verdict.json"
    details = json.loads(path.read_text(encoding="utf-8")).get("details", []) if path.exists() else []
    by_id = {d.get("id"): d for d in details}
    return {"passed": sum(by_id.get(k, {}).get("pass") is True for k in expected_ids),
            "observed_passed": sum(by_id.get(k, {}).get("pass") is True and
                                   by_id[k].get("observed") is not False for k in expected_ids),
            "total": len(expected_ids), "evaluated": len(set(by_id) & set(expected_ids)),
            "not_evaluated": [k for k in expected_ids if k not in by_id]}


def record_solve(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        bound = inspect.signature(function).bind(*args, **kwargs)
        bound.apply_defaults()
        owner, spec = bound.arguments["self"], bound.arguments["spec"]
        plan = bound.arguments.get("test_plan")
        root = owner.runs_root / spec["task_id"]
        ids = [a["id"] for a in spec.get("acceptance", [])]
        if plan:
            ids += ["EXECUTION"] + [p["id"] for p in plan.get("path_checks", [])]
        caller_echo = bound.arguments.get("echo")
        records, active, began = [], None, time.monotonic()

        def save(final=False):
            now = time.monotonic()
            if active:
                active["duration_s"] = round(now - active.pop("_start", now), 3)
                active["ended_at"] = time.time()
                active["acceptance"] = acceptance_counts(root / ("iter_%03d" % active["iter"]), ids)
                calls = []
                for path in (root / "model_calls").glob("*.timing.json"):
                    call = json.loads(path.read_text(encoding="utf-8"))
                    if active["started_at"] <= call["started_at"] <= active["ended_at"]:
                        calls.append(call)
                active["model_calls"] = len(calls)
                active["model_duration_s"] = round(sum(c["duration_s"] for c in calls), 3)
                records.append(dict(active))
                active["_start"] = now
            if root.exists():
                (root / "iteration_metrics.json").write_text(json.dumps({
                    "version": 1, "iteration_definition": "one generated/repaired candidate and its checks; may include multiple model calls",
                    "acceptance_ids": ids, "iterations": records,
                    "solve_duration_s": round(now - began, 3), "complete": final},
                    ensure_ascii=False, indent=2), encoding="utf-8")

        def echo(event, payload):
            nonlocal active
            now = time.monotonic()
            if event == "iter_start":
                save()
                active = {"iter": payload["iter"], "_start": now, "started_at": time.time(), "events": []}
            if active:
                active["events"].append({"event": event, "elapsed_s": round(now - active["_start"], 3),
                                         "payload": payload if event != "acceptance_line" else None})
                if event == "gate_failed":
                    active["failed_gate"] = payload.get("gate")
            if caller_echo:
                caller_echo(event, payload)

        bound.arguments["echo"] = echo
        try:
            result = function(*bound.args, **bound.kwargs)
            return result
        finally:
            save(final=True)
    return wrapped
