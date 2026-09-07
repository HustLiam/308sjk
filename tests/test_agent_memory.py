# -*- coding: utf-8 -*-
"""
标准 Agent 形态增量单测：知识库签名匹配 / 情景记忆 / 归因引擎（KB 优先、
LLM 兜底、不裁定红线）/ 模式库自动策展与上下文预算 / 编排器归因接线。
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from agent.attribution import AttributionEngine  # noqa: E402
from agent.memory import MemoryStore  # noqa: E402

KB = REPO / "src" / "agent" / "knowledge" / "pitfalls.json"


class TestPitfallMatching:
    def test_endcase_semicolon_hit(self):
        m = MemoryStore(kb_path=KB, episodic_path=REPO / "workspace" / "memory" / "nope.json")
        hits = m.match_pitfalls(["./st_files/123.st:509: error: ';' missing at the end of statement"])
        assert any(p["id"] == "P09" for p in hits)

    def test_prog_id_hit(self):
        m = MemoryStore(kb_path=KB, episodic_path=REPO / "workspace" / "memory" / "nope.json")
        hits = m.match_pitfalls(["[verify] 程序不匹配：期望 plotter3axis (prog_id=2)"])
        assert any(p["id"] == "P12" for p in hits)

    def test_strobe_swallow_hit(self):
        m = MemoryStore(kb_path=KB, episodic_path=REPO / "workspace" / "memory" / "nope.json")
        hits = m.match_pitfalls(["序列器步进空转：ix Done 永不回落（选通赋值消失）"])
        assert any(p["id"] == "P10" for p in hits)

    def test_no_false_positive_on_clean_text(self):
        m = MemoryStore(kb_path=KB, episodic_path=REPO / "workspace" / "memory" / "nope.json")
        assert m.match_pitfalls(["一切正常"]) == []


class TestEpisodicMemory:
    def test_record_and_match(self, tmp_path):
        epi = tmp_path / "fixes.json"
        m = MemoryStore(kb_path=KB, episodic_path=epi)
        m.record_fix("deploy", ["matiec 编译失败: ';' missing at end of statement"],
                     "END_CASE 后补分号", "resolved")
        m2 = MemoryStore(kb_path=KB, episodic_path=epi)
        fixes = m2.match_fixes(["error ';' missing at the end of statement in ST"])
        assert fixes and "END_CASE" in fixes[0]["action"]

    def test_abandoned_not_matched(self, tmp_path):
        epi = tmp_path / "fixes.json"
        m = MemoryStore(kb_path=KB, episodic_path=epi)
        m.record_fix("deploy", ["某种从未见过的错误 xyz"], "无解放弃", "abandoned")
        assert MemoryStore(kb_path=KB, episodic_path=epi).match_fixes(["错误 xyz"]) == []


class _CountingClient:
    def __init__(self, content):
        self.content = content
        self.calls = 0

    def chat_completions(self, payload):
        self.calls += 1
        return {"choices": [{"message": {"content": self.content}}]}


class TestAttribution:
    def test_known_error_skips_llm(self):
        client = _CountingClient("```json\n{}\n```")
        engine = AttributionEngine(
            memory=MemoryStore(kb_path=KB,
                               episodic_path=REPO / "workspace" / "memory" / "nope.json"),
            client=client)
        out = engine.attribute("deploy",
                               ["matiec 编译失败: ';' missing at the end of statement"])
        assert any(p["id"] == "P09" for p in out["pitfalls"])
        assert client.calls == 0                      # KB 命中不打 LLM
        assert "END_CASE" in "\n".join(out["repair_hints"])

    def test_unknown_error_llm_fallback(self):
        client = _CountingClient(
            "```json\n{\"root_cause\": \"代码问题：xxx\", \"repair_hints\": [\"改 yyy\"]}\n```")
        engine = AttributionEngine(
            memory=MemoryStore(kb_path=KB,
                               episodic_path=REPO / "workspace" / "memory" / "nope.json"),
            client=client)
        out = engine.attribute("acceptance", ["前所未见的失败 zzq"])
        assert client.calls == 1
        assert out["llm_diagnosis"]["advisory"] is True
        assert "改 yyy" in out["repair_hints"]

    def test_disabled_engine(self):
        engine = AttributionEngine(enabled=False)
        out = engine.attribute("deploy", ["';' missing"])
        assert out.get("disabled") and "pitfalls" not in out

    def test_format_feedback_renders(self):
        client = _CountingClient("")
        engine = AttributionEngine(
            memory=MemoryStore(kb_path=KB,
                               episodic_path=REPO / "workspace" / "memory" / "nope.json"),
            client=client)
        out = engine.attribute("deploy",
                               ["matiec 编译失败: ';' missing at the end of statement"])
        text = AttributionEngine.format_feedback(out)
        assert "已知坑 P09" in text and "修法" in text


class TestPatternRegistry:
    def test_register_and_catalog_merge(self, tmp_path, monkeypatch):
        import agent.patternlib as pl
        reg = tmp_path / "patterns.json"
        monkeypatch.setattr(pl, "REGISTRY_PATH", reg)
        src = REPO / "src" / "plc" / "plotter3axis.xml"
        assert pl.register_pattern("plotter3axis", src,
                                   "三轴绘图仪：画正方形序列", ["绘图", "画", "序列"])
        keys = [e[0] for e in pl._catalog()]
        assert "plotter3axis" in keys and "motion3axis" in keys
        assert not pl.register_pattern("plotter3axis", src, "dup", [])  # 防重复

    def test_budget_drops_cards_with_note(self):
        import agent.patternlib as pl
        cards = [{"key": "k%d" % i, "summary": "s", "st": "x" * 12000} for i in range(4)]
        text = pl.render_cards(cards, max_chars=26000)
        assert "上下文预算" in text and "k3" in text      # 超预算丢卡并注明
        assert text.count("### 模式卡") == 2               # 保住前两张


class TestOrchestratorAttribution:
    """编排器接线：失败 → gate.json 带归因；final+acceptance ok → 模式登记。"""

    SPEC = json.loads((REPO / "examples" / "specs" / "plotter3axis.spec.json")
                      .read_text(encoding="utf-8"))

    def test_failure_gate_json_contains_attribution(self, tmp_path, monkeypatch):
        from agent.orchestrator import Orchestrator
        from agent.pipeline import PLCGenerator
        orch = Orchestrator(runs_root=tmp_path, max_iters=1, project_root=REPO)
        monkeypatch.setattr(
            orch, "acceptance_gate",
            lambda scenario: ("failed", ["matiec 编译失败: ';' missing at the end"]))
        result = orch.solve(self.SPEC,
                            PLCGenerator(client=None,
                                         seed_xml=REPO / "src" / "plc" / "plotter3axis.xml"),
                            acceptance="plotter3axis")
        assert result["status"] == "best_effort"
        gate = json.loads((Path(result["run_dir"]) / "iter_001" / "gate.json")
                          .read_text(encoding="utf-8"))
        assert gate["gate"] == "acceptance"
        assert any(p["id"] == "P09" for p in gate["attribution"]["pitfalls"])

    def test_final_with_acceptance_registers_pattern(self, tmp_path, monkeypatch):
        import agent.patternlib as pl
        from agent.orchestrator import Orchestrator
        from agent.pipeline import PLCGenerator
        reg = tmp_path / "patterns.json"
        monkeypatch.setattr(pl, "REGISTRY_PATH", reg)
        orch = Orchestrator(runs_root=tmp_path, project_root=REPO)
        monkeypatch.setattr(orch, "acceptance_gate",
                            lambda scenario: ("ok", "场景验收: 全部通过 ✅"))
        result = orch.solve(self.SPEC,
                            PLCGenerator(client=None,
                                         seed_xml=REPO / "src" / "plc" / "plotter3axis.xml"),
                            acceptance="plotter3axis")
        assert result["status"] == "final"
        assert reg.is_file()
        entry = json.loads(reg.read_text(encoding="utf-8"))["patterns"][0]
        assert entry["key"] == "plotter3axis"
        assert entry["provenance"] == "orchestrator-final"
