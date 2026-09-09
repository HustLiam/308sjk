# -*- coding: utf-8 -*-
"""
三方一致性检查器单测（gc 文档 §5：定位变量 ≡ io_list ≔ io_map）。

基准：src/plc/motion3axis.xml（已验收交付物）对照 examples/specs/motion3axis.spec.json
的 io_list——两方逐字对齐是所有用例的起点，反例在其上做最小变异。
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from agent.consistency_check import consistency_check, extract_located_vars  # noqa: E402

MOTION_XML = REPO / "src" / "plc" / "motion3axis.xml"
SPEC = json.loads((REPO / "examples" / "specs" / "motion3axis.spec.json").read_text(encoding="utf-8"))
IO_LIST = SPEC["io_list"]


def io_entries_from(io_list, **overrides):
    """按契约 v1.1 ioEntry 生成与 io_list 对齐的条目（bind 为占位，R5 不查注册表）。"""
    tmap = {"BOOL": "bool", "INT": "float"}
    entries = [
        {"plc_var": p["name"], "dir": p["dir"], "type": tmap.get(p["type"], "float"),
         "bind": {"asset": "a_%d" % idx, "quantity": "q"}}
        for idx, p in enumerate(io_list)
    ]
    for idx, patch in overrides.get("patches", []):
        entries[idx].update(patch)
    if "drop" in overrides:
        entries = [e for i, e in enumerate(entries) if i != overrides["drop"]]
    return entries


class TestExtractLocatedVars:
    def test_motion3axis_has_32_external_vars(self):
        located = extract_located_vars(MOTION_XML)
        assert len(located) == 32  # 33 个定位变量 - prog_id（契约 v1.1 元信息豁免）；2026-09-09 PLCopen MC 对齐 +8
        by_name = {v["name"]: v for v in located}
        assert by_name["run"] == {"name": "run", "addr": "%QX0.0", "type": "BOOL"}
        assert by_name["x_fb"] == {"name": "x_fb", "addr": "%QW0", "type": "INT"}
        assert by_name["x_sw"] == {"name": "x_sw", "addr": "%QW6", "type": "WORD"}

    def test_internal_vars_not_extracted(self):
        located = extract_located_vars(MOTION_XML)
        names = {v["name"] for v in located}
        assert "ax_x" not in names and "x_cw" not in names  # FB 实例/内部信号


class TestTwoPartyCheck:
    def test_motion_spec_matches_xml(self):
        ok, problems = consistency_check(MOTION_XML, IO_LIST)
        assert ok, problems
        assert any(p.startswith("SKIP: io_map") for p in problems)  # 仿真侧未产出→提示跳过

    def test_xml_text_input_same_as_path(self):
        xml_text = MOTION_XML.read_text(encoding="utf-8")
        ok1, p1 = consistency_check(MOTION_XML, IO_LIST)
        ok2, p2 = consistency_check(xml_text, IO_LIST)
        assert ok1 == ok2 == True and p1 == p2

    def test_r2_var_renamed_in_xml(self):
        xml_text = MOTION_XML.read_text(encoding="utf-8").replace('"x_fb"', '"x_enc"')
        ok, problems = consistency_check(xml_text, IO_LIST)
        assert not ok
        assert any("R2" in p and "x_fb" in p for p in problems)      # io_list 侧缺
        assert any("R2" in p and "x_enc" in p for p in problems)    # XML 侧多

    def test_r4_duplicate_address(self):
        xml_text = MOTION_XML.read_text(encoding="utf-8").replace('address="%QX0.4"', 'address="%QX0.0"')
        ok, problems = consistency_check(xml_text, IO_LIST)
        assert not ok and any("R4" in p and "%QX0.0" in p for p in problems)

    def test_r3_spec_type_outside_closed_set(self):
        io_list = [dict(p) for p in IO_LIST]
        io_list[2]["type"] = "UINT"  # x_fb：io_list 封闭集外的类型
        ok, problems = consistency_check(MOTION_XML, io_list)
        assert any("R3" in p and "封闭集" in p for p in problems)

    def test_r1_width_gate_short_circuits(self):
        xml_text = MOTION_XML.read_text(encoding="utf-8").replace(
            '<variable name="x_fb" address="%QW0">\n              <type><INT /></type>',
            '<variable name="x_fb" address="%QD0">\n              <type><DINT /></type>')
        ok, problems = consistency_check(xml_text, IO_LIST)
        assert not ok and any("R1(xml2st)" in p for p in problems)


class TestIoMapLeg:
    """R5：契约 v1.1 ioEntry（子集覆盖语义——io_map ⊆ io_list，按钮/灯等非物理通道不在其列）。"""

    def test_aligned_entries_pass(self):
        ok, problems = consistency_check(MOTION_XML, IO_LIST, io_entries_from(IO_LIST))
        assert ok, problems
        assert not any(p.startswith("SKIP") for p in problems)

    def test_subset_coverage_is_legal(self):
        """io_map 只含物理通道子集是合法形态（契约 v1.1：按钮/灯不进 io_map）。"""
        ok, problems = consistency_check(MOTION_XML, IO_LIST, io_entries_from(IO_LIST)[:3])
        assert ok, problems

    def test_r5_direction_mismatch(self):
        io_map = io_entries_from(IO_LIST, patches=[(14, {"dir": "input"})])  # x_sw 方向反转
        ok, problems = consistency_check(MOTION_XML, IO_LIST, io_map)
        assert any("R5" in p and "方向不一致" in p for p in problems)

    def test_r5_type_mismatch(self):
        io_map = io_entries_from(IO_LIST, patches=[(14, {"type": "bool"})])  # INT 通道标 bool
        ok, problems = consistency_check(MOTION_XML, IO_LIST, io_map)
        assert any("R5" in p and "类型不一致" in p for p in problems)

    def test_r5_plc_var_not_in_io_list(self):
        io_map = io_entries_from(IO_LIST, patches=[(0, {"plc_var": "ghost_var"})])
        ok, problems = consistency_check(MOTION_XML, IO_LIST, io_map)
        assert any("R5" in p and "ghost_var" in p for p in problems)

    def test_r5_bind_must_be_asset_quantity(self):
        io_map = io_entries_from(IO_LIST, patches=[(0, {"bind": {"prim": "/World/run"}})])
        ok, problems = consistency_check(MOTION_XML, IO_LIST, io_map)
        assert any("R5" in p and "bind" in p for p in problems)

    def test_contract3_entries_shape_accepted(self):
        """契约③ io_map.json 形态（{entries: [...]}，csk build 产物）同规则对账。"""
        wrapped = {"io_map_version": "1.0.0-draft.1", "entries": io_entries_from(IO_LIST)}
        ok, problems = consistency_check(MOTION_XML, IO_LIST, wrapped)
        assert ok, problems

    def test_io_map_from_file_path(self, tmp_path):
        path = tmp_path / "io_map.json"
        path.write_text(json.dumps(io_entries_from(IO_LIST)), encoding="utf-8")
        ok, problems = consistency_check(MOTION_XML, IO_LIST, path)
        assert ok, problems


class TestR6DeviceAddresses:
    """R6：⓪ 侧地址腿——AML 通道地址 ≡ XML 定位变量地址（画圆场景实证）。"""

    MODEL_AML = REPO / "examples" / "aml" / "plotter3axis_station.aml"
    SPEC = json.loads((REPO / "examples" / "specs" / "plotter3axis.spec.json")
                      .read_text(encoding="utf-8"))

    def _model(self):
        from agent.aml_parser import parse_aml
        model, problems = parse_aml(self.MODEL_AML)
        assert problems == []
        return model

    def test_matching_addresses_pass(self):
        ok, problems = consistency_check(
            REPO / "src" / "plc" / "plotter3axis.xml", self.SPEC["io_list"],
            device_model=self._model())
        assert ok, problems

    def test_diverged_addresses_caught(self, tmp_path):
        # 把 x_sp 挪到错误地址 → R6 必须抓到
        text = (REPO / "src" / "plc" / "plotter3axis.xml").read_text(encoding="utf-8")
        broken = text.replace('name="x_sp" address="%QW10"', 'name="x_sp" address="%QW3"')
        assert broken != text
        bad = tmp_path / "bad_addr.xml"
        bad.write_text(broken, encoding="utf-8")
        ok, problems = consistency_check(bad, self.SPEC["io_list"], device_model=self._model())
        assert not ok
        assert any(p.startswith("R6") and "x_sp" in p for p in problems)
