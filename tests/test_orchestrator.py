# -*- coding: utf-8 -*-
"""
闭环编排器单测（半环骨架，种子模式——不调 LLM、不依赖运行时）。

覆盖：final 冻结路径 / 一致性失败→best_effort / 部署闸门 skipped 语义 /
spec 校验失败即拒绝（人工介入点 1）/ runs 产物布局（gc 文档 §4）。
"""

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from agent.orchestrator import Orchestrator  # noqa: E402
from agent.pipeline import PLCGenerator  # noqa: E402

SPEC = json.loads((REPO / "examples" / "specs" / "motion3axis.spec.json").read_text(encoding="utf-8"))
MOTION_XML = REPO / "src" / "plc" / "motion3axis.xml"
PLOTTER_XML = REPO / "src" / "plc" / "plotter3axis.xml"


def mismatched_seed(tmp_path):
    """负例种子：把到位变量改名，与 spec 的 io_list 对不上 → 一致性闸门失败。"""
    text = MOTION_XML.read_text(encoding="utf-8").replace('"move_done"', '"done_flag"')
    path = tmp_path / "mismatch.xml"
    path.write_text(text, encoding="utf-8")
    return path


class TestFinalPath:
    def test_seed_mode_reaches_final(self, tmp_path):
        orch = Orchestrator(runs_root=tmp_path)
        gen = PLCGenerator(client=None, seed_xml=MOTION_XML)
        events = []
        result = orch.solve(SPEC, gen, echo=lambda ev, p: events.append(ev))
        assert result["status"] == "final" and result["iter"] == 1
        assert events == ["iter_start", "final"]

    def test_runs_layout(self, tmp_path):
        orch = Orchestrator(runs_root=tmp_path)
        result = orch.solve(SPEC, PLCGenerator(client=None, seed_xml=MOTION_XML))
        run_dir = Path(result["run_dir"])
        assert (run_dir / "request.json").is_file()
        iter1 = run_dir / "iter_001"
        assert (iter1 / "plcopen.xml").is_file()
        assert (iter1 / "plc.st").is_file()
        gate = json.loads((iter1 / "gate.json").read_text(encoding="utf-8"))
        assert gate["ok"] is True and "gates" in gate
        assert (run_dir / "final" / "plcopen.xml").is_file()   # 冻结快照
        assert (run_dir / "summary.md").read_text(encoding="utf-8").startswith("# solve")


class TestBestEffort:
    def test_consistency_failure_goes_best_effort(self, tmp_path):
        # 改名种子与 motion3axis 规格的 io_list 对不上 → 一致性闸门每轮失败
        orch = Orchestrator(runs_root=tmp_path, max_iters=2)
        result = orch.solve(SPEC, PLCGenerator(client=None, seed_xml=mismatched_seed(tmp_path)))
        assert result["status"] == "best_effort"
        gate = json.loads((Path(result["run_dir"]) / "iter_001" / "gate.json").read_text(encoding="utf-8"))
        assert gate["gate"] == "consistency" and not gate["ok"]
        assert any("R2" in e for e in gate["errors"])

    def test_no_final_dir_on_failure(self, tmp_path):
        orch = Orchestrator(runs_root=tmp_path, max_iters=1)
        result = orch.solve(SPEC, PLCGenerator(client=None, seed_xml=mismatched_seed(tmp_path)))
        assert not (Path(result["run_dir"]) / "final").exists()
        assert (Path(result["run_dir"]) / "summary.md").is_file()

    def test_invalid_spec_rejected_upfront(self, tmp_path):
        # 人工介入点 1：spec 校验不过直接拒绝进入循环
        bad = json.loads(json.dumps(SPEC))
        bad["acceptance"][0]["value"] = 0.01  # <100ms
        orch = Orchestrator(runs_root=tmp_path)
        try:
            orch.solve(bad, PLCGenerator(client=None, seed_xml=MOTION_XML))
            assert False, "应抛 ValueError"
        except ValueError as exc:
            assert "人工介入点 1" in str(exc)


