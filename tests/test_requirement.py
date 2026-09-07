# -*- coding: utf-8 -*-
"""
① 需求理解模块单测（requirement.py——模板模式与 LLM 模式的锚定/修复回路，
用假 client 模拟，不依赖网络与 API Key）。
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from agent.aml_parser import parse_aml, build_io_list  # noqa: E402
from agent.requirement import (  # noqa: E402
    RequirementUnderstander, _derive_task_id, extract_json)
from agent.spec_validator import validate_requirement_spec  # noqa: E402

PLOTTER_AML = REPO / "examples" / "aml" / "plotter3axis_station.aml"


def plotter_model():
    model, problems = parse_aml(PLOTTER_AML)
    assert problems == []
    return model


def _prefill_spec(io_items, task_id="plotter3axis"):
    """用预填 io_list 构造一份合法 spec（假 LLM 的正确回复）。"""
    return {
        "schema_version": "1.0.0-draft.3",
        "task_id": task_id,
        "task_goal": "三轴绘图仪：抬笔→定位→落笔画正方形→抬笔回中心（测试用）",
        "io_list": io_items,
        "constraints": [{"id": "C1", "kind": "interlock", "desc": "落笔期间禁止手动定位"}],
        "acceptance": [{"id": "AC1", "desc": "仿真健康无发散", "type": "sim_health"}],
    }


class TestExtractJson:
    def test_fenced(self):
        text = '说明\n```json\n{"a": 1}\n```\n尾巴'
        assert json.loads(extract_json(text)) == {"a": 1}

    def test_bare_with_nested_braces_in_strings(self):
        text = '前置 {"a": {"b": "包含}花括号"}, "c": [1, 2]} 后置'
        assert extract_json(text).startswith("{") and json.loads(extract_json(text))["a"]["b"] == "包含}花括号"

    def test_not_found(self):
        assert extract_json("没有对象") is None


class TestTemplateMode:
    def test_valid_spec_from_device_model(self):
        model = plotter_model()
        u = RequirementUnderstander(client=None)
        out = u.understand("三轴绘图仪绘制正方形", device_model=model)
        assert out["spec"] is not None
        assert validate_requirement_spec(out["spec"]) == []
        io_items, pending = build_io_list(model)
        assert out["spec"]["io_list"] == io_items  # io_list = 预填原样
        assert pending == []
        assert out["report"]["mode"] == "template"

    def test_task_id_derivation(self):
        assert _derive_task_id({"station": "Plotter3AxisStation"}) == "plotter3axis"
        assert _derive_task_id({"station": "Motion3AxisStation"}) == "motion3axis"
        assert _derive_task_id(None) == "task"
        assert _derive_task_id({"station": "123站"}) == "task"  # 非法派生回退


class FakeClient:
    """按脚本吐回复的假 LLM（测修复回路）。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat_completions(self, payload):
        self.calls.append(payload)
        return {"choices": [{"message": {"content": self.replies.pop(0)}}]}


class TestLLMMode:
    def test_anchor_ok_one_round(self):
        model = plotter_model()
        io_items, _ = build_io_list(model)
        spec = _prefill_spec(io_items)
        client = FakeClient(["```json\n%s\n```" % json.dumps(spec, ensure_ascii=False)])
        out = RequirementUnderstander(client=client).understand(
            "三轴绘图仪绘制正方形", device_model=model)
        assert out["spec"] is not None
        assert out["report"]["rounds"] == 1
        assert out["spec"]["io_list"] == io_items

    def test_anchor_violation_repaired_second_round(self):
        model = plotter_model()
        io_items, _ = build_io_list(model)
        bad = _prefill_spec([dict(i) for i in io_items])
        bad["io_list"][0]["name"] = "power_on"                # 改名 → 锚定违规
        bad["io_list"].append({"name": "invented", "dir": "input",  # 发明 IO
                               "type": "BOOL", "device": "x"})
        good = _prefill_spec(io_items)
        client = FakeClient([
            "```json\n%s\n```" % json.dumps(bad, ensure_ascii=False),
            "```json\n%s\n```" % json.dumps(good, ensure_ascii=False),
        ])
        out = RequirementUnderstander(client=client).understand(
            "三轴绘图仪绘制正方形", device_model=model)
        assert out["spec"] is not None and out["report"]["rounds"] == 2
        assert any("禁止发明" in p or "必须逐字保留" in p or "不得删减" in p
                   for h in out["report"]["history"] for p in h["problems"])

    def test_max_rounds_exhausted(self):
        model = plotter_model()
        io_items, _ = build_io_list(model)
        bad = _prefill_spec([dict(i) for i in io_items])
        bad["task_goal"] = "短"                                # 校验失败
        client = FakeClient(["```json\n%s\n```" % json.dumps(bad, ensure_ascii=False)] * 3)
        out = RequirementUnderstander(client=client, max_rounds=3).understand(
            "需求", device_model=model)
        assert out["spec"] is None and len(out["report"]["history"]) == 3
