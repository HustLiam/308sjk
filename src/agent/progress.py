"""依据同一测试条件下的实际证据选择修复基底，拒绝用退化候选替换最佳版。"""
import hashlib
import json
import math
import os
from pathlib import Path
import shutil

STAGES = {"generate": 0, "xml2st": 1, "consistency": 2, "scene": 3,
          "deploy": 4, "acceptance": 5, "all": 6}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def profile(iter_dir, iteration, gate, spec, plan, project_root, strict=False):
    """摘要来自本轮原始trace/verdict；不按PASS行数或原始违规次数选版本。"""
    folder, root = Path(iter_dir), Path(project_root)
    context = {"spec": hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest(),
               "plan": hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()}
    for relative in ("src/pipeline/mujoco_verdict.py", "src/pipeline/test_plan_runtime.py",
                     "src/pipeline/trace_query.py",
                     "src/pipeline/condition_response.py",
                     "src/pipeline/scenario_mujoco.py", "src/pipeline/modbus_io.py", "src/pipeline/xml2st.py",
                     "src/agent/progress.py", "src/agent/scene_gen.py", "src/agent/consistency_check.py",
                     "src/pipeline/compiler_guard.py", "src/pipeline/run_deploy.py",
                     "src/pipeline/openplc_client.py", "runtime/mujoco_jog_runtime.py",
                     "runtime/plc_link.py", "runtime/mujoco_build.py", "runtime/gantry_bridge.py", "runtime/frame_stream.py"):
        path = root / relative
        context[relative] = digest(path) if path.is_file() else "missing"
    for name in ("scene.spec.json", "io_map.json"):
        if (folder / name).is_file():
            context[name] = digest(folder / name)
    if (folder / "compiler.json").is_file():
        compiler = read_json(folder / "compiler.json")
        stable = {k: compiler.get(k) for k in ("image_id", "optimizer", "compile_script_sha256")}
        context["compiler"] = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()
    for name, path in {"requirement_file": folder.parent / "requirements/requirement_spec.json",
                       "test_plan_file": folder / "test_plan.json"}.items():
        if path.is_file():
            context[name] = digest(path)
    result = {"iter": iteration, "gate": gate, "stage": STAGES[gate], "context": context,
              "xml_sha256": digest(folder / "plcopen.xml") if (folder / "plcopen.xml").is_file() else None,
              "verified_passes": [], "observed": [], "completed": False, "paths": {},
              "violation_rates": {}, "violation_counts": {}, "failures": [], "online_evidence": False}
    if (folder / "gate.json").is_file():
        result["failures"] = [{"id": gate, "evidence": str(e)} for e in read_json(folder / "gate.json").get("errors", [])]
    if gate not in ("acceptance", "all"):
        return result
    if not (folder / "verdict.json").is_file():
        if strict:
            raise ValueError("在线验收缺少本轮verdict.json，不得把缺证据当作通过")
        return result
    verdict, trace = read_json(folder / "verdict.json"), read_json(folder / "trace.json")
    execution = read_json(folder / "execution.json")
    if not trace or any(not {p["name"] for p in spec["io_list"]}.issubset(s) for s in trace):
        raise ValueError("本轮trace为空或缺少映射信号")
    if strict:
        if read_json(folder.parent / "requirements/requirement_spec.json") != spec or read_json(folder / "test_plan.json") != plan:
            raise ValueError("需求或测试计划与任务开始时的内容不一致")
        for key, path in {"xml": folder / "plcopen.xml", "test_plan": folder / "test_plan.json",
                          "scene": folder / "scene.spec.json", "io_map": folder / "io_map.json"}.items():
            if verdict.get("sha256", {}).get(key) != digest(path):
                raise ValueError("验收证据与本轮%s哈希不符" % key)
        if verdict.get("sha256", {}).get("spec") != digest(folder.parent / "requirements/requirement_spec.json"):
            raise ValueError("验收证据与需求规格哈希不符")
    details = {r["id"]: r for r in verdict["details"]}
    expected = {a["id"] for a in spec["acceptance"]} | {c["id"] for c in plan["path_checks"]} | {"EXECUTION"}
    if not expected.issubset(details):
        raise ValueError("验收证据缺少测试计划规定的判据")
    if strict:
        from mujoco_verdict import build_channels, evaluate
        from test_plan_runtime import evaluate_path_checks
        channels, _, _ = build_channels(spec, read_json(folder / "io_map.json"))
        recomputed = evaluate(spec["acceptance"], trace, channels) + evaluate_path_checks(plan, trace)
        recomputed.append({"id": "EXECUTION", "pass": execution.get("status") == "passed" and execution.get("cleanup_ok") is True})
        if any(details[r["id"]].get("pass") is not r["pass"] for r in recomputed):
            raise ValueError("原始trace重新判定与verdict不一致，不得用于版本晋升")
    result["online_evidence"] = True
    result["completed"] = execution.get("status") == "passed" and execution.get("cleanup_ok") is True
    observed, passed = set(), set()
    for ac in spec["acceptance"]:
        key = ac["id"]
        if ac["type"] == "forbidden_state":
            from mujoco_verdict import condition_violations
            when, forbid = ac["when"], ac["forbid"]
            eligible, violations = condition_violations(ac, trace)
            if eligible:
                observed.add(key)
                count = len(violations["confirmed_indices"])
                result["violation_counts"][key] = count
                result["violation_rates"][key] = count / len(eligible)
        elif ac["type"] == "region_containment":
            if result["completed"]:
                observed.add(key)
        else:
            # event_delay 的 PASS 必须有起止沿；未完成时不能凭完成信号相关条件的空集PASS获益。
            observed.add(key)
        if details[key].get("pass") is True and key in observed:
            passed.add(key)
    for check in plan["path_checks"]:
        r = details[check["id"]]
        deviation = r.get("max_segment_deviation")
        result["paths"][check["id"]] = {"reached": len(r.get("reached", [])), "total": len(check["points"]),
            "deviation": deviation if isinstance(deviation, (int, float)) and math.isfinite(deviation) else None}
        if r.get("pass") is True:
            passed.add(check["id"])
    if result["completed"]:
        passed.add("EXECUTION")
    result["verified_passes"], result["observed"] = sorted(passed), sorted(observed)
    result["failures"] = [{"id": r["id"], "evidence": r.get("evidence", "")}
                           for r in details.values() if r.get("pass") is not True]
    if strict and gate == "all" and (verdict.get("verdict") != "PASS" or not result["completed"]
            or any(r.get("pass") is not True for r in details.values())
            or not {a["id"] for a in spec["acceptance"]}.issubset(observed)):
        raise ValueError("最终交付缺少全部判据的实际触发覆盖或成功证据")
    return result


