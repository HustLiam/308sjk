"""Recognized standard interpolators: finish a discrete step at the requested target."""
import re
import xml.etree.ElementTree as ET


def finish_interpolators(xml, spec):
    from .position_control import position_scales
    scales=position_scales(spec.get('io_list',[]))
    if not any(value>1 for value in scales.values()):return xml,[]
    root=ET.fromstring(xml);ns='{http://www.plcopen.org/xml/tc6_0201}'
    program=root.find('.//'+ns+'pou[@name="PLC_PRG"]')
    if program is None:return xml,[]
    types={}
    for axis in 'xyz':
        instance=program.find('.//'+ns+'variable[@name="ix_'+axis+'"]/'+ns+'type/'+ns+'derived')
        if instance is not None and scales[axis]>1:
            name=instance.get('name');types[name]=min(types.get(name,1),.5/scales[axis])
    changed=[]
    for name,threshold in types.items():
        if name not in ('INTERP','INTERP_Z'):continue
        pou=root.find('.//'+ns+'pou[@name="'+name+'"]')
        if pou is None:continue
        st=pou.find('.//'+ns+'ST')
        if st is None or len(st)!=1:continue
        node=st[0];body=node.text or ''
        if 'VERIFIED_ENDPOINT' in body:continue
        pos,tgt,vel=('posz','tgtz','velz') if name=='INTERP_Z' else ('pos','tgt','vel')
        required=(r'\bIF\s+hold_req\s+THEN',r'\bELSIF\s+Busy\s+THEN',r'\bBusy\s*:=\s*FALSE;\s*Done\s*:=\s*TRUE;')
        if not all(re.search(value,body,re.I) for value in required):continue
        if any(pou.find('.//'+ns+'variable[@name="'+v+'"]/'+ns+'type/'+ns+'REAL') is None for v in (pos,tgt,vel)):
            continue
        pattern=r'\b'+pos+r'\s*:=\s*'+pos+r'\s*\+\s*'+vel+r'\s*\*\s*(SCAN_T\w*)\s*;'
        integrations=list(re.finditer(pattern,body))
        if len(integrations)!=1:continue
        integration=integrations[0];scan=integration[1]
        completion=('(* VERIFIED_ENDPOINT: only clamp a forward crossing; halt still decelerates. *)\n'
            'IF Busy AND NOT hold_req AND (('+tgt+' - '+pos+') * '+vel+' >= 0.0)\n'
            '   AND ABS('+tgt+' - '+pos+') <= ABS('+vel+' * '+scan+') THEN\n'
            '    '+pos+' := '+tgt+'; '+vel+' := 0.0; Busy := FALSE; Done := TRUE; Aborted := FALSE;\n'
            'ELSE\n    '+integration[0]+'\nEND_IF;')
        body=body[:integration.start()]+completion+body[integration.end():]
        # The old half-integer retarget threshold loses nearby REAL SVG points.
        retarget=r'(ABS\(pos_target\s*-\s*'+tgt+r'\)\s*>\s*)(0\.499)\b'
        body=re.sub(retarget,lambda m:m[1]+format(threshold,'.9g'),body)
        node.text=body;changed.append(name)
    if not changed:return xml,[]
    return '<?xml version="1.0" encoding="UTF-8"?>\n'+ET.tostring(root,encoding='unicode'),changed
