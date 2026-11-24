#!/usr/bin/env python
"""标准 AML 配置器：把分离的事实源组装为一份自洽的标准 AML（CAEX 2.15）。

问题背景：AML 输入不统一——几何/装配在资源 AML 里、IO 在 io.json、通讯在
adapter.json、运动约束在 motion.json、验证要求在 requirements.json（五处分裂）。
本配置器按 docs/AML-仿真验证配置.md 的六类信息定义，把它们合流成一份标准 AML，
使下游三方（⓪解析→io_list→PLC 变量、通讯层地址表、③b 仿真组装）从同一份文件
取数、名称天然统一。

输入形态二选一：--aml（打包基座）或 --parts-dir（散装四件：tree/geometry/poses/joints.json，
STL 自动转 DAE、单位归一 m——与基座 AML 等价，见 aml_parts.py）。

四阶段（确定性：同输入同输出，可 diff、可回归）：
  1. audit    审查五件套：引用存在性（component_id/source_file）、地址冲突、
              类型/方向枚举、换算/量程落 16 位域（契约①②）、字段完备性；
              errors 阻断、warnings 放行但记录进 report；
  2. normalize 补充缺省：无 scale 字量的 word 域 range、有 scale 的 INT16 满域换算、
              adapter/motion/requirements 默认值——每项补充显式登记；
  3. assemble 组装：信号接口挂 component_id 组件（address/scale/range/role/source_file/
              source_id 溯源逐字保留）、Dummy_SPS 端子模块（PortInterface 不进 io_list）、
              InternalLink 电气接线、PLC 通讯/运动属性、SimulationRequirements 节点；
  4. verify   自检：well-formed + 包内 CAEX 2.15 XSD 校验 + gc ⓪ 解析器回读 io_list
              与 io.json 逐字比对 + 结构统计。

用法：
  python tools/aml_configurator.py \
      --aml AML_RobotCell_en_public/AML_RobotCell_en/AmlDocument.aml \
      --io examples/robotcell/010RB_100.io.json \
      --adapter examples/robotcell/010RB_100.adapter.json \
      --motion examples/robotcell/010RB_100.motion.json \
      --requirements examples/robotcell/010RB_100.requirements.json \
      -o examples/aml/standard/010RB_100_standard.aml \
      --report workspace/aml_configurator/report.json

产物即"标准 AML"，下游用途见 README（⓪/②a/通讯/③b 四侧消费同一份文件）。
"""

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET

SIG_LIB = "AutomationMLInterfaceClassLib/AutomationMLBaseInterface"
PORT_LIB = SIG_LIB + "/PortInterface"
STD_FORMAT = "308sjk.standard_aml/1"

ROLE_DESC = {
    "target": "关节位置指令（CSP 目标值）",
    "actual": "关节位置反馈（实际值）",
    "enable": "驱动使能字",
    "feedback_sequence": "反馈序号（PLC 扫描计数，用于通讯活性判定）",
}
ADDR_RE_RULES = "%QXn.n / %QWn"
CHECK_KINDS = {"mechanical_joint", "fixed_mount", "joint_coupling", "io_binding"}


def _rel(p):
    return os.path.basename(p) if p else "-"


# ================================================================ 1. audit

