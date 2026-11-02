"""Lossless CAEX projection plus a normalized physical view; no PLC spec changes."""
import copy
import math
from pathlib import Path
import re
import uuid
import xml.etree.ElementTree as ET

from .common import SceneError, digest, vector
from ..aml_parser import parse_aml, build_io_list

FORMAT='308sjk.device_ir/0.1-draft'
UNITS={'m':('m',1), 'mm':('m',.001), 'cm':('m',.01), 's':('s',1),
       'ms':('s',.001), 'deg':('rad',math.pi/180), 'rad':('rad',1),
       'kg':('kg',1),'g':('kg',.001), 'm/s':('m/s',1), 'mm/s':('m/s',.001),
       'kg/m3':('kg/m3',1),'kg/m^3':('kg/m3',1), 'count':('count',1)}


def children(node, tag):
    return [c for c in node if c.tag.rsplit('}',1)[-1]==tag]


def first(node,tag):
    return next(iter(children(node,tag)),None)


def raw_value(node):
    v=first(node,'Value')
    if v is None or v.text is None:return None
    text=v.text.strip()
    if node.get('AttributeDataType','').endswith('string'):return text
    if text.lower() in ('true','false'):return text.lower()=='true'
    try:return int(text)
    except ValueError:
        try:return float(text)
        except ValueError:return text


def attributes(node, locator, pending):
    result={}
    for a in children(node,'Attribute'):
        name=a.get('Name')
        if not name or name in result:raise SceneError('Duplicate/empty attribute at '+locator,'device_description')
        raw=raw_value(a)
        nested=attributes(a,locator+'/'+name,pending)
        unit=a.get('Unit')
        normalized,factor=UNITS.get(unit,(unit,1))
        value=raw
        if unit and unit not in UNITS:
            pending.append({'path':locator+'/'+name,'reason':'unknown_unit','value':unit})
        if type(raw) in (int,float):
            if not math.isfinite(raw):raise SceneError('Nonfinite AML parameter '+name,'device_description')
            value=raw*factor
        result[name]={'value':value,'unit':normalized,'original_value':raw,'original_unit':unit,
                      'datatype':a.get('AttributeDataType'), 'children':nested,
                      'source':{'kind':nested.get('provenance_kind',{}).get('value','aml'),
                                'locator':nested.get('source_locator',{}).get('value',locator+'/'+name)}}
    return result


def values(record):
    return {k:v['value'] for k,v in record.get('parameters',{}).items()}


