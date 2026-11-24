#!/usr/bin/env python
"""标准 AML → PLC 侧 ST 声明 + 通讯层 Modbus 通道表（下游导出器）。

证明"标准 AML 的 IO 配置给 PLC 侧和通讯层、名称统一"：
  --st      生成 ST 全局定位变量声明（VAR_GLOBAL ... AT %QW100 : INT ...）——
            变量名 ≡ AML 信号名 ≡ io.json（②a 代码生成对齐用，xml2st/R2 一致性的锚）；
  --modbus  生成 Modbus 通道表（寄存器→变量→方向→换算 scale→限幅）——
            OpenPLC 轮询/桥端按表即插即用（lx 契约② §5 的地址换算直接可执行）。

用法：
  python tools/aml_export.py examples/aml/standard/010RB_100_standard.aml \
      --st workspace/aml_configurator/010RB_100_io.st \
      --modbus workspace/aml_configurator/010RB_100_modbus.json
"""

import argparse
import json
import re
import xml.etree.ElementTree as ET


def extract_io(aml_path):
    root = ET.parse(aml_path).getroot()
    out = []
    for iface in root.iter("ExternalInterface"):
        base = iface.get("RefBaseClassPath") or ""
        seg = base.rsplit("/", 1)[-1].lower()
        if not ("input" in seg or "output" in seg):
            continue
        a = {x.get("Name"): x.findtext(".//Value") for x in iface.iter("Attribute")}
        out.append({
            "name": iface.get("Name"),
            "dir": "input" if "input" in seg else "output",
            "type": a.get("plc_type") or ("BOOL" if "digital" in seg else "INT"),
            "address": a.get("address"),
            "scale": float(a["scale"]) if a.get("scale") else None,
            "range": ([float(a["range_min"]), float(a["range_max"])]
                      if a.get("range_min") is not None else None),
            "role": a.get("role"), "description": a.get("description", ""),
        })
    return out


def modbus_channel(addr):
    """%QW100 → ('holding_registers', 100)；%QX0.3 → ('coils', 3)。"""
    m = re.match(r"%QW(\d+)$", addr or "")
    if m:
        return "holding_registers", int(m.group(1))
    m = re.match(r"%QX(\d+)\.(\d+)$", addr or "")
    if m:
        return "coils", int(m.group(1)) * 8 + int(m.group(2))
    return None, None


def gen_st(io_items):
    lines = [
        "(* 自动生成：标准 AML IO 导出（tools/aml_export.py），勿手改。",
        "   变量名 ≡ AML 信号名 ≡ io.json——②a 代码生成的对齐锚（R1~R5）。 *)",
        "VAR_GLOBAL"]
    for it in io_items:
        lines.append(f"    {it['name']:<24s} AT {it['address']:<8s} : {it['type']};"
                     f"   (* {it['dir']}/{it.get('role') or '-'}: {it['description'][:40]} *)")
    lines.append("END_PROGRAM")
    lines[-1] = "END_VAR"
    return "\n".join(lines) + "\n"


def gen_modbus(io_items, adapter=None):
    channels = []
    for it in io_items:
        area, offset = modbus_channel(it["address"])
        channels.append({
            "plc_var": it["name"], "area": area, "offset": offset,
            "dir": it["dir"], "type": it["type"],
            "raw_to_eng": it["scale"],
            "range": it["range"], "role": it.get("role"),
        })
    channels.sort(key=lambda c: (c["area"], c["offset"] or 0))
    return {
        "source": "standard_aml",
        "function_codes": {"coils": "FC01/05/15", "holding_registers": "FC03/06/16"},
        "channels": channels,
        "adapter": adapter or {},
    }


def main(argv=None):
    p = argparse.ArgumentParser(description="标准 AML → ST 声明 + Modbus 通道表")
    p.add_argument("aml")
    p.add_argument("--st", help="ST 定位变量声明输出")
    p.add_argument("--modbus", help="Modbus 通道表 json 输出")
    args = p.parse_args(argv)
    if not (args.st or args.modbus):
        p.error("至少指定 --st 或 --modbus 之一")

    io_items = extract_io(args.aml)
    if args.st:
        with open(args.st, "w", encoding="utf-8", newline="\n") as f:
            f.write(gen_st(io_items))
        print(f"OK ST 声明 {args.st}（{len(io_items)} 变量，名称 ≡ AML 信号名）")
    if args.modbus:
        with open(args.modbus, "w", encoding="utf-8") as f:
            json.dump(gen_modbus(io_items), f, ensure_ascii=False, indent=2)
        areas = {}
        for it in io_items:
            a, _ = modbus_channel(it["address"])
            areas[a] = areas.get(a, 0) + 1
        print(f"OK Modbus 通道表 {args.modbus}（{areas}）")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