def audit(base_root, io_items, adapter, motion, requirements, aml_path):
    """返回 (errors, warnings)。errors 非空则阻断组装。"""
    errors, warnings = [], []
    aml_dir = os.path.dirname(os.path.abspath(aml_path))

    comp_ids = {e.get("ID") for e in base_root.iter() if e.tag == "InternalElement"}

    seen_addr, seen_name = {}, {}
    for it in io_items:
        n = it.get("name", "<无名>")
        # 字段完备
        for k in ("name", "dir", "type", "address"):
            if k not in it:
                errors.append(f"io[{n}]: 缺必填字段 {k}")
        if errors and errors[-1].startswith(f"io[{n}]: 缺"):
            continue
        # 枚举
        if it["dir"] not in ("input", "output"):
            errors.append(f"io[{n}]: dir={it['dir']!r} 不在枚举 input/output")
        if it["type"] not in ("INT", "BOOL", "UINT", "WORD"):
            errors.append(f"io[{n}]: type={it['type']!r} 不在契约②位宽集 {{BOOL,INT,UINT,WORD}}")
        # 名称唯一
        if it["name"] in seen_name:
            errors.append(f"io[{n}]: 名称与 {seen_name[it['name']]} 重复")
        seen_name[it["name"]] = n
        # 地址格式 + 冲突
        a = it["address"]
        ok_addr = a.startswith("%QW") and a[3:].isdigit() or \
            (a.startswith("%QX") and "." in a and a[3:].replace(".", "").isdigit())
        if not ok_addr:
            errors.append(f"io[{n}]: address {a!r} 不符合 {ADDR_RE_RULES}（%QD 禁用）")
        if a in seen_addr:
            errors.append(f"io[{n}]: 地址 {a} 与 {seen_addr[a]} 冲突")
        seen_addr[a] = n
        # 引用存在性（component_id 必须在基座 AML）
        cid = it.get("component_id")
        if not cid:
            errors.append(f"io[{n}]: 缺 component_id（无法定位挂载组件）")
        elif cid not in comp_ids:
            errors.append(f"io[{n}]: component_id {cid} 不在基座 AML 中")
        # 溯源（source_file/source_id 成对；文件须在包内）
        sf, sid = it.get("source_file"), it.get("source_id")
        if (sf is None) != (sid is None):
            errors.append(f"io[{n}]: source_file/source_id 必须成对出现")
        if sf and not os.path.isfile(os.path.join(aml_dir, sf)):
            errors.append(f"io[{n}]: source_file {sf} 不在 AML 资源包内")
        # 换算
        dpc = it.get("deg_per_count")
        if dpc is not None and (not isinstance(dpc, (int, float)) or dpc <= 0):
            errors.append(f"io[{n}]: deg_per_count={dpc!r} 须为正数")
        # range 若显式给出须 min<max 且落 16 位域
        rng = it.get("range")
        if rng:
            if len(rng) != 2 or rng[0] >= rng[1]:
                errors.append(f"io[{n}]: range {rng} 须 min<max")
            else:
                in_signed = -32768 <= rng[0] and rng[1] <= 32767
                in_unsigned = 0 <= rng[0] and rng[1] <= 65535
                if not (in_signed or in_unsigned):
                    errors.append(f"io[{n}]: range {rng} 须完整落入 [-32768,32767] 或 [0,65535] 之一（契约① S2，跨域混合量程单个 %QW 无法承载）")
        else:
            warnings.append(f"io[{n}]: 无 range，将按 scale/word 域自动补充（见 normalized）")

    if not io_items:
        errors.append("io_list 为空")

    if adapter:
        if not (isinstance(adapter.get("cycle_hz"), (int, float)) and adapter["cycle_hz"] > 0):
            errors.append("adapter: cycle_hz 须为正数")
        if not (isinstance(adapter.get("command_timeout_s"), (int, float)) and adapter["command_timeout_s"] > 0):
            errors.append("adapter: command_timeout_s 须为正数")
        rt = adapter.get("runtime")
        if rt and not (rt.get("host") and rt.get("port")):
            errors.append("adapter: runtime 需同时含 host 与 port")
    else:
        warnings.append("adapter 未提供，通讯参数用默认值（见 normalized）")

    if motion:
        if not (isinstance(motion.get("max_speed_deg_s"), (int, float)) and motion["max_speed_deg_s"] > 0):
            errors.append("motion: max_speed_deg_s 须为正数")
    else:
        warnings.append("motion 未提供，运动约束用默认值（见 normalized）")

    if requirements:
        fmt = requirements.get("format", "")
        if not fmt.startswith("308sjk.simulation_requirements/"):
            errors.append(f"requirements: format {fmt!r} 非本项目仿真要求标识")
        for c in requirements.get("checks", []):
            if c.get("kind") not in CHECK_KINDS:
                errors.append(f"requirements: checks[].kind {c.get('kind')!r} 不在 {sorted(CHECK_KINDS)}")
    else:
        warnings.append("requirements 未提供，将注入模板默认（见 normalized）")

    return errors, warnings


# ================================================================ 2. normalize

