# -*- coding: utf-8 -*-
"""
⓪ AML 解析器单测（架构 v2.0 / 主方案 §3.0）——确定性解析，无 LLM。

覆盖：基准示例逐字对齐 motion3axis（io_list 预填契约测试）/ 确定性（同输入同输出）/
xmlns 不敏感 / 内容问题收集（重名/地址冲突/方向不可判定/%I 区/BOOL 带量程/非法
axis_type/断链）/ 结构错误（非 CAEX、坏 XML）/ CLI 冒烟。
"""

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from agent.aml_parser import AMLParseError, build_io_list, parse_aml  # noqa: E402

AML = REPO / "examples" / "aml" / "motion3axis_station.aml"
SPEC = json.loads((REPO / "examples" / "specs" / "motion3axis.spec.json").read_text(encoding="utf-8"))


class TestBaseline:
    def test_example_parses_clean(self):
        model, problems = parse_aml(AML)
        assert problems == []
        assert model["station"] == "Motion3AxisStation"
        assert len(model["devices"]) == 4          # gantry + 三轴
        assert len(model["io_points"]) == 24
        assert [a["axis"] for a in model["kinematics"]["axes"]] == ["x_axis", "y_axis", "z_axis"]

    def test_io_dir_type_counts_match_spec(self):
        model, _ = parse_aml(AML)
        got = {}
        for p in model["io_points"]:
            got[(p["dir"], p["type"])] = got.get((p["dir"], p["type"]), 0) + 1
        exp = {}
        for p in SPEC["io_list"]:
            exp[(p["dir"], p["type"])] = exp.get((p["dir"], p["type"]), 0) + 1
        assert got == exp

    def test_topology_links_resolved(self):
        model, _ = parse_aml(AML)
        links = model["topology"]["links"]
        assert len(links) == 3
        assert links[0] == {"name": "mount_x",
                            "a": "Motion3AxisStation/gantry#x_mount",
                            "b": "Motion3AxisStation/gantry/x_axis#mech_flange"}

    def test_axis_params_match_plc_interp(self):
        # 运动学参数取自 motion3axis.xml 的 INTERP 实例参数（VMAX/ACCEL/POSWIN）
        model, _ = parse_aml(AML)
        x = model["kinematics"]["axes"][0]
        assert x["type"] == "linear" and x["stroke"] == [0.0, 100.0]
        assert x["vmax"] == 40.0 and x["accel"] == 80.0 and x["poswin"] == 2.0
        assert model["devices"][1]["electrical"] == {"voltage": 24.0, "power": 400.0}


class TestIOListPrefill:
    def test_prefill_equals_spec_io_list(self):
        # ⓪→① 数据流契约测试：示例 AML 的 io_list 预填须与基准 spec 的 io_list 逐字等价
        #（顺序无关——AML 按设备分组，spec 按功能分组）
        model, problems = parse_aml(AML)
        assert problems == []
        io_items, pending = build_io_list(model)
        assert pending == []
        by_name = {i["name"]: i for i in io_items}
        assert len(by_name) == len(SPEC["io_list"]) == 24
        for item in SPEC["io_list"]:
            got = by_name[item["name"]]
            assert got["dir"] == item["dir"] and got["type"] == item["type"]
            assert got["range"] == item["range"]
            assert got["device"] == item["device"]
            assert got.get("unit") == item.get("unit")

    def test_int_without_range_goes_pending(self):
        # INT 缺量程：预填仍产出条目（range=None），pending 提示须补充——LLM/人工补全对象
        model, _ = parse_aml(AML)
        model["io_points"][12]["range"] = None     # x_fb
        io_items, pending = build_io_list(model)
        assert io_items[12]["range"] is None
        assert any("x_fb" in note and "量程" in note for note in pending)


class TestDeterminism:
    def test_same_input_same_output(self):
        # 主方案 §3.0：确定性（非 LLM）——同输入必同输出（逐字节）
        text1 = json.dumps(parse_aml(AML)[0], ensure_ascii=False)
        text2 = json.dumps(parse_aml(AML)[0], ensure_ascii=False)
        assert text1 == text2

    def test_xmlns_invariant(self):
        # 工程工具导出的 AML 常带 xmlns，localname 解析须与无命名空间等价
        raw = AML.read_text(encoding="utf-8")
        namespaced = raw.replace(
            "<CAEXFile ",
            '<CAEXFile xmlns="http://www.dke.de/CAEX" ', 1)
        m1, p1 = parse_aml(raw)
        m2, p2 = parse_aml(namespaced)
        assert p1 == p2 == []
        m1["source"] = m2["source"] = {}            # file 名不同（<memory> vs 文件名）
        assert m1 == m2


