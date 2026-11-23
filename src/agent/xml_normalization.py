"""模型 XML 的声明布局修正；保留变量、地址、初值和 ST 工艺逻辑。"""
import xml.etree.ElementTree as ET
import re

PLC_NS = 'http://www.plcopen.org/xml/tc6_0201'


def normalize_fb_type_references(xml):
    """Canonicalize leaf references to declared FBs/IEC timers; never infer a type."""
    try:
        root=ET.fromstring(xml,parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
    except ET.ParseError:return xml,0
    ns='{'+PLC_NS+'}'
    if root.tag!=ns+'project':return xml,0
    names={name:[name] for name in ('TON','TOF','TP')}
    for pou in root.findall('./'+ns+'types/'+ns+'pous/'+ns+'pou'):
        if pou.get('pouType')=='functionBlock':
            name=pou.get('name','')
            names.setdefault(name.upper(),[]).append(name)
    count=0
    for declared in root.iter(ns+'type'):
        if not len(declared) and not declared.attrib:
            matches=names.get((declared.text or '').strip().upper(),[])
            if len(matches)==1:
                declared.text=None
                ET.SubElement(declared,ns+'derived',name=matches[0]);count+=1
            continue
        if len(declared)!=1:continue
        child=declared[0]
        if not isinstance(child.tag,str) or not child.tag.startswith(ns):continue
        matches=names.get(child.tag[len(ns):].upper(),[])
        if len(matches)!=1 or len(child) or child.attrib or (child.text or '').strip():continue
        child.tag=ns+'derived';child.set('name',matches[0]);count+=1
    if not count:return xml,0
    ET.register_namespace('',PLC_NS)
    ET.register_namespace('xhtml','http://www.w3.org/1999/xhtml')
    return ET.tostring(root,encoding='utf-8',xml_declaration=True).decode('utf-8'),count


def xml_structure_advice(xml):
    """Explain malformed POU containers without guessing or rewriting interfaces."""
    try:root=ET.fromstring(xml)
    except ET.ParseError:return []
    ns='{'+PLC_NS+'}'
    if root.tag!=ns+'project':return []
    wrong=root.findall('./'+ns+'types/'+ns+'pous/'+ns+'pouType')
    if not wrong:return []
    names=', '.join(p.get('name','?') for p in wrong)
    advice=['[xml_format] '+names+': 程序块元素写成了 <pouType>，转换器不会将其识别为 POU。'
        '必须使用 <pou name="INTERP" pouType="functionBlock">…</pou>，'
        '主程序为 <pou name="PLC_PRG" pouType="program">…</pou>；pouType 只能是属性，不能作为元素名。']
    locals_only=[p.get('name','?') for p in wrong if p.get('pouType')=='functionBlock'
        and p.find(ns+'interface/'+ns+'localVars') is not None
        and not any(p.find(ns+'interface/'+ns+group) is not None
                    for group in ('inputVars','outputVars','inOutVars'))]
    if locals_only:
        advice.append('[xml_format] '+', '.join(locals_only)+': FB接口全部放入localVars。'
            '修正元素名时还必须按system中的权威FB骨架恢复inputVars/outputVars/inOutVars分组；'
            'localVars仅放内部变量，不能将所有形参改成局部变量，否则具名调用会编译失败。'
            '保持原形参名、类型、输入输出方向及工艺逻辑，不能只替换标签。')
    return advice


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