DEFAULTS = {"cycle_hz": 30, "command_timeout_s": 2,
            "max_speed_deg_s": 30,
            "requirements": {
                "format": "308sjk.simulation_requirements/1",
                "description": "验证机构连接、指令响应及实际反馈（模板默认）",
                "position_tolerance_m": 0.002, "angle_tolerance_rad": 0.001,
                "response_timeout_s": 3,
                "allowed_simplifications": {"visual": "可省略外观细节，保留碰撞与检测尺寸"},
                "checks": [{"kind": "io_binding", "target": "*",
                            "description": "逐通道验证绑定和实际响应（模板默认）"}]}}


def normalize(io_items, adapter, motion, requirements):
    """补缺省并登记。返回 (io_items', adapter', motion', requirements', notes)。"""
    notes = []
    io2 = []
    for it in io_items:
        it = dict(it)
        if it["type"] == "BOOL":
            it.pop("range", None)          # BOOL 无量程（带量程会被 ⓪ 拒）
            io2.append(it)
            continue
        dpc = it.get("deg_per_count")
        if dpc:
            if "range" not in it:
                rmax = int(32767 * dpc)
                it["range"] = [-rmax, rmax]
                notes.append(f"io[{it['name']}]: 按 scale={dpc} 补 INT16 满域 range [{-rmax},{rmax}]")
        else:
            if "range" not in it:
                it["range"] = [0, 65535]
                notes.append(f"io[{it['name']}]: 无 scale 字量补 word 域 range [0,65535]")
        io2.append(it)

    if not adapter:
        adapter = {"cycle_hz": DEFAULTS["cycle_hz"],
                   "command_timeout_s": DEFAULTS["command_timeout_s"], "runtime": None}
        notes.append(f"adapter 用默认（cycle_hz={DEFAULTS['cycle_hz']}, timeout={DEFAULTS['command_timeout_s']}s）")
    if not motion:
        motion = {"max_speed_deg_s": DEFAULTS["max_speed_deg_s"]}
        notes.append(f"motion 用默认（max_speed_deg_s={DEFAULTS['max_speed_deg_s']}）")
    if not requirements:
        requirements = json.loads(json.dumps(DEFAULTS["requirements"]))
        notes.append("requirements 注入模板默认（容差/检查范围来自模板，测试页须标注非 AML 声明）")
    return io2, adapter, motion, requirements, notes


# ================================================================ 3. assemble

# CAEX 2.15 严格子元素顺序（InternalElementType sequence）
_ORDER = {"Attribute": 0, "ExternalInterface": 1, "InternalElement": 2,
          "SupportedRoleClass": 3, "MappingObject": 4, "InternalLink": 5,
          "RoleRequirements": 6}   # xs:extension 追加：RoleRequirements 在最后


def _insert_ordered(parent, child):
    """按 CAEX sequence 顺序插入（Attribute→ExternalInterface→InternalElement→InternalLink）。"""
    rank = _ORDER.get(child.tag, 5)
    for existing in parent:
        if _ORDER.get(existing.tag, 5) > rank:
            parent.insert(list(parent).index(existing), child)
            return child
    parent.append(child)
    return child


def _attr(parent, name, value, unit=None):
    a = ET.SubElement(parent, "Attribute", {"Name": name})
    if unit:
        a.set("Unit", unit)
    ET.SubElement(a, "Value").text = str(value)


def _signal_el(item):
    kind = "AnalogInput" if item["dir"] == "input" else "AnalogOutput"   # 接口类视角（与 ⓪/spec 一致）
    if item["type"] == "BOOL":
        kind = kind.replace("Analog", "Digital")
    iface = ET.Element("ExternalInterface", {
        "Name": item["name"], "ID": "io_" + item["name"],
        "RefBaseClassPath": SIG_LIB + "/" + kind})
    _attr(iface, "address", item["address"])
    _attr(iface, "description", ROLE_DESC.get(item.get("role"), item["name"]))
    if item.get("deg_per_count"):
        _attr(iface, "scale", item["deg_per_count"])
    if item.get("range"):
        _attr(iface, "range_min", item["range"][0])
        _attr(iface, "range_max", item["range"][1])
    for k in ("role", "source_file", "source_id"):
        if item.get(k):
            _attr(iface, k, item[k])
    return iface


