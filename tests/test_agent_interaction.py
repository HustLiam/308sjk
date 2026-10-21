"""主动澄清、预览与回放的行为测试；不调用模型或在线 PLC。"""

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from agent.clarification import (ClarificationRequired, analyze_request,
                                 extract_explicit_geometry, prepare_request)
from agent.evidence import save_acceptance_evidence
from agent.replay import collect_replay, write_replay
from agent.trajectory import goal_text, plan_circle, plan_square
from agent.visualization import trajectory_segments, write_preview


def test_missing_geometry_is_asked_before_generation():
    asked = []
    answers = {"center": "40,45", "size": "30"}

    def ask(q):
        asked.append(q["key"])
        return answers[q["key"]]

    prepared = prepare_request("画一个正方形", ask=ask)
    assert asked == ["center", "size"]
    assert prepared["trajectory"]["params"] == {"center": [40, 45], "size": 30}
    assert "40,45" in prepared["request_text"]
    assert len(prepared["clarifications"]) == 2


def test_explicit_parameters_are_not_asked_again():
    prepared = prepare_request("画正方形，中心(40,45)，边长30")
    assert prepared["sources"] == {"center": "explicit", "size": "explicit"}


def test_scripted_defaults_are_explicitly_recorded():
    prepared = prepare_request("画圆", accept_defaults=True)
    assert prepared["trajectory"]["params"]["radius"] == 25
    assert set(prepared["sources"].values()) == {"default"}


def test_scripted_unknown_shape_stops_for_clarification():
    with pytest.raises(ClarificationRequired) as exc:
        prepare_request("画一个图形", accept_defaults=True)
    assert exc.value.questions[0]["key"] == "shape"


def test_answers_resolve_shape_without_interaction():
    prepared = prepare_request("画图", answers={"shape": "圆", "center": [40, 40],
                               "radius": 12, "segments": 16})
    assert prepared["trajectory"]["shape"] == "circle"


def test_multiple_shapes_are_clarified_instead_of_silently_dropped():
    info = analyze_request("先画正方形再画圆")
    assert info["shape"] == "unknown" and info["questions"][0]["key"] == "shape"


def test_vague_process_asks_for_actions():
    with pytest.raises(ClarificationRequired) as exc:
        prepare_request("控制设备", accept_defaults=True)
    assert exc.value.questions[0]["key"] == "process"


def test_plotter_control_request_does_not_require_a_shape():
    assert analyze_request("绘图仪急停后保持停止，复位后等待启动命令")["questions"] == []


def test_physical_lengths_are_not_treated_as_percent_coordinates():
    model = {"kinematics": {"axes": [{"axis": "x_axis", "unit": "%", "stroke": [0, 100]}]}}
    with pytest.raises(ClarificationRequired) as exc:
        prepare_request("画一个边长30mm的正方形", model, accept_defaults=True)
    assert exc.value.questions[0]["key"] == "coordinate_values"
    prepared = prepare_request("画一个边长30mm的正方形", model,
                               answers={"coordinate_values": {"size": 3}}, accept_defaults=True)
    assert prepared["trajectory"]["params"]["size"] == 3


def test_fractional_geometry_is_preserved_in_prompt():
    prepared = prepare_request("画正方形，中心(40.5,45.5)，边长31.5")
    text = goal_text(prepared["trajectory"])
    assert "31.5" in text and "24.75" in text and "56.25" in text


def test_diameter_is_converted_to_radius():
    assert extract_explicit_geometry("画圆，直径30")["params"]["radius"] == 15


@pytest.mark.parametrize("params", [{"size": -1}, {"size": 0}, {"size": float("nan")},
                                   {"size": True}, {"center": [float("inf"), 50]}])
def test_bad_square_geometry_is_rejected(params):
    with pytest.raises(ValueError):
        plan_square(**params)


@pytest.mark.parametrize("params", [{"radius": -2}, {"segments": 0}, {"segments": 12.5},
                                   {"segments": 361}, {"segments": True}])
def test_bad_circle_geometry_is_rejected(params):
    with pytest.raises(ValueError):
        plan_circle(**params)


