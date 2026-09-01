#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三方一致性检查器（gc 文档 §5 的实现，主方案 §3.2 前置校验第 3 步）。

对账三方的"变量身份"：
    ② plc_project.xml 的定位变量（带 AT 地址，统一 %Q 区）
    ① requirement_spec.io_list（三方唯一源头）
    ③ io_map.json（仿真侧产出，字段结构权威见主方案 §3.3）

规则：
  R1 xml2st 结构闸门先过（复用 lx 的 xml2st.parse，位宽/拒绝清单问题直接短路）；
  R2 名称集合双向一致：io_list 每条在定位变量表有逐字同名变量，反之亦然
     （XML 中不允许出现 io_list 未声明的对外变量——单一源头）；
  R3 类型匹配：BOOL↔BOOL；INT↔INT/UINT/WORD（lx 位宽表的字宽兼容集）；
  R4 地址不冲突：定位变量表内地址不得重复；
  R5 io_map 腿（提供了 io_map.json 才检查）：plc_var ⊆ io_list 且双向覆盖、
     dir/type 与 io_list 一致。

调用时机：编排器在生成后、仿真前（半环 = 部署前）。

用法:
    ok, problems = consistency_check("plcopen.xml", spec["io_list"], "io_map.json")
"""

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import xml2st  # noqa: E402  lx 侧契约实现（R1 复用）

ADDRESS_RE = xml2st.ADDRESS_RE

# R3：io_list 类型（BOOL/INT）-> ST 字宽兼容集（lx 文档 §3 位宽表）
TYPE_COMPAT = {
    "BOOL": {"BOOL"},
    "INT": {"INT", "UINT", "WORD"},
}
# 变量块种类（与 xml2st.parse 的支持范围一致；定位变量可出现在任一块）
VAR_BLOCK_TAGS = ("inputVars", "inOutVars", "outputVars", "localVars")


def _is_xml_text(xml_source):
    """True = 传入的是 XML 文本；False = 传入的是文件路径。"""
    return "<" in str(xml_source)[:512]


def _load_root(xml_source):
    """xml_source 为路径或 XML 文本，统一返回 ElementTree 根元素。"""
    if _is_xml_text(xml_source):
        return ET.fromstring(xml_source)
    return ET.parse(xml_source).getroot()


def extract_located_vars(xml_source):
    """从 PLCopen XML 提取定位变量表 [{name, addr, type}]。

    xml_source 可以是文件路径或 XML 文本。只认带 address 属性的变量——
    不带 AT 的是 POU 内部状态，不参与对外对账。
    """
    root = _load_root(xml_source)
    ns = xml2st._ns(root)
    located = []
    for pou in root.findall(".//" + ns + "pou"):
        for block in VAR_BLOCK_TAGS:
            for var in pou.findall(ns + "interface/" + ns + block + "/" + ns + "variable"):
                addr = var.get("address")
                if not addr:
                    continue
                type_el = var.find(ns + "type")
                vtype = None
                if type_el is not None:
                    for child in type_el:
                        tag = child.tag.split("}")[-1]
                        vtype = child.get("name") if tag == "derived" else tag
                        break
                located.append({"name": var.get("name", ""), "addr": addr, "type": vtype or "?"})
    return located


def _check_names_and_types(problems, located, io_list):
    """R2 + R3：名称双向一致、类型宽度匹配。"""
    xml_by_name = {}
    for var in located:
        if var["name"] in xml_by_name:
            problems.append("R4: 定位变量 %r 在 XML 中重复声明" % var["name"])
        xml_by_name[var["name"]] = var

    for point in io_list:
        name, spec_type = point.get("name"), point.get("type")
        var = xml_by_name.get(name)
        if var is None:
            problems.append("R2: io_list 变量 %r 在 XML 定位变量表中不存在（未生成或改名）" % name)
            continue
        compat = TYPE_COMPAT.get(spec_type)
        if compat is None:
            problems.append("R3: io_list 变量 %r 类型 %r 不在对外封闭集 {BOOL,INT}" % (name, spec_type))
        elif var["type"] not in compat:
            problems.append("R3: 变量 %r 类型不匹配——io_list=%s，XML=%s（%s 的位宽兼容集为 %s）"
                            % (name, spec_type, var["type"], spec_type, sorted(compat)))

    for var in located:
        if var["name"] not in {p.get("name") for p in io_list}:
            problems.append("R2: XML 定位变量 %r（%s）未在 io_list 声明——对外变量必须单一源头"
                            % (var["name"], var["addr"]))


def _check_addresses(problems, located):
    """R4：地址不冲突（%QX 与 %QW 分属线圈/寄存器两个编号空间，分别查重）。"""
    seen = {}
    for var in located:
        key = var["addr"]
        if key in seen:
            problems.append("R4: 地址 %s 被 %r 与 %r 重复占用" % (key, seen[key], var["name"]))
        else:
            seen[key] = var["name"]
        # 位套字的静默重叠（%QX0.0 落在 %QW0 字内）在 OpenPLC Modbus 缓冲区是两个空间，
        # 不算冲突；%QD 等非法宽度由 R1 的 xml2st 闸门拦截。


def _check_io_map(problems, io_map, io_list):
    """R5：io_map 腿对账（结构：plc_var/io_channel/bind/dir/type，主方案 §3.3）。"""
    if isinstance(io_map, dict) and isinstance(io_map.get("mappings"), list):
        entries = io_map["mappings"]
    elif isinstance(io_map, list):
        entries = io_map
    else:
        return _err_io_map_shape(problems, io_map)

    io_by_name = {p.get("name"): p for p in io_list}
    mapped = set()
    for idx, entry in enumerate(entries):
        path = "io_map[%d]" % idx
        if not isinstance(entry, dict) or "plc_var" not in entry:
            problems.append("R5: %s 缺少 plc_var 字段（结构见主方案 §3.3）" % path)
            continue
        plc_var = entry.get("plc_var")
        point = io_by_name.get(plc_var)
        if point is None:
            problems.append("R5: %s 的 plc_var %r 不在 io_list 中" % (path, plc_var))
            continue
        if entry.get("dir") is not None and entry["dir"] != point.get("dir"):
            problems.append("R5: %s 方向不一致——io_map=%r，io_list=%r" % (path, entry["dir"], point["dir"]))
        if entry.get("type") is not None and entry["type"] != point.get("type"):
            problems.append("R5: %s 类型不一致——io_map=%r，io_list=%r" % (path, entry["type"], point["type"]))
        if entry.get("io_channel") is None:
            problems.append("R5: %s 缺少 io_channel（Modbus 地址 / IO 镜像通道）" % path)
        bind = entry.get("bind")
        if not (isinstance(bind, dict) and bind.get("prim") and bind.get("quantity")):
            problems.append("R5: %s 的 bind 必须是 {prim, quantity}" % path)
        mapped.add(plc_var)

    for name in io_by_name:
        if name not in mapped:
            problems.append("R5: io_list 变量 %r 在 io_map 中无绑定记录" % name)


def _err_io_map_shape(problems, io_map):
    problems.append("R5: io_map 结构应为 {mappings: [...]} 或数组（主方案 §3.3），实际 %s"
                    % type(io_map).__name__)


def consistency_check(xml_source, io_list, io_map=None):
    """主入口。返回 (ok, problems)。

    xml_source: PLCopen XML 路径或文本；
    io_list:    requirement_spec.io_list；
    io_map:     dict / list / 文件路径；None = 仿真侧尚未产出，跳过该腿。
    """
    problems = []

    # R1：结构/位宽/拒绝清单闸门（lx 权威实现，问题原样返回；文本形态落临时
    # 文件走同一条 parse 代码路径，保证与 xml2st CLI 裁定完全一致）
    if _is_xml_text(xml_source):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-8") as fh:
            fh.write(xml_source)
        gate, _model = xml2st.parse(fh.name)
    else:
        gate, _model = xml2st.parse(xml_source)
    if gate:
        problems.extend("R1(xml2st): %s" % g for g in gate)
        return False, problems

    located = extract_located_vars(xml_source)
    _check_names_and_types(problems, located, io_list)
    _check_addresses(problems, located)

    if io_map is None:
        problems.append("SKIP: io_map 未提供（仿真侧尚未产出）——仅对账 XML ↔ io_list 两方")
    else:
        if isinstance(io_map, (str, Path)):
            try:
                io_map = json.loads(Path(io_map).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                return False, ["R5: io_map 无法读取/解析: %s" % exc]
        _check_io_map(problems, io_map, io_list)

    hard = [p for p in problems if not p.startswith("SKIP")]
    return not hard, problems
