"""把项目契约格式的 IO 层注入 AmlDocument.aml（RobotCell 教程包，保留几何/装配/工序）。

产物：examples/aml/AmlDocument_io.aml —— "真实几何 + 标准 IO 层"的合流实例。
注入内容（IEC 62714 信号建模 + 本项目契约约定）：
  1) 信号 ExternalInterface 挂 Station 真实设备（机器人/抓手/输送/门/围栏/电气柜），
     类路径用文件内已有的 AutomationMLInterfaceClassLib 标准库；
     Attribute: address（统一 %Q 区）+ description + scale/range（INT 定点量程）；
  2) Dummy_SPS（SPS=PLC 占位）内加 DI/DO/AI 端子模块，端子用 PortInterface
     （不含 input/output 关键词 → 不进 io_list，仅作接线端）+ channel 属性；
  3) InternalLink 电气接线（设备信号 × PLC 端子）挂 Station（两端共同祖先）。
io_list：机器人单元 23 点（12 布尔 + 6 轴反馈 + 速度反馈/设定 + prog_id），
位宽对齐 lx 契约②（bool→%QX、INT16→%QW、prog_id AT %QW20）。
"""

import xml.etree.ElementTree as ET

SRC = "AML_RobotCell_en_public/AML_RobotCell_en/AmlDocument.aml"
DST = "examples/aml/AmlDocument_io.aml"
SIG_LIB = "AutomationMLInterfaceClassLib/AutomationMLBaseInterface"
PORT_LIB = SIG_LIB + "/PortInterface"

# (信号名, 类, 地址, 描述, 挂载路径(Station起), scale|None, range|None)
IO_LIST = [
    ("estop_ok",    "DigitalOutput", "%QX0.0", "急停回路正常（Kreis geschlossen）", "HB51_A_Allgemein/Schaltschraenke_A", None, None),
    ("gate_closed", "DigitalOutput", "%QX0.1", "单元门 Tore_A 关闭到位",             "HB51_A_Allgemein/Tore_A", None, None),
    ("fence_ok",    "DigitalOutput", "%QX0.2", "防护围栏联锁正常",                    "HB51_Allgemein/Schutzzaun", None, None),
    ("robot_home_fb",   "DigitalOutput", "%QX0.6", "机器人在原位（P1/Home）",   "ST030/030RB_100", None, None),
    ("robot_prog_done", "DigitalOutput", "%QX0.7", "当前程序段执行完成",         "ST030/030RB_100", None, None),
    ("robot_fault",     "DigitalOutput", "%QX2.0", "机器人故障（需 MC_Reset）",  "ST030/030RB_100", None, None),
    ("robot_enable",     "DigitalInput", "%QX1.0", "机器人驱动使能（MC_Power）",   "ST030/030RB_100", None, None),
    ("robot_home_cmd",   "DigitalInput", "%QX1.1", "回零指令（MC_Home，上升沿）", "ST030/030RB_100", None, None),
    ("robot_start_prog", "DigitalInput", "%QX1.2", "程序启动（上升沿）",           "ST030/030RB_100", None, None),
    ("robot_stop",       "DigitalInput", "%QX1.3", "受控停止（Q stop）",           "ST030/030RB_100", None, None),
    ("robot_j1_pos", "AnalogInput", "%QW0", "J1 轴角度反馈", "ST030/030RB_100", "0.01", (-320.0, 320.0)),
    ("robot_j2_pos", "AnalogInput", "%QW1", "J2 轴角度反馈", "ST030/030RB_100", "0.01", (-320.0, 320.0)),
    ("robot_j3_pos", "AnalogInput", "%QW2", "J3 轴角度反馈", "ST030/030RB_100", "0.01", (-320.0, 320.0)),
    ("robot_j4_pos", "AnalogInput", "%QW3", "J4 轴角度反馈", "ST030/030RB_100", "0.01", (-320.0, 320.0)),
    ("robot_j5_pos", "AnalogInput", "%QW4", "J5 轴角度反馈", "ST030/030RB_100", "0.01", (-320.0, 320.0)),
    ("robot_j6_pos", "AnalogInput", "%QW5", "J6 轴角度反馈", "ST030/030RB_100", "0.01", (-320.0, 320.0)),
    ("gripper_part_held", "DigitalOutput", "%QX0.4", "抓手持有工件（真空/夹持反馈）", "ST030/030RB_300/030GRF341", None, None),
    ("gripper_close_cmd", "DigitalInput",  "%QX1.4", "抓手闭合指令",                  "ST030/030RB_300/030GRF341", None, None),
    ("conveyor_part_present", "DigitalOutput", "%QX0.3", "输送段末端工件到位检测", "HB51_A_Allgemein/Foerdertechnik_Stoerkontur_A", None, None),
    ("conveyor_run",          "DigitalInput",  "%QX1.5", "输送段运行指令",         "HB51_A_Allgemein/Foerdertechnik_Stoerkontur_A", None, None),
    ("conveyor_speed_fb", "AnalogInput",  "%QW6", "带速反馈", "HB51_A_Allgemein/Foerdertechnik_Stoerkontur_A", "0.01", (0.0, 3.0)),
    ("conveyor_speed_sp", "AnalogOutput", "%QW7", "带速设定", "HB51_A_Allgemein/Foerdertechnik_Stoerkontur_A", "0.01", (0.0, 3.0)),
    ("prog_id", "AnalogOutput", "%QW20", "程序身份编号（robotcell_demo=8）", "HB51_A_Allgemein/Schaltschraenke_A", None, (0, 65535)),
]


