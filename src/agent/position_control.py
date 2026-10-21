"""SVG 的位置输出适配：新 AML 资产 + 新 XML，不改写冻结交付。"""
import re
import xml.etree.ElementTree as ET

PLC_NS = "http://www.plcopen.org/xml/tc6_0201"


def position_io(model):
    points = {p['name']:p for p in model.get('io_points', [])}
    for axis in 'xyz':
        cmd, fb = points.get(axis+'_cmd'), points.get(axis+'_fb')
        if not cmd or not fb or cmd['dir']!='output' or cmd['type']!='INT':
            return False
        if not command_scale(cmd,fb):
            return False
    return True


def command_scale(cmd,fb):
    if cmd.get('range')==fb.get('range') and cmd.get('unit')==fb.get('unit'):return 1
    if (cmd.get('range')==[v*100 for v in fb.get('range',[])] and
            cmd.get('unit')=='0.01 '+str(fb.get('unit'))):return 100
    return 0


def position_scales(points):
    table={p['name']:p for p in points}
    return {a:command_scale(table.get(a+'_cmd',{}),table.get(a+'_fb',{})) or 1 for a in 'xyz'}


def make_precision_aml(data):
    """只提高位置输出编码精度，保留 AML 行程、原地址及粗位置反馈。"""
    from .aml_parser import parse_aml
    root=ET.fromstring(make_position_aml(data));ns=root.tag.rsplit('}',1)[0]+'}' if '}' in root.tag else ''
    model,errors=parse_aml(data.decode('utf-8-sig'))
    if errors:raise ValueError('AML 无效')
    points={p['name']:p for p in model['io_points']}
    for a in 'xyz':
        fb=points[a+'_fb'];bounds=[v*100 for v in fb['range']]
        if min(bounds)<-32768 or max(bounds)>32767:raise ValueError('高精度位置输出超出 INT16 域')
        node=next(n for n in root.iter(ns+'ExternalInterface') if n.get('Name')==a+'_cmd')
        for attr in node.findall(ns+'Attribute'):
            key=attr.get('Name')
            if key in ('range_min','range_max','unit'):
                attr.find(ns+'Value').text=str({'range_min':bounds[0],'range_max':bounds[1],'unit':'0.01 '+str(fb['unit'])}[key])
    for node in root.iter(ns+'InternalElement'):
        if node.get('Name') in ('x_axis','y_axis','z_axis'):
            for attr in node.findall(ns+'Attribute'):
                if attr.get('Name')=='poswin':attr.find(ns+'Value').text='0.01'
    ET.indent(root)
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)


