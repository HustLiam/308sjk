#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
⓪ AutomationML 解析器 CLI（架构 v2.0，主方案 §3.0 / gc 文档 §0）。

用法:
    python tools/aml_parser.py examples/aml/motion3axis_station.aml     # 校验+打印 device_model
    python tools/aml_parser.py input.aml -o device_model.json          # 落盘
    python tools/aml_parser.py input.aml --io-list                     # 打印 io_list 预填草稿

退出码：0 = 通过；1 = 内容问题（problems 非空）；2 = 结构错误（不是 CAEX）。
"""

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from agent.aml_parser import AMLParseError, build_io_list, parse_aml  # noqa: E402


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="⓪ AML 解析器（CAEX → device_model.json，确定性非 LLM）")
    parser.add_argument("aml", help="AutomationML 文件路径（IEC 62714 CAEX）")
    parser.add_argument("-o", "--output", default=None, help="device_model.json 落盘路径（缺省打印到 stdout）")
    parser.add_argument("--io-list", action="store_true", help="打印 requirement_spec.io_list 预填草稿（⓪→① 数据流）")
    args = parser.parse_args()

    try:
        model, problems = parse_aml(args.aml)
    except AMLParseError as exc:
        print("[aml] 结构错误: %s" % exc)
        return 2
    except OSError as exc:
        print("[aml] 无法读取 %s: %s" % (args.aml, exc))
        return 2

    if args.io_list:
        io_items, pending = build_io_list(model)
        print(json.dumps(io_items, ensure_ascii=False, indent=2))
        for note in pending:
            print("[待补] %s" % note, file=sys.stderr)
    else:
        text = json.dumps(model, ensure_ascii=False, indent=2)
        if args.output:
            out = Path(args.output)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text + "\n", encoding="utf-8")
            print("[aml] device_model 已写入 %s" % args.output)
        else:
            print(text)

    if problems:
        print("\n[aml] %d 个内容问题:" % len(problems), file=sys.stderr)
        for p in problems:
            print("  - %s" % p, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
