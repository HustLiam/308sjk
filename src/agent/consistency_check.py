#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三方一致性检查器（gc 文档 §5 的实现，主方案 §3.2 前置校验第 3 步）。

对账三方的"变量身份"：
    ② plc_project.xml 的定位变量（带 AT 地址，统一 %Q 区）
    ① requirement_spec.io_list（三方唯一源头）
    ③ io_map（契约 v1.1：scene.spec.json 内嵌 ioEntry 数组 / csk 契约③ io_map.json）

规则：
  R1 xml2st 结构闸门先过（复用 lx 的 xml2st.parse，位宽/拒绝清单问题直接短路）；
  R2 名称集合双向一致：io_list 每条在定位变量表有逐字同名变量，反之亦然
     （XML 中不允许出现 io_list 未声明的对外变量——单一源头）；
  R3 类型匹配：BOOL↔BOOL；INT↔INT/UINT/WORD（lx 位宽表的字宽兼容集）；
  R4 地址不冲突：定位变量表内地址不得重复；
  R5 io_map 一致性检查（提供了 io_map 才检查）：每条 ioEntry 的 plc_var ∈ io_list 且
     dir/type 兼容（bool↔BOOL、float/analog/word↔INT）、bind 为 {asset, quantity}。
     覆盖为单向（io_map ⊆ io_list）——契约 v1.1 的 io_map 只含可绑物理通道，
     按钮/灯与 NC/诊断通道不进 io_map（见 contract/README 字段语义）。
  R6 ⓪ 侧地址检查（提供了 device_model 才检查）：AML 通道地址 ≡ XML 定位变量地址。
  R8 轴参数三方比对（提供了 device_model 才检查，生成方案 §6.2）：AML axis_objects
     ≡ XML 轴实例参数 ≡ scene.spec 轴参数——POSWIN（INTERP 类型初值）/ MC 调用
     动力学（go/rel 实例字面量 ≡ defaults）/ 行程（MC 越程界限 ≡ stroke，
     scene.travel ≡ stroke×scale）。v3 形态 XML（INTERP 带 VMAX 参数版）暂不支持，
     记 SKIP 待种子升 v4.0 后启用。

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
from .scene_gen import is_driver_channel  # noqa: E402  仿真侧驱动通道豁免判定（②b 定义）

ADDRESS_RE = xml2st.ADDRESS_RE

# R3：io_list 类型（BOOL/INT）-> ST 字宽兼容集（lx 文档 §3 位宽表）
TYPE_COMPAT = {
    "BOOL": {"BOOL"},
    "INT": {"INT", "UINT", "WORD"},
}
# 变量块种类（与 xml2st.parse 的支持范围一致；定位变量可出现在任一块）
VAR_BLOCK_TAGS = ("inputVars", "inOutVars", "outputVars", "localVars")