def make_position_aml(data):
    """保留旧 IO/地址；在空闲 %QW 中新增位置输出，并将驱动参考接至新通道。"""
    from .aml_parser import parse_aml
    model, problems = parse_aml(data.decode('utf-8-sig'))
    if problems:
        raise ValueError('AML 校验失败：'+'；'.join(problems))
    if position_io(model):
        return data
    root = ET.fromstring(data)
    ns = root.tag.rsplit('}',1)[0]+'}' if '}' in root.tag else ''
    interfaces = {n.get('Name'):n for n in root.iter() if n.tag==ns+'ExternalInterface'}
    parents = {c:p for p in root.iter() for c in p}
    points = {p['name']:p for p in model['io_points']}
    used = {p.get('address') for p in points.values()} | {'%QW20'}
    ids = {n.get('ID') for n in root.iter() if n.get('ID')}
    for axis in 'xyz':
        name = axis+'_cmd'
        if name in points:
            raise ValueError(name+' 已存在但不是与反馈同量程的位置输出，请检查 AML')
        source, feedback = interfaces.get(axis+'_v'), interfaces.get(axis+'_fb')
        if source is None or feedback is None or not points.get(axis+'_v',{}).get('address'):
            raise ValueError('当前自动转换需要 '+axis+'_v / '+axis+'_fb 通道')
        slot = next((i for i in range(3,32768) if '%QW'+str(i) not in used),None)
        if slot is None:
            raise ValueError('AML 没有空闲的位置输出寄存器')
        address = '%QW'+str(slot);used.add(address)
        ident = 'ch_'+name
        if ident in ids:
            raise ValueError('AML ID 冲突：'+ident)
        ids.add(ident)
        node = ET.SubElement(parents[source],ns+'ExternalInterface',{
            'Name':name,'ID':ident,'RefBaseClassPath':source.get('RefBaseClassPath','')})
        attrs = {'address':address,'description':axis.upper()+' 轴插补位置输出（CSP）',
                 'range_min':points[axis+'_fb']['range'][0],
                 'range_max':points[axis+'_fb']['range'][1], 'unit':points[axis+'_fb']['unit']}
        for key,value in attrs.items():
            attribute=ET.SubElement(node,ns+'Attribute',{'Name':key})
            ET.SubElement(attribute,ns+'Value').text=str(value)
        # 原速度通道保留为诊断，不再连接驱动参考；两类量不共用一个地址。
        for link in root.iter(ns+'InternalLink'):
            if link.get('RefPartnerSideB')!=source.get('ID'):
                continue
            link.set('RefPartnerSideB',ident)
            link.set('Name','wire_'+name)
            driver=next((n for n in root.iter(ns+'ExternalInterface') if n.get('ID')==link.get('RefPartnerSideA')),None)
            if driver is not None:
                driver.set('Name',axis+'_pos_ref')
                driver.set('RefBaseClassPath','AutomationMLInterfaceClassLib/Signal/PositionReference')
        for attr in source.findall(ns+'Attribute'):
            if attr.get('Name')=='description':
                attr.find(ns+'Value').text=axis.upper()+' 轴速度诊断（不作为驱动器位置输入）'
    ET.indent(root)
    result=ET.tostring(root,encoding='utf-8',xml_declaration=True)
    converted, problems=parse_aml(result.decode('utf-8'))
    if problems or not position_io(converted):
        raise ValueError('位置指令 AML 校验失败：'+'；'.join(problems))
    return result