def parse_device_ir(source):
    path=Path(source)
    content=path.read_bytes()
    if b'<!DOCTYPE' in content.upper() or b'<!ENTITY' in content.upper():
        raise SceneError('DTD/entity declarations are not supported','device_description')
    try:root=ET.fromstring(content)
    except ET.ParseError as exc:raise SceneError('AML XML 无法解析：'+str(exc),'device_description') from exc
    hierarchy=next((e for e in root.iter() if e.tag.rsplit('}',1)[-1]=='InstanceHierarchy'),None)
    if hierarchy is None:raise SceneError('Missing InstanceHierarchy','device_description')
    legacy,problems=parse_aml(path)
    io,pending_io=build_io_list(legacy)
    if problems or pending_io:raise SceneError(str(problems+pending_io),'device_description')
    ir={'format':FORMAT,'source':{'file':str(path.resolve()),'sha256':__import__('hashlib').sha256(content).hexdigest(),
         'caex_version':root.get('SchemaVersion')}, 'station':hierarchy.get('Name'),
        'coordinate_system':'right_handed_z_up','units':{'length':'m','angle':'rad','time':'s','mass':'kg'},
        'components':[],'joints':[],'assemblies':[],'interfaces':[],'connections':[],'io':[],
        'pending':[],'revision':0,'changes':[]}
    seen=set(); iface_nodes={}; link_nodes=[]; plc_ports={}
    def stable(node,locator):
        result=node.get('ID')
        if not result:
            result=str(uuid.uuid5(uuid.NAMESPACE_URL,ir['station']+'/'+locator))
            ir['pending'].append({'path':locator,'reason':'missing_stable_id','generated_id':result})
        if result in seen:raise SceneError('Duplicate CAEX ID '+result,'device_description')
        seen.add(result)
        return result
    def walk(node,parent,locator):
        cid=stable(node,locator); attrs=attributes(node,locator,ir['pending'])
        p={k:v['value'] for k,v in attrs.items()}
        role=first(node,'RoleRequirements')
        role_path=role.get('RefBaseRoleClassPath','') if role is not None else ''
        # Degree-named fields in old AML without a CAEX Unit still have explicit semantics.
        xyz=[p.get('position_'+a+'_m',0) for a in 'xyz']
        angles=[attrs.get('rotation_'+a+'_deg',{}).get('value',0) for a in 'xyz']
        for i,a in enumerate('xyz'):
            rec=attrs.get('rotation_'+a+'_deg',{})
            if rec and not rec.get('original_unit'):angles[i]=math.radians(angles[i])
        c={'id':cid,'name':node.get('Name'),'type':p.get('component_kind') or node.get('RefBaseSystemUnitPath','').rsplit('/',1)[-1],
           'parent_id':parent,'role':role_path.rsplit('/',1)[-1] or p.get('semantic_role'),
           'parameters':attrs,'pose':{'position_m':xyz,'rpy_rad':angles},
           'source':{'locator':locator},'interfaces':[]}
        if p.get('coordinate_frame') not in (None,'right_handed_z_up'):
            ir['pending'].append({'path':locator,'reason':'coordinate_conversion_required','value':p['coordinate_frame']})
        ir['components'].append(c)
        for f in children(node,'ExternalInterface'):
            fid=stable(f,locator+'#'+str(f.get('Name')))
            fa=attributes(f,locator+'#'+str(f.get('Name')),ir['pending'])
            record={'id':fid,'name':f.get('Name'),'component_id':cid,
                    'type':f.get('RefBaseClassPath','').rsplit('/',1)[-1], 'parameters':fa,
                    'source':{'locator':locator+'#'+str(f.get('Name'))}}
            ir['interfaces'].append(record);c['interfaces'].append(fid);iface_nodes[fid]=record
            if record['name'] in {item['name'] for item in io}:plc_ports[record['name']]=record
        link_nodes.extend(children(node,'InternalLink'))
        for child in children(node,'InternalElement'):
            walk(child,cid,locator+'/'+str(child.get('Name')))
    for node in children(hierarchy,'InternalElement'):walk(node,None,str(node.get('Name')))
    from .simulation_contract import parse_requirements
    requirements=parse_requirements(hierarchy,attributes,ir['pending'])
    if requirements is not None:ir['simulation_requirements']=requirements
    link_nodes.extend(children(hierarchy,'InternalLink'))
    for node in link_nodes:
        a,b=node.get('RefPartnerSideA'),node.get('RefPartnerSideB')
        if a not in iface_nodes or b not in iface_nodes:raise SceneError('Unresolved link '+str(node.get('Name')),'assembly')
        ir['connections'].append({'id':stable(node,'link/'+str(node.get('Name'))),'name':node.get('Name'),
                                 'a':a,'b':b,'parameters':attributes(node,'link/'+str(node.get('Name')),ir['pending'])})
    interfaces={i['id']:i for i in ir['interfaces']}
    for link in ir['connections']:
        a,b=interfaces[link['a']],interfaces[link['b']]
        if a['type']=='MechanicalMount' and b['type']=='MechanicalMount':
            ends={values(f).get('assembly_role'):f for f in (a,b)}
            if set(ends)!={'parent','child'}:
                ir['pending'].append({'path':link['id'],'reason':'mount_requires_parent_child_roles'});continue
            p=values(link)
            ir['assemblies'].append({'id':link['id'],'parent_component':ends['parent']['component_id'],
                'child_component':ends['child']['component_id'],'parent_interface':ends['parent']['id'],
                'child_interface':ends['child']['id'],'placement':p.get('placement_mode','check'),
                'offset':{'position_m':[p.get('offset_'+a+'_m',0) for a in 'xyz'],
                          'rpy_rad':[p.get('offset_'+a+'_rad',0) for a in 'xyz']},'source':{'connection_id':link['id']}})
    for point in io:
        port=plc_ports.get(point['name'])
        attrs=values(port) if port else {}
        opposite=[]
        if port:
            for link in ir['connections']:
                if port['id'] in (link['a'],link['b']):opposite.append(interfaces[link['b'] if link['a']==port['id'] else link['a']])
        if len(opposite)!=1:
            ir['pending'].append({'path':'io/'+point['name'],'reason':'expected_one_physical_binding','count':len(opposite)})
        target=opposite[0] if len(opposite)==1 else None
        target_values=values(target) if target else {}
        original_writer=attrs.get('value_writer')
        writer={'device_model':'simulation','fault_injector':'fault_harness','fault_injection':'fault_harness'}.get(original_writer,original_writer)
        if point['dir']=='output':writer='plc'
        if writer is None:
            ir['pending'].append({'path':'io/'+point['name'],'reason':'missing_value_writer'})
        ir['io'].append({**point,'id':port['id'] if port else point['name'],
                         'component_id':target['component_id'] if target else None,
                         'quantity':target_values.get('quantity',attrs.get('quantity')),
                         'writer':writer,'original_writer':original_writer,
                         'initial':attrs.get('initial_value',False if point['type']=='BOOL' else 0),
                         'physical_per_plc_unit':attrs.get('reference_value_per_plc_unit',1),
                         'source':{'interface_id':port['id'] if port else None,'attributes':port['parameters'] if port else {}}})
    for c in ir['components']:
        p=values(c)
        if p.get('joint_kind'):
            ends={}
            for fid in c['interfaces']:
                for l in ir['connections']:
                    if fid in (l['a'],l['b']):
                        target=interfaces[l['b'] if l['a']==fid else l['a']]
                        ends[interfaces[fid]['name']]=target['component_id']
            ir['joints'].append({'id':c['id'],'name':c['name'],'type':{'prismatic':'slide','revolute':'hinge'}.get(p['joint_kind'],p['joint_kind']),
                 'parent_body':ends.get('parent_body'),'child_body':ends.get('child_body'),
                 'axis':[p.get('joint_axis_'+a,0) for a in 'xyz'],'axis_frame':'child',
                 'range':[p.get('joint_lower_m',p.get('joint_lower_rad',p.get('joint_lower_deg'))),
                          p.get('joint_upper_m',p.get('joint_upper_rad',p.get('joint_upper_deg')))],
                 'initial':p.get('joint_initial_m',p.get('joint_initial_rad',p.get('joint_initial_deg',0))),
                 'parent_anchor_m':[p.get('parent_anchor_'+a+'_m',0) for a in 'xyz'],
                 'child_anchor_m':[p.get('child_anchor_'+a+'_m',0) for a in 'xyz'],
                 'source':c['source']})
    couplings=[]
    for c in ir['components']:
        p=values(c)
        if p.get('coupled_to'):
            couplings.append({'id':'coupling_'+c['id'],'leader':p['coupled_to'],'follower':c['id'],
                'ratio':p.get('coupling_ratio',1),'offset':p.get('coupling_offset',0)})
    if couplings:ir['couplings']=couplings
    validate_device_ir(ir)
    ir['facts_sha256']=facts_digest(ir)
    return ir


