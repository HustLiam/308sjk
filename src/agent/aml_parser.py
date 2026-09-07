#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AutomationML（IEC 62714 / CAEX）解析器 —— 架构 v2.0 的 ⓪ 模块（gc 拥有）。

主方案 §3.0 处理要点的落地：
  1. CAEX InstanceHierarchy → 设备树（拓扑，devices[].path/children + InternalLink 连接）；
  2. ExternalInterface（RefBaseClassPath 含 input/output）→ IO 点位 → requirement_spec.io_list；
  3. SystemUnitClass（RefBaseSystemUnitPath）→ 设备类型；Attribute → 设备参数；
  4. 运动学属性（axis_type/stroke_min/stroke_max/vmax/accel/...）→ 轴对象
     （linear / rotary_modulo / rotary_finite）；
  5. device_model.json 是 ① 的结构化锚点——io_list 初始值由 build_io_list() 填充，
     LLM 只做校验与补充，不从零猜测设备结构。

确定性保证（主方案 §3.0 明确"非 LLM"）：同一输入必产出逐字节相同的 device_model
（文档序遍历，输出不含任何时间戳/随机量）；带或不带 xmlns 的 CAEX 均可解析（按
localname 匹配，工程工具导出两种形态都存在）。

错误语义：结构错误（不是 CAEX / XML 不合法）抛 AMLParseError；内容问题（IO 重名、
地址冲突、方向不可判定、axis_type 非法…）不抛异常，收集进 problems 返回——调用方
决定拒绝还是带伤下传（编排器按闸门处理，问题文本可回喂）。

用法:
    from aml_parser import parse_aml, build_io_list
    model, problems = parse_aml("station.aml")          # problems 为空 = 通过
    io_items, pending = build_io_list(model)            # io_list 初始值 + 待补条目

CLI 入口: tools/aml_parser.py（gc 文档 §0 登记的交付物形态）。
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .spec_validator import IDENT_RE

DEVICE_MODEL_VERSION = "1.0.0-draft.1"

# 运动学轴类型封闭集（主方案 §3.0；与 lx《运动控制代码生成方案》的三类轴一致）
AXIS_TYPES = {"linear", "rotary_modulo", "rotary_finite"}

# 定位变量地址（lx 位宽契约：BOOL↔%QX 位、INT↔%QW 字，统一 %Q 区）
ADDR_RE = re.compile(r"^%([QI])([XW])(\d+)(?:\.(\d+))?$")

# 属性同义词表：AML 工程侧命名不统一，解析侧归一到 device_model 字段
STROKE_KEYS = ("stroke_min", "stroke_max")
ELECTRICAL_KEYS = {"voltage": ("voltage", "voltage_v", "voltage_V"),
                   "power": ("power", "power_w", "power_W")}
ADDR_ATTRS = ("address", "plc_address", "modbus_address")
SIGNAL_ATTRS = ("description", "signal", "desc")


class AMLParseError(Exception):
    """结构性错误：不是可识别的 CAEX 文档（与内容问题 problems 相区分）。"""


# ---------------- 基础工具 ----------------

def _local(tag):
    """去命名空间取 localname（CAEX 带/不带 xmlns 都按同一套标签解析）。"""
    return tag.rsplit("}", 1)[-1]


def _short(path):
    """类路径取末段作短名：'Lib/Gantry/Basic' → 'Basic'；空路径原样返回。"""
    return path.rsplit("/", 1)[-1] if path else path


def _children(el, name):
    return [c for c in el if _local(c.tag) == name]


def _first_child(el, name):
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _attr_value(el):
    """Attribute 元素的 <Value> 文本 → Python 值（int → float → bool → str）。"""
    node = _first_child(el, "Value")
    if node is None or not (node.text or "").strip():
        return None
    text = node.text.strip()
    if re.fullmatch(r"[+-]?\d+", text):
        return int(text)
    try:
        return float(text)
    except ValueError:
        pass
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    return text


def _el_attrs(el):
    """元素直接子 Attribute 的 {name: value}（本项目子集：扁平属性 + 单 Value）。"""
    out = {}
    for a in _children(el, "Attribute"):
        value = _attr_value(a)
        if value is not None:
            out[a.get("Name")] = value
    return out


# ---------------- IO 点位 ----------------

