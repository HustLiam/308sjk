#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PLCopen XML (IEC 61131-10) 结构校验器。

纯标准库实现，对 agent 生成的 XML 做部署前的第一道把关：
  1. 可解析、根命名空间属于 PLCopen TC6 家族
  2. 工程骨架完整（fileHeader / contentHeader / types / instances）
  3. 每个 POU：名称合法、类型受支持、接口存在、ST 本体非空
  4. 提取各 POU 的 ST 源码（供人工检查 / 回喂 agent / 人工比对）

用法:
    python src/pipeline/validate_xml.py [path/to/file.xml]
    不带参数时校验 src/plc/counter.xml
退出码: 0 = 通过, 1 = 发现问题（问题清单打印到 stdout）
"""

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PLCOPEN_FAMILY = "{http://www.plcopen.org/xml/"
XHTML_NS = "{http://www.w3.org/1999/xhtml}"
POU_TYPES = {"program", "functionBlock", "function"}
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate(path):
    """返回 (是否通过, 问题列表, {pou名: ST源码})。"""
    problems = []
    st_sources = {}

    try:
        tree = ET.parse(path)
    except ET.ParseError as e:
        return False, ["XML 无法解析: %s" % e], st_sources
    root = tree.getroot()

    if not root.tag.startswith(PLCOPEN_FAMILY) or not root.tag.endswith("}project"):
        problems.append("根元素应为 PLCopen 命名空间下的 <project>，实际为 %s" % root.tag)

    # 从根元素提取完整命名空间（tc6_0200 / tc6_0201 等版本均接受）
    ns_match = re.match(r"^\{([^}]+)\}", root.tag)
    ns = "{" + ns_match.group(1) + "}" if ns_match else ""

    def has(tag):
        return root.find(".//" + ns + tag) is not None

    for tag in ("fileHeader", "contentHeader", "types", "instances"):
        if not has(tag):
            problems.append("缺少工程骨架元素 <%s>" % tag)

    pous = root.findall(".//" + ns + "pou")
    if not pous:
        problems.append("未找到任何 <pou>（至少需要一个程序 POU）")

    for pou in pous:
        name = pou.get("name", "")
        if not IDENT_RE.match(name):
            problems.append("POU 名称非法: %r" % name)
        ptype = pou.get("pouType", "")
        if ptype not in POU_TYPES:
            problems.append("POU %s 的 pouType=%r 不受支持（允许: %s）"
                            % (name, ptype, sorted(POU_TYPES)))
        if pou.find(ns + "interface") is None:
            problems.append("POU %s 缺少 <interface>（变量声明）" % name)

        body = pou.find(".//" + ns + "ST")
        if body is None:
            problems.append("POU %s 缺少 ST 本体（本流水线约定只生成 ST 本体）" % name)
            continue
        text = "".join(body.itertext()).strip()
        if not text:
            problems.append("POU %s 的 ST 本体为空" % name)
        else:
            st_sources[name] = text
            if ";" not in text:
                problems.append("POU %s 的 ST 本体看起来不完整（没有分号）" % name)

        for var in pou.findall(".//" + ns + "variable"):
            vname = var.get("name", "")
            if not IDENT_RE.match(vname):
                problems.append("POU %s 中变量名非法: %r" % (name, vname))

    return (not problems), problems, st_sources


def main():
    default = Path(__file__).resolve().parents[2] / "src" / "plc" / "counter.xml"
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else default

    if not target.exists():
        print("文件不存在: %s" % target)
        return 1

    ok, problems, sources = validate(target)
    print("校验对象: %s" % target)
    if ok:
        print("结果: 通过")
        for name, st in sources.items():
            print("\n--- POU %s 的 ST 本体 ---" % name)
            print(st)
        return 0
    print("结果: 发现 %d 个问题" % len(problems))
    for i, p in enumerate(problems, 1):
        print("  %d. %s" % (i, p))
    return 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
