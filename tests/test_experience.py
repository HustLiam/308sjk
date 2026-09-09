# -*- coding: utf-8 -*-
"""
自动化学习机制单测（经验库 v2 / 翻转自动记录 / 相似度分级借鉴 / 去模式卡）。
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from agent.memory import MemoryStore, normalize_errors  # noqa: E402
from agent.orchestrator import Orchestrator  # noqa: E402
from agent.attribution import AttributionEngine  # noqa: E402
from agent.pipeline import PLCGenerator  # noqa: E402

KB = REPO / "src" / "agent" / "knowledge" / "pitfalls.json"

FAIL_A = ["  FAIL 画圆序列完成 plot_done=TRUE（999.0s ≤ 30s，AC1）",
          "    [trace t=36s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0",
          "  FAIL 终态回圆心 (50,50,10)（实际 (0,0,0)，AC6）"]
# 同质变体：仅采样时刻/位置数值微变
FAIL_A_TWIN = ["  FAIL 画圆序列完成 plot_done=TRUE（999.0s ≤ 30s，AC1）",
               "    [trace t=99s] pos=(0,0,0) v=(0,0,0) pen=1 done=0 moving=0",
               "  FAIL 终态回圆心 (50,50,10)（实际 (0,0,0)，AC6）"]
# 无关失败（编译类）
FAIL_B = ["matiec 编译失败: ';' missing at the end of statement"]


def _store(tmp_path):
    return MemoryStore(kb_path=KB, episodic_path=tmp_path / "fixes.json",
                       lessons_path=tmp_path / "lessons.json")


class TestLessons:
    def test_record_and_match_homogeneous(self):
        m = _store(Path("tests/_tmp_exp1"))
        try:
            m.record_lesson("acceptance", FAIL_A, "修复 X：翻转触发线电平",
                            diff_hunks=["[PLC_PRG]\n+go_x_exe := TRUE;"],
                            outcome="resolved", task="t1")
            hits = m.match_lessons(FAIL_A_TWIN, gate="acceptance")
            assert hits and hits[0]["sim"] >= 0.55       # 同质 → detail 级
            assert hits[0]["diff_hunks"]                 # 高相似带变更骨架
            assert "翻转" in hits[0]["fix_summary"]
        finally:
            import shutil
            shutil.rmtree("tests/_tmp_exp1", ignore_errors=True)

    def test_similarity_gating(self):
        """中相似只给方向、低相似不返回（借鉴程度与相似度相关）。"""
        m = _store(Path("tests/_tmp_exp2"))
        try:
            m.record_lesson("acceptance", FAIL_A, "修法甲", outcome="resolved", task="t1")
            # 低相似（编译错误 vs 验收失败，几乎无交集行）
            assert m.match_lessons(FAIL_B, gate="acceptance") == []
            # 跨闸门降权后仍需过阈：同证据不同闸门
            cross = m.match_lessons(FAIL_A, gate="deploy")
            assert all(h["sim"] < 0.55 or not h.get("diff_hunks") for h in cross) or cross == []
        finally:
            import shutil
            shutil.rmtree("tests/_tmp_exp2", ignore_errors=True)

    def test_abandoned_not_matched(self):
        m = _store(Path("tests/_tmp_exp3"))
        try:
            m.record_lesson("acceptance", FAIL_A, "未收敛教训", outcome="abandoned", task="t1")
            assert m.match_lessons(FAIL_A_TWIN, gate="acceptance") == []
        finally:
            import shutil
            shutil.rmtree("tests/_tmp_exp3", ignore_errors=True)

    def test_signature_shared_with_orchestrator(self):
        """经验库指纹与零推进熔断指纹同源（normalize_errors 单一实现）。"""
        assert Orchestrator._fail_signature(FAIL_A) == Orchestrator._fail_signature(FAIL_A_TWIN)


class TestFlipRecording:
    def test_final_via_repair_records_lesson(self, tmp_path, monkeypatch):
        """A 机制：repair 修复后 final → 自动记录失败→修复骨架经验对。"""
        calls = {"fresh": 0, "repair": 0}
        PLOTTER = (REPO / "src" / "plc" / "plotter3axis.xml").read_text(encoding="utf-8")

        class FakeGen:
            client = object()

            def generate(self, spec, feedback=None, trajectory=None):
                calls["fresh"] += 1
                return {"ok": True, "xml": PLOTTER}

            def repair(self, previous_xml, spec, feedback, attempts=None):
                calls["repair"] += 1
                return {"ok": True, "xml": previous_xml, "mode": "repair"}

        spec = json.loads((REPO / "examples" / "specs" / "plotter3axis.spec.json")
                          .read_text(encoding="utf-8"))
        mem = MemoryStore(kb_path=KB, episodic_path=tmp_path / "f.json",
                          lessons_path=tmp_path / "lessons.json")
        orch = Orchestrator(runs_root=tmp_path, project_root=REPO,
                            attribution_engine=AttributionEngine(
                                memory=mem, client=None))
        states = iter([("failed", ["  FAIL 画圆序列完成 plot_done=TRUE（999.0s）"]),
                       ("ok", "场景验收: 全部通过 ✅")])
        monkeypatch.setattr(orch, "acceptance_gate", lambda s: next(states))
        result = orch.solve(spec, FakeGen(), acceptance="plotter3axis")
        assert result["status"] == "final"
        lessons = json.loads((tmp_path / "lessons.json").read_text(encoding="utf-8"))["lessons"]
        assert lessons and lessons[0]["outcome"] == "resolved"
        assert lessons[0]["gate"] == "acceptance"
        assert "plot_done" in "".join(lessons[0]["errors"])   # 失败证据被记录

    def test_diff_skeleton_extracts_change(self):
        xml = (REPO / "src" / "plc" / "plotter3axis.xml").read_text(encoding="utf-8")
        hunks = Orchestrator._diff_skeleton(xml, xml)
        assert hunks == []                                    # 无差异 → 空
        xml2 = xml.replace("prog_id := 2;", "prog_id := 3;")
        hunks2 = Orchestrator._diff_skeleton(xml, xml2)
        assert hunks2 and any("prog_id" in h for h in hunks2)


class TestNoCards:
    def test_no_cards_prompt_has_no_pattern_cards(self, tmp_path):
        gen = PLCGenerator(client=None, no_cards=True)
        spec = json.loads((REPO / "examples" / "specs" / "motion3axis.spec.json")
                          .read_text(encoding="utf-8"))
        msgs = gen.build_messages(spec)
        assert "### 模式卡" not in msgs[0]["content"]       # 零卡注入（渲染卡标题不出现）
        assert "参考模式" not in msgs[0]["content"]
        gen2 = PLCGenerator(client=None)
        assert "### 模式卡" in gen2.build_messages(spec)[0]["content"]   # 默认路线不受影响
