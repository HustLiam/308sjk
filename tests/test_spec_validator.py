# -*- coding: utf-8 -*-
"""
requirement_spec 校验器单测（契约①，gc 侧）。

基准样例 = examples/specs/motion3axis.spec.json（与 src/plc/motion3axis.xml 的定位变量
逐字对齐，兼作一致性检查器的对照样例）。反例覆盖 Schema 结构规则 + 语义规则 S1~S4。
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from agent.spec_validator import validate_requirement_spec  # noqa: E402

SPEC_PATH = REPO / "examples" / "specs" / "motion3axis.spec.json"


def load_spec():
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


class TestMotion3AxisExample:
    def test_valid(self):
        assert validate_requirement_spec(load_spec()) == []

    def test_acceptance_four_closed_types_present(self):
        spec = load_spec()
        types = {a["type"] for a in spec["acceptance"]}
        assert types == {"event_delay", "region_containment", "forbidden_state", "sim_health"}


class TestStructureRules:
    def test_missing_top_field(self):
        spec = load_spec()
        del spec["task_goal"]
        assert any("task_goal" in p for p in validate_requirement_spec(spec))

    def test_bad_task_id(self):
        spec = load_spec()
        spec["task_id"] = "9Bad-ID"
        assert any("task_id" in p for p in validate_requirement_spec(spec))

    def test_bad_schema_version(self):
        spec = load_spec()
        spec["schema_version"] = "v1"
        assert any("schema_version" in p for p in validate_requirement_spec(spec))

    def test_empty_io_list(self):
        spec = load_spec()
        spec["io_list"] = []
        assert any("io_list" in p for p in validate_requirement_spec(spec))

    def test_empty_acceptance(self):
        spec = load_spec()
        spec["acceptance"] = []
        assert any("acceptance" in p for p in validate_requirement_spec(spec))

    def test_io_type_closed_set(self):
        spec = load_spec()
        spec["io_list"][0]["type"] = "REAL"  # REAL 只允许 POU 内部计算
        assert any("REAL 仅限" in p or "BOOL|INT" in p for p in validate_requirement_spec(spec))

    def test_io_dir_closed_set(self):
        spec = load_spec()
        spec["io_list"][0]["dir"] = "in"
        assert any("dir" in p for p in validate_requirement_spec(spec))

    def test_acceptance_type_closed(self):
        spec = load_spec()
        spec["acceptance"][0]["type"] = "max_overshoot"
        problems = validate_requirement_spec(spec)
        assert any("封闭四类" in p for p in problems)

    def test_bad_ac_id(self):
        spec = load_spec()
        spec["acceptance"][0]["id"] = "准则1"
        assert any("AC1/AC2" in p for p in validate_requirement_spec(spec))


class TestSemanticRules:
    def test_s1_duplicate_io_name(self):
        spec = load_spec()
        spec["io_list"].append(dict(spec["io_list"][0]))  # start_btn 重复
        assert any("重复" in p and "io_list" in p for p in validate_requirement_spec(spec))

    def test_s2_int_requires_range(self):
        spec = load_spec()
        idx = next(i for i, p in enumerate(spec["io_list"]) if p["name"] == "x_fb")
        spec["io_list"][idx]["range"] = None  # x_fb: INT 无量程
        assert any("INT 必须带 range" in p for p in validate_requirement_spec(spec))

    def test_s2_int_range_order(self):
        spec = load_spec()
        idx = next(i for i, p in enumerate(spec["io_list"]) if p["name"] == "x_fb")
        spec["io_list"][idx]["range"] = [100, 0]
        assert any("min<max" in p for p in validate_requirement_spec(spec))

    def test_s2_bool_forbids_range(self):
        spec = load_spec()
        spec["io_list"][0]["range"] = [0, 1]  # start_btn: BOOL 带量程
        assert any("BOOL 不允许带 range" in p for p in validate_requirement_spec(spec))

    def test_s3_signal_must_exist_in_io_list(self):
        spec = load_spec()
        spec["acceptance"][0]["from"]["signal"] = "pe1_detected"  # 不在 io_list
        assert any("不在 io_list" in p for p in validate_requirement_spec(spec))

    def test_s4_time_threshold_100ms(self):
        spec = load_spec()
        spec["acceptance"][0]["value"] = 0.05  # 50ms < 100ms 下限
        assert any("100ms" in p for p in validate_requirement_spec(spec))

    def test_event_delay_missing_field(self):
        spec = load_spec()
        del spec["acceptance"][0]["op"]
        assert any("缺少必填字段 'op'" in p for p in validate_requirement_spec(spec))

    def test_region_containment_center_shape(self):
        spec = load_spec()
        idx = next(i for i, a in enumerate(spec["acceptance"]) if a["type"] == "region_containment")
        spec["acceptance"][idx]["region_center"] = [1.0, 2.0]  # 只有两个分量
        assert any("region_center" in p for p in validate_requirement_spec(spec))

    def test_forbidden_state_equals_type(self):
        spec = load_spec()
        spec["acceptance"][2]["when"]["equals"] = "yes"
        assert any("equals" in p for p in validate_requirement_spec(spec))


class TestLoadAndValidate:
    def test_file_not_found(self):
        from agent.spec_validator import load_and_validate
        spec, problems = load_and_validate(REPO / "nope.json")
        assert spec is None and problems

    def test_good_file(self):
        from agent.spec_validator import load_and_validate
        spec, problems = load_and_validate(SPEC_PATH)
        assert spec["task_id"] == "motion3axis_demo" and problems == []