def _requirements_el(requirements):
    r = ET.Element("Attribute", {"Name": "SimulationRequirements"})
    _attr(r, "format", requirements["format"])
    _attr(r, "description", requirements.get("description", ""))
    for k in ("position_tolerance_m", "angle_tolerance_rad", "response_timeout_s"):
        if k in requirements:
            _attr(r, k, requirements[k])
    simp = requirements.get("allowed_simplifications")
    if simp:
        s = ET.SubElement(r, "Attribute", {"Name": "allowed_simplifications"})
        for k, v in simp.items():
            _attr(s, k, v)
    checks = requirements.get("checks", [])
    if checks:
        cs = ET.SubElement(r, "Attribute", {"Name": "checks"})
        for i, c in enumerate(checks):
            ci = ET.SubElement(cs, "Attribute", {"Name": f"check_{i}"})
            _attr(ci, "kind", c["kind"])
            _attr(ci, "target", c.get("target", "*"))
            _attr(ci, "description", c.get("description", ""))
    return r


def assemble(base_tree, io_items, adapter, motion, requirements, inputs_meta):
    root = base_tree.getroot() if hasattr(base_tree, "getroot") else base_tree
    station = next((e for e in root.iter()
                    if e.tag == "InternalElement" and e.get("Name") == "Station"), None)
    ih = root.find("InstanceHierarchy")
    if ih is None:
        ih = station
    if station is None:                      # 散装模式：锚点=IH 下首个根组件
        station = ih.find("InternalElement")
        if station is None:
            raise RuntimeError("基座无任何组件（tree 为空？）")
    sps = next((e for e in root.iter()
                if e.tag == "InternalElement" and e.get("Name") == "Dummy_SPS"), None)
    if sps is None:                          # 散装模式：自动创建 PLC 组件
        sps = _insert_ordered(ih, ET.Element("InternalElement",
                                             {"ID": "plc_main", "Name": "PLC_Main"}))
    by_id = {e.get("ID"): e for e in root.iter() if e.tag == "InternalElement"}

    stats = {"signals": 0, "links": 0, "terminals": 0}
    ao = _insert_ordered(sps, ET.Element("InternalElement", {"Name": "AO_Module"}))
    ai = _insert_ordered(sps, ET.Element("InternalElement", {"Name": "AI_Module"}))
    do = _insert_ordered(sps, ET.Element("InternalElement", {"Name": "DO_Module"}))

    for it in io_items:
        comp = by_id[it["component_id"]]
        _insert_ordered(comp, _signal_el(it))
        stats["signals"] += 1
        # 端子（指令→AO/DO 模块，反馈→AI）
        mod = (do if it["type"] == "BOOL" and it["dir"] == "output"
               else ao if it["dir"] == "output" and it["type"] != "BOOL"
               else ai)
        prefix = {"AO_Module": "AO", "AI_Module": "AI", "DO_Module": "DO"}[mod.get("Name")]
        n = len(mod.findall("ExternalInterface"))
        p = _insert_ordered(mod, ET.Element("ExternalInterface", {
            "Name": "ch_" + it["name"], "ID": "port_" + it["name"],
            "RefBaseClassPath": PORT_LIB}))
        _attr(p, "channel", f"{prefix}{n}")
        stats["terminals"] += 1
        # 接线
        _insert_ordered(station, ET.Element("InternalLink", {
            "Name": "wire_" + it["name"],
            "RefPartnerSideA": "io_" + it["name"],
            "RefPartnerSideB": "port_" + it["name"]}))
        stats["links"] += 1

    rt = adapter.get("runtime") or {}
    _sp = ET.Element("Attribute", {"Name": "cycle_hz"})
    ET.SubElement(_sp, "Value").text = str(adapter["cycle_hz"])
    _insert_ordered(sps, _sp)
    for _n, _v in (("command_timeout_s", adapter["command_timeout_s"]),):
        _e = ET.Element("Attribute", {"Name": _n})
        ET.SubElement(_e, "Value").text = str(_v)
        _insert_ordered(sps, _e)
    for _n, _v in (("modbus_endpoint", f"{rt.get('host','')}:{rt.get('port','')}" if rt else "-"),
                   ("max_speed_deg_s", motion["max_speed_deg_s"])):
        _e = ET.Element("Attribute", {"Name": _n})
        ET.SubElement(_e, "Value").text = str(_v)
        _insert_ordered(sps, _e)

    # 2.15 XSD：InstanceHierarchy 仅允许 InternalElement 子元素 → 验证要求挂 Station
    _insert_ordered(station, _requirements_el(requirements))

    # 文档头注释（确定性：不含时间戳/绝对路径）
    note = ET.Comment(
        f"\n  标准 AML（{STD_FORMAT}）：由 aml_configurator 组装，确定性产物（同输入同输出）。\n"
        f"  输入五件套：base={_rel(inputs_meta['aml'])} io={_rel(inputs_meta['io'])} "
        f"adapter={_rel(inputs_meta['adapter'])} motion={_rel(inputs_meta['motion'])} "
        f"requirements={_rel(inputs_meta['requirements'])}\n"
        f"  IO {stats['signals']} 点（接线 {stats['links']}）| 通讯 cycle_hz={adapter['cycle_hz']} "
        f"timeout={adapter['command_timeout_s']}s | 运动 vmax={motion['max_speed_deg_s']}deg/s\n"
        f"  下游：⓪解析→io_list→②a PLC 变量 / 通讯地址表 / ③b 仿真组装。\n")
    root.insert(0, note)
    return stats


