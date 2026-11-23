"""Scoped property edits for the workbench; reviewed rule/runtime code stays unchanged."""
import copy
import math
import re

from .common import SceneError, digest
from .device_ir import apply_patch, facts_digest, validate_device_ir, values


def interface_properties(ir):
    """Describe local mechanical frames and explicit edit scope, without inventing frames."""
    frames={f['id']:f for f in ir['interfaces']}
    joints={j['id']:j for j in ir['joints']}
    result={}
    for fid,frame in frames.items():
        owner=frame['component_id'];p=values(frame)
        record={'editable':False,'required_ids':[owner],'position_m':None,'rpy_rad':None,'joint_bindings':[],
                'connected':any(fid in (c['a'],c['b']) for c in ir['connections'])}
        if frame['type']=='MechanicalMount':
            record['editable']=True
            if all('position_'+a+'_m' in p for a in 'xyz'):
                record['position_m']=[p['position_'+a+'_m'] for a in 'xyz']
                record['rpy_rad']=[_angle(frame,a) for a in 'xyz']
            required={owner}
            for link in ir['connections']:
                if fid in (link['a'],link['b']):required.add(frames[link['b'] if link['a']==fid else link['a']]['component_id'])
            for mount in ir.get('assemblies',[]):
                if fid in (mount['parent_interface'],mount['child_interface']):
                    required.update((mount['parent_component'],mount['child_component']))
            record['required_ids']=sorted(required)
        elif frame['type']=='JointAttachment' and owner not in joints:
            bindings=[];required={owner}
            for link in ir['connections']:
                if fid not in (link['a'],link['b']):continue
                other=frames[link['b'] if link['a']==fid else link['a']]
                joint=joints.get(other['component_id'])
                if not joint or other['name'] not in ('parent_body','child_body'):continue
                side=other['name'].split('_')[0]
                if joint[side+'_body']!=owner:continue
                bindings.append({'joint_id':joint['id'],'side':side})
                required.update((joint['id'],joint['parent_body'],joint['child_body']))
            if bindings:
                anchors=[joints[b['joint_id']][b['side']+'_anchor_m'] for b in bindings]
                record.update(editable=all(a==anchors[0] for a in anchors),position_m=copy.deepcopy(anchors[0]),
                              required_ids=sorted(required),joint_bindings=bindings)
                if all(joints[b['joint_id']].get('definition_source')=='interfaces' for b in bindings):
                    record['rpy_rad']=[_angle(frame,a) for a in 'xyz']
        result[fid]=record
    return result


def _angle(frame,axis):
    rec=frame['parameters'].get('rotation_'+axis+'_deg',{})
    value=rec.get('value',0)
    return value if rec.get('original_unit') else math.radians(value)


def _vec(value):
    if not isinstance(value,list) or len(value)!=3 or any(type(x) not in (int,float) or not math.isfinite(x) for x in value):
        raise SceneError('Interface pose requires three finite numeric coordinates','assembly')
    return value


def _write_frame(frame,position,angles,reason):
    for stem,coords in [('position_',position),('rotation_',angles)]:
        for axis,value in zip('xyz',coords):
            key=stem+axis+('_m' if stem=='position_' else '_deg')
            previous=frame['parameters'].get(key)
            # Legacy degree-named fields without a Unit are consumed as degrees by the builder.
            if stem=='rotation_' and previous and not previous.get('original_unit'):value=math.degrees(value)
            record=copy.deepcopy(previous) if previous else {
                'unit':'m' if stem=='position_' else 'rad','original_unit':'m' if stem=='position_' else 'rad',
                'original_value':None,'datatype':'xs:double','children':{}}
            record['value']=value
            record['source']={'kind':'user_revision','previous':copy.deepcopy(previous.get('source')) if previous else None,'reason':reason}
            frame['parameters'][key]=record


