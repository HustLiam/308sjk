import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/pipeline"))
from agent.program_review import scan_order_review
from agent.run_metrics import record_solve
from mujoco_verdict import evaluate, signal_matches
from agent.attribution import AttributionEngine
from agent.benchmark import matrix, request_oracle
from trace_query import event_window
import pytest


@pytest.mark.parametrize("name,start,next_state", [("conveyor_phase", 17, 23),
                                                     ("pump_cycle", 106, 205),
                                                     ("cylinder_stage", 4, 9)])
def test_cross_device_scan_overwrite(name, start, next_state):
    source = f"""CASE {name} OF
{start}: IF finished THEN {name} := 900; END_IF;
{next_state}: IF ready THEN {name} := {start}; END_IF;
END_CASE;
IF {name} = {start} THEN
    IF enabled THEN target := 8; END_IF;
    {name} := {next_state};
END_IF;"""
    found = scan_order_review(source)["findings"]
    assert len(found) == 1
    assert found[0]["selector"] == name
    assert found[0]["case_write_lines"] == [3]
    assert found[0]["later_write_lines"] == [7]
    assert found[0]["advisory"] is True


def test_comments_strings_and_other_selectors_are_not_control_flow():
    source = """(* CASE state OF 1: state := 4; END_CASE; *)
text := 'CASE state OF 1: state := 4; END_CASE;';
CASE state OF 2: state := 4; END_CASE;
IF other = 4 THEN state := 5; END_IF;"""
    assert scan_order_review(source)["findings"] == []


def test_last_event_is_explicit_and_never_fabricates_initial_edge():
    ac = {"id": "AC1", "type": "event_delay", "from": {"signal": "valve", "edge": "falling"},
          "to": {"signal": "complete", "edge": "rising"}, "op": "<=", "value": 3}
    trace = [{"t": t, "valve": v, "complete": d} for t, v, d in
             [(0, False, False), (1, True, False), (2, False, False),
              (8, True, False), (9, False, False), (11, False, True)]]
    assert evaluate([ac], trace)[0]["pass"] is False
    ac["from"]["occurrence"] = "last"
    assert evaluate([ac], trace)[0]["pass"] is True
    assert evaluate([ac], [trace[0], trace[-1]])[0]["pass"] is False


def test_metrics_keeps_failed_and_not_executed_checks(tmp_path):
    class Runner:
        runs_root = tmp_path
        @record_solve
        def solve(self, spec, echo=None, test_plan=None):
            root = self.runs_root / spec["task_id"]
            (root / "iter_001").mkdir(parents=True)
            echo("iter_start", {"iter": 1})
            echo("gate_failed", {"gate": "deploy"})
            (root / "iter_002").mkdir()
            echo("iter_start", {"iter": 2})
            (root / "iter_002/verdict.json").write_text(json.dumps({"details": [
                {"id": "AC1", "pass": True}, {"id": "AC2", "pass": True, "observed": False}]}))
            raise RuntimeError("interrupted")
    with pytest.raises(RuntimeError):
        Runner().solve({"task_id": "test", "acceptance": [{"id": "AC1"}, {"id": "AC2"}]})
    data = json.loads((tmp_path / "test/iteration_metrics.json").read_text())
    a, b = data["iterations"]
    assert a["acceptance"] == {"passed": 0, "observed_passed": 0, "total": 2,
                                "evaluated": 0, "not_evaluated": ["AC1", "AC2"]}
    assert b["acceptance"]["passed"] == 2
    assert b["acceptance"]["observed_passed"] == 1
    assert a["duration_s"] >= 0 and b["duration_s"] >= 0


def test_online_failure_is_diagnosed_despite_kb_hit():
    class Memory:
        def match_pitfalls(self, errors):
            return [{"id": "generic", "title": "Timeout", "diagnosis": "Unknown", "fix": "Check timer"}]
        def match_fixes(self, errors):
            return []
    class Client:
        def chat_completions(self, payload):
            prompt = payload["messages"][0]["content"]
            assert "conveyor_phase" in prompt and "Check timer" in prompt
            return {"choices": [{"message": {"content": json.dumps({
                "category": "program", "root_cause": "state overwritten", "repair_hints": ["Check later assignment"],
                "preserve": ["physical feedback"], "evidence": ["line 7"]})}}]}
    result = AttributionEngine(Memory(), Client()).attribute("acceptance", ["FAIL completion"],
                                                               {"program_st": "conveyor_phase := 17;"})
    assert result["llm_diagnosis"]["root_cause"] == "state overwritten"
    assert "line 7" in AttributionEngine.format_feedback(result)


def test_benchmark_matrix_includes_irregular_concave_and_two_scales():
    cases = matrix()
    assert len(cases) == 20
    assert len({case["id"] for case in cases}) == 20
    assert {case["scale"] for case in cases} == {15, 30}
    assert {"irregular", "concave", "ellipse", "rectangle"}.issubset({c["family"] for c in cases})


def test_benchmark_oracle_rejects_claimed_completion_without_physical_route(tmp_path):
    case = matrix()[0]
    trace = [{"t": 0, "x_fb": 50, "y_fb": 50, "z_fb": 0, "pen_down": False},
             {"t": 1, "x_fb": 50, "y_fb": 50, "z_fb": 0, "pen_down": False},
             {"t": 2, "x_fb": 50, "y_fb": 50, "z_fb": 0, "pen_down": False},
             {"t": 3, "x_fb": 50, "y_fb": 50, "z_fb": 10, "pen_down": False}]
    (tmp_path / "trace.json").write_text(json.dumps(trace))
    results = {r["id"]: r for r in request_oracle(case, tmp_path)}
    assert results["REQUEST_FINISH"]["pass"] is True
    assert results["REQUEST_PATH"]["pass"] is False
    assert results["CONTACT_TRUTH"]["pass"] is False