def _parse_io_interface(iface, device_path, problems):
    """识别 ExternalInterface 是否 IO 点位。返回 io_point 或 None（非 IO 接口）。

    判定规则（确定性，无猜测；关键词只看类路径**末段**——接口类名本身，
    避免库名误命中，如 'xxxInterfaceClassLib' 的 'int' 子串）：
      · 类名含 'input'/'output'（不区分大小写）→ IO 点位，方向即其语义；
      · 类型：digital/bool → BOOL，analog/word → INT；无此关键词时按地址
        %QX→BOOL / %QW→INT 推断，两处矛盾记 problem；
      · 方向关键词同时出现（如 'InputOutput'）视为不可判定，记 problem。
    """
    base = (iface.get("RefBaseClassPath") or "")
    seg_l = (_short(base) or "").lower()
    has_in, has_out = "input" in seg_l, "output" in seg_l
    if not (has_in or has_out):
        return None  # Mechanical/Flange 等非信号接口
    name = iface.get("Name")
    if has_in and has_out:
        problems.append("io[%s]: RefBaseClassPath %r 同时含 input/output，方向不可判定"
                        % (name, base))
        return None
    attrs = _el_attrs(iface)
    address = next((attrs[k] for k in ADDR_ATTRS if k in attrs), None)
    type_kw = "digital" in seg_l or "bool" in seg_l
    ana_kw = "analog" in seg_l or "word" in seg_l
    if type_kw and ana_kw:
        problems.append("io[%s]: 接口类名 %r 数字/模拟关键词并存，类型不可判定"
                        % (name, _short(base)))
        return None
    vtype = "BOOL" if type_kw else "INT" if ana_kw else None
    if address is not None:
        m = ADDR_RE.match(str(address))
        if not m:
            problems.append("io[%s]: 地址 %r 不符合 %%QXn.n / %%QWn 格式"
                            % (name, address))
        else:
            addr_type = "BOOL" if m.group(2) == "X" else "INT"
            if vtype is not None and vtype != addr_type:
                problems.append("io[%s]: 接口关键词指向 %s 但地址 %r 指向 %s，类型矛盾"
                                % (name, vtype, address, addr_type))
            vtype = vtype or addr_type
            if m.group(1) == "I":
                problems.append("io[%s]: 地址 %r 在 %%I 区——项目位宽契约统一 %%Q 区"
                                "（lx 文档 §3），请核对工程侧映射" % (name, address))
    if vtype is None:
        problems.append("io[%s]: 无 digital/analog 关键词也无合法地址，类型不可判定" % name)
        return None
    if not IDENT_RE.match(name or ""):
        problems.append("io[%s]: name 不是合法标识符（io_list 对账键，须与 ST 定位变量一致）"
                        % name)
    rng = None
    if "range_min" in attrs and "range_max" in attrs:
        rng = [attrs["range_min"], attrs["range_max"]]
        if not (isinstance(rng[0], (int, float)) and isinstance(rng[1], (int, float))
                and rng[0] < rng[1]):
            problems.append("io[%s]: range [%r,%r] 必须是 min<max 的数值" % (name, *rng))
            rng = None
    elif "range_min" in attrs or "range_max" in attrs:
        problems.append("io[%s]: range_min/range_max 必须成对出现" % name)
    if vtype == "BOOL" and rng is not None:
        problems.append("io[%s]: BOOL 点位不允许带量程" % name)
        rng = None
    signal = next((attrs[k] for k in SIGNAL_ATTRS if k in attrs), None)
    return {"name": name, "device": device_path,
            "dir": "input" if has_in else "output", "type": vtype,
            "address": address, "signal": signal, "range": rng,
            "unit": attrs.get("unit")}


# ---------------- 设备树遍历 ----------------

def _walk(ie, parent_path, ctx):
    """先序遍历 InternalElement，收集 devices / io_points / links / 接口索引。"""
    name = ie.get("Name")
    path = "%s/%s" % (parent_path, name) if parent_path else name
    role_req = _first_child(ie, "RoleRequirements")
    dev = {"name": name, "path": path,
           "type": _short(ie.get("RefBaseSystemUnitPath") or "") or None,
           "role": _short(role_req.get("Role") or "") if role_req is not None else None,
           "params": _el_attrs(ie), "electrical": {}, "io": [], "children": []}
    for key, names in ELECTRICAL_KEYS.items():
        for src in names:
            if dev["params"].get(src) is not None:
                dev["electrical"][key] = dev["params"][src]
                break
    ctx["devices"].append(dev)
    ctx["by_path"][path] = dev

    for child in ie:
        tag = _local(child.tag)
        if tag == "InternalElement":
            child_dev = _walk(child, path, ctx)
            dev["children"].append(child_dev["path"])
        elif tag == "ExternalInterface":
            iface_id = child.get("ID")
            if iface_id:
                ctx["ifaces"][iface_id] = (path, child.get("Name"))
            point = _parse_io_interface(child, path, ctx["problems"])
            if point is not None:
                if point["name"] in ctx["io_names"]:
                    ctx["problems"].append("io[%s]: 点位名重复" % point["name"])
                else:
                    ctx["io_names"].add(point["name"])
                if point["address"] is not None:
                    if point["address"] in ctx["io_addrs"]:
                        ctx["problems"].append("io[%s]: 地址 %s 与其他点位冲突"
                                               % (point["name"], point["address"]))
                    else:
                        ctx["io_addrs"].add(point["address"])
                ctx["io_points"].append(point)
                dev["io"].append(point["name"])
        elif tag == "InternalLink":
            ctx["links"].append({"name": child.get("Name"),
                                 "a_ref": child.get("RefPartnerSideA"),
                                 "b_ref": child.get("RefPartnerSideB")})
    return dev