def precision_svg_program(xml,model,trajectory):
    """标准 SVG 生成一份保留小数路点、同步 XY 进度的高精度新源码。"""
    ns='{'+PLC_NS+'}';root=ET.fromstring(xml)
    prg=root.find('.//'+ns+'pou[@name="PLC_PRG"]');content=prg.find('.//'+ns+'ST')[0]
    body=content.text or ''
    if 'SVG_CONTINUOUS_SEQUENCE' not in body:raise ValueError('请先修复连续 SVG 步序')
    variables={v.get('name'):v for v in prg.findall('.//'+ns+'interface//'+ns+'variable')}
    z_type=variables['ix_z'].find(ns+'type/'+ns+'derived').get('name')
    if z_type not in ('INTERP','INTERP_Z'):raise ValueError('Z 插补器结构不受支持')
    z_pos='posz' if z_type=='INTERP_Z' else 'pos'
    def block_end(text,start,kind):
        depth=0
        for m in re.finditer(r'\b('+kind+'|END_'+kind+r')\b',text[start:],re.I):
            depth+=1 if m[1].upper()==kind else -1
            if not depth:return start+m.end()+1
        raise ValueError('步序块不完整')
    def num(v):return format(float(v),'.6f').rstrip('0').rstrip('.')+'.0' if float(v).is_integer() else format(float(v),'.6f').rstrip('0')
    targets=[];cases=[]
    for step in trajectory['steps']:
        points=step.get('waypoints') or [step['to']]
        targets.append(points)
        assignments=lambda p:' '.join('pl_t'+a+' := '+num(v)+';' for a,v in zip('xyz',p))
        cases.append('    '+str(step['no'])+': '+(assignments(points[0]) if len(points)==1 else
            'CASE pl_k OF\n'+'\n'.join('        '+str(i)+': '+assignments(p) for i,p in enumerate(points))+'\n    END_CASE;'))
    start=body.index('CASE pl_step OF');end=block_end(body,start,'CASE')
    body=body[:start]+'CASE pl_step OF\n    -1: pl_tx := INT_TO_REAL(x_fb_i); pl_ty := INT_TO_REAL(y_fb_i); pl_tz := '+num(trajectory['pen_z']['up'])+';\n'+'\n'.join(cases)+'\nEND_CASE;'+body[end:]
    # 第一次移动前先原地抬笔，禁止从初始接触纸面的位置斜着拖到起点。
    body=body.replace('pl_step := 1;','pl_step := -1;',1)
    start=body.index('IF (pl_step <> 0)');end=block_end(body,start,'IF')
    lines=['IF draw_active AND ix_x.Done AND ix_y.Done AND ix_z.Done',
           '   AND ABS(ix_x.pos - pl_tx) <= 0.011 AND ABS(ix_y.pos - pl_ty) <= 0.011 AND ABS(ix_z.'+z_pos+' - pl_tz) <= 0.011 THEN',
           '    CASE pl_step OF','        -1: pl_step := 1; pl_k := 0;']
    for i,points in enumerate(targets,1):
        if i==len(targets):code='plot_done_i := TRUE; draw_active := FALSE; pl_step := 0; pl_k := 0; go_x_exe := FALSE; go_y_exe := FALSE; go_z_exe := FALSE;'
        elif len(points)>1:code='IF pl_k < '+str(len(points)-1)+' THEN pl_k := pl_k + 1; ELSE pl_k := 0; pl_step := '+str(i+1)+'; END_IF;'
        else:code='pl_step := '+str(i+1)+'; pl_k := 0;'
        lines.append('        '+str(i)+': '+code)
    lines+=['    END_CASE;','END_IF;']
    body=body[:start]+'\n'.join(lines)+body[end:]
    before=body.index('(* SVG_CONTINUOUS_SEQUENCE:')
    body=body[:before]+'''IF draw_active AND pl_step = -1 THEN
    ix_x.pos := INT_TO_REAL(x_fb_i); ix_x.vel := 0.0;
    ix_y.pos := INT_TO_REAL(y_fb_i); ix_y.vel := 0.0;
END_IF;
'''+body[before:]
    for var in prg.findall('.//'+ns+'interface//'+ns+'variable'):
        if var.get('name') in ('pl_tx','pl_ty','pl_tz'):
            typ=var.find(ns+'type');typ.clear();ET.SubElement(typ,ns+'REAL')
    locals=ET.SubElement(prg.find(ns+'interface'),ns+'localVars')
    for name in ('path_tx','path_ty','path_dx','path_dy','path_span','path_rx','path_ry','path_goal_x','path_goal_y','path_goal_z'):
        var=ET.SubElement(locals,ns+'variable',name=name);ET.SubElement(ET.SubElement(var,ns+'type'),ns+'REAL')
        if name in ('path_rx','path_ry'):ET.SubElement(ET.SubElement(var,ns+'initialValue'),ns+'simpleValue',value='1.0')
    start=body.index('ix_x(fire')
    goals='IF draw_active THEN\n'+''.join('    path_goal_'+a+' := pl_t'+a+';\n' for a in 'xyz')+'ELSE\n'
    for a in 'xyz':
        target=a+'_tgt'
        expression=target if variables[target].find(ns+'type/'+ns+'REAL') is not None else 'INT_TO_REAL('+target+')'
        goals+='    path_goal_'+a+' := '+expression+';\n'
    goals+='END_IF;\n'
    ratios=goals+'''IF draw_active AND ((pl_tx <> path_tx) OR (pl_ty <> path_ty)) THEN
    path_dx := ABS(pl_tx - ix_x.pos); path_dy := ABS(pl_ty - ix_y.pos);
    path_span := MAX(path_dx, path_dy);
    IF path_span > 0.001 THEN
        path_rx := MAX(0.001, path_dx / path_span); path_ry := MAX(0.001, path_dy / path_span);
    ELSE path_rx := 1.0; path_ry := 1.0; END_IF;
    path_tx := pl_tx; path_ty := pl_ty;
END_IF;
'''
    body=body[:start]+ratios+body[start:]
    for a in 'xyz':
        if variables[a+'_tgt'].find(ns+'type/'+ns+'REAL') is None:
            body=body.replace(a+'_tgt := pl_t'+a+';',a+'_tgt := REAL_TO_INT(pl_t'+a+');')
        body=body.replace('pos_target := '+a+'_tgt','pos_target := path_goal_'+a)
    for a in 'xy':
        for stem in ('vel','acc','dec'):
            body=body.replace(stem+'_req := '+a+'_'+stem+'_bus',stem+'_req := '+a+'_'+stem+'_bus * path_r'+a)
    content.text=body
    for pou in root.findall('.//'+ns+'pou'):
        if pou.get('name') not in ('INTERP','INTERP_Z'):continue
        z=pou.get('name')=='INTERP_Z';pos,tgt,vel=('posz','tgtz','velz') if z else ('pos','tgt','vel')
        for var in pou.iter(ns+'variable'):
            if var.get('name')=='pos_target':
                typ=var.find(ns+'type');typ.clear();ET.SubElement(typ,ns+'REAL')
            if var.get('name')=='POSWIN':
                value=var.find('.//'+ns+'simpleValue')
                if value is None:
                    value=ET.SubElement(ET.SubElement(var,ns+'initialValue'),ns+'simpleValue')
                value.set('value','0.01')
        st=pou.find('.//'+ns+'ST')[0]
        code=(st.text or '').replace('INT_TO_REAL(pos_target)','pos_target').replace('> 0.499','> 0.0049')
        code=re.sub(r'\bpos_target\s*([<>])\s*(\d+)(?![\d.])\b',lambda m:'pos_target '+m[1]+' '+m[2]+'.0',code)
        integration=re.search(r'\b'+pos+r'\s*:=\s*'+pos+r'\s*\+\s*'+vel+r'\s*\*\s*(SCAN_T\w*)\s*;',code)
        if integration is None:raise ValueError('插补器积分结构不受支持：'+pou.get('name'))
        old=integration[0];scan=integration[1]
        new='IF Busy AND ABS('+tgt+' - '+pos+') <= ABS('+vel+' * '+scan+') THEN\n    '+pos+' := '+tgt+'; '+vel+' := 0.0; Busy := FALSE; Done := TRUE;\nELSE\n    '+old+'\nEND_IF;'
        st.text=code.replace(old,new,1)
    result='<?xml version="1.0" encoding="UTF-8"?>\n'+ET.tostring(root,encoding='unicode')
    result=convert_position_program(result,model)
    declared={v.get('name') for v in prg.findall('.//'+ns+'interface//'+ns+'variable')}
    active=[name for name in ('draw_active','go_x_exe','go_y_exe','go_z_exe','hm_x_exe','hm_y_exe','hm_z_exe',
                             'rel_x_exe','rel_y_exe','rel_z_exe','jog_fwd_i','jog_rev_i') if name in declared]
    return result.replace('IF run AND all_oe AND NOT quickstop AND NOT fault_any THEN',
        'IF run AND all_oe AND NOT quickstop AND NOT fault_any AND ('+' OR '.join(active)+') THEN')