class TestDeployGate:
    def test_service_offline_is_skipped_not_failed(self, tmp_path):
        # 半环约定：deploy 服务不在线 = skipped，不阻塞 final
        orch = Orchestrator(runs_root=tmp_path, deploy_url="http://127.0.0.1:1/deploy")
        result = orch.solve(SPEC, PLCGenerator(client=None, seed_xml=MOTION_XML), deploy=True)
        assert result["status"] == "final"
        gate = json.loads((Path(result["run_dir"]) / "final" / "gate.json").read_text(encoding="utf-8"))
        assert gate["gates"]["deploy"]["state"] == "skipped"


class TestAcceptanceGate:
    """闸门4：链路 B 在线验收（scenario_<场景>.py 子进程调用）。"""

    def test_missing_scenario_script_is_skipped(self, tmp_path):
        orch = Orchestrator(runs_root=tmp_path, project_root=REPO)
        state, detail = orch.acceptance_gate("no_such_scenario")
        assert state == "skipped" and "无验收脚本" in detail

    def test_offline_runtime_is_skipped(self, tmp_path, monkeypatch):
        # OpenPLC/Modbus 不在线（脚本打印"无法连接"）→ skipped，与闸门3 同语义
        orch = Orchestrator(runs_root=tmp_path, project_root=REPO)
        fake = subprocess.CompletedProcess([], returncode=1,
                                           stdout="Traceback ... ConnectionError: 无法连接 Modbus 127.0.0.1:502")
        monkeypatch.setattr(orch, "_run_acceptance", lambda script: fake)
        state, detail = orch.acceptance_gate("motion3axis")
        assert state == "skipped" and "不在线" in detail

    def test_acceptance_ok_reaches_final(self, tmp_path, monkeypatch):
        orch = Orchestrator(runs_root=tmp_path, project_root=REPO)
        monkeypatch.setattr(orch, "acceptance_gate",
                            lambda scenario: ("ok", "场景验收: 全部通过 ✅"))
        result = orch.solve(SPEC, PLCGenerator(client=None, seed_xml=MOTION_XML),
                            acceptance="motion3axis")
        assert result["status"] == "final"
        gate = json.loads((Path(result["run_dir"]) / "final" / "gate.json").read_text(encoding="utf-8"))
        assert gate["gates"]["acceptance"]["state"] == "ok"

    def test_acceptance_fail_feeds_back_and_best_effort(self, tmp_path, monkeypatch):
        # 真失败（exit 1 的 PASS/FAIL 明细）→ 回喂下一轮，6 轮不过取 best_effort。
        # deploy_url 指向死端口：隔离 _runtime_probe 的真部署（serve 在线时防占用运行时）
        orch = Orchestrator(runs_root=tmp_path, project_root=REPO, max_iters=2,
                            deploy_url="http://127.0.0.1:1/deploy")
        monkeypatch.setattr(orch, "acceptance_gate",
                            lambda scenario: ("failed", ["  FAIL X 回零（实际 37）",
                                                         "场景验收: 存在失败 ❌"]))
        events = []
        result = orch.solve(SPEC, PLCGenerator(client=None, seed_xml=MOTION_XML),
                            acceptance="motion3axis",
                            echo=lambda ev, p: events.append(ev))
        assert result["status"] == "best_effort"
        assert events.count("gate_failed") == 2
        gate = json.loads((Path(result["run_dir"]) / "iter_001" / "gate.json").read_text(encoding="utf-8"))
        assert gate["gate"] == "acceptance" and "FAIL" in gate["errors"][0]

    def test_skipped_acceptance_does_not_block_final(self, tmp_path, monkeypatch):
        orch = Orchestrator(runs_root=tmp_path, project_root=REPO)
        monkeypatch.setattr(orch, "acceptance_gate",
                            lambda scenario: ("skipped", "OpenPLC/Modbus 不在线"))
        result = orch.solve(SPEC, PLCGenerator(client=None, seed_xml=MOTION_XML),
                            acceptance="motion3axis")
        assert result["status"] == "final"
        gate = json.loads((Path(result["run_dir"]) / "final" / "gate.json").read_text(encoding="utf-8"))
        assert gate["gates"]["acceptance"]["state"] == "skipped"


