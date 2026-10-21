"""模型 XML 的声明布局修正；保留变量、地址、初值和 ST 工艺逻辑。"""
import xml.etree.ElementTree as ET
import re

PLC_NS = 'http://www.plcopen.org/xml/tc6_0201'


def normalize_pou_terminators(xml):
    """XML ST contains the body only; the converter emits the POU terminator."""
    try:root=ET.fromstring(xml)
    except ET.ParseError:return xml,0
    ns='{'+PLC_NS+'}';count=0
    for pou in root.findall('.//'+ns+'pou'):
        end={'functionBlock':'END_FUNCTION_BLOCK','program':'END_PROGRAM'}.get(pou.get('pouType'))
        st=pou.find('.//'+ns+'ST')
        if not end or st is None or len(st)!=1 or len(st[0]):continue
        node=st[0];body=node.text or ''
        # Only strip a lone, trailing redundant footer, never POU declarations.
        if re.search(r'(?im)^\s*(?:FUNCTION_BLOCK|PROGRAM)\b',body):continue
        fixed,n=re.subn(r'(?im)^\s*'+end+r'\s*;?\s*\Z','',body,count=1)
        if n:node.text=fixed;count+=1
    if not count:return xml,0
    return '<?xml version="1.0" encoding="UTF-8"?>\n'+ET.tostring(root,encoding='unicode'),count


def normalize_program_io(xml):
    """PROGRAM 的 AT 定位变量必须在 localVars，不能充当输入/输出形参。

    只调整 PROGRAM 声明，FB 形参保持原样；无修正时逐字返回原 XML。
    无法解析/未知命名空间交给原有静态闸门拒绝，不猜测或修补工艺逻辑。
    """
    try:
        root = ET.fromstring(xml, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
    except ET.ParseError:
        return xml, 0
    ns = '{'+PLC_NS+'}'
    if root.tag != ns+'project':
        return xml, 0
    count = 0
    for pou in root.iter(ns+'pou'):
        if pou.get('pouType') != 'program':
            continue
        interface = pou.find(ns+'interface')
        if interface is None:
            continue
        for block in list(interface):
            if block.tag not in (ns+'inputVars', ns+'outputVars', ns+'inOutVars'):
                continue
            located = [var for var in block.findall(ns+'variable') if var.get('address')]
            if not located:
                continue
            local = ET.SubElement(interface, ns+'localVars', dict(block.attrib))
            for var in located:
                block.remove(var)
                local.append(var)
                count += 1
    if not count:
        return xml, 0
    ET.register_namespace('', PLC_NS)
    ET.register_namespace('xhtml', 'http://www.w3.org/1999/xhtml')
    return ET.tostring(root, encoding='utf-8', xml_declaration=True).decode('utf-8'), count