def repair_generated_svg(xml, model, trajectory):
    """针对已识别的标准轴栈修复绘图层；保留原 FB 库，在新任务中交付。"""
    xml=convert_position_program(xml,model)
    root=ET.fromstring(xml);ns='{'+PLC_NS+'}'
    prg=root.find('.//'+ns+'pou[@name="PLC_PRG"]');content=prg.find('.//'+ns+'ST')[0]
    body=content.text or ''
    if 'SVG_CONTINUOUS_SEQUENCE' in body:
        raise ValueError('已修复程序不能重复转换，请使用原始生成稿')
    # 只接受已经验证过命名/接口的轴栈，未知结构拒绝自动改写。
    required={'pl_step','pl_k','pl_tx','pl_ty','pl_tz','draw_active','plot_done_i',
              'x_fb_i','y_fb_i','z_fb_i','go_x_exe','go_y_exe','go_z_exe'}
    declared={v.get('name') for v in prg.findall('.//'+ns+'interface//'+ns+'variable')}
    if not required<=declared:raise ValueError('当前程序不是支持的 SVG 轴栈')
    # 取完整 IF，而不是停在内部 IF 的第一个 END_IF。
    candidates=list(re.finditer(r'\bIF\b([\s\S]*?)\bTHEN\b',body,re.I))
    match=next((m for m in candidates if 'pl_step' in m[1] and all('ix_'+a+'.Done' in m[1] for a in 'xyz')),None)
    if match is None:
        # 同一轴栈的变体：外层判断绘图步号，内层联合三轴 Busy 和目标到位。
        match=next((m for m in candidates if re.fullmatch(r'\s*draw_active\s+AND\s+pl_step\s*<>\s*0\s*',m[1],re.I)),None)
    if match is None:raise ValueError('未找到标准 SVG 步进条件')
    depth=0;end=None
    for token in re.finditer(r'\b(IF|END_IF)\b',body[match.start():],re.I):
        depth+=1 if token[1].upper()=='IF' else -1
        if depth==0:
            end=match.start()+token.end();break
    if end is None:raise ValueError('SVG 步进 IF 不完整')
    if body[end:end+1]==';':end+=1
    advance=body[match.start():end]
    if 'pl_step :=' not in advance or not all('ix_'+a+'.' in advance for a in 'xyz'):
        raise ValueError('SVG 步进内容不受支持')
    body=body[:match.start()]+body[end:]
    for axis in 'xyz':
        pattern=r'IF\s+ix_'+axis+r'\.Done\s+OR\s+ix_'+axis+r'\.Aborted\s+THEN[^;]*;[\s\S]*?END_IF;'
        clear=re.search(pattern,body,re.I)
        if clear is None or 'go_'+axis+'_exe := FALSE;' not in clear[0]:
            raise ValueError('完成信号清理结构不受支持：'+axis)
        body=body[:clear.start()]+'IF NOT draw_active THEN\n'+clear[0]+'\nEND_IF;'+body[clear.end():]
    offset=body.index('(* CSP_POSITION_OUTPUT:')
    body=body[:offset]+'''(* SVG_CONTINUOUS_SEQUENCE: 完成检查在插补调用后，绘制期间保持跟踪。 *)
IF (pl_step <> 0) AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    pl_step := pl_step + 1;
END_IF;
'''+body[offset:]
    content.text=body
    return precision_svg_program(ET.tostring(root,encoding='unicode'),model,trajectory)


