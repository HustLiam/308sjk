"""离线验证最佳版选择、退化拒绝与余额阻断；禁止真实模型调用。"""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from agent.progress import ProgressTracker, compare, profile
from agent.orchestrator import Orchestrator
from agent.client import BigModelClient
from agent.closed_loop import record_api_blocked
from test_test_plan import spec, plan


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def write_case(run, i, completed=True, reached=1, bad=False, tag="base"):
    folder = run / ("iter_%03d" % i)
    folder.mkdir(parents=True)
    # XML均来自既有测试工件，仅作为离线候选；不会部署运行。
    xml = (ROOT / "src/plc/plotter_square_csp.xml").read_text(encoding="utf-8")
    (folder / "plcopen.xml").write_text(xml.replace("<project ", "<!-- %s -->\n<project " % tag, 1), encoding="utf-8")
    p, s = plan(), spec()
    sample = {v["name"]: False if v["type"] == "BOOL" else 0 for v in s["io_list"]}
    sample.update(t=0, plot_done=completed, pen_down=bad)
    dump(folder / "trace.json", [sample])
    dump(folder / "execution.json", {"status": "passed" if completed else "failed", "cleanup_ok": True})
    details = [{"id": a["id"], "pass": (a["id"] not in {"AC1", "AC2"} or completed)
                and (a["id"] != "AC3" or not bad), "evidence": a["id"]} for a in s["acceptance"]]
    details += [{"id": "EXECUTION", "pass": completed, "evidence": "execution"},
                {"id": "PATH", "pass": reached == 1, "reached": [0] * reached, "evidence": "path"}]
    dump(folder / "verdict.json", {"verdict": "FAIL", "details": details})
    return folder


def test_completed_base_is_preserved_when_candidate_no_longer_moves(tmp_path):
    tracker = ProgressTracker(tmp_path, spec(), plan(), ROOT)
    first = write_case(tmp_path, 1, bad=True)
    second = write_case(tmp_path, 2, completed=False, reached=0)
    assert tracker.consider(first, 1, "acceptance")["decision"] == "promote"
    rejection = tracker.consider(second, 2, "acceptance")
    assert rejection["decision"] == "reject"
    assert any("不能完成" in r for r in rejection["reasons"])
    assert any("路点进度" in r for r in rejection["reasons"])
    assert tracker.best["iter"] == 1
    assert (tmp_path / "best/plcopen.xml").read_bytes() == (first / "plcopen.xml").read_bytes()


def test_vacuous_forbidden_pass_is_not_a_verified_capability(tmp_path):
    folder = write_case(tmp_path, 1, completed=False, reached=0)
    result = profile(folder, 1, "acceptance", spec(), plan(), ROOT)
    assert "AC3" not in result["observed"] and "AC3" not in result["verified_passes"]


def test_changed_compiler_is_not_comparable_but_install_status_is(tmp_path):
    first = write_case(tmp_path, 1)
    second = write_case(tmp_path, 2)
    compiler = {'image_id': 'image', 'optimizer': 'bypassed', 'compile_script_sha256': 'same',
                'status': 'configured'}
    dump(first / 'compiler.json', compiler)
    compiler['status'] = 'verified'
    dump(second / 'compiler.json', compiler)
    a = profile(first, 1, 'acceptance', spec(), plan(), ROOT)
    b = profile(second, 2, 'acceptance', spec(), plan(), ROOT)
    assert compare(a, b)['decision'] == 'retain'
    compiler['compile_script_sha256'] = 'different'
    dump(second / 'compiler.json', compiler)
    b = profile(second, 2, 'acceptance', spec(), plan(), ROOT)
    assert compare(a, b)['decision'] == 'incomparable'


def test_same_quality_keeps_previous_version_and_stops_after_two_attempts(tmp_path):
    tracker = ProgressTracker(tmp_path, spec(), plan(), ROOT)
    for i in range(1, 4):
        d = tracker.consider(write_case(tmp_path, i, bad=True, tag=str(i)), i, "acceptance")
    assert d["decision"] == "retain" and tracker.best["iter"] == 1
    assert tracker.stop_reason == "no_progress"


