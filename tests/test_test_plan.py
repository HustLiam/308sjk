"""声明式计划：拒绝伪造反馈/任意脚本，按实际信号动作并始终执行清理。"""
import copy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src/pipeline"))
from agent.test_plan import TestPlanGenerator, TestPlanError, validate_test_plan
from test_plan_runtime import execute_test_plan, evaluate_path_checks
from mujoco_verdict import build_channels


def spec():
    return json.loads((ROOT / "examples/specs/plotter_square_csp.spec.json").read_text(encoding="utf-8"))


def plan():
    return {"version": "1.0", "task_id": spec()["task_id"], "intent": "test polygon",
        "runtime": {"backend": "mujoco", "startup_timeout_s": 30},
        "sampling": {"scope": "all_mapped", "period_ms": 20}, "timeout_s": 2,
        "initial_inputs": {"run": False, "cmd_reset": False, "cmd_draw": False},
        "actions": [{"op": "start_simulation"}, {"op": "set", "values": {"run": True}},
                    {"op": "pulse", "signal": "cmd_draw", "value": True, "restore": False, "duration_ms": 200}],
        "completion": {"all": [{"signal": "plot_done", "equals": True}], "settle_s": .1},
        "cleanup_inputs": {"run": False, "cmd_reset": False, "cmd_draw": False},
        "path_checks": [{"id": "PATH", "desc": "physical path", "points": [
            {"values": {"x_fb": 20, "y_fb": 20, "z_fb": 10},
             "tolerance": {"x_fb": 2, "y_fb": 2, "z_fb": 1}, "states": {}}]}]}


def test_plan_schema_and_semantic_validator_agree():
    validate_test_plan(plan(), spec())
    js = pytest.importorskip("jsonschema")
    js.validate(plan(), json.loads((ROOT / "schemas/test_plan.schema.json").read_text(encoding="utf-8")))


def test_intermediate_event_is_not_forced_to_remain_true_at_completion():
    s, p = spec(), plan()
    s['acceptance'].append({'id': 'MID', 'type': 'event_delay',
        'from': {'signal': 'cmd_draw', 'edge': 'rising'},
        'to': {'signal': 'pen_down', 'edge': 'rising'}, 'op': '<=', 'value': 20})
    validate_test_plan(p, s)
    assert p['completion']['all'] == [{'signal': 'plot_done', 'equals': True}]


def test_completion_conflict_is_returned_to_plan_generator_before_plc(tmp_path):
    bad = plan()
    bad['path_checks'][0].update(terminal=True)
    bad['path_checks'][0]['points'][-1]['states'] = {'pen_down': False}
    bad['completion']['all'].append({'signal': 'pen_down', 'equals': True})
    good = copy.deepcopy(bad)
    good['completion']['all'][-1]['equals'] = False
    class Client:
        calls = []
        def chat_completions(self, payload, **kwargs):
            self.calls.append(copy.deepcopy(payload))
            return {'choices': [{'message': {'content': json.dumps(bad if len(self.calls) == 1 else good)}}]}
    client = Client()
    assert TestPlanGenerator(client, report=lambda _: None).generate('complete task', spec(), tmp_path) == good
    assert len(client.calls) == 2
    assert '终态条件冲突' in client.calls[-1]['messages'][-1]['content']
    assert not list(tmp_path.glob('*.xml'))


def test_terminal_path_must_hold_at_last_sample_but_checkpoint_need_not():
    check = plan()['path_checks'][0]
    check.update(terminal=True)
    trace = [{'t': 0, 'x_fb': 20, 'y_fb': 20, 'z_fb': 10},
             {'t': 1, 'x_fb': 60, 'y_fb': 20, 'z_fb': 10}]
    assert not evaluate_path_checks({'path_checks': [check]}, trace)[0]['pass']
    check['terminal'] = False
    assert evaluate_path_checks({'path_checks': [check]}, trace)[0]['pass']


def test_terminal_plan_schema_matches_semantic_validation():
    p = plan()
    p['path_checks'][0]['terminal'] = True
    validate_test_plan(p, spec())
    js = pytest.importorskip('jsonschema')
    js.validate(p, json.loads((ROOT / 'schemas/test_plan.schema.json').read_text(encoding='utf-8')))
    p['path_checks'][0]['terminal'] = 1
    with pytest.raises(TestPlanError, match='terminal'):
        validate_test_plan(p, spec())