def _non_drawing_guard(body, offset):
    """Recognize a shared inactive branch across nested IFs and sibling statements."""
    code=re.sub(r'\(\*[\s\S]*?\*\)|//[^\n]*',lambda m:' '*len(m[0]),body)
    stack=[]
    for token in re.finditer(r'\b(IF|ELSIF|ELSE|END_IF)\b',code[:offset],re.I):
        kind=token[1].upper()
        if kind in ('IF','ELSIF'):
            end=re.search(r'\bTHEN\b',code[token.end():],re.I)
            condition=code[token.end():token.end()+end.start()].strip() if end else ''
            normalized=re.sub(r'[\s()]','',condition).lower()
            guarded=(1 if normalized in ('notdraw_active','pl_step=0') else
                     -1 if normalized in ('draw_active','pl_step<>0','pl_step>0') else 0)
            if kind=='IF':stack.append(guarded)
            elif stack:stack[-1]=guarded
        elif kind=='ELSE' and stack:stack[-1]=-stack[-1]
        elif kind=='END_IF' and stack:stack.pop()
    return 1 in stack


def svg_program_problems(xml, trajectory):
    """拦截可确定的 SVG 扫描顺序和小数丢失问题，不对未知逻辑宣称正确。"""
    if not trajectory or trajectory.get('shape')!='svg':
        return []
    root=ET.fromstring(xml);ns='{'+PLC_NS+'}'
    prg=root.find('.//'+ns+'pou[@name="PLC_PRG"]')
    if prg is None:return []
    body=''.join(prg.find('.//'+ns+'ST').itertext())
    problems=[]
    for match in re.finditer(r'\b(\w+)\s*:=\s*(\w+)\s+AND\s+NOT\s+\1\s*;',body,re.I):
        problems.append(match[1]+' 将边沿输出当作前值记忆，会在持续命令下反复触发；请用独立的上一扫描状态')
    if 'draw_active' in body or 'pl_step' in body:
        for axis in 'xyz':
            pattern=r'\bIF\s+ix_'+axis+r'\.Done\s+OR\s+ix_'+axis+r'\.Aborted\s+THEN\s+go_'+axis+r'_exe\s*:=\s*FALSE;'
            for match in re.finditer(pattern,body,re.I):
                if not _non_drawing_guard(body,match.start()):
                    problems.append('绘图期间单轴完成即清 go_'+axis+'_exe，会打断连续跟踪；清理须限定在非绘图期间')
    for m in re.finditer(r'\bIF\b([\s\S]*?)\bTHEN\b',body,re.I):
        if 'pl_step' in m[1] and all('ix_'+a+'.Done' in m[1] for a in 'xyz'):
            if any(m.start()<body.find('ix_'+a+'(') for a in 'xyz'):
                problems.append('SVG 步进在插补调用之前读取 Done，可能连续跳步；完成判断须移至三轴调用之后并确认新目标实际到位')
    fractional=any(not float(v).is_integer() for step in trajectory.get('steps',[])
                   for point in (step.get('waypoints') or [step['to']]) for v in point)
    if fractional:
        for var in prg.findall('.//'+ns+'interface//'+ns+'variable'):
            if var.get('name') in ('pl_tx','pl_ty','pl_tz') and var.find(ns+'type/'+ns+'INT') is not None:
                problems.append('SVG 含小数路点，'+var.get('name')+' 必须使用 REAL，禁止将轨迹取整')
    return problems