def test_fixing_failed_criterion_promotes_without_losing_capability(tmp_path):
    tracker = ProgressTracker(tmp_path, spec(), plan(), ROOT)
    tracker.consider(write_case(tmp_path, 1, bad=True), 1, "acceptance")
    assert tracker.consider(write_case(tmp_path, 2), 2, "acceptance")["decision"] == "promote"
    assert tracker.best["iter"] == 2
    assert "AC3" in tracker.contract()["preserve_verified_passes"]


def test_fewer_bad_samples_do_not_help_when_violation_ratio_increases(tmp_path):
    a = profile(write_case(tmp_path, 1, bad=True), 1, "acceptance", spec(), plan(), ROOT)
    b = copy.deepcopy(a)
    a["violation_rates"]["AC3"] = .1
    b["violation_rates"]["AC3"] = .2
    assert compare(a, b)["decision"] == "reject"


def test_changed_plan_is_not_comparable(tmp_path):
    a = profile(write_case(tmp_path, 1), 1, "acceptance", spec(), plan(), ROOT)
    b = copy.deepcopy(a)
    b["context"]["plan"] = "changed"
    assert compare(a, b)["decision"] == "incomparable"


def test_padding_safe_wait_to_dilute_violation_ratio_does_not_promote(tmp_path):
    a = profile(write_case(tmp_path, 1, bad=True), 1, "acceptance", spec(), plan(), ROOT)
    b = copy.deepcopy(a)
    b["violation_rates"]["AC3"] = .01  # 违规数不变，只增加了无违规观察时间。
    assert compare(a, b)["decision"] == "retain"


def test_failed_geometry_cannot_regress_to_buy_improvement_elsewhere(tmp_path):
    a = profile(write_case(tmp_path, 1, bad=True), 1, "acceptance", spec(), plan(), ROOT)
    a["verified_passes"].remove("PATH")
    a["paths"]["PATH"]["deviation"] = 3
    b = copy.deepcopy(a)
    b["paths"]["PATH"]["deviation"] = 4
    assert compare(a, b)["decision"] == "reject"


def test_strict_mode_rejects_missing_online_evidence(tmp_path):
    folder = write_case(tmp_path, 1)
    (folder / "verdict.json").unlink()
    tracker = ProgressTracker(tmp_path, spec(), plan(), ROOT, strict=True)
    assert tracker.consider(folder, 1, "all")["decision"] == "incomparable"
    assert tracker.best is None and tracker.stop_reason == "evidence_changed"


def test_int_forbidden_value_does_not_turn_into_boolean_one():
    from src.pipeline.mujoco_verdict import evaluate
    ac = {"id": "AC", "type": "forbidden_state", "when": {"signal": "pen", "equals": True},
          "forbid": {"signal": "height", "equals": 5}}
    assert not evaluate([ac], [{"t": 0, "pen": True, "height": 5}])[0]["pass"]
    assert evaluate([ac], [{"t": 0, "pen": True, "height": 1}])[0]["pass"]
    r = evaluate([ac], [{"t": 0, "pen": False, "height": 5}])[0]
    assert not r["observed"] and r["observed_samples"] == 0


@pytest.mark.parametrize("stream", [False, True])
def test_quota_failure_stops_subsequent_network_calls(stream):
    client = BigModelClient("unused-test-key")
    calls = []
    client.session.post = lambda *a, **k: calls.append(1) or SimpleNamespace(status_code=402, text="Insufficient Balance")
    for _ in range(2):
        with pytest.raises(RuntimeError, match="HTTP 402"):
            client.chat_completions({}, prefer_stream=stream)
    assert len(calls) == 1


def test_quota_failure_before_planning_has_manifest(tmp_path):
    assert record_api_blocked(RuntimeError("HTTP 402: Insufficient Balance"), tmp_path,
                              SimpleNamespace(calls=1), "requirement")
    assert json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))["status"] == "api_blocked"