# R5：io_map 条目类型（契约 v1.1 ioEntry enum bool|float；契约③另含 analog|word）
# -> io_list 类型（BOOL/INT 封闭集）。模拟量工程值由桥侧定点/浮点承载（共同议题）。
IO_ENTRY_TYPE_COMPAT = {"bool": "BOOL", "float": "INT", "analog": "INT", "word": "INT"}


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
                if var.get("name", "") in xml2st.META_VARS:  # 契约 v1.1 元信息（prog_id）豁免
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
    """R5：io_map 对账（契约 v1.1 ioEntry：plc_var/dir/type{bool,float}/bind{asset,quantity}）。

    io_map 只覆盖**可绑物理通道**（契约 v1.1：hmi_panel 无注册 quantity，按钮/灯
    及 NC 设定值/状态字/速度指令通道不在其列），故覆盖检查为单向 io_map ⊆ io_list；
    反向覆盖（可绑通道不得静默丢失）由 ②b 生成器自检的路由覆盖规则保证
    （scene_gen.validate_scene_outputs V3）。
    """
    if isinstance(io_map, dict) and isinstance(io_map.get("entries"), list):
        entries = io_map["entries"]  # 契约③ io_map.json 形态（csk build 产物）
    elif isinstance(io_map, list):
        entries = io_map            # scene.spec 内嵌形态（gc ②b 产物）
    else:
        return _err_io_map_shape(problems, io_map)

    io_by_name = {p.get("name"): p for p in io_list}
    for idx, entry in enumerate(entries):
        path = "io_map[%d]" % idx
        if not isinstance(entry, dict) or "plc_var" not in entry:
            problems.append("R5: %s 缺少 plc_var 字段（ioEntry 见契约 contract/README）" % path)
            continue
        plc_var = entry.get("plc_var")
        point = io_by_name.get(plc_var)
        if point is None:
            if is_driver_channel(entry):
                pass  # 仿真侧驱动通道（②b 合成的 <axis>_cmd 位置指令）非 PLC 对外变量，
                      # 豁免 ⊆ 检查——待 RFC 把位置指令通道并入契约②后改为真实对账
            else:
                problems.append("R5: %s 的 plc_var %r 不在 io_list 中" % (path, plc_var))
            continue
        if entry.get("dir") is not None and entry["dir"] != point.get("dir"):
            problems.append("R5: %s 方向不一致——io_map=%r，io_list=%r" % (path, entry["dir"], point["dir"]))
        etype = entry.get("type")
        if etype is not None and IO_ENTRY_TYPE_COMPAT.get(etype) != point.get("type"):
            problems.append("R5: %s 类型不一致——io_map=%r 与 io_list %r 不兼容"
                            "（bool↔BOOL；float/analog/word↔INT）" % (path, etype, point.get("type")))
        bind = entry.get("bind")
        if not (isinstance(bind, dict) and bind.get("asset") and bind.get("quantity")):
            problems.append("R5: %s 的 bind 必须是 {asset, quantity}（契约 v1.1，bind.asset=场景资产 id）" % path)


def _err_io_map_shape(problems, io_map):
    problems.append("R5: io_map 结构应为 ioEntry 数组或 {entries: [...]}（契约 contract/README），实际 %s"
                    % type(io_map).__name__)


# ---------------- R8：轴参数三方比对（device_model v1.1，生成方案 §6.2） ----------------

_NUM = r"-?\d+(?:\.\d+)?"


def _iter_local(root, tag):
    """按 localname 迭代（PLCopen XML 根带 tc6_0201 命名空间，iter 不支持通配）。"""
    for el in root.iter():
        if isinstance(el.tag, str) and el.tag.rsplit("}", 1)[-1] == tag:
            yield el


def _pou_st_text(root, pou_name):
    """取 POU 的 ST 体文本（xhtml 叶子）；POU 或 body 不存在返回 None。"""
    for pou in _iter_local(root, "pou"):
        if pou.get("name") == pou_name:
            xh = pou.find(".//{*}xhtml")
            return xh.text if xh is not None else None
    return None


def _pou_var_init(root, pou_name, var_name):
    """取 POU 接口内变量的初值（simpleValue 数值化）；无则 None。"""
    for pou in _iter_local(root, "pou"):
        if pou.get("name") != pou_name:
            continue
        for var in _iter_local(pou, "variable"):
            if var.get("name") == var_name:
                sv = var.find(".//{*}simpleValue")
                try:
                    return float(sv.get("value")) if sv is not None else None
                except (TypeError, ValueError):
                    return None
    return None


def _interp_is_v3(root):
    """INTERP 带 VMAX 参数 = v3 形态（命令级动力学版为 v4.0，生成方案 §2.1）。"""
    for pou in _iter_local(root, "pou"):
        if pou.get("name") == "INTERP":
            return any(v.get("name") == "VMAX" for v in _iter_local(pou, "variable"))
    return False


def _call_args(st_text, call_name):
    """ST 体中 call_name(...) 的实参文本（跨行）；调用不存在返回 None。"""
    m = re.search(r"\b%s\s*\(([^)]*)\)" % re.escape(call_name), st_text)
    return m.group(1) if m else None


def _arg_number(args_text, arg_name):
    m = re.search(r"\b%s\s*:=\s*(%s)" % (re.escape(arg_name), _NUM), args_text)
    return float(m.group(1)) if m else None


