"""把 examples/robotcell/010RB_100_io.json（gc 侧事实源）注入 AmlDocument.aml。

产物：examples/aml/AmlDocument_io.aml —— 官方 KUKA 机器人单元 AML + 真实 io_list 的合流。

注入内容（IEC 62714 信号建模 + 项目契约约定）：
  1) io_list 14 条信号挂 component_id 指向的机器人组件（010RB_100_KR240R2700prime_），
     属性：address（%QW 统一 Q 区）/ description / scale（deg_per_count=0.02°/LSB）/
     range（INT16 满域换算，契约① S2 落 16 位域）/ role / source_file / source_id
     （逐字保留 DAE 关节溯源——本 io_list 的灵魂字段）；
     dir→设备侧接口类：output（PLC→设备指令）→AnalogInput；input（设备→PLC 反馈）→AnalogOutput；
  2) Dummy_SPS（SPS=PLC 占位）加 AI/AO 端子模块（PortInterface 端子不进 io_list）；
     PLC 属性注入 adapter.json + motion.json 的通讯/运动约束：cycle_hz、command_timeout_s、
     modbus_endpoint、max_speed_deg_s；
  3) InternalLink 电气接线挂 Station（两端共同祖先）。

用法：python tools/inject_io_into_aml.py
"""

import json
import xml.etree.ElementTree as ET

SRC = "AML_RobotCell_en_public/AML_RobotCell_en/AmlDocument.aml"
IO_JSON = "examples/robotcell/010RB_100.io.json"
ADAPTER_JSON = "examples/robotcell/010RB_100.adapter.json"
MOTION_JSON = "examples/robotcell/010RB_100.motion.json"
DST = "examples/aml/AmlDocument_io.aml"

SIG_LIB = "AutomationMLInterfaceClassLib/AutomationMLBaseInterface"
PORT_LIB = SIG_LIB + "/PortInterface"

ROLE_DESC = {
    "target": "关节位置指令（CSP 目标值）",
    "actual": "关节位置反馈（实际值）",
    "enable": "驱动使能字",
    "feedback_sequence": "反馈序号（PLC 扫描计数，用于通讯活性判定）",
}


def find_by_id(root, comp_id):
    return next(e for e in root.iter()
                if e.tag == "InternalElement" and e.get("ID") == comp_id)


def signal(item):
    # dir 视角 = 接口类视角（与 aml_parser/spec 一致）：input→AnalogInput，output→AnalogOutput
    kind = "AnalogInput" if item["dir"] == "input" else "AnalogOutput"
    desc = ROLE_DESC.get(item.get("role"), item["name"])
    iface = ET.Element("ExternalInterface", {
        "Name": item["name"], "ID": "io_" + item["name"],
        "RefBaseClassPath": SIG_LIB + "/" + kind})
    def attr(n, v):
        a = ET.SubElement(iface, "Attribute", {"Name": n})
        ET.SubElement(a, "Value").text = str(v)
    attr("address", item["address"])
    attr("description", desc)
    if item.get("deg_per_count"):
        attr("scale", item["deg_per_count"])
        rng_max = int(32767 * item["deg_per_count"])      # INT16 满域换算
        attr("range_min", -rng_max)
        attr("range_max", rng_max)
    else:                                                  # 无 scale 的字量：word 域
        attr("range_min", 0)
        attr("range_max", 65535)
    if item.get("role"):
        attr("role", item["role"])
    if item.get("source_file"):
        attr("source_file", item["source_file"])           # DAE 关节溯源（逐字保留）
    if item.get("source_id"):
        attr("source_id", item["source_id"])
    return iface


def port(parent, name, prefix):
    n = len(parent.findall("ExternalInterface"))
    p = ET.SubElement(parent, "ExternalInterface", {
        "Name": "ch_" + name, "ID": "port_" + name,
        "RefBaseClassPath": PORT_LIB})
    a = ET.SubElement(p, "Attribute", {"Name": "channel"})
    ET.SubElement(a, "Value").text = "%s%d" % (prefix, n)


def sps_attr(sps, name, value):
    a = ET.SubElement(sps, "Attribute", {"Name": name})
    ET.SubElement(a, "Value").text = str(value)


def main():
    io = json.load(open(IO_JSON, encoding="utf-8"))["io_list"]
    adapter = json.load(open(ADAPTER_JSON, encoding="utf-8"))
    motion = json.load(open(MOTION_JSON, encoding="utf-8"))

    tree = ET.parse(SRC)
    root = tree.getroot()
    station = next(e for e in root.iter()
                   if e.tag == "InternalElement" and e.get("Name") == "Station")
    sps = next(e for e in root.iter()
               if e.tag == "InternalElement" and e.get("Name") == "Dummy_SPS")

    # ① 信号挂机器人本体（component_id 定位）
    targets = [it for it in io if it["dir"] == "output"]     # 指令 → PLC 模拟量输出模块
    actuals = [it for it in io if it["dir"] == "input"]      # 反馈 → PLC 模拟量输入模块
    for it in io:
        robot = find_by_id(root, it["component_id"])
        robot.append(signal(it))

    # ② PLC 端子模块 + 通讯/运动约束属性
    ao = ET.SubElement(sps, "InternalElement", {"Name": "AO_Module"})
    ai = ET.SubElement(sps, "InternalElement", {"Name": "AI_Module"})
    for it in targets:
        port(ao, it["name"], "AO")
    for it in actuals:
        port(ai, it["name"], "AI")
    rt = adapter.get("runtime", {})
    sps_attr(sps, "cycle_hz", adapter.get("cycle_hz"))
    sps_attr(sps, "command_timeout_s", adapter.get("command_timeout_s"))
    sps_attr(sps, "modbus_endpoint",
             "%s:%s" % (rt.get("host"), rt.get("port")) if rt else "-")
    sps_attr(sps, "max_speed_deg_s", motion.get("max_speed_deg_s"))

    # ③ 电气接线
    for it in io:
        ET.SubElement(station, "InternalLink", {
            "Name": "wire_" + it["name"],
            "RefPartnerSideA": "io_" + it["name"],
            "RefPartnerSideB": "port_" + it["name"]})

    ET.indent(tree, space="\t")
    tree.write(DST, encoding="utf-8", xml_declaration=True)
    print("注入完成:", DST)
    print("  信号 %d（指令 %d + 反馈 %d）| 端子模块 AO/AI | 接线 %d"
          % (len(io), len(targets), len(actuals), len(io)))


if __name__ == "__main__":
    main()