def test_orchestrator_repairs_best_instead_of_last_rejected_candidate(tmp_path, monkeypatch):
    bases = []
    class Generator:
        client = object()
        def generate(self, s, **kw):
            return {"xml": (write_case(tmp_path / "fixtures", 1, bad=True) / "plcopen.xml").read_text(encoding="utf-8")}
        def repair(self, xml, s, feedback, **kw):
            bases.append(xml)
            assert "baseline_iter" in feedback
            return {"xml": xml.replace("<!-- base -->", "<!-- regressed -->")}
    orch = Orchestrator(runs_root=tmp_path, project_root=ROOT, max_iters=5)
    monkeypatch.setattr(orch, "_learn_from_outcome", lambda *a, **kw: None)
    rounds = []
    def acceptance(*a, **kw):
        rounds.append(1)
        folder = Path(orch._acceptance_context["AGENT_EVIDENCE_DIR"])
        evidence = write_case(tmp_path / "evidence", len(rounds), completed=len(rounds)==1,
                              reached=1 if len(rounds)==1 else 0, bad=len(rounds)==1)
        for name in ("trace.json", "execution.json", "verdict.json"):
            (folder / name).write_bytes((evidence / name).read_bytes())
        return "failed", ["FAIL physical acceptance"]
    monkeypatch.setattr(orch, "acceptance_gate", acceptance)
    result = orch.solve(spec(), Generator(), acceptance="mujoco", test_plan=plan())
    assert result["status"] == "best_effort" and result["iter"] == 1
    assert len(bases) == 2 and bases[0] == bases[1] and "regressed" not in bases[1]
    root = Path(result["run_dir"])
    assert json.loads((root / "best.json").read_text(encoding="utf-8"))["iter"] == 1
    assert json.loads((root / "iter_003/repair_contract.json").read_text(encoding="utf-8"))["baseline_iter"] == 1


def test_orchestrator_saves_best_on_quota_without_another_repair(tmp_path, monkeypatch):
    class Generator:
        client = object()
        def generate(self, s, **kw):
            return {"xml": (ROOT / "src/plc/plotter_square_csp.xml").read_text(encoding="utf-8")}
        def repair(self, *a, **kw):
            raise RuntimeError("HTTP 402: Insufficient Balance")
    orch = Orchestrator(runs_root=tmp_path, project_root=ROOT, max_iters=6)
    monkeypatch.setattr(orch, "acceptance_gate", lambda *a, **kw: ("failed", ["FAIL first acceptance"]))
    result = orch.solve(spec(), Generator(), acceptance="mujoco", test_plan=plan())
    root = Path(result["run_dir"])
    assert result["status"] == "api_blocked" and result["iter"] == 1
    assert (root / "best/plcopen.xml").exists()
    assert not (root / "iter_003").exists()


def test_clearance_too_close_to_device_tolerance_stops_before_xml_generation(tmp_path):
    from agent.test_plan import TestPlanGenerator, PlanFeasibilityError
    p = plan()
    check = p["path_checks"][0]
    check["points"] = [{"values": {"x_fb": x, "y_fb": 20, "z_fb": z},
        "tolerance": {"x_fb": 2, "y_fb": 2, "z_fb": .5},
        "states": {"pen_down": True} if z == 0 else {}}
        for x, z in [(20,1),(20,0),(80,0)]]
    check["segments"] = {"signals": ["x_fb","y_fb"], "tolerance": 2, "when": {"pen_down": True}}
    class Client:
        calls = 0
        def chat_completions(self, *a, **kw):
            self.calls += 1
            return {"choices": [{"message": {"content": json.dumps(p)}}]}
    client = Client()
    with pytest.raises(PlanFeasibilityError, match="两倍poswin"):
        TestPlanGenerator(client, report=lambda s: None).generate("draw", spec(), tmp_path)
    assert client.calls == 1  # 物理条件冲突不会反复请求PLC生成或计划修补。