# ================================================================ 4. verify

def verify(out_path, io_items, xsd_path=None):
    """well-formed + XSD + ⓪ 回读逐字比对。返回 verify dict。"""
    v = {"well_formed": False, "xsd": None, "roundtrip": None}
    try:
        root = ET.parse(out_path).getroot()
        v["well_formed"] = True
        n_ie = sum(1 for e in root.iter() if e.tag == "InternalElement")
        n_if = sum(1 for e in root.iter() if e.tag == "ExternalInterface")
        n_lk = sum(1 for e in root.iter() if e.tag == "InternalLink")
        n_dae = sum(1 for a in root.iter() if a.get("Name") == "refURI"
                    and (a.findtext(".//Value") or "").endswith(".dae"))
        v["stats"] = {"components": n_ie, "interfaces": n_if,
                      "links": n_lk, "dae_refs": n_dae}
    except ET.ParseError as e:
        v["error"] = str(e)
        return v

    if xsd_path and os.path.isfile(xsd_path):
        try:
            import xmlschema
            schema = xmlschema.XMLSchema(xsd_path)
            schema.validate(out_path)
            v["xsd"] = {"ok": True, "schema": _rel(xsd_path)}
        except Exception as e:                       # noqa: BLE001
            v["xsd"] = {"ok": False, "error": str(e)[:500]}

    # ⓪ 回读：复用 gc 解析器（仓库内相对导入）
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, repo)
    from src.agent.aml_parser import parse_aml, build_io_list  # noqa: E402
    model, problems = parse_aml(out_path)
    io_rt, pending = build_io_list(model)
    src = {it["name"]: it for it in io_items}
    mism, missing = [], []
    for it in io_rt:
        s = src.get(it["name"])
        if not s:
            mism.append(f"{it['name']}: 回读出现 io.json 未声明的条目")
            continue
        if not (it["dir"] == s["dir"] and it["type"] == s["type"]
                and it.get("address") == s["address"]):
            mism.append(f"{it['name']}: AML=({it['dir']},{it['type']},{it.get('address')}) "
                        f"vs io.json=({s['dir']},{s['type']},{s['address']})")
    for n in src:
        if n not in {i["name"] for i in io_rt}:
            missing.append(n)
    inj_problems = [p for p in problems if "wire_" in p or "io[" in p]
    v["roundtrip"] = {"ok": not (mism or missing or pending or inj_problems),
                      "io_readback": len(io_rt), "expected": len(src),
                      "mismatches": mism, "missing": missing,
                      "pending": pending, "injected_problems": inj_problems}
    return v


# ================================================================ main

