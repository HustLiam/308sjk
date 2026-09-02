# -*- coding: utf-8 -*-
"""
ST 模式库单测：种子选取（关键词命中 + 兜底）与卡片渲染。
种子直接来自 src/plc/*.xml，lx 侧维护场景后内容自动跟随（无第二份拷贝）。
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from agent.patternlib import DEFAULT_PICKS, pattern_cards, render_cards  # noqa: E402


class TestSelection:
    def test_sorting_keywords(self):
        cards = pattern_cards("传送带分拣线，光电检测后气缸剔除")
        assert cards and cards[0]["key"] == "sorting"

    def test_pid_keywords(self):
        cards = pattern_cards("单容水箱液位连续 PID 调节，积分抗饱和")
        assert cards[0]["key"] == "pid_tank"

    def test_sequence_keywords(self):
        cards = pattern_cards("双气缸顺序动作循环控制")
        assert cards[0]["key"] == "cylinder_seq"

    def test_no_hit_falls_back_to_defaults(self):
        cards = pattern_cards("煮咖啡")
        assert [c["key"] for c in cards] == list(DEFAULT_PICKS)

    def test_two_cards_returned(self):
        assert len(pattern_cards("传送带分拣")) == 2


class TestCardContent:
    def test_cards_carry_st_and_summary(self):
        card = pattern_cards("计数")[0]
        assert card["summary"] and "--- POU" in card["st"]
        assert "cnt" in card["st"]  # counter 种子的定位变量

    def test_render_contains_st_fences(self):
        text = render_cards(pattern_cards("分拣"))
        assert "```st" in text and "### 模式卡：sorting" in text

    def test_all_six_seeds_extractable(self):
        # 六个种子全部可提取（任一失败说明 src/plc 被破坏，模式库会显式抛错）
        for key in ("counter", "sorting", "pump_alternation", "traffic_light", "cylinder_seq", "pid_tank"):
            from agent.patternlib import CATALOG
            fname = next(f for k, f, _s, _t in CATALOG if k == key)
            cards = pattern_cards(key)
            assert cards, fname