@pytest.mark.parametrize("signal", ["tank_pressure", "cylinder_position", "heater_temperature"])
def test_generic_measured_thresholds_and_tolerances(signal):
    samples = [{"t": 0, signal: 8, "alarm": False}, {"t": 1, signal: 9, "alarm": False}]
    when = {"signal": signal, "op": ">=", "value": 9}
    assert not signal_matches(samples[0], when)
    assert signal_matches(samples[1], when)
    assert signal_matches(samples[1], {"signal": signal, "equals": 10, "tolerance": 1})
    ac = {"id": "AC1", "type": "forbidden_state", "when": when,
          "forbid": {"signal": "alarm", "equals": True}}
    result = evaluate([ac], samples)[0]
    assert result["pass"] and result["observed_samples"] == 1
    samples[1]["alarm"] = True
    assert evaluate([ac], samples)[0]["pass"] is False


def test_physical_equality_precondition_requires_device_tolerance():
    from agent.spec_validator import validate_requirement_spec
    root = Path(__file__).resolve().parents[1]
    spec = json.loads((root / "examples/specs/plotter_square_csp.spec.json").read_text(encoding="utf-8"))
    ac = {"id": "AC99", "desc": "Measured endpoint safety", "type": "forbidden_state",
          "when": {"signal": "z_fb", "equals": 10}, "forbid": {"signal": "pen_down", "equals": True}}
    spec["acceptance"].append(ac)
    assert any("acceptance" in p and "poswin" in p for p in validate_requirement_spec(spec))
    ac["when"]["tolerance"] = 1
    assert validate_requirement_spec(spec) == []
    ac["when"]["tolerance"] = 2
    assert validate_requirement_spec(spec)
    ac["when"] = {"signal": "z_fb", "op": ">=", "value": 9}
    assert validate_requirement_spec(spec) == []


@pytest.mark.parametrize("signal", ["pump_start", "conveyor_cycle", "cylinder_extend"])
def test_task_window_requires_observed_start_edge(signal):
    trace = [{"t": 0, signal: False}, {"t": 1, signal: False},
             {"t": 2, signal: True}, {"t": 3, signal: False}]
    assert event_window(trace, {"signal": signal, "edge": "rising"}) == trace[2:]
    assert event_window(trace[2:], {"signal": signal, "edge": "rising"}) == []


def test_stationary_contact_before_drawing_is_not_a_drawn_line(tmp_path):
    case = matrix()[0]
    trace = [{"t": 0, "cmd_draw": False, "x_fb": 0, "y_fb": 0, "z_fb": 0, "pen_down": True},
             {"t": 1, "cmd_draw": True, "x_fb": 0, "y_fb": 0, "z_fb": 0, "pen_down": True},
             {"t": 2, "cmd_draw": False, "x_fb": 0, "y_fb": 0, "z_fb": 10, "pen_down": False}]
    for point in case["vertices"] + [case["vertices"][0]]:
        trace.append({"t": len(trace), "cmd_draw": False, "x_fb": point[0], "y_fb": point[1],
                      "z_fb": 0, "pen_down": True})
    trace.append({"t": len(trace), "cmd_draw": False, "x_fb": 50, "y_fb": 50, "z_fb": 10, "pen_down": False})
    (tmp_path / "trace.json").write_text(json.dumps(trace))
    assert all(r["pass"] for r in request_oracle(case, tmp_path))
    # Actual XY motion while in contact before reaching the requested stroke is a defect.
    trace[2].update(x_fb=10, z_fb=0, pen_down=True)
    (tmp_path / "trace.json").write_text(json.dumps(trace))
    assert next(r for r in request_oracle(case, tmp_path) if r["id"] == "REQUEST_CORRIDOR")["pass"] is False


def test_truncated_xml_is_not_deployed_and_retry_reserves_output_budget(monkeypatch):
    from agent.client import BigModelClient
    from agent.pipeline import PLCGenerator
    root = Path(__file__).resolve().parents[1]
    spec = json.loads((root / "examples/specs/plotter_square_csp.spec.json").read_text(encoding="utf-8"))
    xml = (root / "src/plc/plotter_square_csp.xml").read_text(encoding="utf-8")
    client = BigModelClient("test-key")
    requests = []
    def response(payload, **kwargs):
        requests.append(payload)
        return {"choices": [{"finish_reason": "length" if len(requests) == 1 else "stop",
                             "message": {"content": xml}}]}
    monkeypatch.setattr(client, "_post_stream", response)
    monkeypatch.setenv("LLM_THINKING", "enabled")
    monkeypatch.setenv("LLM_REASONING_EFFORT", "high")
    generator = PLCGenerator(client=client, no_cards=True, max_rounds=2)
    result = generator.generate(spec)
    assert result["ok"] and result["rounds"] == 2
    assert result["history"][0]["errors"][0].startswith("[output_limit]")
    assert requests[0]["reasoning_effort"] == "high" and requests[1]["reasoning_effort"] == "low"
    assert requests[0]["max_tokens"] == requests[1]["max_tokens"]