def main(argv=None):
    p = argparse.ArgumentParser(description="标准 AML 配置器（五件套 → 标准 AML）")
    p.add_argument("--aml", help="资源基座 AML（打包形态，几何/装配）")
    p.add_argument("--parts-dir", dest="parts_dir",
                   help="散装四件目录（tree/geometry/poses/joints.json，细颗粒形态）")
    p.add_argument("--io", required=True, help="io.json（IO 事实源）")
    p.add_argument("--adapter", help="adapter.json（通讯）")
    p.add_argument("--motion", help="motion.json（运动约束）")
    p.add_argument("--requirements", help="requirements.json（验证要求）")
    p.add_argument("-o", "--out", required=True, help="标准 AML 输出路径")
    p.add_argument("--report", help="组装报告 report.json 输出路径")
    p.add_argument("--xsd", help="CAEX XSD（缺省取基座同目录 CAEX_ClassModel_V2.15.xsd）")
    p.add_argument("--assets-dir", default=None,
                   help="散装模式 DAE 输出目录（缺省为输出 AML 同级 assets/）")
    args = p.parse_args(argv)
    if not (args.aml or args.parts_dir):
        p.error("--aml 与 --parts-dir 二选一")

    def _load(path):
        return json.load(open(path, encoding="utf-8")) if path else None

    io_items = _load(args.io)["io_list"]
    adapter, motion, requirements = _load(args.adapter), _load(args.motion), _load(args.requirements)

    if args.parts_dir:                       # 散装四件 → 基座树（含 STL→DAE）
        import aml_parts
        parts = aml_parts.load_parts(args.parts_dir)
        assets = args.assets_dir or os.path.join(
            os.path.dirname(os.path.abspath(args.out)) or ".", "assets")
        dae_refs = aml_parts.convert_geometries(parts, args.parts_dir, assets)
        base_root = aml_parts.build_base(parts, dae_refs)
        base_tree = base_root
        aml_for_audit = os.path.join(assets, "..")       # source_file 相对资产目录
    else:
        base_tree = ET.parse(args.aml)
        base_root = base_tree.getroot()
        if base_root.get("SchemaVersion") != "2.15":
            print(f"FAIL：基座 SchemaVersion={base_root.get('SchemaVersion')}，本配置器约定 2.15")
            return 1
        aml_for_audit = args.aml

    report = {"format": STD_FORMAT, "inputs": {
        "aml": _rel(args.aml), "io": _rel(args.io), "adapter": _rel(args.adapter),
        "motion": _rel(args.motion), "requirements": _rel(args.requirements)}}

    # ① audit
    errors, warnings = audit(base_root, io_items, adapter, motion, requirements, aml_for_audit)
    report["audit"] = {"errors": errors, "warnings": warnings}
    if errors:
        report["ok"] = False
        print(f"FAIL：审查未通过（{len(errors)} 项错误）：")
        for e in errors:
            print("  -", e)
        if args.report:
            os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
            json.dump(report, open(args.report, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
        return 1

    # ② normalize
    io2, adapter2, motion2, req2, notes = normalize(io_items, adapter, motion, requirements)
    report["normalized"] = notes

    # ③ assemble
    stats = assemble(base_tree, io2, adapter2, motion2, req2, {
        "aml": args.parts_dir or args.aml, "io": args.io, "adapter": args.adapter,
        "motion": args.motion, "requirements": args.requirements})
    report["assembly"] = stats
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    tree_out = base_tree if hasattr(base_tree, "write") else ET.ElementTree(base_tree)
    ET.indent(tree_out, space="	")
    tree_out.write(args.out, encoding="utf-8", xml_declaration=True)

    # ④ verify
    xsd = args.xsd or os.path.join(
        os.path.dirname(os.path.abspath(args.parts_dir if args.parts_dir else args.aml)),
        "CAEX_ClassModel_V2.15.xsd")
    v = verify(args.out, io2, xsd_path=xsd)
    report["verify"] = v
    rt_ok = v["roundtrip"]["ok"] if v.get("roundtrip") else False
    report["ok"] = bool(v["well_formed"] and rt_ok
                        and (v.get("xsd") is None or v["xsd"]["ok"]))

    if args.report:
        os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
        json.dump(report, open(args.report, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
    print(("OK " if report["ok"] else "FAIL ") + args.out)
    print(f"  信号 {stats['signals']}（端子 {stats['terminals']} / 接线 {stats['links']}）"
          f" | 审查 warnings {len(warnings)} | 补充 {len(notes)}")
    if v.get("roundtrip"):
        r = v["roundtrip"]
        print(f"  ⓪回读 {r['io_readback']}/{r['expected']} 逐字一致={r['ok']}"
              + ("" if v.get("xsd") is None else f" | XSD={'过' if v['xsd']['ok'] else '不过'}"))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