def test_aml_stroke_checked_before_generation():
    model = {"kinematics": {"axes": [{"axis": "x_axis", "stroke": [0, 60]}]}}
    with pytest.raises(ClarificationRequired, match="AML 行程"):
        prepare_request("画正方形，中心(50,50)，边长60", model)


def test_invalid_interactive_answer_is_reasked():
    replies = iter(["bad", "40,40", "-1", "30"])
    prepared = prepare_request("画正方形", ask=lambda q: next(replies))
    assert prepared["trajectory"]["params"] == {"center": [40, 40], "size": 30}


def test_circle_preview_closes_while_pen_is_down():
    segments = trajectory_segments(plan_circle())
    drawn = [s for s in segments if s["draw"]]
    assert drawn[-1]["end"] == drawn[0]["start"]
    assert len(drawn) == 25  # includes the stationary starting waypoint, then 24 edges


def test_preview_is_self_contained_and_escapes_user_text(tmp_path):
    trajectory = plan_square()
    trajectory["steps"][0]["note"] = "</script><script>alert(1)</script>"
    path = write_preview(trajectory, tmp_path / "preview.html")
    html = path.read_text(encoding="utf-8")
    assert "https://" not in html
    assert "</script><script>alert(1)</script>" not in html
    assert "\\u003c/script>" in html
    assert 'id="cursor"' in html and 'id="path"' in html


def make_run(tmp_path, gate=None):
    run = tmp_path / "test_run"
    iteration = run / "iter_001"
    iteration.mkdir(parents=True)
    (run / "request.json").write_text(json.dumps({"task_id": "test_run"}), encoding="utf-8")
    (iteration / "gate.json").write_text(json.dumps(gate or {
        "gate": "acceptance", "ok": False, "errors": ["FAIL Z 轴不动"]}), encoding="utf-8")
    return run, iteration


def test_legacy_failure_without_samples_does_not_invent_trajectory(tmp_path):
    run, _ = make_run(tmp_path)
    report = collect_replay(run)
    assert report["iterations"][0]["samples"] == []
    assert "没有时序采样" in report["iterations"][0]["sample_note"]
    assert report["trajectory"] is None
    assert write_replay(run).is_file()


def test_record_and_replay_actual_trace_verdict_and_diff(tmp_path):
    run, first = make_run(tmp_path)
    trace = [{"t": 0, "x_fb": 20, "y_fb": 20, "pen_down": True},
             {"t": 1, "x_fb": 30, "y_fb": 20, "pen_down": True}]
    results = [{"id": "AC1", "pass": False, "evidence": "plot_done 未置位"}]
    save_acceptance_evidence(trace, results, first)
    (first / "plc.st").write_text("state := 0;", encoding="utf-8")
    second = run / "iter_002"
    second.mkdir()
    (second / "plc.st").write_text("state := 1;", encoding="utf-8")
    (second / "gate.json").write_text('{"ok":true,"gate":"all"}', encoding="utf-8")
    report = collect_replay(run)
    assert report["iterations"][0]["samples"] == trace
    assert report["iterations"][0]["verdict"] == results
    assert "+state := 1;" in report["iterations"][1]["diff"]
    assert "未完成全量在线验证" in report["iterations"][1]["status"]


def test_skipped_online_gate_is_not_presented_as_online_success(tmp_path):
    run, _ = make_run(tmp_path, {"ok": True, "gate": "all", "gates": {
        "acceptance": {"state": "skipped", "detail": "运行时不在线"}}})
    assert "跳过：acceptance" in collect_replay(run)["iterations"][0]["status"]


def test_diagnostic_timeline_can_be_replayed(tmp_path):
    run, _ = make_run(tmp_path, {"ok": False, "errors": [
        "[diag t=0.5s] step=2 exe=1", "[diag t=1.0s] step=2 exe=1"]})
    result = collect_replay(run)["iterations"][0]
    assert result["samples"] == [{"t": 0.5, "step": 2., "exe": 1.},
                                  {"t": 1., "step": 2., "exe": 1.}]
    assert "探针" in result["sample_note"]