def test_new_motion_plan_without_terminal_declaration_is_repaired(tmp_path):
    bad, good = plan(), plan()
    good['path_checks'][0]['terminal'] = True
    class Client:
        calls = 0
        def chat_completions(self, *args, **kwargs):
            self.calls += 1
            return {'choices': [{'message': {'content': json.dumps(bad if self.calls == 1 else good)}}]}
    client = Client()
    assert TestPlanGenerator(client, report=lambda _: None).generate('task', spec(), tmp_path) == good
    assert client.calls == 2


@pytest.mark.parametrize("fault", ["output_write", "feedback_write", "unknown_signal", "short_pulse", "slow_sampling",
    "infinite_timeout", "wrong_task", "missing_cleanup", "script", "second_start", "missing_completion",
    "command_as_feedback", "loose_tolerance", "missing_axis", "unknown_property", "bad_initial", "bad_state"])
def test_invalid_or_spoofing_plans_are_rejected(fault):
    p = plan()
    if fault in ("output_write", "feedback_write", "unknown_signal"):
        name = {"output_write": "plot_done", "feedback_write": "x_fb", "unknown_signal": "invented"}[fault]
        p["actions"][1]["values"] = {name: True if fault != "feedback_write" else 10}
    elif fault == "short_pulse":
        p["actions"][2]["duration_ms"] = 1
    elif fault == "slow_sampling":
        p["sampling"]["period_ms"] = 1000
    elif fault == "infinite_timeout":
        p["timeout_s"] = float("inf")
    elif fault == "wrong_task":
        p["task_id"] = "other_task"
    elif fault == "missing_cleanup":
        del p["cleanup_inputs"]["cmd_draw"]
    elif fault == "script":
        p["actions"][1] = {"op": "python", "code": "raise RuntimeError()"}
    elif fault == "second_start":
        p["actions"][1] = {"op": "start_simulation"}
    elif fault == "missing_completion":
        p["completion"]["all"] = [{"signal": "run", "equals": True}]
    elif fault == "command_as_feedback":
        p["path_checks"][0]["points"][0]["values"]["x_cmd"] = 20
    elif fault == "loose_tolerance":
        p["path_checks"][0]["points"][0]["tolerance"]["x_fb"] = 100
    elif fault == "missing_axis":
        del p["path_checks"][0]["points"][0]["values"]["z_fb"]
    elif fault == "unknown_property":
        p["shell"] = "anything"
    elif fault == "bad_initial":
        p["initial_inputs"]["run"] = 1
    elif fault == "bad_state":
        p["path_checks"][0]["points"][0]["states"] = {"missing": True}
    with pytest.raises(TestPlanError):
        validate_test_plan(p, spec())


def renamed():
    s, p = spec(), plan()
    names = {"run": "permit_cycle", "cmd_draw": "request_cycle", "cmd_reset": "reset_cycle", "plot_done": "cycle_complete"}
    text = json.dumps([s, p])
    for old, new in names.items():
        text = text.replace('"%s"' % old, '"%s"' % new)
    return json.loads(text)