def _check_axis_params(problems, root, device_model, scene):
    """R8：轴参数三方比对——AML axis_objects ≡ XML 轴实例参数 ≡ scene.spec 轴参数。

    AML 是唯一来源（RFC 2026-09-15）：比对 POSWIN（INTERP 共享 FB 的类型级初值，
    须与每轴一致）/ MC 调用动力学（go/rel 实例字面量 ≡ defaults）/ 行程
    （MC_MOVEABSOLUTE 体内越程界限 ≡ stroke——现为字面常量，仅全轴同行程可比，
    lx 参数化后改逐轴实例参数；scene.travel ≡ stroke×scale）。
    io 未绑定的轴不比对（⓪ 已记 problems）；scene 未提供时跳过 scene 侧。
    """
    from .scene_gen import _SCALE_BY_UNIT
    axes = [a for a in device_model.get("kinematics", {}).get("axes", []) if a.get("io")]
    if not axes:
        return
    if _interp_is_v3(root):
        problems.append("SKIP: R8 轴参数比对需 v4.0 形态 XML（检测到 INTERP 带 VMAX 参数"
                        "的 v3 形态，种子升 v4.0 后启用——生成方案 §6.4）")
        return

    def short_of(a):
        name = a.get("axis") or ""
        return name[:-len("_axis")] if name.endswith("_axis") else name

    prg = _pou_st_text(root, "PLC_PRG") or ""

    # POSWIN：INTERP 为共享 FB，类型级初值须与每根轴的 AML poswin 一致
    poswin = _pou_var_init(root, "INTERP", "POSWIN")
    if poswin is None:
        problems.append("SKIP: R8 POSWIN 比对——INTERP 无 POSWIN 初值（模板形态未预期）")
    else:
        for a in axes:
            if a.get("poswin") is not None and abs(poswin - a["poswin"]) > 1e-9:
                problems.append("R8: 轴 %r POSWIN 不一致——XML INTERP=%s，AML=%s"
                                "（poswin 是 PLC 与判定引擎共用容差源，生成方案 §6.1）"
                                % (a["axis"], poswin, a["poswin"]))

    # MC 调用动力学：go/rel 实例字面量 ≡ defaults
    for a in axes:
        s = short_of(a)
        for kind in ("go", "rel"):
            if kind == "rel" and "rel_d" not in (a["io"] or {}):
                continue  # 无 rel_d 角色（可选）→ 模板不展开 MC_MOVERELATIVE
            args = _call_args(prg, "%s_%s" % (kind, s))
            if args is None:
                problems.append("R8: XML 缺 %s_%s 调用（轴实例组不完整，模板 §2.2）"
                                % (kind, s))
                continue
            for arg, field in (("Velocity", "velocity"), ("Acceleration", "acceleration"),
                               ("Deceleration", "deceleration")):
                want = (a.get("defaults") or {}).get(field)
                got = _arg_number(args, arg)
                if got is None:
                    problems.append("R8: %s_%s 调用缺 %s 实参（动力学须来自 axis_object"
                                    " defaults，LLM 不得自编）" % (kind, s, arg))
                elif want is not None and abs(got - want) > 1e-9:
                    problems.append("R8: 轴 %r %s_%s 的 %s=%s 与 AML defaults.%s=%s 不一致"
                                    % (a["axis"], kind, s, arg, got, field, want))

    # 行程：MC_MOVEABSOLUTE 体内越程界限（字面常量，仅全轴同行程可比）
    ma = _pou_st_text(root, "MC_MOVEABSOLUTE") or ""
    m = re.search(r"Position\s*<\s*(%s)\s+OR\s+Position\s*>\s*(%s)" % (_NUM, _NUM), ma)
    strokes = [a.get("stroke") for a in axes]
    if m is None:
        problems.append("SKIP: R8 行程比对——MC_MOVEABSOLUTE 体内未找到越程界限字面量"
                        "（lx 参数化为实例参数后此处改读实例参数）")
    elif any(s is None for s in strokes) or len({tuple(s) for s in strokes}) > 1:
        problems.append("SKIP: R8 XML 侧行程比对——多轴行程不一，越程界限现为 FB 体内"
                        "共享常量（lx 参数化后逐轴比对）；scene 侧仍比对")
    else:
        lo, hi = float(m.group(1)), float(m.group(2))
        if abs(lo - strokes[0][0]) > 1e-9 or abs(hi - strokes[0][1]) > 1e-9:
            problems.append("R8: MC 越程界限 [%s,%s] 与 AML stroke %s 不一致"
                            % (lo, hi, strokes[0]))

    # scene 侧：gantry travel ≡ stroke×scale（SI 米）；speed ≡ x 轴 limits.vmax×scale
    if scene is None:
        return
    if isinstance(scene, (str, Path)):
        try:
            scene = json.loads(Path(scene).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            problems.append("R8: scene.spec 无法读取/解析: %s" % exc)
            return
    gantry = next((s for s in scene.get("assets", [])
                   if s.get("type") == "gantry_xyz"), None)
    if gantry is None:
        return  # scene 无 gantry 轴承载（多资产/无仿真）——scene 侧无比对对象
    p = gantry.get("params", {})
    for a in axes:
        s, stroke = short_of(a), a.get("stroke")
        travel = p.get("travel_%s" % s)
        if travel is None or stroke is None:
            continue
        scale = _SCALE_BY_UNIT.get(a.get("unit") or "%", 0.01)
        expect = round((stroke[1] - stroke[0]) * scale, 9)
        if abs(expect - travel) > 1e-6:
            problems.append("R8: 轴 %r scene travel_%s=%s 与 AML stroke×scale=%s 不一致"
                            "（scene.spec 轴参数须与 PLC 侧同源 device_model）"
                            % (a["axis"], s, travel, expect))
    speed = p.get("speed")
    x = next((a for a in axes if short_of(a) == "x"), None)
    if speed is not None and x is not None and (x.get("limits") or {}).get("vmax") is not None:
        scale = _SCALE_BY_UNIT.get(x.get("unit") or "%", 0.01)
        expect = round(x["limits"]["vmax"] * scale, 9)
        if abs(expect - speed) > 1e-6:
            problems.append("R8: scene speed=%s 与 AML x 轴 limits.vmax×scale=%s 不一致"
                            % (speed, expect))


def _check_device_addresses(problems, located, device_model):
    """R6：⓪ 侧地址检查——AML 通道地址是 io_map/验收脚本的共同语言，
    生成代码必须逐字遵循（名字↔地址双向对账）。画圆场景实证：名字/类型
    全对但地址自编一套，静态层全绿、Modbus 层才露馅。"""
    points = {p["name"]: p.get("address") for p in device_model.get("io_points", [])
              if p.get("address")}
    if not points:
        return
    xml_addr = {v["name"]: v["addr"] for v in located}
    for name, aml_addr in points.items():
        got = xml_addr.get(name)
        if got is None:
            continue  # R2 已报缺失，不重复
        if got != aml_addr:
            problems.append("R6: 变量 %r 地址 %s 与设备模型通道 %s 不一致"
                            "（AML/io_map/验收脚本按站约定表寻址，须逐字遵循）"
                            % (name, got, aml_addr))
    for name, addr in xml_addr.items():
        if name not in points and name != "prog_id":
            if addr in set(points.values()):
                problems.append("R6: 变量 %r（%s）占用了设备模型其他通道的地址"
                                % (name, addr))


def consistency_check(xml_source, io_list, io_map=None, device_model=None, scene=None):
    """主入口。返回 (ok, problems)。

    xml_source:   PLCopen XML 路径或文本；
    io_list:      requirement_spec.io_list；
    io_map:       dict / list / 文件路径；None = 仿真侧尚未产出，跳过 R5。
    device_model: ⓪ 的设备模型；提供时启用 R6 地址检查与 R8 轴参数比对。
    scene:        scene.spec dict / 文件路径；提供时 R8 增比对 scene 侧轴参数。
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
    if device_model is not None:
        _check_device_addresses(problems, located, device_model)
        _check_axis_params(problems, _load_root(xml_source), device_model, scene)

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