def test_interrupted_checkpoint_copy_keeps_previous_best_pointer(tmp_path, monkeypatch):
    import agent.progress as progress
    tracker = ProgressTracker(tmp_path, spec(), plan(), ROOT)
    tracker.consider(write_case(tmp_path, 1, bad=True), 1, "acceptance")
    old = (tmp_path / "best.json").read_bytes()
    def interrupted(*a, **kw):
        raise OSError("simulated interruption")
    monkeypatch.setattr(progress.shutil, "copytree", interrupted)
    with pytest.raises(OSError, match="interruption"):
        tracker.consider(write_case(tmp_path, 2), 2, "acceptance")
    assert (tmp_path / "best.json").read_bytes() == old
    checkpoint = tmp_path / json.loads(old)["checkpoint"]
    assert (checkpoint / "plcopen.xml").exists()
    assert tracker.best["iter"] == 1


def test_strict_profile_recomputes_saved_trace_and_rejects_forged_pass(tmp_path):
    from agent.scene_gen import SceneSpecGenerator, build_scene_artifacts
    from agent.progress import digest
    from mujoco_verdict import build_channels, evaluate
    from test_plan_runtime import evaluate_path_checks
    folder = write_case(tmp_path, 1)
    s, p = spec(), plan()
    dump(tmp_path / "requirements/requirement_spec.json", s)
    dump(folder / "test_plan.json", p)
    scene = SceneSpecGenerator().generate(s)["scene"]
    dump(folder / "scene.spec.json", scene)
    build_scene_artifacts(scene, s, folder)
    trace = []
    for i in range(3):
        row = {v["name"]: False if v["type"] == "BOOL" else 0 for v in s["io_list"]}
        row.update(t=i/2, run=i>0, cmd_draw=i==1, plot_done=i==2,
                   x_fb=50 if i==2 else 20, y_fb=50 if i==2 else 20, z_fb=10)
        trace.append(row)
    dump(folder / "trace.json", trace)
    channels, _, _ = build_channels(s, json.loads((folder / "io_map.json").read_text(encoding="utf-8")))
    details = evaluate(s["acceptance"], trace, channels) + evaluate_path_checks(p, trace) + [{"id":"EXECUTION","pass":True}]
    verdict = {"verdict":"PASS", "details":details, "sha256":{k:digest(path) for k,path in {
        "xml":folder/"plcopen.xml","spec":tmp_path/"requirements/requirement_spec.json",
        "test_plan":folder/"test_plan.json","scene":folder/"scene.spec.json","io_map":folder/"io_map.json"}.items()}}
    dump(folder / "verdict.json", verdict)
    assert profile(folder, 1, "all", s, p, ROOT, strict=True)["completed"]
    trace[-1]["pen_down"] = True  # 原PASS报告未变化，实际trace已违反AC3。
    dump(folder / "trace.json", trace)
    with pytest.raises(ValueError, match="重新判定"):
        profile(folder, 1, "all", s, p, ROOT, strict=True)


def test_quota_in_advisory_diagnosis_at_last_iteration_is_still_api_blocked(tmp_path, monkeypatch):
    class Generator:
        client = SimpleNamespace(blocked_error=None)
        def generate(self, s, **kw):
            return {"xml": (ROOT / "src/plc/plotter_square_csp.xml").read_text(encoding="utf-8")}
    gen = Generator()
    orch = Orchestrator(runs_root=tmp_path, project_root=ROOT, max_iters=1)
    original = orch._fail
    def diagnosed(*a, **kw):
        feedback = original(*a, **kw)
        gen.client.blocked_error = "HTTP 402: Insufficient Balance"
        return feedback
    monkeypatch.setattr(orch, "_fail", diagnosed)
    monkeypatch.setattr(orch, "acceptance_gate", lambda *a, **kw: ("failed", ["FAIL acceptance"]))
    result = orch.solve(spec(), gen, acceptance="mujoco", test_plan=plan())
    assert result["status"] == "api_blocked" and result["iter"] == 1
    assert (Path(result["run_dir"]) / "best.json").exists()
