"""严格闭环回归：完整 XML 来源、迭代绑定、部署跳过、轨迹伪通过。"""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src/pipeline"))

from agent.pipeline import PLCGenerator
from agent.orchestrator import Orchestrator
from agent.scene_gen import SceneSpecGenerator, build_scene_artifacts
from agent.trajectory import plan_square
from mujoco_verdict import evaluate
from scenario_mujoco import verify_trajectory
from mujoco_verdict import validate_collection_spec, AcceptanceConfigurationError
from agent.closed_loop import bind_square_acceptance_protocol
from xml2st import convert
from test_xml2st import GOOD_MINIMAL


def csp_spec():
    return json.loads((ROOT / "examples/specs/plotter_square_csp.spec.json").read_text(encoding="utf-8"))


def test_axes_do_not_silently_select_hybrid(tmp_path, monkeypatch):
    xml = (ROOT / "src/plc/plotter_square_csp.xml").read_text(encoding="utf-8")
    class Client:
        def chat_completions(self, payload, **kwargs):
            assert "PLCopen XML 工程" in payload["messages"][0]["content"]
            assert "只输出**一个** ```xml 代码块" in payload["messages"][0]["content"]
            assert "不要输出任何 XML" not in payload["messages"][0]["content"]
            return {"choices": [{"message": {"content": "```xml\n" + xml + "\n```"}}]}
    gen = PLCGenerator(Client(), no_cards=True)
    gen.evidence_dir = tmp_path / "generation"
    result = gen.generate(csp_spec())
    assert result["ok"], result["errors"]
    assert "<project" in result["xml"]
    assert (gen.evidence_dir / "call_001.response.json").exists()


def test_address_child_is_rejected_with_actionable_error(tmp_path):
    xml = GOOD_MINIMAL.replace('name="y" address="%QW2"', 'name="y"').replace(
        '<variable name="y"><type><INT /></type></variable>',
        '<variable name="y"><type><INT /></type><address>%QW2</address></variable>')
    p = tmp_path / "bad.xml"
    p.write_text(xml, encoding="utf-8")
    ok, _, problems = convert(p)
    assert not ok
    assert any("address 属性" in s for s in problems)


def test_scene_mapping_uses_spec_addresses_not_entry_order(tmp_path):
    spec = csp_spec()
    spec["io_list"] = list(reversed(spec["io_list"]))
    scene = SceneSpecGenerator().generate(spec)["scene"]
    mapped = build_scene_artifacts(scene, spec, tmp_path)
    expected = {p["name"]: p["address"] for p in spec["io_list"]}
    assert all(e["modbus"]["plc_addr"] == expected[e["plc_var"]] for e in mapped)
    assert (tmp_path / "scene.xml").is_file()


def test_strict_deploy_skip_cannot_freeze_final(tmp_path, monkeypatch):
    spec = csp_spec()
    xml = (ROOT / "src/plc/plotter_square_csp.xml").read_text(encoding="utf-8")
    class Generator:
        client = None
        generation_mode = "full_xml"
        def generate(self, *args, **kwargs):
            return {"ok": True, "xml": xml}
    orch = Orchestrator(runs_root=tmp_path, max_iters=1)
    monkeypatch.setattr(orch, "deploy_gate", lambda p: ("skipped", "offline"))
    monkeypatch.setattr(orch, "status_probe", lambda: None)
    result = orch.solve(spec, Generator(), deploy=True, acceptance="mujoco",
                        scene_generator=SceneSpecGenerator(), require_online=True)
    assert result["status"] == "best_effort"
    assert not (tmp_path / spec["task_id"] / "final").exists()


def test_strict_run_rejects_seed_and_partial_gates(tmp_path):
    orch = Orchestrator(runs_root=tmp_path)
    with pytest.raises(ValueError, match="完整闭环"):
        orch.solve(csp_spec(), object(), require_online=True)
    seed = PLCGenerator(None, seed_xml=ROOT / "src/plc/plotter_square_csp.xml")
    with pytest.raises(ValueError, match="种子"):
        orch.solve(csp_spec(), seed, deploy=True, acceptance="mujoco",
                   scene_generator=SceneSpecGenerator(), require_online=True)


def test_done_lamp_alone_cannot_pass_square():
    result = verify_trajectory([{"t": 0, "x_fb": 50, "y_fb": 50, "z_fb": 10,
                                 "plot_done": True, "pen_down": False}], plan_square(), csp_spec())
    assert result["pass"] is False


def test_missing_trace_or_signal_never_passes():
    ac = {"id": "AC1", "type": "forbidden_state", "when": {"signal": "a", "equals": True},
          "forbid": {"signal": "b", "equals": True}}
    assert not evaluate([ac], [])[0]["pass"]
    assert not evaluate([ac], [{"t": 0, "a": False}])[0]["pass"]
    assert not evaluate([{"id": "AC2", "type": "unknown"}], [{"t": 0}])[0]["pass"]


def test_falling_edge_is_evaluated_as_falling():
    ac = {"id": "AC1", "type": "event_delay", "from": {"signal": "stop", "edge": "rising"},
          "to": {"signal": "moving", "edge": "falling"}, "op": "<=", "value": 0.5}
    trace = [{"t": 0, "stop": False, "moving": True},
             {"t": 1, "stop": True, "moving": True},
             {"t": 1.2, "stop": True, "moving": False}]
    assert evaluate([ac], trace)[0]["pass"]