def compare(base, candidate):
    """先检查所有不可退步项，再要求至少一项可验证推进；不做加权抵消。"""
    if base is None:
        return {"decision": "promote" if candidate["xml_sha256"] and candidate["stage"] >= 3 else "retain",
                "reasons": ["建立首个通过静态检查的修复基底"]}
    optional = {"scene.spec.json", "io_map.json", "compiler"}
    changed = [k for k, v in base["context"].items() if (k not in optional or k in candidate["context"]
                or candidate["stage"] >= base["stage"]) and candidate["context"].get(k) != v]
    if changed:
        return {"decision": "incomparable", "reasons": ["测试条件或判定器改变：%s；须建立新的实验记录" % changed]}
    regressions = []
    if candidate["stage"] < base["stage"]:
        regressions.append("检查阶段倒退")
    if not set(base["verified_passes"]).issubset(candidate["verified_passes"]):
        regressions.append("丢失已实际验证的判据：%s" % sorted(set(base["verified_passes"]) - set(candidate["verified_passes"])))
    if base["completed"] and not candidate["completed"]:
        regressions.append("从能够完成退化为不能完成")
    if base["online_evidence"] and not candidate["online_evidence"]:
        regressions.append("丢失在线验收证据")
    for key, old in base["paths"].items():
        new = candidate["paths"].get(key, {})
        if new.get("reached", 0) < old["reached"]:
            regressions.append("%s 路点进度从%d退到%d" % (key, old["reached"], new.get("reached", 0)))
        if new.get("total") != old["total"]:
            regressions.append("%s 路径检查数量改变" % key)
        if key not in base["verified_passes"] and old["deviation"] is not None and (new.get("deviation") is None or new["deviation"] > old["deviation"] + 1e-12):
            regressions.append("%s 线段偏差变差" % key)
    for key, old in base["violation_rates"].items():
        if key not in candidate["violation_rates"]:
            regressions.append("%s 原有触发条件不再被观察到" % key)
        elif candidate["violation_rates"][key] > old + 1e-12:
            regressions.append("%s 违规比例升高" % key)
        if candidate["violation_counts"].get(key, 0) > base["violation_counts"].get(key, 0):
            regressions.append("%s 违规样本增加" % key)
    if regressions:
        return {"decision": "reject", "reasons": regressions}
    improvements = []
    if candidate["stage"] > base["stage"]:
        improvements.append("通过了更后面的检查阶段")
    if set(candidate["verified_passes"]) > set(base["verified_passes"]):
        improvements.append("新增实际验证通过的判据")
    for key, new in candidate["paths"].items():
        old = base["paths"].get(key, {})
        if new["reached"] > old.get("reached", 0):
            improvements.append("%s 物理路点进度增加" % key)
        if key not in candidate["verified_passes"] and new["deviation"] is not None:
            if old.get("deviation") is None or new["deviation"] < old["deviation"] - 1e-12:
                improvements.append("%s 未通过线段的偏差降低" % key)
    for key, value in candidate["violation_rates"].items():
        if (key in base["violation_rates"] and value < base["violation_rates"][key] - 1e-12
                and candidate["violation_counts"].get(key, 0) < base["violation_counts"].get(key, 0)):
            improvements.append("%s 违规比例与样本数同时降低" % key)
    return {"decision": "promote" if improvements else "retain", "reasons": improvements or ["证据未显示推进，保留原最佳版"]}