def facts_digest(ir):
    keys=['source','components','joints','assemblies','interfaces','connections','io','pending']
    keys.extend(k for k in ('simulation_requirements','couplings') if k in ir)
    return digest({k:ir.get(k,[]) for k in keys})


def validate_device_ir(ir):
    if ir.get('format')!=FORMAT:raise SceneError('Unsupported DeviceIR version','device_description')
    comps={c['id']:c for c in ir['components']}
    if len(comps)!=len(ir['components']):raise SceneError('Duplicate component ID','device_description')
    for c in comps.values():
        vector(c['pose']['position_m']);vector(c['pose']['rpy_rad'])
        seen={c['id']};parent=c.get('parent_id')
        while parent:
            if parent not in comps or parent in seen:raise SceneError('Invalid/cyclic component parent','assembly')
            seen.add(parent);parent=comps[parent].get('parent_id')
    interfaces={f['id']:f for f in ir['interfaces']}
    if len(interfaces)!=len(ir['interfaces']):raise SceneError('Duplicate interface ID','device_description')
    for f in interfaces.values():
        if f['component_id'] not in comps:raise SceneError('Interface owner missing','assembly')
    for link in ir['connections']:
        if link['a'] not in interfaces or link['b'] not in interfaces:raise SceneError('Connection endpoint missing','assembly')
    mounted=set()
    for a in ir.get('assemblies',[]):
        if a['placement'] not in ('check','solve') or a['child_component'] in mounted:
            raise SceneError('Invalid/duplicate mounting placement','assembly')
        mounted.add(a['child_component'])
        for side in ('parent','child'):
            f=interfaces.get(a[side+'_interface'])
            if not f or f['component_id']!=a[side+'_component']:raise SceneError('Mount interface owner mismatch','assembly')
        if a['parent_component']==a['child_component']:raise SceneError('Mount connects body to itself','assembly')
        vector(a.get('offset',{}).get('position_m',[0,0,0]));vector(a.get('offset',{}).get('rpy_rad',[0,0,0]))
    names=set();addresses=set();bindings=set()
    for p in ir['io']:
        if p['name'] in names or p['address'] in addresses:raise SceneError('Duplicate IO identity/address','device_description')
        names.add(p['name']);addresses.add(p['address'])
        pattern=r'%QX\d+\.[0-7]' if p['type']=='BOOL' else r'%QW\d+'
        if p['type'] not in ('BOOL','INT') or not re.fullmatch(pattern,str(p['address'])):
            raise SceneError('Invalid AML IO type/address','device_description')
        if p['dir'] not in ('input','output') or (p['component_id'] is not None and p['component_id'] not in comps):
            raise SceneError('Invalid IO binding','device_description')
        if p['dir']=='output' and p['writer']!='plc':raise SceneError('PLC output writer mismatch','device_description')
        if p['dir']=='input' and p['writer'] not in ('simulation','operator','fault_harness',None):
            raise SceneError('Unknown input writer '+str(p['writer']),'device_description')
        binding=(p['component_id'],p['quantity'])
        if p['component_id'] and binding in bindings:raise SceneError('Duplicate physical IO binding','device_description')
        bindings.add(binding)
        if p['type']=='BOOL' and type(p['initial']) is not bool:raise SceneError('Invalid BOOL initial value','device_description')
        if p['type']=='INT' and (type(p['initial']) is not int or (p.get('range') and not p['range'][0]<=p['initial']<=p['range'][1])):
            raise SceneError('Invalid INT initial/range','device_description')
    joint_children=set()
    for j in ir['joints']:
        if j['parent_body'] not in comps or j['child_body'] not in comps or j['parent_body']==j['child_body']:
            raise SceneError('Joint has unresolved/same bodies','assembly')
        if j['type'] not in ('slide','hinge','ball','free'):raise SceneError('Unsupported joint kind','assembly')
        if j['child_body'] in joint_children or j['child_body'] in mounted:raise SceneError('Multiple kinematic parents','assembly')
        joint_children.add(j['child_body']);vector(j['parent_anchor_m']);vector(j['child_anchor_m'])
        if j['type'] in ('slide','hinge'):
            if vector(j['axis']).dot(vector(j['axis']))<1e-20:raise SceneError('Zero joint axis','assembly')
            if any(v is None for v in j['range']) or j['range'][0]>=j['range'][1]:raise SceneError('Invalid joint range','assembly')
            if not j['range'][0]<=j['initial']<=j['range'][1]:raise SceneError('Joint initial outside range','assembly')
    from .simulation_contract import validate_requirements, validate_couplings
    validate_couplings(ir);validate_requirements(ir)
    return ir


