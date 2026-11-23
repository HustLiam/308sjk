import pytest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/pipeline"))
from condition_response import bounded_violations


def test_single_sample_response_is_bounded_by_real_timestamps():
    trace = [{"t": t} for t in (0, .02, .04, .06)]
    result = bounded_violations(trace, [False, True, True, False], .04)
    assert len(result["raw_indices"]) == 2
    assert result["confirmed_indices"] == []
    assert result["max_observed_duration_s"] == pytest.approx(.04)


def test_two_samples_are_not_automatically_allowed_if_clock_gap_is_long():
    trace = [{"t": t} for t in (0, .02, .09)]
    assert bounded_violations(trace, [False, True, False], .04)["confirmed_indices"] == [1]


def test_unresolved_end_cannot_pass_even_within_budget():
    assert bounded_violations([{"t": 0}, {"t": .02}], [False, True], .04)["confirmed_indices"] == [1]


def test_safety_zero_budget_keeps_single_sample_violation():
    assert bounded_violations([{"t": 0}, {"t": .02}], [True, False], 0)["confirmed_indices"] == [0]


def test_separated_transients_do_not_get_concatenated_or_deleted():
    trace = [{"t": t} for t in (0, .02, .04, .06, .08)]
    result = bounded_violations(trace, [False, True, False, True, False], .04)
    assert len(result["intervals"]) == 2 and len(result["raw_indices"]) == 2


def test_invalid_timestamps_fail():
    with pytest.raises(ValueError):
        bounded_violations([{"t": 1}, {"t": 0}], [False, False], .04)


@pytest.mark.parametrize("sensor,state", [("pressure", "pressurized"), ("position", "extended"), ("temperature", "heated")])
def test_generic_acceptance_preserves_raw_mismatch_and_uses_time(sensor, state):
    from mujoco_verdict import evaluate
    ac = {"id": "AC1", "type": "forbidden_state", "when": {"signal": sensor, "op": ">", "value": 1},
          "forbid": {"signal": state, "equals": False}, "purpose": "derived_state_consistency", "response_s": .04,
          "response_source": sensor}
    trace = [{"t": 0, sensor: 0, state: False}, {"t": .02, sensor: 2, state: False},
             {"t": .04, sensor: 2, state: True}]
    result = evaluate([ac], trace)[0]
    assert result["pass"] and result["raw_violation_samples"] == 1
    assert result["violation_samples"] == 0 and result["observed"]
    trace[-1]["t"] = .09
    assert evaluate([ac], trace)[0]["pass"] is False
    assert evaluate([ac], trace[:-1])[0]["pass"] is False
    ac.pop("purpose")
    ac.pop("response_s")
    assert evaluate([ac], trace)[0]["pass"] is False


def test_safety_budget_is_rejected_by_spec_validation():
    import json
    from agent.spec_validator import validate_requirement_spec
    root = Path(__file__).resolve().parents[1]
    spec = json.loads((root / "examples/specs/plotter_square_csp.spec.json").read_text(encoding="utf-8"))
    spec["acceptance"].append({"id": "AC99", "desc": "Enable safety remains strict", "type": "forbidden_state",
                               "when": {"signal": "run", "equals": False},
                               "forbid": {"signal": "any_moving", "equals": True},
                               "purpose": "safety", "response_s": .04})
    assert any("response" in p or "安全" in p for p in validate_requirement_spec(spec))


def test_stable_sensor_does_not_grant_a_new_response_window_to_output_glitches():
    trace = [{"t": t} for t in (0, .02, 2, 2.02)]
    result = bounded_violations(trace, [False, False, True, False], .04, [False, True, True, True])
    assert result["confirmed_indices"] == [2]
    assert result["intervals"][0]["response_delay_s"] == pytest.approx(2)


def test_response_is_measured_from_sensor_change_not_first_wrong_output():
    trace = [{"t": t} for t in (0, .02, .04, .07)]
    result = bounded_violations(trace, [False, False, True, False], .04, [False, True, True, True])
    assert result["confirmed_indices"] == [2]
    assert result["intervals"][0]["duration_s"] == pytest.approx(.03)
    assert result["intervals"][0]["response_delay_s"] == pytest.approx(.05)


def test_sampler_reads_status_before_numeric_feedback_without_dropping_channels():
    from types import SimpleNamespace
    from mujoco_verdict import Collector
    calls = []
    class Client:
        def read_coils(self, **kwargs):
            calls.append("status")
            return SimpleNamespace(bits=[False] * 16)
        def read_holding_registers(self, **kwargs):
            calls.append("feedback")
            return SimpleNamespace(registers=[9])
    collector = Collector.__new__(Collector)
    collector.cli = Client()
    sample = collector.sample_once([{"name": "pressure", "type": "analog", "addr": 0, "dir": "input"},
                                    {"name": "pressurized", "type": "bool", "bit": 0, "dir": "output"}])
    assert calls == ["status", "feedback"]
    assert sample == {"pressure": 9, "pressurized": False}
