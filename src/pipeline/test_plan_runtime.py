"""受限测试计划解释器：执行数据中的动作、记录全量信号、检查真实反馈。"""
import math
import json
import time

from pymodbus.exceptions import ModbusException


class PlanTimeout(RuntimeError):
    pass


def conditions_match(clauses, sample):
    for clause in clauses:
        value = sample.get(clause["signal"])
        if value is None:
            return False
        if "equals" in clause:
            if value != clause["equals"]:
                return False
        elif not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value - clause["target"]) > clause["tolerance"]:
            return False
    return True


def apply_inputs(client, channels, values):
    """只写已校验的输入；线圈按完整16位跨度读改写，保留其他位。"""
    by_name = {c["name"]: c for c in channels}
    bools = {by_name[n]["bit"]: v for n, v in values.items() if by_name[n]["type"] == "bool"}
    if bools:
        span = (max(c["bit"] for c in channels if c["type"] == "bool") // 16 + 1) * 16
        response = client.read_coils(address=0, count=span, slave=1)
        if response.isError():
            raise ConnectionError("读取激励线圈失败")
        group = [bool(v) for v in response.bits[:span]]
        for bit, value in bools.items():
            group[bit] = value
        if client.write_coils(address=0, values=group, slave=1).isError():
            raise ConnectionError("写入激励线圈失败")
    for name, value in values.items():
        channel = by_name[name]
        if channel["type"] == "analog":
            if client.write_register(address=channel["addr"], value=value & 0xffff, slave=1).isError():
                raise ConnectionError("写入输入寄存器失败：%s" % name)


def execute_test_plan(plan, collector, channels, start_simulation, runtime_alive=lambda: True,
                      report=print, clock=time.monotonic, sleep=time.sleep):
    """执行校验后的计划；启动/清理机制固定，动作信号与先后顺序完全取自计划。"""
    trace, events = [], []
    started = clock()
    event_started = started
    deadline = None
    period = plan["sampling"]["period_ms"] / 1000
    current_action = -1
    status, error, cleanup_ok = "passed", None, False

    def event(op, **fields):
        item = {"t": round(clock() - event_started, 6), "action": current_action, "op": op, **fields}
        events.append(item)
        report("[execution] %s" % item)

    def take():
        if clock() > deadline:
            raise PlanTimeout("测试计划总超时 %ss" % plan["timeout_s"])
        if not runtime_alive():
            raise ConnectionError("MuJoCo 运行时提前退出")
        sample = collector.sample_once(channels)
        sample["t"] = round(clock() - started, 6)
        trace.append(sample)
        return sample

    def wait(seconds, clauses=None):
        requested_until = clock() + seconds
        until = min(requested_until, deadline)
        while True:
            sample = take()
            if clauses is not None and conditions_match(clauses, sample):
                return True
            remain = until - clock()
            if remain <= 0:
                if clauses is None and requested_until > deadline + 1e-9:
                    raise PlanTimeout("测试总超时截断了计划等待或脉冲")
                return clauses is None
            sleep(min(period, remain))

    try:
        apply_inputs(collector.cli, channels, plan["initial_inputs"])
        event("initial_inputs", values=plan["initial_inputs"])
        current_action = 0
        event("start_simulation")
        start_simulation()
        started = clock()
        deadline = started + plan["timeout_s"]
        take()
        for current_action, action in enumerate(plan["actions"][1:], start=1):
            op = action["op"]
            event(op, parameters={k: v for k, v in action.items() if k != "op"})
            if op == "set":
                apply_inputs(collector.cli, channels, action["values"])
                take()
            elif op == "pulse":
                name = action["signal"]
                apply_inputs(collector.cli, channels, {name: action["value"]})
                wait(action["duration_ms"] / 1000)
                apply_inputs(collector.cli, channels, {name: action["restore"]})
                take()
            elif op == "wait":
                wait(action["seconds"])
            elif op == "wait_until":
                if not wait(action["timeout_s"], action["all"]):
                    raise PlanTimeout("动作%d wait_until 超时：%s" % (current_action, action["all"]))
            else:
                raise ValueError("未知测试计划动作 %s" % op)
        event("wait_completion", conditions=plan["completion"]["all"])
        if not wait(max(0, deadline - clock()), plan["completion"]["all"]):
            raise PlanTimeout("完成条件未满足")
        wait(plan["completion"]["settle_s"])
    except Exception as exc:
        error = "%s: %s" % (type(exc).__name__, exc)
        status = "environment_error" if isinstance(exc, (OSError, ModbusException, ConnectionError)) else "failed"
        event("failed", error=error)
    finally:
        try:
            apply_inputs(collector.cli, channels, plan["cleanup_inputs"])
            cleanup_ok = True
            event("cleanup_inputs", values=plan["cleanup_inputs"])
        except Exception as exc:
            status = "environment_error"
            error = (error or "") + "; 清理输入失败：%s" % exc
        collector.cli.close()
    result = {"id": "EXECUTION", "desc": "LLM 测试计划执行与清理", "pass": status == "passed" and cleanup_ok,
              "status": status, "action": current_action, "cleanup_ok": cleanup_ok,
              "evidence": error or "动作、完成条件与清理均已执行", "events": events}
    return trace, result


def evaluate_path_checks(plan, trace):
    results = []
    for check in plan["path_checks"]:
        reached, cursor = [], 0
        result = {"id": check["id"], "desc": check["desc"], "pass": True, "reached": reached}
        for no, point in enumerate(check["points"], start=1):
            found = None
            def matches(sample):
                return all(isinstance(sample.get(n), (int, float)) and math.isfinite(sample[n])
                           and abs(sample[n] - val) <= point["tolerance"][n] for n, val in point["values"].items()) and all(
                            n in sample and sample[n] == val for n, val in point["states"].items())
            for index in range(cursor, len(trace)):
                sample = trace[index]
                if matches(sample):
                    # 以首次连续到位区间内最接近顶点的样本分段。
                    # 刚进入逐轴容差框的来向采样仍属于前一条边，不能提前归到下一条边。
                    window = [index]
                    for later in range(index + 1, len(trace)):
                        if not matches(trace[later]):
                            break
                        window.append(later)
                    found = min(window, key=lambda j: sum(
                        ((trace[j][n]-v) / (point["tolerance"][n] or 1))**2 for n, v in point["values"].items()))
                    break
            if found is None:
                result.update({"pass": False, "evidence": "未按顺序到达路点%d：%s" % (no, point)})
                break
            reached.append({"step": no, "t": trace[found]["t"], "sample": found})
            cursor = found + 1
        if result["pass"]:
            result["evidence"] = "LLM 计划的全部%d个物理路点按序到达且工艺状态满足" % len(reached)
        segments = check.get("segments")
        if segments and result["pass"]:
            names, when = segments["signals"], segments["when"]
            edges = [(i, a, b) for i, (a, b) in enumerate(zip(check["points"], check["points"][1:]))
                     if all(a["states"].get(n) == v and b["states"].get(n) == v for n, v in when.items())]
            errors, worst_sample = [], None
            for index, a, b in edges:
                start = [a["values"][n] for n in names]
                delta = [b["values"][n] - a["values"][n] for n in names]
                length2 = sum(d*d for d in delta)
                for sample in trace[reached[index]["sample"]:reached[index+1]["sample"]+1]:
                    held_axes = {n: v for n, v in a["values"].items() if n not in names and b["values"][n] == v}
                    if (not all(sample.get(n) == v for n, v in when.items())
                            or not all(n in sample and abs(sample[n]-v) <= max(a["tolerance"][n], b["tolerance"][n]) for n, v in held_axes.items())
                            or not all(isinstance(sample.get(n), (int, float)) and math.isfinite(sample[n]) for n in names)):
                        errors.append(float("inf"))
                        if worst_sample is None or worst_sample[0] != float("inf"):
                            worst_sample = (float("inf"), {"segment": index + 1, "t": sample.get("t"),
                                "actual": {n: sample.get(n) for n in a["values"]},
                                "reason": "工艺状态中断、保持轴越差或反馈缺失"})
                        continue
                    ratio = max(0, min(1, sum((sample[n]-x)*d for n, x, d in zip(names, start, delta)) / length2)) if length2 else 0
                    distance = math.sqrt(sum((sample[n] - x - ratio*d)**2 for n, x, d in zip(names, start, delta)))
                    errors.append(distance)
                    if worst_sample is None or distance > worst_sample[0]:
                        worst_sample = (distance, {"segment": index + 1, "t": sample.get("t"),
                            "actual": {n: sample.get(n) for n in a["values"]},
                            "from": a["values"], "to": b["values"], "reason": "偏离计划线段"})
            worst = max(errors, default=float("inf"))
            result["max_segment_deviation"] = worst if math.isfinite(worst) else None
            if not errors or worst > segments["tolerance"]:
                result["pass"] = False
                result["evidence"] += "；工艺开启时偏离计划线段，最大偏差%s（容差%s）" % (worst, segments["tolerance"])
                if worst_sample is not None:
                    result["segment_failure"] = worst_sample[1]
                    result["evidence"] += "；最差样本" + json.dumps(worst_sample[1], ensure_ascii=False)
        results.append(result)
    return results