def signal(name, kind, addr, desc, scale, rng):
    iface = ET.Element("ExternalInterface", {
        "Name": name, "ID": "io_" + name,
        "RefBaseClassPath": SIG_LIB + "/" + kind})
    def attr(n, v):
        a = ET.SubElement(iface, "Attribute", {"Name": n})
        ET.SubElement(a, "Value").text = str(v)
    attr("address", addr)
    attr("description", desc)
    if scale:
        attr("scale", scale)
    if rng:
        attr("range_min", rng[0])
        attr("range_max", rng[1])
    return iface


def port(parent, name, module_prefix):
    n = len(parent.findall("ExternalInterface"))
    p = ET.SubElement(parent, "ExternalInterface", {
        "Name": "ch_" + name, "ID": "port_" + name,
        "RefBaseClassPath": PORT_LIB})
    a = ET.SubElement(p, "Attribute", {"Name": "channel"})
    ET.SubElement(a, "Value").text = "%s%d" % (module_prefix, n)


def find_by_path(station, path):
    el = station
    for seg in path.split("/"):
        el = next(c for c in el
                  if c.tag == "InternalElement" and c.get("Name") == seg)
    return el


def main():
    tree = ET.parse(SRC)
    root = tree.getroot()
    station = next(e for e in root.iter()
                   if e.tag == "InternalElement" and e.get("Name") == "Station")
    sps = next(e for e in root.iter()
               if e.tag == "InternalElement" and e.get("Name") == "Dummy_SPS")

    # ① 信号挂设备
    for name, kind, addr, desc, path, scale, rng in IO_LIST:
        find_by_path(station, path).append(signal(name, kind, addr, desc, scale, rng))

    # ② PLC 端子模块
    di = ET.SubElement(sps, "InternalElement", {"Name": "DI_Module"})
    do = ET.SubElement(sps, "InternalElement", {"Name": "DO_Module"})
    ai = ET.SubElement(sps, "InternalElement", {"Name": "AI_Module"})
    for name, kind, *_ in IO_LIST:
        if kind == "DigitalOutput":
            port(di, name, "DI")
        elif kind == "DigitalInput":
            port(do, name, "DO")
        else:
            port(ai, name, "AI")

    # ③ 电气接线
    for name, *_ in IO_LIST:
        ET.SubElement(station, "InternalLink", {
            "Name": "wire_" + name,
            "RefPartnerSideA": "io_" + name,
            "RefPartnerSideB": "port_" + name})

    ET.indent(tree, space="	")
    tree.write(DST, encoding="utf-8", xml_declaration=True)
    print("注入完成:", DST, "| 信号", len(IO_LIST), "点 | 接线", len(IO_LIST), "条")


if __name__ == "__main__":
    main()
