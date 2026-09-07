# -*- coding: utf-8 -*-
"""
ST 模式库单测：种子选取（关键词命中 + 兜底）与卡片渲染。
种子直接来自 src/plc/*.xml，lx 侧维护场景后内容自动跟随（无第二份拷贝）。
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from agent import patternlib  # noqa: E402
from agent.patternlib import CATALOG, DEFAULT_PICKS, pattern_cards, render_cards  # noqa: E402


class TestSelection:
    def test_motion_keywords(self):
        cards = pattern_cards("三轴龙门点到点定位，Z 轴安全互锁")
        assert cards and cards[0]["key"] == "motion3axis"

    def test_axis_keywords(self):
        cards = pattern_cards("多轴伺服平移到位控制")
        assert cards[0]["key"] == "motion3axis"

    def test_no_hit_falls_back_to_defaults(self):
        cards = pattern_cards("煮咖啡")
        assert [c["key"] for c in cards] == list(DEFAULT_PICKS)

    def test_single_seed_returns_one_card(self, tmp_path, monkeypatch):
        # 隔离自动策展注册表：纯 CATALOG 下仅一种子，picks=2 也只返回 1 张
        monkeypatch.setattr(patternlib, "REGISTRY_PATH", tmp_path / "none.json")
        assert len(pattern_cards("三轴运动")) == 1


class TestCardContent:
    def test_cards_carry_st_and_summary(self):
        card = pattern_cards("定位")[0]
        assert card["summary"] and "--- POU" in card["st"]
        assert "prog_id" in card["st"]  # motion3axis 种子的身份常量

    def test_render_contains_st_fences(self):
        text = render_cards(pattern_cards("运动"))
        assert "```st" in text and "### 模式卡：motion3axis" in text

    def test_all_seeds_extractable(self):
        # 全部种子可提取（任一失败说明 src/plc 被破坏，模式库会显式抛错）
        for key, fname, _s, _t in CATALOG:
            cards = pattern_cards(key)
            assert cards, fname


class TestGenericOnly:
    """泛化验证口径：include_curated=False 时自动策展卡不参与选卡。"""

    def test_curated_excluded_when_generic_only(self, tmp_path, monkeypatch):
        import agent.patternlib as pl
        reg = tmp_path / "patterns.json"
        reg.write_text(json.dumps({"patterns": [
            {"key": "plotter_circle", "file": "plotter_circle.xml",
             "summary": "三轴绘图仪画圆", "tags": ["绘图", "画", "圆"]}]}, ensure_ascii=False),
            encoding="utf-8")
        monkeypatch.setattr(pl, "REGISTRY_PATH", reg)
        # 含策展卡：绘图类需求命中 plotter_circle
        with_curated = [c["key"] for c in pattern_cards("三轴绘图仪 画圆 绘制", picks=2)]
        assert "plotter_circle" in with_curated
        # 泛化口径：仅静态 CATALOG（motion3axis），策展卡被排除
        generic = [c["key"] for c in pattern_cards("三轴绘图仪 画圆 绘制", picks=2,
                                                   include_curated=False)]
        assert "plotter_circle" not in generic and "motion3axis" in generic