def repair_svg_sequence(xml):
    """修复标准 SVG 连续跟踪步序；调用方必须创建新迭代，禁止覆写 final。"""
    root=ET.fromstring(xml)
    ns='{'+PLC_NS+'}'
    prg=root.find('.//'+ns+'pou[@name="PLC_PRG"]')
    content=prg.find('.//'+ns+'ST')[0]
    body=content.text or ''
    if 'SVG_CONTINUOUS_SEQUENCE' in body:return xml
    start=body.find('IF (pl_step <> 0) AND NOT ix_x.Busy')
    end=body.find('\nEND_IF;',start)+len('\nEND_IF;')
    if start<0 or end<=start or 'pl_step := pl_step + 1;' not in body[start:end]:
        raise ValueError('当前程序不是可确定修复的 SVG 步序模板')
    advance=body[start:end]
    body=body[:start]+body[end:]
    for a in 'xyz':
        old='IF ix_'+a+'.Done OR ix_'+a+'.Aborted THEN go_'+a+'_exe := FALSE; hm_'+a+'_exe := FALSE; rel_'+a+'_exe := FALSE; END_IF;'
        if old not in body:raise ValueError('当前程序的完成信号清理不符合标准模板')
        body=body.replace(old,'IF NOT draw_active THEN\n    '+old+'\nEND_IF;')
    advance=advance.replace('AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN',
        'AND ix_x.Done AND ix_y.Done AND ix_z.Done\n'
        '   AND ABS(x_fb_i - pl_tx) <= 1 AND ABS(y_fb_i - pl_ty) <= 1 AND ABS(z_fb_i - pl_tz) <= 1 THEN')
    offset=body.find('(* CSP_POSITION_OUTPUT:')
    if offset<0:raise ValueError('当前程序缺少位置输出')
    content.text=body[:offset]+'(* SVG_CONTINUOUS_SEQUENCE: 完成判定在插补调用后，绘制期间持续跟踪。 *)\n'+advance+'\n\n'+body[offset:]
    return '<?xml version="1.0" encoding="UTF-8"?>\n'+ET.tostring(root,encoding='unicode')