def test_initial_low_is_not_a_falling_edge():
    ac = {"id": "AC1", "type": "event_delay", "from": {"signal": "contact", "edge": "falling"},
          "to": {"signal": "done", "edge": "rising"}, "op": "<=", "value": 5}
    trace = [{'t': 0, 'contact': False, 'done': False},
             {'t': 4, 'contact': True, 'done': False},
             {'t': 13, 'contact': False, 'done': False},
             {'t': 15, 'contact': False, 'done': True}]
    result = evaluate([ac], trace)[0]
    assert result['pass'] and 'contact↓@13s' in result['evidence']


def test_initial_level_does_not_replace_an_unobserved_event():
    for edge, level in [('falling', False), ('rising', True)]:
        ac = {"id": "AC1", "type": "event_delay", "from": {"signal": "contact", "edge": edge},
              "to": {"signal": "done", "edge": "rising"}, "op": "<=", "value": 5}
        trace = [{'t': 0, 'contact': level, 'done': False},
                 {'t': 1, 'contact': level, 'done': True}]
        result = evaluate([ac], trace)[0]
        assert not result['pass'] and '未观测到真实' in result['evidence']


def test_real_long_delay_still_fails():
    ac = {"id": "AC1", "type": "event_delay", "from": {"signal": "contact", "edge": "falling"},
          "to": {"signal": "done", "edge": "rising"}, "op": "<=", "value": 5}
    trace = [{'t': 0, 'contact': True, 'done': False},
             {'t': 1, 'contact': False, 'done': False},
             {'t': 8, 'contact': False, 'done': True}]
    assert not evaluate([ac], trace)[0]['pass']


def test_square_protocol_binds_missing_model_triggers_without_changing_code():
    spec = csp_spec()
    for point in spec["io_list"]:
        point.pop("trigger", None)
    with pytest.raises(AcceptanceConfigurationError, match="trigger=true"):
        validate_collection_spec(spec)
    protocol = bind_square_acceptance_protocol(spec)
    assert {p["name"] for p in spec["io_list"] if p.get("trigger")} == {"run", "cmd_draw"}
    assert all(b["model_trigger"] is None for b in protocol["bindings"])
    assert next(p for p in spec["io_list"] if p["name"] == "cmd_reset")["trigger"] is False
    validate_collection_spec(spec)


@pytest.mark.parametrize("fault", ["missing_done", "analog_done", "input_done", "falling_done", "invalid_trigger_address"])
def test_preflight_rejects_unusable_collection_contract(fault):
    spec = csp_spec()
    event = next(a for a in spec["acceptance"] if a["type"] == "event_delay")
    if fault == "missing_done":
        spec["acceptance"].remove(event)
    elif fault == "analog_done":
        event["to"]["signal"] = "x_fb"
    elif fault == "input_done":
        event["to"]["signal"] = "run"
    elif fault == "falling_done":
        event["to"]["edge"] = "falling"
    else:
        next(p for p in spec["io_list"] if p.get("trigger"))["address"] = "%QW10"
    with pytest.raises(AcceptanceConfigurationError):
        validate_collection_spec(spec)


def test_missing_stimuli_stop_before_generation_or_deploy(tmp_path, monkeypatch):
    spec = csp_spec()
    for point in spec["io_list"]:
        point.pop("trigger", None)
    class Generator:
        generation_mode = "full_xml"
        def generate(self, *args, **kwargs):
            pytest.fail("配置错误不应调用生成器")
    orch = Orchestrator(runs_root=tmp_path, max_iters=6)
    monkeypatch.setattr(orch, "deploy_gate", lambda *a: pytest.fail("配置错误不应部署"))
    with pytest.raises(AcceptanceConfigurationError):
        orch.solve(spec, Generator(), deploy=True, acceptance="mujoco",
                   scene_generator=SceneSpecGenerator(), require_online=True)
    assert not list(tmp_path.glob("**/iter_*"))


def test_collection_config_failure_does_not_repair_xml_or_learn_wrong_lesson(tmp_path, monkeypatch):
    spec = csp_spec()
    class Generator:
        generation_mode = "full_xml"
        client = None
        calls = 0
        def generate(self, *args, **kwargs):
            self.calls += 1
            return {"ok": True, "xml": (ROOT / "src/plc/plotter_square_csp.xml").read_text(encoding="utf-8")}
    gen = Generator()
    orch = Orchestrator(runs_root=tmp_path, max_iters=6)
    monkeypatch.setattr(orch, "deploy_gate", lambda *a: ("ok", {}))
    monkeypatch.setattr(orch, "status_probe", lambda: None)
    monkeypatch.setattr(orch, "acceptance_gate", lambda *a, **kw: ("configuration_error", ["invalid stimulus"]))
    monkeypatch.setattr(orch, "_fail", lambda *a, **kw: pytest.fail("不应归因给 PLC"))
    monkeypatch.setattr(orch, "_learn_from_outcome", lambda *a, **kw: pytest.fail("不应学习 PLC 修复经验"))
    result = orch.solve(spec, gen, deploy=True, acceptance="mujoco",
                        scene_generator=SceneSpecGenerator(), require_online=True)
    assert result["status"] == "configuration_error" and gen.calls == 1
    assert not (tmp_path / spec["task_id"] / "final").exists()