class TestGeneratorSeedGate:
    def test_seed_mode_still_runs_gates(self):
        # 种子模式不是免检通道：产物必须过 xml2st + 一致性双闸门
        gen = PLCGenerator(client=None, seed_xml=MOTION_XML)
        out = gen.generate(SPEC)
        assert out["ok"] and out["rounds"] == 0 and out["errors"] == []

    def test_seed_with_wrong_spec_fails_gate(self, tmp_path):
        out = PLCGenerator(client=None, seed_xml=mismatched_seed(tmp_path)).generate(SPEC)
        assert not out["ok"] and any("R2" in e for e in out["errors"])


class TestSceneGate:
    """闸门2b：②b 场景描述生成（确定性）+ R5 全腿一致性。"""

    PLOTTER_SPEC = json.loads((REPO / "examples" / "specs" / "plotter3axis.spec.json")
                              .read_text(encoding="utf-8"))
    PLOTTER_XML = REPO / "src" / "plc" / "plotter3axis.xml"

    def test_scene_artifacts_written_and_r5_active(self, tmp_path):
        from agent.aml_parser import parse_aml
        from agent.scene_gen import SceneSpecGenerator
        model, _ = parse_aml(REPO / "examples" / "aml" / "plotter3axis_station.aml")
        orch = Orchestrator(runs_root=tmp_path)
        gen = PLCGenerator(client=None, seed_xml=self.PLOTTER_XML)
        result = orch.solve(self.PLOTTER_SPEC, gen,
                            scene_generator=SceneSpecGenerator(), device_model=model)
        assert result["status"] == "final"
        iter1 = Path(result["run_dir"]) / "iter_001"
        scene = json.loads((iter1 / "scene.spec.json").read_text(encoding="utf-8"))
        io_map = json.loads((iter1 / "io_map.json").read_text(encoding="utf-8"))
        assert scene["scene_id"] == self.PLOTTER_SPEC["task_id"]
        assert len(io_map["mappings"]) == len(self.PLOTTER_SPEC["io_list"])
        gate = json.loads((iter1 / "gate.json").read_text(encoding="utf-8"))
        assert gate["gates"]["scene"]["r5"] == "active"
        assert (Path(result["run_dir"]) / "final" / "io_map.json").is_file()  # 冻结含 ②b 产物

    def test_scene_gate_failure_goes_best_effort(self, tmp_path):
        class BrokenGen:
            def generate(self, spec, device_model=None):
                raise ValueError("V4: 生成自检失败（注入）")
        orch = Orchestrator(runs_root=tmp_path, max_iters=2)
        gen = PLCGenerator(client=None, seed_xml=MOTION_XML)
        result = orch.solve(SPEC, gen, scene_generator=BrokenGen())
        assert result["status"] == "best_effort"
        gate = json.loads((Path(result["run_dir"]) / "iter_001" / "gate.json").read_text(encoding="utf-8"))
        assert gate["gate"] == "scene" and not gate["ok"]

    def test_scene_off_by_default_backward_compatible(self, tmp_path):
        """不传 scene_generator：行为与旧半环一致（无 scene 产物）。"""
        orch = Orchestrator(runs_root=tmp_path)
        result = orch.solve(SPEC, PLCGenerator(client=None, seed_xml=MOTION_XML))
        assert result["status"] == "final"
        assert not (Path(result["run_dir"]) / "iter_001" / "io_map.json").exists()