class Clock:
    now = 0.0
    def read(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


def fake_collector(clock, complete=True):
    class Client:
        bits = [False] * 32
        closed = False
        writes = []
        def read_coils(self, **kw):
            return SimpleNamespace(bits=list(self.bits), isError=lambda: False)
        def write_coils(self, **kw):
            self.bits = list(kw["values"])
            self.writes.append((clock.now, self.bits[:]))
            return SimpleNamespace(isError=lambda: False)
        def close(self):
            self.closed = True
    client = Client()
    client.writes = []
    def sample(channels):
        result = {c["name"]: client.bits[c["bit"]] if c["type"] == "bool" else 0 for c in channels}
        result.update(x_fb=20, y_fb=20, z_fb=10)
        # 完成信号以通道位号确定，故改名后仍能执行。
        for c in channels:
            if c["type"] == "bool" and c["bit"] == 18:
                result[c["name"]] = complete and clock.now >= .24
        return result
    return SimpleNamespace(cli=client, sample_once=sample)


@pytest.mark.parametrize("rename", [False, True])
def test_executor_uses_plan_signal_names_and_closes_after_cleanup(rename):
    s, p = renamed() if rename else (spec(), plan())
    validate_test_plan(p, s)
    channels, _, _ = build_channels(s, [])
    clock = Clock()
    col = fake_collector(clock)
    started = []
    trace, result = execute_test_plan(p, col, channels, lambda: started.append(True),
        report=lambda s: None, clock=clock.read, sleep=clock.sleep)
    assert started == [True] and result["pass"] and col.cli.closed
    assert all(v is False for v in col.cli.bits)
    high = [t for t, b in col.cli.writes if b[16]]
    low_after = [t for t, b in col.cli.writes if not b[16] and t > min(high)]
    assert min(low_after) - min(high) >= .2
    assert trace and evaluate_path_checks(p, trace)[0]["pass"]


def test_timeout_keeps_trace_and_resets_inputs():
    p = plan()
    clock = Clock()
    col = fake_collector(clock, complete=False)
    channels, _, _ = build_channels(spec(), [])
    trace, result = execute_test_plan(p, col, channels, lambda: None,
        report=lambda s: None, clock=clock.read, sleep=clock.sleep)
    assert trace and not result["pass"] and result["cleanup_ok"]
    assert not any(col.cli.bits) and col.cli.closed


def test_completion_does_not_allow_truncating_required_settle_time():
    p = plan()
    p["timeout_s"] = .29  # 直接测解释器边界：完成后剩余时间不足 settle。
    clock = Clock()
    col = fake_collector(clock)
    channels, _, _ = build_channels(spec(), [])
    trace, result = execute_test_plan(p, col, channels, lambda: None,
        report=lambda s: None, clock=clock.read, sleep=clock.sleep)
    assert trace and not result["pass"] and result["cleanup_ok"]
    assert "PlanTimeout" in result["evidence"]


def test_done_without_motion_cannot_pass_model_path():
    assert not evaluate_path_checks(plan(), [{"t": 0, "plot_done": True, "x_fb": 0, "y_fb": 0, "z_fb": 0}])[0]["pass"]


@pytest.mark.parametrize("fault", [None, "dogleg", "lift", "pen_off", "missing_feedback", "nan"])
def test_segment_checks_actual_straight_motion_and_unbroken_contact(fault):
    p = plan()
    check = p["path_checks"][0]
    check["points"] = [
        {"values": {"x_fb": x, "y_fb": y, "z_fb": 0},
         "tolerance": {"x_fb": 2, "y_fb": 2, "z_fb": 1}, "states": {"pen_down": True}}
        for x, y in [(20, 20), (80, 80)]]
    check["segments"] = {"signals": ["x_fb", "y_fb"], "tolerance": 2, "when": {"pen_down": True}}
    validate_test_plan(p, spec())
    trace = [{"t": i / 10, "x_fb": xy, "y_fb": xy, "z_fb": 0, "pen_down": True}
             for i, xy in enumerate([20, 35, 50, 65, 80])]
    if fault == "dogleg":
        trace[2]["x_fb"] = 80
    elif fault == "lift":
        trace[2]["z_fb"] = 10
    elif fault == "pen_off":
        trace[2]["pen_down"] = False
    elif fault == "missing_feedback":
        del trace[2]["y_fb"]
    elif fault == "nan":
        trace[2]["x_fb"] = float("nan")
    result = evaluate_path_checks(p, trace)[0]
    assert result["pass"] is (fault is None)
    assert len(result["reached"]) == 2  # 端点都到达仍不能掩盖中途错误。


def test_turn_is_split_at_closest_vertex_not_first_entry_into_tolerance_box():
    p = plan()
    c = p["path_checks"][0]
    c["points"] = [{"values": {"x_fb": x, "y_fb": y, "z_fb": 0},
        "tolerance": {"x_fb": 2, "y_fb": 2, "z_fb": 1}, "states": {"pen_down": True}}
        for x, y in [(80,20),(50,80),(20,20)]]
    c["segments"] = {"signals": ["x_fb","y_fb"], "tolerance": 2, "when": {"pen_down": True}}
    validate_test_plan(p, spec())
    xy = [(80,20),(65,50),(52,78),(50,80),(50,80),(49,78),(35,50),(20,20)]
    trace = [dict(t=i/10, x_fb=x, y_fb=y, z_fb=0, pen_down=True) for i,(x,y) in enumerate(xy)]
    result = evaluate_path_checks(p, trace)[0]
    assert result["pass"], result
    assert result["reached"][1]["sample"] == 3
    # 真实偏离入边仍然失败，不能借角点重新分段掩盖错误。
    trace[1]["x_fb"] = 80
    assert not evaluate_path_checks(p, trace)[0]["pass"]


def test_planner_repairs_invalid_metadata_and_preserves_raw_responses(tmp_path):
    p = plan()
    p['path_checks'][0]['terminal'] = True
    broken = copy.deepcopy(p)
    broken["actions"][1]["values"] = {"plot_done": True}
    class Client:
        calls = 0
        def chat_completions(self, payload, **kwargs):
            self.calls += 1
            return {"choices": [{"message": {"content": json.dumps(broken if self.calls == 1 else p)}}]}
    client = Client()
    got = TestPlanGenerator(client, report=lambda s: None).generate("draw", spec(), tmp_path)
    assert got == p and client.calls == 2
    assert (tmp_path / "round_001.error.txt").exists()
    assert (tmp_path / "round_002.response.json").exists()


def test_gui_requires_exact_plan_hash_and_execution_success(tmp_path):
    from runtime.studio_backend import verified_result
    from test_studio_backend import evidence
    evidence(tmp_path)
    p, s = plan(), spec()
    (tmp_path / "requirements").mkdir()
    (tmp_path / "requirements/requirement_spec.json").write_text(json.dumps(s), encoding="utf-8")
    pp = tmp_path / "final/test_plan.json"
    pp.write_text(json.dumps(p), encoding="utf-8")
    digest = hashlib.sha256(pp.read_bytes()).hexdigest()
    mp = tmp_path / "manifest.json"
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    manifest.update(test_plan_source="llm", test_plan_sha256=digest)
    mp.write_text(json.dumps(manifest), encoding="utf-8")
    ep = tmp_path / "final/execution.json"
    ep.write_text(json.dumps({"status": "passed", "cleanup_ok": True}), encoding="utf-8")
    vp = tmp_path / "final/verdict.json"
    v = json.loads(vp.read_text(encoding="utf-8"))
    v["details"].append({"id": "EXECUTION", "pass": True})
    v["sha256"] = {"test_plan": digest}
    vp.write_text(json.dumps(v), encoding="utf-8")
    assert verified_result(tmp_path, 0)
    pp.write_text(pp.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert not verified_result(tmp_path, 0)


def test_orchestrator_freezes_same_plan_across_plc_repair(tmp_path, monkeypatch):
    from agent.orchestrator import Orchestrator
    p, s = plan(), spec()
    for point in s["io_list"]:
        point.pop("trigger", None)  # 新计划不依赖旧入口启动标记。
    seen, paths = [], []
    class Generator:
        client = object()
        def generate(self, spec, feedback=None, test_plan=None):
            seen.append(json.dumps(test_plan, sort_keys=True))
            return {"ok": True, "xml": (ROOT / "src/plc/plotter_square_csp.xml").read_text(encoding="utf-8")}
        def repair(self, previous_xml, spec, feedback, attempts=None, test_plan=None):
            seen.append(json.dumps(test_plan, sort_keys=True))
            return {"ok": True, "xml": previous_xml, "mode": "repair"}
    orch = Orchestrator(runs_root=tmp_path, project_root=ROOT, max_iters=2)
    def acceptance(*args, **kw):
        paths.append(Path(orch._acceptance_context["AGENT_TEST_PLAN"]))
        return ("failed", ["FAIL actual path"]) if len(paths) == 1 else ("ok", ["PASS actual path"])
    monkeypatch.setattr(orch, "acceptance_gate", acceptance)
    result = orch.solve(s, Generator(), acceptance="mujoco", test_plan=p)
    assert result["status"] == "final" and result["iter"] == 2
    assert len(seen) == 2 and seen[0] == seen[1]
    final = Path(result["run_dir"]) / "final/test_plan.json"
    assert paths[0] != paths[1]
    assert all(path.read_bytes() == final.read_bytes() for path in paths)
