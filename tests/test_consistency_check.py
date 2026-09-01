# -*- coding: utf-8 -*-
"""
三方一致性检查器单测（gc 文档 §5：定位变量 ≡ io_list ≔ io_map）。

基准：src/plc/sorting.xml（已验收交付物）对照 examples/specs/sorting.spec.json
的 io_list——两方逐字对齐是所有用例的起点，反例在其上做最小变异。
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from agent.consistency_check import consistency_check, extract_located_vars  # noqa: E402

SORTING_XML = REPO / "src" / "plc" / "sorting.xml"
SPEC = json.loads((REPO / "examples" / "specs" / "sorting.spec.json").read_text(encoding="utf-8"))
IO_LIST = SPEC["io_list"]


def io_map_from(io_list, **overrides):
    """按主方案 §3.3 结构生成一份与 io_list 对齐的 io_map。"""
    mappings = [
        {"plc_var": p["name"], "io_channel": "ch%d" % idx,
         "bind": {"prim": "/World/%s" % p["name"], "quantity": "state"},
         "dir": p["dir"], "type": p["type"]}
        for idx, p in enumerate(io_list)
    ]
    for idx, patch in overrides.get("patches", []):
        mappings[idx].update(patch)
    if "drop" in overrides:
        mappings = [m for i, m in enumerate(mappings) if i != overrides["drop"]]
    return {"mappings": mappings}


class TestExtractLocatedVars:
    def test_sorting_has_ten_external_vars(self):
        located = extract_located_vars(SORTING_XML)
        assert len(located) == 10
        by_name = {v["name"]: v for v in located}
        assert by_name["start_btn"] == {"name": "start_btn", "addr": "%QX0.0", "type": "BOOL"}
        assert by_name["total_cnt"] == {"name": "total_cnt", "addr": "%QW10", "type": "INT"}

    def test_internal_vars_not_extracted(self):
        located = extract_located_vars(SORTING_XML)
        names = {v["name"] for v in located}
        assert "edge_total" not in names and "pusher_delay" not in names  # FB 实例=内部


class TestTwoPartyCheck:
    def test_sorting_spec_matches_xml(self):
        ok, problems = consistency_check(SORTING_XML, IO_LIST)
        assert ok, problems
        assert any(p.startswith("SKIP: io_map") for p in problems)  # 仿真侧未产出→提示跳过

    def test_xml_text_input_same_as_path(self):
        xml_text = SORTING_XML.read_text(encoding="utf-8")
        ok1, p1 = consistency_check(SORTING_XML, IO_LIST)
        ok2, p2 = consistency_check(xml_text, IO_LIST)
        assert ok1 == ok2 == True and p1 == p2

    def test_r2_var_renamed_in_xml(self):
        xml_text = SORTING_XML.read_text(encoding="utf-8").replace('"pusher"', '"kicker"')
        ok, problems = consistency_check(xml_text, IO_LIST)
        assert not ok
        assert any("R2" in p and "pusher" in p for p in problems)      # io_list 侧缺
        assert any("R2" in p and "kicker" in p for p in problems)      # XML 侧多

    def test_r4_duplicate_address(self):
        xml_text = SORTING_XML.read_text(encoding="utf-8").replace('address="%QX1.1"', 'address="%QX1.0"')
        ok, problems = consistency_check(xml_text, IO_LIST)
        assert not ok and any("R4" in p and "%QX1.0" in p for p in problems)

    def test_r3_spec_type_outside_closed_set(self):
        io_list = [dict(p) for p in IO_LIST]
        io_list[6]["type"] = "UINT"  # io_list 封闭集外的类型
        ok, problems = consistency_check(SORTING_XML, io_list)
        assert any("R3" in p and "封闭集" in p for p in problems)

    def test_r1_width_gate_short_circuits(self):
        xml_text = SORTING_XML.read_text(encoding="utf-8").replace(
            '<variable name="total_cnt" address="%QW10">\n              <type><INT /></type>',
            '<variable name="total_cnt" address="%QD10">\n              <type><DINT /></type>')
        ok, problems = consistency_check(xml_text, IO_LIST)
        assert not ok and any("R1(xml2st)" in p for p in problems)


class TestIoMapLeg:
    def test_aligned_io_map_passes(self):
        ok, problems = consistency_check(SORTING_XML, IO_LIST, io_map_from(IO_LIST))
        assert ok, problems
        assert not any(p.startswith("SKIP") for p in problems)

    def test_r5_missing_binding(self):
        io_map = io_map_from(IO_LIST, drop=6)  # pusher 无绑定
        ok, problems = consistency_check(SORTING_XML, IO_LIST, io_map)
        assert not ok and any("R5" in p and "pusher" in p for p in problems)

    def test_r5_direction_mismatch(self):
        io_map = io_map_from(IO_LIST, patches=[(6, {"dir": "input"})])  # pusher 方向反转
        ok, problems = consistency_check(SORTING_XML, IO_LIST, io_map)
        assert any("R5" in p and "方向不一致" in p for p in problems)

    def test_r5_plc_var_not_in_io_list(self):
        io_map = io_map_from(IO_LIST, patches=[(0, {"plc_var": "ghost_var"})])
        ok, problems = consistency_check(SORTING_XML, IO_LIST, io_map)
        assert any("R5" in p and "ghost_var" in p for p in problems)

    def test_io_map_from_file_path(self, tmp_path):
        path = tmp_path / "io_map.json"
        path.write_text(json.dumps(io_map_from(IO_LIST)), encoding="utf-8")
        ok, problems = consistency_check(SORTING_XML, IO_LIST, path)
        assert ok, problems