def _command_source(root, program, body, axis, scale=1):
    """原 DRIVE402 的 Setpoint 输入就是位置量；保留点动等已有位置选择。"""
    from .consistency_check import _call_args
    ns='{'+PLC_NS+'}'
    args=_call_args(body,'dv_'+axis) or ''
    match=re.search(r'\bSetpoint\s*:=\s*([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)\s*(?:,|$)',args,re.I)
    if not match:raise ValueError('无法确定 dv_'+axis+' 的原位置输入')
    source=match[1]
    variables={v.get('name'):v for v in program.findall('.//'+ns+'interface//'+ns+'variable')}
    if scale!=1:
        instance=variables.get('ix_'+axis)
        derived=instance.find(ns+'type/'+ns+'derived') if instance is not None else None
        pou=root.find('.//'+ns+'pou[@name="'+(derived.get('name') if derived is not None else '')+'"]')
        pos=next((n.get('name') for n in pou.iter(ns+'variable') if n.get('name') in ('pos','posz') and n.find(ns+'type/'+ns+'REAL') is not None),None) if pou is not None else None
        if not pos:raise ValueError('高精度位置输出需要 REAL 插补状态：ix_'+axis)
        return 'REAL_TO_INT(ix_'+axis+'.'+pos+' * '+str(float(scale))+')'
    if source.lower()=='ix_'+axis+'.setpoint':
        instance=variables.get('ix_'+axis)
        derived=instance.find(ns+'type/'+ns+'derived') if instance is not None else None
        pou=root.find('.//'+ns+'pou[@name="'+(derived.get('name') if derived is not None else '')+'"]')
        var=pou.find('.//'+ns+'variable[@name="Setpoint"]') if pou is not None else None
    elif source==axis+'_interp' and re.search(r'\b'+source+r'\s*:=\s*ix_'+axis+r'\.Setpoint\s*;',body,re.I):
        var=variables.get(source)
    else:raise ValueError('原驱动位置输入不是标准插补位置：'+source)
    if var is not None and var.find(ns+'type/'+ns+'INT') is not None:return source
    if var is not None and var.find(ns+'type/'+ns+'REAL') is not None:return 'REAL_TO_INT('+source+')'
    raise ValueError('原驱动位置输入必须是 INT 或 REAL：'+source)


def position_program_problems(xml, address_table, prog_id=2, io_list=None):
    root=ET.fromstring(xml)
    local=lambda n:n.tag.rsplit('}',1)[-1]
    program=next((n for n in root.iter() if local(n)=='pou' and n.get('name')=='PLC_PRG'),None)
    if program is None:return ['位置指令程序必须包含 PLC_PRG']
    located={n.get('name'):n.get('address') for n in program.iter() if local(n)=='variable'}
    body='\n'.join(''.join(n.itertext()) for n in program.iter() if local(n)=='ST') if program is not None else ''
    problems=[]
    scales=position_scales(io_list or [])
    for axis in 'xyz':
        name=axis+'_cmd'
        if located.get(name)!=address_table.get(name) or not address_table.get(name):
            problems.append(name+' 必须绑定 AML 中的位置输出地址')
        try:
            source=_command_source(root,program,body,axis,scales[axis])
            if not re.search(r'\b'+name+r'\s*:=\s*'+re.escape(source)+r'\s*;',body,re.I):
                problems.append(name+' 必须直接输出原驱动器使用的插补位置 '+source)
        except ValueError as exc:problems.append(str(exc))
        for rhs in re.findall(r'\b'+name+r'\s*:=\s*([^;]+);',body,re.I):
            if re.search(r'\bv_cmd\b|\b[xyz]_(?:v|sp)\b',rhs,re.I):
                problems.append(name+' 接入了速度或 HMI 通道，须改为插补位置')
    if located.get('prog_id')!='%QW20' or not re.search(r'\bprog_id\s*:=\s*'+str(prog_id)+r'\s*;',body,re.I):
        problems.append('prog_id 必须绑定 %QW20 并在扫描中写入 '+str(prog_id))
    return problems