def _resolve_links(ctx):
    links = []
    for link in ctx["links"]:
        ends = []
        for ref in (link["a_ref"], link["b_ref"]):
            end = ctx["ifaces"].get(ref)
            if end is None:
                ctx["problems"].append("link[%s]: 端点 %r 未在任意 ExternalInterface ID 中找到"
                                       % (link["name"], ref))
                ends = None
                break
            ends.append("%s#%s" % end)
        if ends:
            links.append({"name": link["name"], "a": ends[0], "b": ends[1]})
    return links


def _extract_axes(ctx):
    """运动学属性 → 轴对象（主方案 §3.0 要点 4）。axis_type 必须显式声明。"""
    axes = []
    for dev in ctx["devices"]:
        params = dev["params"]
        axis_type = params.get("axis_type")
        stroke = [params.get("stroke_min"), params.get("stroke_max")]
        has_motion = all(v is not None for v in stroke) or params.get("vmax") is not None
        if axis_type is None:
            if has_motion:
                ctx["problems"].append("axis[%s]: 声明了运动参数但缺 axis_type"
                                       "（linear|rotary_modulo|rotary_finite）" % dev["name"])
            continue
        if not isinstance(axis_type, str) or axis_type not in AXIS_TYPES:
            ctx["problems"].append("axis[%s]: axis_type %r 不在封闭集 %s"
                                   % (dev["name"], axis_type, sorted(AXIS_TYPES)))
            continue
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in stroke) \
                or not stroke[0] < stroke[1]:
            ctx["problems"].append("axis[%s]: stroke_min/stroke_max 必须是 min<max 的数值"
                                   % dev["name"])
            stroke = None
        axes.append({"axis": dev["name"], "device": dev["path"], "type": axis_type,
                     "stroke": stroke, "unit": params.get("unit"),
                     "vmax": params.get("vmax"), "accel": params.get("accel"),
                     "poswin": params.get("poswin"), "wrap": params.get("wrap")})
    return axes


# ---------------- 对外入口 ----------------

def parse_aml(source):
    """解析 AML 文件路径或 XML 文本。返回 (device_model, problems)。

    device_model 结构见 schemas/device_model.schema.json（gc 拥有）；problems 为空即
    通过，非空时模型仍尽量完整产出（best-effort），问题文本可直接进反馈包。
    """
    if isinstance(source, (str, Path)) and Path(source).is_file():
        text = Path(source).read_text(encoding="utf-8")
        display = Path(source).name
        from_file = True
    else:
        text = str(source)
        display = "<memory>"
        from_file = False
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise AMLParseError("XML 解析失败: %s" % exc)

    if _local(root.tag) != "CAEXFile":
        raise AMLParseError("根元素是 %r，不是 CAEXFile（IEC 62714 CAEX 容器）"
                            % _local(root.tag))
    hierarchy = _first_child(root, "InstanceHierarchy")
    if hierarchy is None:
        raise AMLParseError("CAEXFile 内无 InstanceHierarchy（设备树根）")

    ctx = {"devices": [], "io_points": [], "links": [], "ifaces": {}, "by_path": {},
           "io_names": set(), "io_addrs": set(), "problems": []}
    for ie in _children(hierarchy, "InternalElement"):
        _walk(ie, hierarchy.get("Name") or "", ctx)
    # 站级直接挂的接口/链接（不属任何设备）也纳入拓扑
    station = hierarchy.get("Name") or ""
    for iface in _children(hierarchy, "ExternalInterface"):
        iface_id = iface.get("ID")
        if iface_id:
            ctx["ifaces"][iface_id] = (station, iface.get("Name"))
        point = _parse_io_interface(iface, station, ctx["problems"])
        if point is not None:
            ctx["io_points"].append(point)
    ctx["links"].extend({"name": l.get("Name"), "a_ref": l.get("RefPartnerSideA"),
                         "b_ref": l.get("RefPartnerSideB")}
                        for l in _children(hierarchy, "InternalLink"))

    model = {
        "schema_version": DEVICE_MODEL_VERSION,
        "source": {"file": display,
                   "caex_schema_version": root.get("SchemaVersion"),
                   "from_file": from_file},
        "station": station,
        "devices": ctx["devices"],
        "io_points": ctx["io_points"],
        "topology": {"links": _resolve_links(ctx)},
        "kinematics": {"axes": _extract_axes(ctx)},
    }
    return model, ctx["problems"]


def build_io_list(model):
    """device_model.io_points → requirement_spec.io_list 初始值（主方案 §3.1）。

    返回 (io_items, pending)：io_items 可直接作为 spec 的 io_list 草稿（LLM 校验/
    补充的对象）；pending 列出尚不可通过契约校验、必须补充的条目（如 INT 缺量程）。
    """
    io_items, pending = [], []
    for point in model.get("io_points", []):
        item = {"name": point["name"], "dir": point["dir"], "type": point["type"],
                "range": point["range"],
                "device": point["signal"] or "%s#%s" % (point["device"], point["name"])}
        if point.get("unit") is not None:
            item["unit"] = point["unit"]
        if point["type"] == "INT" and point["range"] is None:
            pending.append("io[%s]: INT 缺量程 range（16 位寄存器域 [-32768,65535] 内，"
                           "供 io_map 定点换算）" % point["name"])
        io_items.append(item)
    return io_items, pending