def apply_property_patch(ir,patch):
    operations=patch.get('operations',[])
    if not isinstance(operations,list) or any(not isinstance(op,dict) for op in operations):
        raise SceneError('Expected local edit operations','device_description')
    property_ops={'set_interface_pose','add_interface'}
    if not any(op.get('op') in property_ops for op in operations):return apply_patch(ir,patch)
    if patch.get('base_sha256')!=facts_digest(ir):raise SceneError('Stale scene edit','device_description')
    if not operations or len(operations)>64 or any(op.get('op') not in property_ops for op in operations):
        raise SceneError('Interface properties require a separate local revision','device_description')
    result=copy.deepcopy(ir);components={c['id']:c for c in result['components']}
    scope=set(patch.get('editable_ids',[]))
    if not scope.issubset(components):raise SceneError('Unknown interface edit scope','device_description')
    for op in operations:
        cid=op.get('component_id')
        if cid not in components or cid not in scope:raise SceneError('Edit outside explicit user scope','device_description')
        position=_vec(op.get('position_m'))
        if op['op']=='add_interface':
            fid=op.get('interface_id','');name=op.get('name','')
            existing_ids={r['id'] for key in ('components','interfaces','connections','joints') for r in result[key]}
            if not isinstance(fid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',fid) or fid in existing_ids:
                raise SceneError('Invalid/duplicate interface ID','device_description')
            if not isinstance(name,str) or not name.strip() or len(name)>100 or any(f['name']==name for f in result['interfaces'] if f['component_id']==cid):
                raise SceneError('Invalid/duplicate component interface name','device_description')
            if cid in {j['id'] for j in result['joints']}:raise SceneError('Define mount frames on rigid components','assembly')
            angles=_vec(op.get('rpy_rad'))
            frame={'id':fid,'name':name,'component_id':cid,'type':'MechanicalMount','parameters':{},
                   'source':{'kind':'user_revision','reason':patch.get('reason')}}
            result['interfaces'].append(frame);components[cid]['interfaces'].append(fid)
            _write_frame(frame,position,angles,patch.get('reason'))
        else:
            fid=op.get('interface_id');frame=next((f for f in result['interfaces'] if f['id']==fid),None)
            if not frame or frame['component_id']!=cid:raise SceneError('Interface owner mismatch','assembly')
            description=interface_properties(result)[fid]
            if not description['editable']:raise SceneError('Signal/topology interfaces are read-only','assembly')
            if not set(description['required_ids']).issubset(scope):raise SceneError('Interface edit requires connected nodes in explicit scope','assembly')
            derived=[j for j in result['joints'] if j.get('definition_source')=='interfaces' and fid in (j.get('parent_interface'),j.get('child_interface'))]
            if derived:
                _write_frame(frame,position,_vec(op.get('rpy_rad',description['rpy_rad'] or [0,0,0])),patch.get('reason'))
                from .verification import joint_from_interfaces
                for joint in derived:joint.update(joint_from_interfaces(result,joint,required=True))
            elif frame['type']=='MechanicalMount':_write_frame(frame,position,_vec(op.get('rpy_rad')),patch.get('reason'))
            else:
                if op.get('rpy_rad') is not None:raise SceneError('Joint direction is defined by its axis, not interface rotation','assembly')
                # Anchor position is canonical in DeviceIR.joints; update it rather than unused attributes.
                for binding in description['joint_bindings']:
                    joint=next(j for j in result['joints'] if j['id']==binding['joint_id'])
                    side=binding['side'];joint[side+'_anchor_m']=copy.deepcopy(position)
                    joint['source']={'kind':'user_revision','previous':joint['source'],'reason':patch.get('reason')}
                    for axis,value in zip('xyz',position):
                        rec=components[joint['id']]['parameters'].get(side+'_anchor_'+axis+'_m')
                        if rec:
                            rec['value']=value;rec['source']={'kind':'user_revision','previous':rec.get('source'),'reason':patch.get('reason')}
    # Interface edits never alter PLC channels, IO bindings or connection topology.
    if digest(result['io'])!=digest(ir['io']) or result['connections']!=ir['connections']:raise SceneError('Interface edit changed IO/topology','assembly')
    result['revision']=ir['revision']+1;result['changes'].append(copy.deepcopy(patch))
    validate_device_ir(result);result['facts_sha256']=facts_digest(result)
    return result