class TestStatusProbe:
    def test_offline_returns_none(self, tmp_path):
        orch = Orchestrator(runs_root=tmp_path, deploy_url="http://127.0.0.1:1/deploy")
        assert orch.status_probe() is None

    def test_online_json_recorded_in_deploy_gate(self, tmp_path):
        """deploy ok 后 /status 观测写入 gate（不裁定）。"""
        orch = Orchestrator(runs_root=tmp_path)
        monkey_probe = {"serve": "ok", "runtime": {"status": "RUNNING"},
                        "prog_id": 2, "program": "plotter3axis"}
        orch.status_probe = lambda: monkey_probe
        orch.deploy_gate = lambda xml: ("ok", {"status": "OK"})
        result = orch.solve(SPEC, PLCGenerator(client=None, seed_xml=MOTION_XML), deploy=True)
        assert result["status"] == "final"
        gate = json.loads((Path(result["run_dir"]) / "final" / "gate.json").read_text(encoding="utf-8"))
        assert gate["gates"]["deploy"]["detail"]["runtime_status"]["prog_id"] == 2


class TestRepairStrategy:
    """定向修复为 LLM 迭代默认策略（上轮过静态闸门的产物不丢、最小修改）。"""

    PLOTTER_XML = REPO / "src" / "plc" / "plotter3axis.xml"

    SPEC = json.loads((REPO / "examples" / "specs" / "plotter3axis.spec.json")
                      .read_text(encoding="utf-8"))

    def test_repair_mode_used_after_static_pass_failure(self, tmp_path, monkeypatch):
        """静态闸门过后失败 → 下一迭代必须走 repair（用上轮产物），不再重生成。"""
        from agent.orchestrator import Orchestrator
        calls = {"fresh": 0, "repair": 0}

        class FakeGen:
            client = object()  # LLM 可用标记

            def generate(self, spec, feedback=None):
                calls["fresh"] += 1
                return {"ok": True, "xml": PLOTTER_XML.read_text(encoding="utf-8")}

            def repair(self, previous_xml, spec, feedback, attempts=None):
                calls["repair"] += 1
                assert previous_xml == PLOTTER_XML.read_text(encoding="utf-8")
                return {"ok": True, "xml": previous_xml, "mode": "repair"}

        states = iter([("failed", ["闸门4失败样本"]), ("ok", "场景验收: 全部通过 ✅")])
        orch = Orchestrator(runs_root=tmp_path, project_root=REPO,
                            deploy_url="http://127.0.0.1:1/deploy")
        monkeypatch.setattr(orch, "acceptance_gate", lambda s: next(states))
        result = orch.solve(self.SPEC, FakeGen(), acceptance="plotter3axis")
        assert result["status"] == "final"
        assert calls == {"fresh": 1, "repair": 1}      # 第二轮定向修复而非重生成

    def test_repair_circuit_breaker_falls_back_to_fresh(self, tmp_path, monkeypatch):
        """修复连续失败 3 次 → 回退全新生成（防死循环）。"""
        from agent.orchestrator import Orchestrator
        calls = {"fresh": 0, "repair": 0}

        class FlakyGen:
            client = object()

            def generate(self, spec, feedback=None):
                calls["fresh"] += 1
                return {"ok": True, "xml": PLOTTER_XML.read_text(encoding="utf-8")}

            def repair(self, previous_xml, spec, feedback, attempts=None):
                calls["repair"] += 1
                return {"ok": False, "xml": None, "errors": ["修复失败"], "mode": "repair"}

        orch = Orchestrator(runs_root=tmp_path, project_root=REPO, max_iters=6,
                            deploy_url="http://127.0.0.1:1/deploy")
        monkeypatch.setattr(orch, "acceptance_gate",
                            lambda s: ("failed", ["持续失败"]))
        result = orch.solve(self.SPEC, FlakyGen(), acceptance="plotter3axis")
        assert result["status"] == "best_effort"
        # 6 轮预算：fresh(1)→repair×3(熔断)→fresh(2,过静态闸重置计数)→repair(4)
        assert calls["fresh"] == 2 and calls["repair"] == 4