class TestContentProblems:
    def test_duplicate_io_name_and_address(self, tmp_path):
        raw = AML.read_text(encoding="utf-8")
        dup = raw.replace('Name="fault_any"', 'Name="move_done"')
        _, problems = parse_aml(dup)
        assert any("重复" in p for p in problems)
        # 同地址冲突：x_fb 的 %QW0 改给 x_sp（%QW10）
        clash = raw.replace(
            '<Attribute Name="address"><Value>%QW0</Value></Attribute>',
            '<Attribute Name="address"><Value>%QW10</Value></Attribute>')
        _, problems = parse_aml(clash)
        assert any("地址 %QW10" in p and "冲突" in p for p in problems)

    def test_ambiguous_direction_rejected(self, tmp_path):
        raw = AML.read_text(encoding="utf-8")
        amb = raw.replace('RefBaseClassPath="308sjkInterfaceClassLib/Signal/DigitalInput"',
                          'RefBaseClassPath="308sjkInterfaceClassLib/Signal/InputOutput"', 1)
        _, problems = parse_aml(amb)
        assert any("方向不可判定" in p for p in problems)

    def test_i_area_address_flagged(self):
        raw = AML.read_text(encoding="utf-8")
        bad = raw.replace("%QX0.0", "%IX0.0")
        _, problems = parse_aml(bad)
        assert any("%I 区" in p for p in problems)

    def test_bool_with_range_flagged(self):
        raw = AML.read_text(encoding="utf-8")
        bad = raw.replace(
            '<Attribute Name="address"><Value>%QX0.0</Value></Attribute>\n        <Attribute Name="description"><Value>MC_Power 总使能（低=shutdown 命令）</Value></Attribute>',
            '<Attribute Name="address"><Value>%QX0.0</Value></Attribute>\n        <Attribute Name="description"><Value>MC_Power 总使能（低=shutdown 命令）</Value></Attribute>\n        <Attribute Name="range_min"><Value>0</Value></Attribute>\n        <Attribute Name="range_max"><Value>1</Value></Attribute>')
        _, problems = parse_aml(bad)
        assert any("BOOL" in p and "量程" in p for p in problems)

    def test_bad_axis_type_and_missing_axis_type(self):
        raw = AML.read_text(encoding="utf-8")
        bad = raw.replace("<Value>linear</Value>", "<Value>scara</Value>", 1)
        _, problems = parse_aml(bad)
        assert any("axis_type" in p and "封闭集" in p for p in problems)
        # 有运动参数但缺 axis_type → 显式问题，不猜测
        miss = raw.replace('<Attribute Name="axis_type"><Value>linear</Value></Attribute>\n        ', "", 1)
        _, problems = parse_aml(miss)
        assert any("缺 axis_type" in p for p in problems)

    def test_dangling_link_flagged(self):
        raw = AML.read_text(encoding="utf-8")
        bad = raw.replace('RefPartnerSideA="if_gx_mount"', 'RefPartnerSideA="if_nope"', 1)
        model, problems = parse_aml(bad)
        assert any("if_nope" in p for p in problems)
        assert len(model["topology"]["links"]) == 2   # 断链剔除，其余保留


class TestStructuralErrors:
    def test_not_caex_raises(self):
        try:
            parse_aml("<foo/>")
            assert False, "应抛 AMLParseError"
        except AMLParseError as exc:
            assert "CAEXFile" in str(exc)

    def test_malformed_xml_raises(self):
        try:
            parse_aml("<CAEXFile><unclosed>")
            assert False, "应抛 AMLParseError"
        except AMLParseError:
            pass

    def test_missing_hierarchy_raises(self):
        try:
            parse_aml('<CAEXFile FileName="x"/>')
            assert False, "应抛 AMLParseError"
        except AMLParseError as exc:
            assert "InstanceHierarchy" in str(exc)


class TestCLI:
    def test_cli_writes_model_and_exits_zero(self, tmp_path):
        out = tmp_path / "dm.json"
        proc = subprocess.run(
            [sys.executable, str(REPO / "tools" / "aml_parser.py"), str(AML), "-o", str(out)],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert proc.returncode == 0, proc.stderr
        assert json.loads(out.read_text(encoding="utf-8"))["station"] == "Motion3AxisStation"

    def test_cli_io_list_flag(self):
        proc = subprocess.run(
            [sys.executable, str(REPO / "tools" / "aml_parser.py"), str(AML), "--io-list"],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert proc.returncode == 0, proc.stderr
        items = json.loads(proc.stdout)
        assert len(items) == 24 and items[0]["name"] == "run"