def convert_position_program(xml, model, prog_id=2):
    """只转换已有标准轴栈的输出接线，原 SVG 步序、MC 参数、互锁保留。"""
    if not position_io(model):
        raise ValueError('请先准备位置指令 AML')
    root=ET.fromstring(xml)
    ns='{'+PLC_NS+'}'
    prg=root.find('.//'+ns+'pou[@name="PLC_PRG"]')
    if prg is None:
        raise ValueError('当前 XML 没有 PLC_PRG')
    interface=prg.find(ns+'interface')
    text=prg.find('.//'+ns+'ST')
    if interface is None or text is None or len(text)!=1:
        raise ValueError('当前程序不是可转换的 PLC_PRG ST 结构')
    content=text[0]
    body=content.text or ''
    variables={v.get('name'):v for v in interface.iter(ns+'variable')}
    for axis in 'xyz':
        instance=variables.get('ix_'+axis)
        if instance is None or not re.search(r'\bix_'+axis+r'\s*\(',body,re.I):
            raise ValueError('当前程序缺少标准插补器 ix_'+axis+'，请使用位置 AML 重新生成')
        for name in (axis+'_fb',):
            if name not in variables:
                raise ValueError('当前程序缺少 '+name)
    for name in ('run','quickstop','fault_any','all_oe'):
        if name not in variables:
            raise ValueError('当前程序缺少位置输出门控信号 '+name)
    table={p['name']:p['address'] for p in model['io_points'] if p.get('address')}
    scales=position_scales(model['io_points'])
    local_vars=ET.SubElement(interface,ns+'localVars')
    for name in ('x_cmd','y_cmd','z_cmd','prog_id'):
        address='%QW20' if name=='prog_id' else table[name]
        if any(v.get('address')==address and v.get('name')!=name for v in variables.values()):
            raise ValueError('位置输出或程序身份地址冲突：'+address)
        var=variables.get(name)
        if var is None:
            var=ET.SubElement(local_vars,ns+'variable',{'name':name})
            ET.SubElement(ET.SubElement(var,ns+'type'),ns+'INT')
        var.set('address',address)
    assignments=re.findall(r'\bprog_id\s*:=\s*([^;]+);',body,re.I)
    if assignments and any(value.strip()!=str(prog_id) for value in assignments):
        raise ValueError('原程序身份与需求规格不一致，拒绝自动改写')
    if not assignments:body='prog_id := '+str(prog_id)+';\n'+body
    if 'CSP_POSITION_OUTPUT' in body and any(v!=1 for v in scales.values()):
        body=body[:body.index('(* CSP_POSITION_OUTPUT:')]
    if 'CSP_POSITION_OUTPUT' not in body:
        body+='\n\n(* CSP_POSITION_OUTPUT: 插补位置输出，速度通道仅诊断。 *)\n'
        body+='IF run AND all_oe AND NOT quickstop AND NOT fault_any THEN\n'
        body+='\n'.join('    '+a+'_cmd := '+_command_source(root,prg,body,a,scales[a])+';' for a in 'xyz')
        body+='\nELSE\n'+'\n'.join('    '+a+'_cmd := '+('REAL_TO_INT(INT_TO_REAL('+a+'_fb) * '+str(float(scales[a]))+')' if scales[a]!=1 else a+'_fb')+';' for a in 'xyz')+'\nEND_IF;\n'
    content.text=body
    ET.register_namespace('',PLC_NS)
    ET.register_namespace('xhtml','http://www.w3.org/1999/xhtml')
    result=ET.tostring(root,encoding='unicode',xml_declaration=False)
    result='<?xml version="1.0" encoding="UTF-8"?>\n'+result
    problems=position_program_problems(result,table,prog_id,model['io_points'])
    if problems:raise ValueError('；'.join(problems))
    return result