class TestFeedbackFullArchive:
    """F2a：验收证据全量落盘 gate.json；LLM 反馈包截尾并注明（token 预算不变）。"""

    def test_pack_feedback_truncates_with_note(self):
        errors = ["失败行 %d" % i for i in range(1, 51)]      # 50 行 > 40
        text = Orchestrator._pack_feedback(errors, [])
        assert "全量见 gate.json" in text                     # 截断显式注明
        assert "- 失败行 50" in text                          # 尾部保留
        assert "- 失败行 1\n" not in text                     # 头部截除（行 1，非行 10~19）
        assert text.count("- 失败行") == 40                   # 恰好尾部 40 行

    def test_gate_json_keeps_full_errors(self, tmp_path, monkeypatch):
        full = ["FAIL 证据 %02d" % i for i in range(60)]
        orch = Orchestrator(runs_root=tmp_path, project_root=REPO, max_iters=1,
                            deploy_url="http://127.0.0.1:1/deploy")
        monkeypatch.setattr(orch, "acceptance_gate", lambda s: ("failed", full))
        result = orch.solve(SPEC, PLCGenerator(client=None, seed_xml=MOTION_XML),
                            acceptance="motion3axis")
        gate = json.loads((Path(result["run_dir"]) / "iter_001" / "gate.json")
                          .read_text(encoding="utf-8"))
        assert len(gate["errors"]) == 60                      # 全量（旧逻辑仅尾部 40）
        assert gate["errors"][0] == "FAIL 证据 00"


class TestZeroProgressBreaker:
    """F3：repair 对零推进失败提前熔断——连续 2 轮失败签名同质即强制全新生成。"""

    PLOTTER_XML = REPO / "src" / "plc" / "plotter3axis.xml"
    SPEC = json.loads((REPO / "examples" / "specs" / "plotter3axis.spec.json")
                      .read_text(encoding="utf-8"))

    @staticmethod
    def _gen(calls):
        class FakeGen:
            client = object()   # LLM 可用标记（repair 路由前提）

            def generate(self, spec, feedback=None):
                calls["fresh"] += 1
                return {"ok": True,
                        "xml": TestZeroProgressBreaker.PLOTTER_XML.read_text(encoding="utf-8")}

            def repair(self, previous_xml, spec, feedback, attempts=None):
                calls["repair"] += 1
                return {"ok": True, "xml": previous_xml, "mode": "repair"}
        return FakeGen()

    def test_two_homogeneous_failures_force_fresh(self, tmp_path, monkeypatch):
        """iter1 fresh 失败 → iter2 repair 失败（证据同质）→ iter3 强制 fresh。"""
        calls = {"fresh": 0, "repair": 0}
        events = []
        orch = Orchestrator(runs_root=tmp_path, project_root=REPO, max_iters=3,
                            deploy_url="http://127.0.0.1:1/deploy")
        monkeypatch.setattr(orch, "acceptance_gate",
                            lambda s: ("failed", ["FAIL 序列冻结：pl_step=1 恒定"]))
        orch.solve(self.SPEC, self._gen(calls), acceptance="plotter3axis",
                   echo=lambda ev, p: events.append(ev))
        assert calls == {"fresh": 2, "repair": 1}
        assert "switch_fresh" in events

    def test_changing_evidence_does_not_break_early(self, tmp_path, monkeypatch):
        """失败证据逐轮变化（签名不同质）→ 不触发提前熔断，repair 继续。"""
        calls = {"fresh": 0, "repair": 0}
        states = iter([("failed", ["FAIL 回零（实际 37）"]),
                       ("failed", ["FAIL 回零（实际 25）"]),
                       ("failed", ["FAIL 回零（实际 12）"])])
        orch = Orchestrator(runs_root=tmp_path, project_root=REPO, max_iters=3,
                            deploy_url="http://127.0.0.1:1/deploy")
        monkeypatch.setattr(orch, "acceptance_gate", lambda s: next(states))
        result = orch.solve(self.SPEC, self._gen(calls), acceptance="plotter3axis")
        assert result["status"] == "best_effort"
        assert calls == {"fresh": 1, "repair": 2}

    def test_signature_normalization_ignores_volatile_parts(self):
        """采样时刻/时间戳/行号/st 文件名归一化；状态数值保留（是推进证据）。"""
        a = Orchestrator._fail_signature([
            "    [trace t=36s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0",
            "./st_files/12.st:509: error: ';' missing"])
        b = Orchestrator._fail_signature([
            "    [trace t=99s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0",
            "./st_files/77.st:88: error: ';' missing"])
        progressed = Orchestrator._fail_signature([
            "    [trace t=36s] pos=(50,50,10) v=(0,0,0) pen=1 done=1 moving=0"])
        assert a == b                       # 易变成分剥离后同质
        assert a != progressed              # 状态数值变化 = 有推进，不同签名


