# -*- coding: utf-8 -*-
"""
轨迹规划模块单测（trajectory.py——确定性几何：方/圆步表、参数确认、
prompt 注入文本；与验收脚本的站标准几何逐字对齐）。
"""

import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from agent.trajectory import (  # noqa: E402
    DEFAULTS, goal_text, missing_params, plan_circle, plan_square,
    plan_with_confirm, summarize_for_prompt)


class TestSquare:
    def test_station_geometry(self):
        """站标准：中心 (50,50) 边长 60 → 20..80，四边闭合后回中心。"""
        t = plan_square()
        steps = t["steps"]
        assert len(steps) == 8
        assert steps[0]["to"] == [20, 20, 10]        # 抬笔定位起点
        assert steps[1]["to"] == [20, 20, 0]         # 落笔
        assert [s["to"] for s in steps[2:6]] == [
            [80, 20, 0], [80, 80, 0], [20, 80, 0], [20, 20, 0]]   # 四边+闭合
        assert steps[6]["to"] == [20, 20, 10]        # 抬笔
        assert steps[7]["to"] == [50, 50, 10]        # 回中心

    def test_custom_params(self):
        t = plan_square(center=[60, 60], size=40)
        assert t["steps"][0]["to"] == [40, 40, 10]
        assert t["steps"][3]["to"] == [80, 80, 0]

    def test_out_of_range_rejected(self):
        try:
            plan_square(size=250)
            assert False, "越程应报错"
        except ValueError as exc:
            assert "行程" in str(exc)


class TestCircle:
    def test_station_geometry(self):
        """站标准：圆心 (50,50) r25，起点 (50,25)，24 段，全部在圆周上。"""
        t = plan_circle()
        steps = t["steps"]
        assert len(steps) == 5
        assert steps[0]["to"] == [50, 25, 10]        # 抬笔定位圆周起点
        assert steps[1]["to"] == [50, 25, 0]         # 落笔
        sweep = steps[2]
        wp = sweep["waypoints"]
        assert len(wp) == 24 and wp[0] == [50, 25, 0]
        for k, (x, y, z) in enumerate(wp):           # 每点距圆心≈半径（0.1 舍入容差）
            d = math.hypot(x - 50, y - 50)
            assert abs(d - 25) < 0.1, "k=%d 距圆心 %s" % (k, d)
            assert z == 0
        assert steps[3]["to"] == [50, 25, 10]        # 抬笔
        assert steps[4]["to"] == [50, 50, 10]        # 回圆心

    def test_segments_floor(self):
        try:
            plan_circle(segments=4)
            assert False, "段数过少应报错"
        except ValueError:
            pass


class TestConfirmFlow:
    def test_missing_detection(self):
        assert missing_params("square", {}) == ["center", "size"]
        assert missing_params("circle", {"radius": 20}) == ["center", "segments"]
        try:
            missing_params("triangle", {})
            assert False, "不支持的形状应报错"
        except ValueError:
            pass

    def test_no_confirm_adopts_defaults(self):
        t = plan_with_confirm("circle", {})          # 非交互：站标准值补齐
        assert t["params"] == DEFAULTS["circle"]

    def test_confirm_callback_overrides(self):
        seen = []

        def ask(questions):
            seen.extend(q["key"] for q in questions)
            return {"radius": 20}                    # 只答半径
        t = plan_with_confirm("circle", {}, confirm=ask)
        assert "radius" in seen
        assert t["params"]["radius"] == 20 and t["params"]["center"] == [50, 50]


class TestPromptText:
    def test_summarize_contains_geometry(self):
        text = summarize_for_prompt(plan_square())
        assert "20,20,10" in text and "80,80,0" in text
        assert "plot_done" in text and "权威" in text
        circ = summarize_for_prompt(plan_circle())
        assert "SIN" in circ and "k=00 (50,25,0)" in circ and "k=23" in circ

    def test_goal_text_for_understanding(self):
        assert "中心 (50,50)" in goal_text(plan_square())
        assert "半径 25" in goal_text(plan_circle())
