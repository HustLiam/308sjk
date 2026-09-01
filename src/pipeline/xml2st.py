#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PLCopen XML (IEC 61131-10) → ST 转换器。

本流水线的核心适配层：agent 产出 61131-10 XML（唯一源码），本模块校验其结构
并拼装为 OpenPLC v3 运行时直接可编译的 .st 文件（POU + CONFIGURATION）。

契约（超出契约的内容会被校验器拒绝）：
  - 只接受 ST 本体的 POU（LD/FBD/SFC 图形本体不支持）
  - POU 类型限 program / functionBlock / function
  - 对外接口 = 带 AT 地址的变量：%QX/%QW 映射 Modbus 线圈/保持寄存器
  - 不带 AT 地址的变量为 POU 内部状态，不对外发布

用法:
    python src/pipeline/xml2st.py <file.xml> [--check] [--out out.st]
    --check 只校验并打印 ST 本体；默认输出完整 .st
"""

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PLCOPEN_FAMILY = "{http://www.plcopen.org/xml/"
XHTML_NS = "{http://www.w3.org/1999/xhtml}"
POU_TYPES = {"program": "PROGRAM", "functionBlock": "FUNCTION_BLOCK", "function": "FUNCTION"}
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ADDRESS_RE = re.compile(r"^%[IQ]([WDX])?[0-9]+(\.[0-9]+)?$")

# 地址宽度 <-> 类型位宽匹配表（matiec 位宽不匹配会报晦涩错误；%QD/%ID 不映射
# Modbus 缓冲区，直接禁用，避免"编译通过但外部读不到"的静默故障）
ADDR_WIDTH = {"X": 1, "W": 16}
BIT_TYPES = {"BOOL"}
WORD_TYPES = {"INT", "UINT", "WORD"}
DWORD_TYPES = {"DINT", "UDINT", "DWORD"}  # 仅用于校验报错提示，不允许 AT %*D


def _check_addr_width(addr, vtype, ctx, vname, problems):
    if addr is None:
        return
    m = ADDRESS_RE.match(addr)
    width = m.group(1) or "X"
    if width == "D":
        problems.append("%s: 变量 %r 使用 %r —— 双字地址不映射 OpenPLC 的 Modbus 缓冲区"
                        "（保持寄存器/线圈只挂 %%QW/%%QX），请改用 INT@%%QW（32位值用两个连续 %%QW 拼接）"
                        % (ctx, vname, addr))
        return
    if width == "X" and vtype not in BIT_TYPES:
        problems.append("%s: 变量 %r 类型 %s 不能放位地址 %r（%s 只接受 BOOL）"
                        % (ctx, vname, vtype, addr, addr[:3]))
    if width == "W" and vtype not in WORD_TYPES:
        problems.append("%s: 变量 %r 类型 %s 与字地址 %r 位宽不匹配（%s 只接受 INT/UINT/WORD）"
                        % (ctx, vname, vtype, addr, addr[:3]))

# 支持的基本类型（XML 元素名 -> ST 类型名）
PRIMITIVE_TYPES = {
    "BOOL", "SINT", "INT", "DINT", "LINT", "USINT", "UINT", "UDINT", "ULINT",
    "REAL", "LREAL", "TIME", "DATE", "TIME_OF_DAY", "TOD", "DATE_AND_TIME", "DT",
    "STRING", "WSTRING", "BYTE", "WORD", "DWORD", "LWORD",
}


def _ns(root):
    m = re.match(r"^\{([^}]+)\}", root.tag)
    return "{" + m.group(1) + "}" if m else ""


def _var_type_text(var, ns, problems, ctx):
    """把 <type> 子树渲染成 ST 类型文本。"""
    type_el = var.find(ns + "type")
    if type_el is None:
        problems.append("%s: 变量 %r 缺少 <type>" % (ctx, var.get("name")))
        return None
    for child in type_el:
        tag = child.tag.split("}")[-1]
        if tag == "derived":
            return child.get("name", "")
        if tag in ("string", "wstring"):
            length = child.get("length", "")
            base = "STRING" if tag == "string" else "WSTRING"
            return "%s[%s]" % (base, length) if length else base
        if tag == "array":
            # <array><dimension x="1" y="5"/><baseType><INT/></baseType></array>
            dims = []
            for d in child.findall(ns + "dimension"):
                x, y = d.get("x", "0"), d.get("y", "0")
                dims.append("%s..%s" % (x, y))
            base_el = child.find(ns + "baseType")
            base = None
            if base_el is not None:
                for bc in base_el:
                    btag = bc.tag.split("}")[-1]
                    if btag == "derived":
                        base = bc.get("name", "")
                    elif btag in PRIMITIVE_TYPES:
                        base = btag
            if not dims or base is None:
                problems.append("%s: 变量 %r 的数组类型不完整" % (ctx, var.get("name")))
                return None
            return "ARRAY[%s] OF %s" % (", ".join(dims), base)
        if tag in PRIMITIVE_TYPES:
            return tag
        problems.append("%s: 变量 %r 的类型 %r 不受支持" % (ctx, var.get("name"), tag))
        return None
    problems.append("%s: 变量 %r 的 <type> 为空" % (ctx, var.get("name")))
    return None


def _initial_text(var, ns):
    init = var.find(ns + "initialValue/" + ns + "simpleValue")
    if init is None:
        return None
    return init.get("value")


def parse(xml_path):
    """解析并校验，返回 (problems, model)。model: {pous: [...], } """
    problems = []
    model = {"pous": []}
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as e:
        return ["XML 无法解析: %s" % e], model
    root = tree.getroot()

    if not root.tag.startswith(PLCOPEN_FAMILY) or not root.tag.endswith("}project"):
        problems.append("根元素应为 PLCopen 命名空间下的 <project>，实际为 %s" % root.tag)
        return problems, model
    ns = _ns(root)

    for tag in ("fileHeader", "contentHeader", "types", "instances"):
        if root.find(".//" + ns + tag) is None:
            problems.append("缺少工程骨架元素 <%s>" % tag)

    pous = root.findall(".//" + ns + "pou")
    if not pous:
        problems.append("未找到任何 <pou>（至少需要一个程序 POU）")

    # ---- 防静默丢失：未支持的构造一律显式拒绝 ----
    for dt in root.findall(".//" + ns + "dataTypes/" + ns + "dataType"):
        problems.append("不支持 <dataTypes> 中的自定义类型 %r（类型请用基本类型/数组/已定义 FB）"
                        % dt.get("name", "?"))
    for bad in ("action", "method", "property", "transition", "step"):
        for el in root.findall(".//" + ns + bad):
            problems.append("不支持 POU 内的 <%s>（%r）——请把逻辑写进 ST 本体"
                            % (bad, el.get("name", "?")))
    for pv in root.findall(".//" + ns + "persistentVars"):
        problems.append("不支持 <persistentVars>（持久变量块）")
    for cfg in root.findall(".//" + ns + "configuration"):
        if len(cfg):
            problems.append("不支持 <configuration> 内容（任务/资源由流水线模板统一装配）")

    # 变量块种类 -> (ST 关键字, 是否受支持)
    VAR_BLOCKS = [
        ("inputVars", "VAR_INPUT", True),
        ("inOutVars", "VAR_IN_OUT", True),
        ("outputVars", "VAR_OUTPUT", True),
        ("localVars", "VAR", True),
        ("externalVars", "externalVars", False),
        ("temporaryVars", "temporaryVars", False),
        ("tempVars", "tempVars", False),
    ]

    has_program = False
    for pou in pous:
        name = pou.get("name", "")
        ctx = "POU %s" % name
        if not IDENT_RE.match(name):
            problems.append("%s 名称非法: %r" % (ctx, name))
        ptype = pou.get("pouType", "")
        if ptype not in POU_TYPES:
            problems.append("%s 的 pouType=%r 不受支持（允许: %s）"
                            % (ctx, ptype, sorted(POU_TYPES)))
            continue
        if ptype == "program":
            has_program = True

        body = pou.find(".//" + ns + "ST")
        if body is None:
            problems.append("%s 缺少 ST 本体（本流水线只支持 ST 本体）" % ctx)
            continue
        st_body = "".join(body.itertext()).strip()
        if not st_body:
            problems.append("%s 的 ST 本体为空" % ctx)
            continue

        iface_lines = []
        for xml_tag, st_kw, supported in VAR_BLOCKS:
            for vb in pou.findall(ns + "interface/" + ns + xml_tag):
                if not supported:
                    problems.append("%s: 不支持变量块 <%s>" % (ctx, xml_tag))
                    continue
                kw = st_kw
                if vb.get("constant", "false") in ("true", "1"):
                    kw += " CONSTANT"
                if vb.get("retain", "false") in ("true", "1"):
                    kw += " RETAIN"
                if vb.get("persistent", "false") in ("true", "1"):
                    problems.append("%s: 不支持 PERSISTENT 限定符（matiec 限制）" % ctx)
                decls_plain = []   # 普通变量
                decls_located = []  # 带 AT 地址的定位变量
                for var in vb.findall(ns + "variable"):
                    vname = var.get("name", "")
                    if not IDENT_RE.match(vname):
                        problems.append("%s 中变量名非法: %r" % (ctx, vname))
                        continue
                    vtype = _var_type_text(var, ns, problems, ctx)
                    if vtype is None:
                        continue
                    addr = var.get("address")
                    if addr is not None and not ADDRESS_RE.match(addr):
                        problems.append("%s 中变量 %r 的地址 %r 不合法（形如 %%QW0/%%QX0.1）"
                                        % (ctx, vname, addr))
                    elif addr is not None:
                        _check_addr_width(addr, vtype, ctx, vname, problems)
                    init = _initial_text(var, ns)
                    parts = ["    " + vname]
                    if addr:
                        parts.append("AT " + addr)
                    parts.append(": " + vtype)
                    if init is not None:
                        parts.append(":= " + init)
                    (decls_located if addr else decls_plain).append(" ".join(parts) + ";")
                # matiec 语法怪癖：同一个 VAR 块内，FB 实例等普通声明与带 AT 的
                # 定位声明混放会报 invalid variable(s) declaration——必须分块
                for decls in (decls_plain, decls_located):
                    if decls:
                        iface_lines.append("  " + kw)
                        iface_lines.extend(decls)
                        iface_lines.append("  END_" + st_kw)

        ret_type = ""
        if ptype == "function":
            rt = pou.find(ns + "interface/" + ns + "returnType")
            if rt is not None:
                for child in rt:
                    tag = child.tag.split("}")[-1]
                    if tag == "derived":
                        ret_type = child.get("name", "")
                    elif tag in PRIMITIVE_TYPES:
                        ret_type = tag

        model["pous"].append({
            "name": name, "kw": POU_TYPES[ptype], "ret": ret_type,
            "iface": iface_lines, "body": st_body,
        })

    if not problems and not has_program:
        problems.append("至少需要一个 pouType=program 的 POU")
    return problems, model


def to_st(model):
    """把 parse() 的 model 拼装为 OpenPLC 可编译的 .st 文本。"""
    chunks = []
    for pou in model["pous"]:
        head = "FUNCTION %s" % pou["name"] if pou["kw"] == "FUNCTION" and not pou["ret"] \
            else "%s %s%s" % (pou["kw"], pou["name"],
                              " : " + pou["ret"] if pou["ret"] else "")
        chunks.append(head)
        chunks.extend(pou["iface"])
        chunks.append(pou["body"])
        chunks.append("END_" + pou["kw"])
        chunks.append("")
    chunks.append("CONFIGURATION Config0")
    chunks.append("  RESOURCE Res0 ON PLC")
    chunks.append("    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);")
    chunks.append("    PROGRAM instance0 WITH task0 : PLC_PRG;")
    chunks.append("  END_RESOURCE")
    chunks.append("END_CONFIGURATION")
    return "\n".join(chunks) + "\n"


def extract_st_bodies(xml_path):
    """校验并提取各 POU 的 ST 本体（供回喂 agent / 人工检查）。"""
    problems, model = parse(xml_path)
    return problems, {p["name"]: p["body"] for p in model["pous"]}


def convert(xml_path):
    """一步到位：校验 + 转 .st。返回 (ok, st_text, problems)。"""
    problems, model = parse(xml_path)
    if problems or not model["pous"]:
        return False, None, problems
    return True, to_st(model), problems


def main():
    import argparse
    parser = argparse.ArgumentParser(description="PLCopen XML -> ST converter/validator")
    parser.add_argument("xml", help="PLCopen XML 文件路径")
    parser.add_argument("--check", action="store_true", help="只校验并打印 ST 本体")
    parser.add_argument("--out", default=None, help="输出 .st 路径（默认打印到 stdout）")
    args = parser.parse_args()

    if not Path(args.xml).exists():
        print("文件不存在: %s" % args.xml)
        return 1

    if args.check:
        problems, sources = extract_st_bodies(args.xml)
        if problems:
            print("校验发现 %d 个问题:" % len(problems))
            for i, p in enumerate(problems, 1):
                print("  %d. %s" % (i, p))
            return 1
        print("校验通过")
        for name, st in sources.items():
            print("\n--- POU %s ---" % name)
            print(st)
        return 0

    ok, st_text, problems = convert(args.xml)
    if not ok:
        print("校验发现 %d 个问题:" % len(problems))
        for i, p in enumerate(problems, 1):
            print("  %d. %s" % (i, p))
        return 1
    if args.out:
        Path(args.out).write_text(st_text, encoding="utf-8")
        print("已生成: %s" % args.out)
    else:
        sys.stdout.write(st_text)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