class TestSectionFailFast:
    """验收段级 fail-fast：FAIL 段检查完毕即暂停（kill 子进程），后续段不执行。"""

    @staticmethod
    def _write_scenario(root):
        """临时验收脚本：[1] 过 → [2] 挂 → （停 1.2s）→ [3][4] 不应被执行。"""
        d = root / "src" / "pipeline"
        d.mkdir(parents=True)
        script = d / "scenario_testsec.py"
        script.write_text(
            "import time\n"
            "print('[1] 上电初始')\n"
            "print('  PASS 初始态')\n"
            "print('[2] 使能')\n"
            "print('  FAIL all_oe=TRUE')\n"
            "time.sleep(1.2)\n"
            "print('[3] 手动定位')\n"
            "print('  PASS 定位')\n"
            "print('[4] 绘图')\n"
            "print('场景验收: 全部通过')\n", encoding="utf-8")
        return script

    def test_failed_section_pauses_rest(self, tmp_path):
        self._write_scenario(tmp_path)
        orch = Orchestrator(runs_root=tmp_path, project_root=tmp_path,
                            deploy_url="http://127.0.0.1:1/deploy")
        live = []
        orch._acceptance_live = live.append
        state, detail = orch.acceptance_gate("testsec")
        assert state == "failed"
        text = "\n".join(detail)
        assert "[1]" in text and "FAIL all_oe=TRUE" in text   # 失败段明细保留
        assert "验收暂停" in text and "段 [2]" in text        # 暂停说明
        assert "已通过段：[1]" in text
        assert "[3]" not in text and "[4]" not in text        # 后续段未执行
        assert any("[3]" not in ln and ln for ln in live)     # 直播通道也收到

    def test_all_pass_runs_to_end(self, tmp_path):
        d = tmp_path / "src" / "pipeline"
        d.mkdir(parents=True)
        (d / "scenario_oksec.py").write_text(
            "print('[1] 段一')\nprint('  PASS a')\nprint('[2] 段二')\n"
            "print('  PASS b')\nprint('场景验收: 全部通过')\n", encoding="utf-8")
        orch = Orchestrator(runs_root=tmp_path, project_root=tmp_path)
        state, detail = orch.acceptance_gate("oksec")
        assert state == "ok"                                  # 全过 → 正常完成
        assert "验收暂停" not in "\n".join(detail)


class TestTrajectoryFlow:
    """轨迹参数化路线：trajectory 落盘 run_dir 并透传生成器。"""

    def test_trajectory_archived_and_passed_to_generator(self, tmp_path, monkeypatch):
        from agent.trajectory import plan_circle
        calls = {}

        class FakeGen:
            client = None   # 无 LLM → 永远 fresh 路径

            def generate(self, spec, feedback=None, trajectory=None):
                calls["trajectory"] = trajectory
                return {"ok": True, "xml": MOTION_XML.read_text(encoding="utf-8")}

            def repair(self, *a, **kw):
                raise AssertionError("无 client 不应走 repair")

        traj = plan_circle()
        orch = Orchestrator(runs_root=tmp_path)
        result = orch.solve(SPEC, FakeGen(), trajectory=traj)
        assert result["status"] == "final"
        assert calls["trajectory"] is traj                     # 生成器收到权威步表
        archived = json.loads((Path(result["run_dir"]) / "trajectory.json")
                              .read_text(encoding="utf-8"))
        assert archived["shape"] == "circle"                   # 任务级留档
        assert archived["params"]["radius"] == 25