def test_broken_trace_is_explained_and_other_evidence_remains(tmp_path):
    run, iteration = make_run(tmp_path)
    (iteration / "trace.json").write_text("{bad", encoding="utf-8")
    data = collect_replay(run)
    assert any("无法读取" in n for n in data["notes"])
    assert data["iterations"][0]["errors"] == ["FAIL Z 轴不动"]


def test_replay_specific_iteration_and_csv(tmp_path):
    run, iteration = make_run(tmp_path)
    (iteration / "trace.csv").write_text("t,x_fb,pen_down\n0.0,20,true\n", encoding="utf-8")
    data = collect_replay(iteration)
    assert data["selected_iteration"] == 1
    assert data["iterations"][0]["samples"] == [{"t": 0., "x_fb": 20, "pen_down": True}]


def test_chat_preview_confirm_never_reads_input_or_calls_llm(tmp_path, monkeypatch):
    from agent import chat
    monkeypatch.setattr(chat, "get_api_key", lambda: "")
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("脚本化模式不得等待输入"))
    result = chat.main(["--request", "画正方形", "--confirm", "--preview-only",
                        "--runs-root", str(tmp_path), "--task-id", "preview_test"])
    assert result == 0
    assert (tmp_path / "preview_test" / "trajectory-preview.html").is_file()
    assert not (tmp_path / "preview_test" / "iter_001").exists()


def test_chat_unknown_shape_returns_questions_without_generation(tmp_path, monkeypatch, capsys):
    from agent import chat
    monkeypatch.setattr(chat, "get_api_key", lambda: "")
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("不应读取输入"))
    result = chat.main(["--request", "画一个图形", "--confirm", "--preview-only", "--runs-root", str(tmp_path)])
    assert result == 2
    assert "needs_clarification" in capsys.readouterr().out
    assert not list(tmp_path.glob("*/iter_001"))


def test_failure_loop_saves_subprocess_log_trace_and_replay(tmp_path, monkeypatch):
    from agent.orchestrator import Orchestrator
    from agent.pipeline import PLCGenerator
    pipeline = tmp_path / "src" / "pipeline"
    pipeline.mkdir(parents=True)
    script = pipeline / "scenario_capture.py"
    script.write_text(
        "import os,json\nfrom pathlib import Path\n"
        "p=Path(os.environ['AGENT_EVIDENCE_DIR'])\n"
        "(p/'trace.json').write_text(json.dumps([{'t':0,'x_fb':20,'y_fb':20}]))\n"
        "print('[1] initial')\nprint('  FAIL actuator stuck')\nraise SystemExit(1)\n", encoding="utf-8")
    spec = json.loads((REPO / "examples/specs/motion3axis.spec.json").read_text(encoding="utf-8"))
    orch = Orchestrator(runs_root=tmp_path, project_root=tmp_path, max_iters=1)
    monkeypatch.setattr(orch, "_runtime_probe", lambda *a: [])
    result = orch.solve(spec, PLCGenerator(client=None, seed_xml=REPO / "src/plc/motion3axis.xml"),
                        acceptance="capture")
    run = Path(result["run_dir"])
    assert result["status"] == "best_effort"
    assert "actuator stuck" in (run / "iter_001/acceptance.log").read_text(encoding="utf-8")
    assert (run / "replay.html").is_file()
    assert collect_replay(run)["iterations"][0]["samples"][0]["x_fb"] == 20
    assert orch._evidence_dir is None


def test_success_loop_also_generates_replay(tmp_path):
    from agent.orchestrator import Orchestrator
    from agent.pipeline import PLCGenerator
    spec = json.loads((REPO / "examples/specs/motion3axis.spec.json").read_text(encoding="utf-8"))
    result = Orchestrator(runs_root=tmp_path).solve(
        spec, PLCGenerator(client=None, seed_xml=REPO / "src/plc/motion3axis.xml"))
    assert result["status"] == "final"
    assert (Path(result["run_dir"]) / "replay.html").is_file()