def apply_patch(ir, patch):
    """Explicit user-requested device revision. IO and component rule code are immutable."""
    if patch.get('base_sha256')!=facts_digest(ir):raise SceneError('Stale scene edit','device_description')
    result=copy.deepcopy(ir);comps={c['id']:c for c in result['components']}
    scope=set(patch.get('editable_ids',[]));ops=patch.get('operations',[])
    if not ops or len(ops)>64:raise SceneError('Expected 1..64 local edits','device_description')
    for op in ops:
        cid=op.get('component_id')
        if cid not in scope or cid not in comps:raise SceneError('Edit outside explicit user scope','device_description')
        if op['op']=='set_pose':
            vector(op['position_m']);vector(op['rpy_rad'])
            comps[cid]['pose']={'position_m':op['position_m'],'rpy_rad':op['rpy_rad']}
            for prefix,coords in (('position_',op['position_m']),('rotation_',op['rpy_rad'])):
                for axis,value in zip('xyz',coords):
                    key=prefix+axis+('_m' if prefix=='position_' else '_deg')
                    if key in comps[cid]['parameters']:
                        rec=comps[cid]['parameters'][key];rec['value']=value
                        rec['source']={'kind':'user_revision','previous':rec['source'],'reason':patch.get('reason')}
        elif op['op']=='set_parameter':
            key=op['parameter']
            if key in ('component_kind','coordinate_frame','driven_joint_id','short_product_id','tall_product_id'):
                raise SceneError('Identity/reference fields require a dedicated description revision','device_description')
            if cid in {j['id'] for j in result['joints']} or key.startswith(('position_','rotation_')):
                raise SceneError('Use set_joint/set_pose for mechanical fields','device_description')
            if key not in comps[cid]['parameters']:raise SceneError('Unknown parameter','device_description')
            record=comps[cid]['parameters'][key]
            old=record['value'];new=op['value']
            if type(old) in (int,float):
                if type(new) not in (int,float) or not math.isfinite(new):raise SceneError('Expected finite numeric parameter','device_description')
            elif type(new) is not type(old):raise SceneError('Parameter type cannot change','device_description')
            record['value']=op['value'];record['source']={'kind':'user_revision','previous':record['source'],'reason':patch.get('reason')}
        elif op['op']=='set_joint':
            joint=next((j for j in result['joints'] if j['id']==cid),None)
            changes=op.get('changes',{})
            if not joint or not changes or set(changes)-{'parent_body','child_body','axis','range','initial','parent_anchor_m','child_anchor_m'}:
                raise SceneError('Invalid joint edit','assembly')
            involved={joint['parent_body'],joint['child_body'],changes.get('parent_body',joint['parent_body']),changes.get('child_body',joint['child_body'])}
            if not involved.issubset(scope):raise SceneError('Joint edit must include connected bodies in explicit scope','assembly')
            joint.update(copy.deepcopy(changes));joint['source']={'kind':'user_revision','reason':patch.get('reason')}
        elif op['op']=='set_mount':
            assembly=next((a for a in result.get('assemblies',[]) if a['child_component']==cid),None)
            changes=op.get('changes',{})
            if not assembly or not changes or set(changes)-{'parent_component','parent_interface','child_interface','offset','placement'}:
                raise SceneError('Invalid mount edit','assembly')
            if not {assembly['parent_component'],changes.get('parent_component',assembly['parent_component'])}.issubset(scope):
                raise SceneError('Mount edit requires connected components in scope','assembly')
            assembly.update(copy.deepcopy(changes));assembly['source']={'kind':'user_revision','reason':patch.get('reason')}
        else:raise SceneError('Unsupported local edit '+str(op['op']),'device_description')
    result['revision']+=1;result['changes'].append(copy.deepcopy(patch))
    validate_device_ir(result);result['facts_sha256']=facts_digest(result)
    return result
