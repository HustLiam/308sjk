# -*- coding: utf-8 -*-
"""
闭环编排器单测（半环骨架，种子模式——不调 LLM、不依赖运行时）。

覆盖：final 冻结路径 / 一致性失败→best_effort / 部署闸门 skipped 语义 /
spec 校验失败即拒绝（人工介入点 1）/ runs 产物布局（gc 文档 §4）。
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from agent.orchestrator import Orchestrator  # noqa: E402
from agent.pipeline import PLCGenerator  # noqa: E402

SPEC = json.loads((REPO / "examples" / "specs" / "motion3axis.spec.json").read_text(encoding="utf-8"))
MOTION_XML = REPO / "src" / "plc" / "motion3axis.xml"


def mismatched_seed(tmp_path):
    """负例种子：把到位变量改名，与 spec 的 io_list 对不上 → 一致性闸门失败。"""
    text = MOTION_XML.read_text(encoding="utf-8").replace('"in_pos"', '"at_pos"')
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


class TestGeneratorSeedGate:
    def test_seed_mode_still_runs_gates(self):
        # 种子模式不是免检通道：产物必须过 xml2st + 一致性双闸门
        gen = PLCGenerator(client=None, seed_xml=MOTION_XML)
        out = gen.generate(SPEC)
        assert out["ok"] and out["rounds"] == 0 and out["errors"] == []

    def test_seed_with_wrong_spec_fails_gate(self, tmp_path):
        out = PLCGenerator(client=None, seed_xml=mismatched_seed(tmp_path)).generate(SPEC)
        assert not out["ok"] and any("R2" in e for e in out["errors"])
