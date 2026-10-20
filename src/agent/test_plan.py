"""LLM 生成的声明式测试计划及设备引用校验；不执行模型输出的代码。"""
import json
import math
from pathlib import Path
import re


class TestPlanError(ValueError):
    __test__ = False


class PlanFeasibilityError(TestPlanError):
    """位置与设备误差余量冲突；先修正规格/计划，不通过改PLC绕过。"""


def _number(value, lo, hi, name):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not lo <= value <= hi):
        raise TestPlanError("%s 须在 [%s,%s] 内" % (name, lo, hi))


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise TestPlanError("对象字段应为 %s，可选 %s；实际 %s" % (required, optional, value))


def validate_test_plan(plan, spec):
    _keys(plan, ("version", "task_id", "intent", "runtime", "sampling", "timeout_s",
                 "initial_inputs", "actions", "completion", "cleanup_inputs", "path_checks"))
    if plan["version"] != "1.0" or plan["task_id"] != spec["task_id"]:
        raise TestPlanError("测试计划版本或 task_id 不匹配")
    if not isinstance(plan["intent"], str) or not plan["intent"].strip():
        raise TestPlanError("intent 不能为空")
    _keys(plan["runtime"], ("backend", "startup_timeout_s"))
    if plan["runtime"]["backend"] != "mujoco":
        raise TestPlanError("当前仅支持 mujoco 后端")
    _number(plan["runtime"]["startup_timeout_s"], 1, 60, "startup_timeout_s")
    _keys(plan["sampling"], ("scope", "period_ms"))
    if plan["sampling"]["scope"] != "all_mapped":
        raise TestPlanError("保留全部映射信号，scope 应为 all_mapped")
    _number(plan["sampling"]["period_ms"], 10, 500, "period_ms")
    _number(plan["timeout_s"], 1, 180, "timeout_s")
    points = {p["name"]: p for p in spec["io_list"]}
    feedback = {a["io"]["fb"]: a.get("poswin", 1) for a in spec.get("axes", []) if a.get("io", {}).get("fb")}
    protected = {n for a in spec.get("axes", []) for n in a.get("io", {}).values()
                 if n in points and points[n]["dir"] == "input"}

    def value(name, val, write=False):
        point = points.get(name)
        if point is None or not point.get("address"):
            raise TestPlanError("信号 %r 不存在或缺少地址" % name)
        if write and (point["dir"] != "input" or name in protected):
            raise TestPlanError("禁止写输出或物理反馈信号 %s" % name)
        if point["type"] == "BOOL":
            if not isinstance(val, bool) or not re.fullmatch(r"%QX\d+\.[0-7]", point["address"]):
                raise TestPlanError("%s 须为 BOOL 且具有 %%QX 地址" % name)
        elif point["type"] == "INT":
            if isinstance(val, bool) or not isinstance(val, int):
                raise TestPlanError("%s 须为整数" % name)
            _number(val, *point["range"], name)
            if not re.fullmatch(r"%QW\d+", point["address"]):
                raise TestPlanError("%s 缺少 %%QW 地址" % name)
        else:
            raise TestPlanError("不支持的信号类型 %s" % name)

    def values(mapping, write=False):
        if not isinstance(mapping, dict) or not mapping:
            raise TestPlanError("信号值表不得为空")
        for name, val in mapping.items():
            value(name, val, write)

    def conditions(items):
        if not isinstance(items, list) or not 1 <= len(items) <= 64:
            raise TestPlanError("条件数组应有1~64项")
        for item in items:
            if "equals" in item:
                _keys(item, ("signal", "equals"))
                value(item["signal"], item["equals"])
            else:
                _keys(item, ("signal", "target", "tolerance"))
                value(item["signal"], item["target"])
                if points[item["signal"]]["type"] != "INT":
                    raise TestPlanError("target/tolerance 只适用于 INT")
                _number(item["tolerance"], 0, feedback.get(item["signal"], 65535), "条件 tolerance")

    for field in ("initial_inputs", "cleanup_inputs"):
        values(plan[field], write=True)
        controls = {n for n, p in points.items() if p["dir"] == "input" and p["type"] == "BOOL" and n not in protected}
        if not controls.issubset(plan[field]):
            raise TestPlanError("%s 未定义全部 BOOL 控制输入：%s" % (field, sorted(controls - set(plan[field]))))
    actions = plan["actions"]
    if not isinstance(actions, list) or not 2 <= len(actions) <= 128:
        raise TestPlanError("actions 应有2~128项")
    if actions[0] != {"op": "start_simulation"}:
        raise TestPlanError("第一个动作必须是 start_simulation")
    _keys(plan["completion"], ("all", "settle_s"))
    _number(plan["completion"]["settle_s"], .1, 5, "settle_s")
    written = set(plan["initial_inputs"])
    stimulus = False
    minimum_duration = plan["completion"].get("settle_s", 0)
    for action in actions[1:]:
        op = action.get("op") if isinstance(action, dict) else None
        if op == "set":
            _keys(action, ("op", "values"))
            values(action["values"], write=True)
            written.update(action["values"])
            stimulus = True
        elif op == "pulse":
            _keys(action, ("op", "signal", "value", "restore", "duration_ms"))
            name = action["signal"]
            value(name, action["value"], write=True)
            value(name, action["restore"], write=True)
            if points[name]["type"] != "BOOL" or action["value"] == action["restore"]:
                raise TestPlanError("pulse 应为 BOOL 电平切换")
            _number(action["duration_ms"], max(100, 2 * plan["sampling"]["period_ms"]), 10000, "duration_ms")
            written.add(name)
            stimulus = True
            minimum_duration += action["duration_ms"] / 1000
        elif op == "wait":
            _keys(action, ("op", "seconds"))
            _number(action["seconds"], 0, 30, "wait seconds")
            minimum_duration += action["seconds"]
        elif op == "wait_until":
            _keys(action, ("op", "all", "timeout_s"))
            conditions(action["all"])
            _number(action["timeout_s"], .1, plan["timeout_s"], "wait_until timeout_s")
        else:
            raise TestPlanError("未知动作 %r；禁止模型输出可执行脚本" % op)
    if not stimulus or not written.issubset(plan["cleanup_inputs"]):
        raise TestPlanError("缺少动作激励，或 cleanup_inputs 未覆盖全部被写信号")
    for ac in spec.get("acceptance", []):
        if ac.get("response_s", 0) > 0 and any(ac[key]["signal"] in written for key in ("when", "forbid")):
            raise TestPlanError("派生状态响应预算不能用于测试计划写入的命令输入；启停和安全互锁保持严格检查")
    _keys(plan["completion"], ("all", "settle_s"))
    conditions(plan["completion"]["all"])
    _number(plan["completion"]["settle_s"], .1, 5, "settle_s")
    if minimum_duration > plan["timeout_s"]:
        raise TestPlanError("固定等待和脉冲时长已超过测试总超时")
    completion = {c["signal"] for c in plan["completion"]["all"]}
    ends = {a["to"]["signal"] for a in spec["acceptance"] if a["type"] == "event_delay"}
    if not ends or not ends.issubset(completion):
        raise TestPlanError("completion 应覆盖验收条件中的全部完成信号")
    checks = plan["path_checks"]
    if not isinstance(checks, list) or not 1 <= len(checks) <= 16:
        raise TestPlanError("运动任务必须提供1~16条物理路径检查")
    ids = {a["id"] for a in spec["acceptance"]} | {"EXECUTION"}
    for check in checks:
        _keys(check, ("id", "desc", "points"), ("segments",))
        if not isinstance(check["id"], str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", check["id"]) or check["id"] in ids:
            raise TestPlanError("检查 id 非法或重复")
        ids.add(check["id"])
        if not isinstance(check["desc"], str) or not check["desc"].strip():
            raise TestPlanError("路径检查 desc 不能为空")
        if not isinstance(check["points"], list) or not 1 <= len(check["points"]) <= 256:
            raise TestPlanError("每条路径须有1~256个路点")
        for point in check["points"]:
            _keys(point, ("values", "tolerance", "states"))
            values(point["values"])
            if not feedback or set(point["values"]) != set(feedback):
                raise TestPlanError("路点必须使用全部轴的真实反馈信号，禁止用位置指令冒充")
            if not isinstance(point["tolerance"], dict) or set(point["tolerance"]) != set(feedback):
                raise TestPlanError("路点 tolerance 应逐轴定义")
            for name, tol in point["tolerance"].items():
                _number(tol, 0, feedback[name], "%s tolerance" % name)
            if not isinstance(point["states"], dict):
                raise TestPlanError("states 应为对象")
            for name, val in point["states"].items():
                value(name, val)
        if any(v is True for point in check["points"] for v in point["states"].values()) and "segments" not in check:
            raise TestPlanError("有工艺开启状态的路径必须提供 segments 线段检查")
        if "segments" in check:
            segments = check["segments"]
            _keys(segments, ("signals", "tolerance", "when"))
            signals = segments["signals"]
            if (not isinstance(signals, list) or not all(isinstance(n, str) for n in signals)
                    or len(signals) < 2 or len(set(signals)) != len(signals) or not set(signals).issubset(feedback)):
                raise TestPlanError("segments.signals 必须为至少两个不同的物理反馈信号")
            _number(segments["tolerance"], .01, max(feedback[n] for n in signals), "segments.tolerance")
            values(segments["when"])
            if any(not isinstance(v, bool) for v in segments["when"].values()):
                raise TestPlanError("segments.when 仅支持 BOOL 工艺状态")
            active = [p for p in check["points"] if all(p["states"].get(n) == v for n, v in segments["when"].items())]
            if len(active) < 2:
                raise TestPlanError("segments 至少需要两个工艺开启状态的路点")
            if not any(all(a["states"].get(n) == v and b["states"].get(n) == v for n, v in segments["when"].items())
                       and any(a["values"][n] != b["values"][n] for n in signals)
                       for a, b in zip(check["points"], check["points"][1:])):
                raise TestPlanError("segments 缺少相邻且不同的工艺开启路点")
            for a, b in zip(check["points"], check["points"][1:]):
                state_a = all(a["states"].get(n) == v for n, v in segments["when"].items())
                state_b = all(b["states"].get(n) == v for n, v in segments["when"].items())
                changed = [n for n in feedback if n not in signals and a["values"][n] != b["values"][n]]
                if (state_a != state_b and changed and all(a["values"][n] == b["values"][n] for n in signals)
                        and all(abs(a["values"][n] - b["values"][n]) <= 2 * feedback[n] for n in changed)):
                    raise PlanFeasibilityError("工艺状态切换的位置间距不足以区分设备到位误差：%s。间距须大于两倍poswin；先修正设备/规格/计划，不进入PLC生成。" % changed)
    return plan


class TestPlanGenerator:
    __test__ = False
    def __init__(self, client, max_rounds=3, report=print):
        self.client, self.max_rounds, self.report = client, max_rounds, report

    def generate(self, request, spec, evidence):
        from .config import MODEL, generation_options
        from .requirement import extract_json
        prompt = Path(__file__).with_name("prompts").joinpath("testplan_skill.md").read_text(encoding="utf-8")
        messages = [{"role": "system", "content": prompt}, {"role": "user", "content":
            "原始用户需求：\n%s\n\n已校验设备与需求规格：\n%s" % (request, json.dumps(spec, ensure_ascii=False, indent=2))}]
        evidence = Path(evidence)
        evidence.mkdir(parents=True, exist_ok=True)
        for round_no in range(1, self.max_rounds + 1):
            self.report("[test-plan] LLM 正在生成启动、激励、采样与路径验收计划（第%d次）…" % round_no)
            payload = {"model": MODEL, "messages": messages, **generation_options(12288)}
            response = self.client.chat_completions(payload, prefer_stream=True)
            (evidence / ("round_%03d.response.json" % round_no)).write_text(
                json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
            reply = response["choices"][0]["message"].get("content") or ""
            try:
                plan = json.loads(extract_json(reply) or "null")
                validate_test_plan(plan, spec)
            except (ValueError, TypeError, KeyError) as exc:
                error = str(exc)
                (evidence / ("round_%03d.error.txt" % round_no)).write_text(error, encoding="utf-8")
                if isinstance(exc, PlanFeasibilityError):
                    raise
                messages += [{"role": "assistant", "content": reply}, {"role": "user", "content":
                    "计划未通过校验：%s。只修复计划结构/引用，保留原始工艺目标；重新输出完整JSON。" % error}]
            else:
                self.report("[test-plan] 计划校验通过：%d个动作，%d条路径检查，采样%sms。" %
                            (len(plan["actions"]), len(plan["path_checks"]), plan["sampling"]["period_ms"]))
                return plan
        raise TestPlanError("LLM 测试计划未通过校验：" + error)