class ProgressTracker:
    def __init__(self, run_dir, spec, plan, project_root, strict=False, max_rejected=2):
        self.run_dir, self.project_root = Path(run_dir), project_root
        self.spec, self.plan = json.loads(json.dumps(spec)), json.loads(json.dumps(plan))
        self.strict, self.max_rejected = strict, max_rejected
        self.best, self.history, self.rejected = None, [], 0
        self.stop_reason = None

    def consider(self, folder, iteration, gate):
        try:
            candidate = profile(folder, iteration, gate, self.spec, self.plan, self.project_root, self.strict)
            decision = compare(self.best, candidate)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            candidate = None
            decision = {"decision": "incomparable", "reasons": ["验收证据不可比较：%s" % exc]}
        decision.update(iter=iteration, baseline_iter=(self.best or {}).get("iter"), candidate=candidate)
        self.history.append(decision)
        Path(folder, "progress.json").write_text(json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8")
        if decision["decision"] == "promote":
            # 不可变快照先完整落盘，最后才原子替换最佳版指针；中断不会覆盖旧源码。
            checkpoints = self.run_dir / "checkpoints"
            checkpoints.mkdir(exist_ok=True)
            if checkpoints.resolve().parent != self.run_dir.resolve():
                raise ValueError("checkpoints目录不在本次运行目录内")
            checkpoint = checkpoints / ("iter_%03d" % iteration)
            staged = checkpoint.with_name(checkpoint.name + ".pending")
            shutil.copytree(folder, staged)
            os.replace(staged, checkpoint)
            candidate["checkpoint"] = str(checkpoint.relative_to(self.run_dir))
            atomic_json(self.run_dir / "best.json", candidate)
            self.best, self.rejected = candidate, 0
            dest = self.run_dir / "best"
            if dest.exists():
                if dest.resolve().parent != self.run_dir.resolve():
                    raise ValueError("best目录不在本次运行目录内")
                shutil.rmtree(dest)
            shutil.copytree(checkpoint, dest)
        else:
            self.rejected += 1
            if decision["decision"] == "incomparable":
                self.stop_reason = "evidence_changed"
            elif self.rejected >= self.max_rejected:
                self.stop_reason = "no_progress"
        (self.run_dir / "progress_history.json").write_text(json.dumps(self.history, ensure_ascii=False, indent=2), encoding="utf-8")
        return decision

    def contract(self):
        if self.best is None:
            return None
        failures = self.best["failures"]
        return {"baseline_iter": self.best["iter"], "baseline_xml_sha256": self.best["xml_sha256"],
                "target": failures[0] if failures else {"gate": self.best["gate"]},
                "preserve_verified_passes": self.best["verified_passes"],
                "preserve_completed": self.best["completed"], "preserve_paths": self.best["paths"],
                "baseline_failures": failures,
                "rules": ["本轮仅解决目标问题及必要的关联修改，仍输出完整XML", "需求、测试计划、接口与容差不许改变",
                          "不得用减少运行步骤、缩短观察或取消触发条件来减少违规样本",
                          "失败候选不作为下一轮源码；使用baseline_iter的源码和失败证据"]}

    def feedback(self, previous):
        contract = self.contract()
        if contract is None:
            return previous
        return ("修复约束（当前最佳版本，优先级高于失败候选的行号）：\n" + json.dumps(contract, ensure_ascii=False, indent=2)
                + "\n最近候选评估：" + json.dumps({k: v for k, v in self.history[-1].items() if k != "candidate"}, ensure_ascii=False)
                + "\n以下是候选尝试的证据，不能据此覆盖基底已通过的行为：\n" + previous)
