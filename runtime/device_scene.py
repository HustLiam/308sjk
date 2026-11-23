"""DeviceIR -> MJCF plus physical IO kernels, independent of PLC code generation.

This format is separate from the frozen SceneSpec v1.1. Candidate mode is only
for isolated component/model review. No candidate can be frozen for PLC use.
"""
import copy
import hashlib
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
import mujoco

from src.agent.scene_agent.common import SceneError, digest, expression, pose_matrix, quaternion, vector, write_json
from src.agent.scene_agent.device_ir import values, validate_device_ir, facts_digest
from src.agent.scene_agent.rules import ApprovalRequired, validate_rule

FORMAT='308sjk.physical_scene/0.1-draft'


def _numbers(xs):return ' '.join(format(float(x),'.12g') for x in xs)


def _mass_geometries(geoms,physical):
    """Distribute an explicit body mass by primitive volume; visuals add no mass."""
    geoms=copy.deepcopy(geoms)
    if 'mass_kg' not in physical:return geoms
    mass=physical['mass_kg']
    if type(mass) not in (int,float) or not math.isfinite(mass) or mass<=0:raise SceneError('Invalid body mass')
    moving=[g for g in geoms if g.get('collision',True)]
    if not moving or any('mass' in g or 'density' in g or g['type']=='mesh' for g in moving):
        raise SceneError('Body mass requires primitive colliders without conflicting mass/density')
    def volume(g):
        s=g['size'];kind=g['type']
        if kind=='box':return 8*s[0]*s[1]*s[2]
        if kind=='sphere':return 4*math.pi*s[0]**3/3
        if kind=='ellipsoid':return 4*math.pi*s[0]*s[1]*s[2]/3
        return 2*math.pi*s[0]**2*s[1]+(4*math.pi*s[0]**3/3 if kind=='capsule' else 0)
    volumes=[volume(g) for g in moving]
    for g,v in zip(moving,volumes):g['mass']=mass*v/sum(volumes)
    return geoms


def build_device_scene(ir, library, output, *, candidates=None, pool_per_product=4, blender='E:/Blender/blender.exe'):
    validate_device_ir(ir)
    from src.agent.scene_agent.verification import enabled,apply_physics,PHYSICS,PROFILE
    fixed_profile=enabled(ir)
    def evaluated(entry,component):
        return validate_rule(apply_physics(entry['rule']) if fixed_profile else entry['rule'],values(component))
    if ir.get('pending'):raise SceneError('DeviceIR has unresolved items','device_description',{'pending':ir['pending']})
    if not 1<=pool_per_product<=32:raise SceneError('Pool size must be 1..32')
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    candidate_mode=candidates is not None
    components={c['id']:c for c in ir['components']};world={};resolved={};missing=[]
    for c in components.values():
        if c['id'] in {j['id'] for j in ir['joints']}:continue
        try:
            if candidate_mode:
                entry=candidates[c['type']]
                rule=entry['rule'];validate_rule(rule,values(c))
            else:entry=library.resolve(c['type'],values(c))
            if entry['rule'].get('blender_script'):
                from src.agent.scene_agent.validation import instantiate_geometry
                from src.agent.scene_agent.common import confined
                instance=confined(output,'instances/'+digest(c['id']))
                manifest,checks,issues=instantiate_geometry(entry['rule'],values(c),instance/'geometry',blender=blender)
                if issues or not all(x['passed'] for x in checks):
                    raise SceneError('Parameterized Blender instance failed checks',details={'checks':checks,'unresolved':issues})
                source=Path(entry['directory'])/'geometry'/'component_manifest.json'
                if not candidate_mode and source.is_file():
                    from src.agent.scene_agent.common import read_json
                    if read_json(source)['blender_version']!=manifest['blender_version']:
                        raise SceneError('Blender version differs from reviewed version; revalidate rule')
                entry={**entry,'asset_directory':str(instance),'instance_manifest':manifest,'instance_checks':checks}
            resolved[c['id']]=entry
        except (ApprovalRequired,KeyError):missing.append(c['type'])
    if missing:raise ApprovalRequired(sorted(set(missing)))
    assembly_by_child={a['child_component']:a for a in ir.get('assemblies',[])}
    iface_by_id={f['id']:f for f in ir['interfaces']}
    def mount_frame(cid,fid):
        frame=iface_by_id[fid];p=values(frame)
        if all('position_'+a+'_m' in p for a in 'xyz'):
            from src.agent.scene_agent.common import transform
            angles=[]
            for a in 'xyz':
                rec=frame['parameters'].get('rotation_'+a+'_deg',{})
                angles.append(rec.get('value',0) if rec.get('original_unit') else math.radians(rec.get('value',0)))
            return transform([p['position_'+a+'_m'] for a in 'xyz'],angles)
        recipe=evaluated(resolved[cid],components[cid])
        found=[f for f in recipe.get('interfaces',[]) if f['name']==frame['name']]
        if len(found)!=1:raise SceneError('Mount frame missing or ambiguous: '+frame['name'],'assembly')
        return pose_matrix(found[0])
    pose_active=set()
    def wt(cid):
        if cid not in world:
            if cid in pose_active:raise SceneError('Mount/parent transform cycle','assembly')
            pose_active.add(cid)
            c=components[cid];parent=c['parent_id']
            mount=assembly_by_child.get(cid)
            if mount and mount['placement']=='solve':
                world[cid]=(wt(mount['parent_component'])@mount_frame(mount['parent_component'],mount['parent_interface'])
                            @pose_matrix(mount.get('offset',{}))@np.linalg.inv(mount_frame(cid,mount['child_interface'])))
            else:world[cid]=(wt(parent) if parent else np.eye(4))@pose_matrix(c['pose'])
            pose_active.remove(cid)
        return world[cid]
    for cid in components:wt(cid)
    root=ET.Element('mujoco',model=ir['station'])
    ET.SubElement(root,'compiler',angle='radian',autolimits='true')
    ET.SubElement(root,'option',timestep='0.002',gravity='0 0 -9.81',integrator='implicitfast')
    ET.SubElement(root,'statistic',center='1.5 0 0.5',extent='4')
    asset=ET.SubElement(root,'asset');wb=ET.SubElement(root,'worldbody')
    ET.SubElement(wb,'light',pos='1 0 5',dir='0 0 -1',diffuse='0.8 0.8 0.8')
    ET.SubElement(wb,'geom',name='floor',type='plane',size='10 10 .1',rgba='.85 .87 .9 1')
    actuators=ET.SubElement(root,'actuator');eq=ET.SubElement(root,'equality')
    metadata={'format':FORMAT,'candidate':candidate_mode,'device_ir_sha256':facts_digest(ir),
              'components':{},'joints':{},'products':[],'rules':[], 'io':copy.deepcopy(ir['io']),
              'physics_dt':.002,'model_checks':[], 'assets':{}, 'asset_root':str(output.resolve()),
              'material_flow':[]}
    import platform
    metadata['software_versions']={'python':platform.python_version(),'mujoco':mujoco.__version__,'numpy':np.__version__}
    if fixed_profile:metadata.update(verification_profile=PROFILE,physics_profile=copy.deepcopy(PHYSICS))
    for a in ir.get('assemblies',[]):
        actual=wt(a['child_component'])@mount_frame(a['child_component'],a['child_interface'])
        expected=wt(a['parent_component'])@mount_frame(a['parent_component'],a['parent_interface'])@pose_matrix(a.get('offset',{}))
        residual=float(np.max(np.abs(actual-expected)))
        metadata['model_checks'].append({'name':'mount_alignment:'+a['id'],'passed':residual<=1e-6,'residual':residual})
        if residual>1e-6:raise SceneError('Fixed mounting interfaces do not align','assembly',{'id':a['id'],'residual':residual})
    for connection in ir['connections']:
        a,b=iface_by_id[connection['a']],iface_by_id[connection['b']]
        if a['type']=='MaterialFlow' and b['type']=='MaterialFlow':
            metadata['material_flow'].append({'from':a['component_id'],'to':b['component_id'],
                                               'a_port':a['name'],'b_port':b['name']})
    physical_joints={j['child_body']:j for j in ir['joints']}
    body_nodes={};body_names={};active=set()
    def add_geom(parent,g,name,entry,physical):
        attrs={'name':name,'type':g['type'],'pos':_numbers(g.get('pos',[0,0,0])),
               'rgba':_numbers(g.get('rgba',[.4,.55,.7,1]))}
        if g.get('rpy_rad'):attrs['quat']=_numbers(quaternion(pose_matrix({'rpy_rad':g['rpy_rad']})[:3,:3]))
        if g.get('size'):attrs['size']=_numbers(g['size'])
        if g['type']=='mesh':
            from src.agent.scene_agent.common import confined
            mesh=confined(entry.get('asset_directory',entry['directory']),g['mesh'])
            if not mesh.is_file():raise SceneError('Mesh asset missing: '+str(mesh))
            content=mesh.read_bytes();key='assets/'+hashlib.sha256(content).hexdigest()+mesh.suffix
            target=output/key;target.parent.mkdir(exist_ok=True);target.write_bytes(content)
            metadata['assets'][key]=hashlib.sha256(content).hexdigest()
            ET.SubElement(asset,'mesh',name=name+'_mesh',file=key)
            attrs['mesh']=name+'_mesh'
        if not g.get('collision',True):attrs.update(contype='0',conaffinity='0',group='5')
        if g.get('mass') is not None:attrs['mass']=str(g['mass'])
        elif not g.get('collision',True) and 'density' not in g:attrs['mass']='0'
        if g.get('density') is not None:attrs['density']=str(g['density'])
        if 'priority' in g:attrs['priority']=str(g['priority'])
        if g.get('friction') is not None:
            friction=g['friction'];attrs['friction']=_numbers(friction if isinstance(friction,list) else [friction,.005,.0001])
        return ET.SubElement(parent,'geom',**attrs)
    def add_component(cid):
        if cid in body_nodes:return body_nodes[cid]
        if cid in active:raise SceneError('Joint hierarchy cycle','assembly')
        active.add(cid)
        c=components[cid];entry=resolved[cid];p=values(c);rule=entry['rule'];model=evaluated(entry,c)
        if fixed_profile:
            p['gravity_compensation']=PHYSICS['gravity_compensation']
            if 'surface_friction' in p:p['surface_friction']=PHYSICS['friction'][0]
        j=physical_joints.get(cid)
        mount=assembly_by_child.get(cid)
        parent_id=j['parent_body'] if j else (mount['parent_component'] if mount else c['parent_id'])
        while parent_id and parent_id not in resolved:parent_id=components[parent_id]['parent_id']
        parent=add_component(parent_id) if parent_id else wb
        local=np.linalg.inv(wt(parent_id))@wt(cid) if parent_id else wt(cid)
        bname='c_'+cid
        body=ET.SubElement(parent,'body',name=bname,pos=_numbers(local[:3,3]),quat=_numbers(quaternion(local[:3,:3])))
        body_nodes[cid]=body;body_names[cid]=bname
        physical=model.get('physical_parameters',{})
        metadata['components'][cid]={'name':c['name'],'type':c['type'],'body':bname,'parameters':p,
            'behavior':model.get('behavior','none'),'world_transform':wt(cid).tolist(),
            'rule_sha256':digest(rule),'rule_directory':entry['directory'],'assumptions':rule['assumptions'],
            'internal_joints':[j['name'] for j in model.get('joints',[])],
            'geometry_instance':entry.get('asset_directory'),'geometry_checks':entry.get('instance_checks',[])}
        if model.get('behavior')=='joint_position':
            owner=c.get('parent_id')
            while owner and resolved[owner]['rule']['model'].get('behavior')!='robot_control':owner=components[owner].get('parent_id')
            if not owner:raise SceneError('Joint-position component has no robot controller')
            metadata['components'][cid]['robot_owner']=owner
        metadata['rules'].append({'component_id':cid,'directory':entry['directory'],'rule_sha256':digest(rule),
                                  'approval':entry.get('approval')})
        if j:
            pa=wt(j['parent_body'])@np.r_[j['parent_anchor_m'],1]
            ca=wt(cid)@np.r_[j['child_anchor_m'],1]
            residual=float(np.linalg.norm(pa-ca))
            metadata['model_checks'].append({'name':'interface_alignment:'+j['id'],'passed':residual<=1e-6,'residual_m':residual})
            if residual>1e-6:raise SceneError('Joint anchors do not align: '+j['name'],'assembly',{'residual_m':residual})
            joint_name='j_'+j['id']
            ja={'name':joint_name,'type':j['type'],'pos':_numbers(j['child_anchor_m'])}
            if j['type'] in ('slide','hinge'):
                ja.update(axis=_numbers(j['axis']),range=_numbers(j['range']),limited='true',ref=str(j['initial']),damping=str(physical.get('damping',5)))
            ET.SubElement(body,'joint',**ja)
            if 'mass_kg' not in physical and not any(g.get('mass') is not None or g.get('density') is not None for g in model.get('geoms',[])):
                raise SceneError('Moving body mass must be explicit in candidate rule: '+c['name'])
            if j['type'] in ('slide','hinge'):
                kp=physical.get('position_kp')
                if kp is None:raise SceneError('Driven joint requires explicit actuator gain in rule')
                ET.SubElement(actuators,'position',name='a_'+j['id'],joint=joint_name,kp=str(kp),
                              ctrllimited='true',ctrlrange=_numbers(j['range']))
                ET.SubElement(eq,'joint',name='hold_'+j['id'],joint1=joint_name,active='false',polycoef='0 0 0 0 0')
                metadata['joints'][j['id']]={**j,'mjcf_joint':joint_name,'actuator':'a_'+j['id'],'hold_equality':'hold_'+j['id']}
            else:metadata['joints'][j['id']]={**j,'mjcf_joint':joint_name}
        # Rules may define their own validated internal rigid-body tree and joints.
        local_bodies={'root':body}
        for sub in model.get('bodies',[]):
            if sub['name'] in local_bodies or sub.get('parent','root') not in local_bodies:raise SceneError('Invalid internal body order/tree')
            node=ET.SubElement(local_bodies[sub.get('parent','root')],'body',name=bname+'__'+sub['name'],
                pos=_numbers(sub.get('position_m',[0,0,0])),quat=_numbers(quaternion(pose_matrix(sub)[:3,:3])))
            local_bodies[sub['name']]=node
        for local_name,node in local_bodies.items():
            gs=[g for g in model.get('geoms',[])+model.get('blender_bodies',[]) if g.get('body','root')==local_name]
            for g in _mass_geometries(gs,physical if local_name=='root' else {}):
                add_geom(node,g,bname+'__'+g['name'],entry,physical)
        for joint in model.get('joints',[]):
            if joint['body'] not in local_bodies:raise SceneError('Internal joint body missing')
            ET.SubElement(local_bodies[joint['body']],'joint',name=bname+'__'+joint['name'],type=joint['type'],
                pos=_numbers(joint.get('pos',[0,0,0])),axis=_numbers(joint.get('axis',[0,0,1])),
                limited='true',range=_numbers(joint['range']),damping=str(joint.get('damping',1)))
        for constraint in model.get('equalities',[]):
            kind=constraint['type'];params={'name':bname+'__'+constraint['name']}
            if kind=='joint':
                params.update(joint1=bname+'__'+constraint['joint1'],joint2=bname+'__'+constraint['joint2'],polycoef=_numbers(constraint.get('polycoef',[0,1,0,0,0])))
            elif kind in ('connect','weld'):
                params.update(body1=local_bodies[constraint['body1']].get('name'),body2=local_bodies[constraint['body2']].get('name'))
                if kind=='connect':params['anchor']=_numbers(constraint['anchor'])
            else:raise SceneError('Unsupported equality constraint')
            ET.SubElement(eq,kind,**params)
        active.remove(cid)
        return body
    products={cid for cid,entry in resolved.items() if entry['rule']['model'].get('behavior')=='rigid'}
    for cid in resolved:
        if cid not in products:add_component(cid)
    for cid in products:
        c=components[cid];p=values(c);entry=resolved[cid];model=evaluated(entry,c)
        if model.get('bodies') or model.get('joints'):raise SceneError('Articulated product spawning needs a separate behavior kernel')
        metadata['rules'].append({'component_id':cid,'directory':entry['directory'],'rule_sha256':digest(entry['rule']),
                                  'approval':entry.get('approval')})
        for slot in range(pool_per_product):
            bname='item_'+cid+'_'+str(slot)
            b=ET.SubElement(wb,'body',name=bname,pos=f'0 0 {-20-slot}')
            ET.SubElement(b,'freejoint',name=bname+'_free')
            for g in _mass_geometries(model.get('geoms',[])+model.get('blender_bodies',[]),model.get('physical_parameters',{})):
                add_geom(b,g,bname+'__'+g['name'],entry,{})
            metadata['products'].append({'body':bname,'product_id':cid,'category':c['name'],'parameters':p})
    robot_links={cid:c for cid,c in metadata['components'].items() if c['behavior']=='joint_position'}
    if robot_links:
        # The declared self-collision scope includes the pedestal as well as
        # moving links; excluding only link pairs can lock the base swivel.
        robot_parts={}
        for cid,c in metadata['components'].items():
            owner=cid
            while owner and metadata['components'].get(owner,{}).get('behavior')!='robot_control':owner=components[owner].get('parent_id')
            if owner and cid!=owner:robot_parts[cid]=owner
        contact=ET.SubElement(root,'contact');ids=list(robot_parts)
        for index,a in enumerate(ids):
            for b in ids[index+1:]:
                if robot_parts[a]==robot_parts[b]:
                    owner=metadata['components'][robot_parts[a]]
                    if owner['parameters'].get('self_collision_policy')=='excluded_for_joint_space_validation':
                        ET.SubElement(contact,'exclude',body1=metadata['components'][a]['body'],body2=metadata['components'][b]['body'])
                    elif owner['parameters'].get('self_collision_policy')=='enabled_non_adjacent':
                        if any({a,b}=={j['parent_body'],j['child_body']} for j in ir['joints']):
                            ET.SubElement(contact,'exclude',body1=metadata['components'][a]['body'],body2=metadata['components'][b]['body'])
        if all(metadata['components'][c['robot_owner']]['parameters'].get('environment_contact_policy')=='not_in_joint_space_validation' for c in robot_links.values()):
            wb.find('geom').set('contype','0');wb.find('geom').set('conaffinity','0')
    from src.agent.scene_agent.common import finalize_visual_inertia
    for body in wb.iter('body'):finalize_visual_inertia(body)
    for coupling in ir.get('couplings',[]):
        leader=metadata['joints'][coupling['leader']];follower=metadata['joints'][coupling['follower']]
        # MuJoCo joint equalities are expressed relative to qpos0 (joint ref).
        ET.SubElement(eq,'joint',name=coupling['id'],joint1=follower['mjcf_joint'],
            joint2=leader['mjcf_joint'],polycoef=_numbers([0,coupling['ratio'],0,0,0]))
    metadata['couplings']=copy.deepcopy(ir.get('couplings',[]))
    if ir.get('simulation_requirements'):
        metadata['simulation_requirements_sha256']=digest(ir['simulation_requirements'])
    xml=ET.tostring(root,encoding='unicode')
    vfs={key:(output/key).read_bytes() for key in metadata['assets']}
    try:m=mujoco.MjModel.from_xml_string(xml,assets=vfs)
    except ValueError as exc:raise SceneError('MJCF compilation failed: '+str(exc)) from exc
    io_map=[{'plc_var':p['name'],'dir':p['dir'],'type':p['type'],
             'bind':{'asset':p['component_id'],'quantity':p['quantity']},
             'address':p['address'],'writer':p['writer'],'physical_per_plc_unit':p['physical_per_plc_unit']} for p in ir['io']]
    if len(io_map)!=len({p['plc_var'] for p in io_map}) or any(p['bind']['asset'] not in resolved for p in io_map):
        raise SceneError('IO binding incomplete','assembly')
    supported={'conveyor':{'run_cmd','fault'},'source':{'emit_cmd'},'binary_prismatic':{'extend_cmd','extended','retracted','fault'},
               'ray':{'detect'},'sink':{'count'},'indicator':{'green_cmd','red_cmd'},
               'panel':{'green_cmd','red_cmd','start','stop','reset','estop_ok','delay_setpoint'}}
    supported.update(robot_control={'enable_cmd','reset_cmd','ready','fault'},joint_position={'joint_position_cmd','joint_position_fb','in_position'})
    for point in ir['io']:
        behavior=metadata['components'][point['component_id']]['behavior']
        if point['quantity'] not in supported.get(behavior,set()):
            raise SceneError('No physical kernel for IO: '+point['name'],details={'behavior':behavior,'quantity':point['quantity']})
    metadata['io_map']=io_map
    metadata['model_checks'].append({'name':'mjcf_compile','passed':True,'nbody':m.nbody,'nv':m.nv})
    metadata['model_xml_sha256']=__import__('hashlib').sha256(xml.encode()).hexdigest()
    (output/'scene.xml').write_text(xml,encoding='utf-8')
    write_json(output/'device_ir.json',ir);write_json(output/'scene.physical.json',metadata);write_json(output/'io_map.json',io_map)
    return metadata,xml


class DeviceRuntime:
    """Actual rigid-body motion/contact/raycast/counts; never executes a sorting policy."""
    def __init__(self,metadata,xml):
        self.meta=metadata;vfs={}
        if metadata['software_versions']['mujoco']!=mujoco.__version__:
            raise SceneError('MuJoCo version differs from built model; rebuild and revalidate')
        if hashlib.sha256(xml.encode()).hexdigest()!=metadata['model_xml_sha256']:raise SceneError('Scene XML differs from model manifest')
        for key,sha in metadata.get('assets',{}).items():
            from src.agent.scene_agent.common import confined
            content=confined(metadata['asset_root'],key).read_bytes()
            if hashlib.sha256(content).hexdigest()!=sha:raise SceneError('Scene mesh was modified')
            vfs[key]=content
        self.model=mujoco.MjModel.from_xml_string(xml,assets=vfs);self.data=mujoco.MjData(self.model)
        self.definition_sha256=digest(metadata)
        self.points={p['name']:p for p in metadata['io']};self.signals={p['name']:p['initial'] for p in metadata['io']}
        self.ports={(p['component_id'],p['quantity']):p['name'] for p in metadata['io']}
        self.products=[];self.events=[];self.edge={};self.phase={};self.trajectory={};self.joint_targets={}
        self.operator_requests={};self.contact_changed={};self.properties={}
        for record in metadata['products']:
            b=mujoco.mj_name2id(self.model,mujoco.mjtObj.mjOBJ_BODY,record['body'])
            j=self.model.body_jntadr[b];q=self.model.jnt_qposadr[j];v=self.model.jnt_dofadr[j]
            geoms=list(range(self.model.body_geomadr[b],self.model.body_geomadr[b]+self.model.body_geomnum[b]))
            item={**record,'bid':b,'q':int(q),'v':int(v),'geoms':geoms,'active':False,'generation':0}
            self.products.append(item)
            for gid in geoms:self.model.geom_contype[gid]=0;self.model.geom_conaffinity[gid]=0
        for jid,j in metadata['joints'].items():self.joint_targets[jid]=j['initial']
        mujoco.mj_forward(self.model,self.data)
        from .device_robot import RobotDriver
        self.robot=RobotDriver(self)

    def _value(self,cid,quantity,default=False):return self.signals.get(self.ports.get((cid,quantity)),default)

    def _feedback(self,cid,quantity,value):
        name=self.ports.get((cid,quantity))
        if name:
            if self.points[name]['writer']!='simulation':raise SceneError('Physical feedback does not own signal '+name)
            self.signals[name]=value

    def set_inputs(self,updates):
        for name,value in updates.items():
            p=self.points.get(name)
            if p is None or p['dir']!='input' or p['writer'] not in ('operator','fault_harness'):
                raise SceneError('Test/user cannot write physical feedback: '+name,'device_description')
            self._check_value(p,value)
            component=self.meta['components'].get(p['component_id'],{})
            if p['writer']=='operator' and p['type']=='BOOL' and p['quantity'] in ('start','stop','reset') and component.get('behavior')=='panel':
                self.operator_requests[name]=value
            else:self.signals[name]=value
        for name,value in self.operator_requests.items():
            p=self.points[name];parameters=self.meta['components'][p['component_id']]['parameters']
            minimum=parameters.get('momentary_minimum_high_s' if self.signals[name] else 'momentary_minimum_low_s',0.)
            if value!=self.signals[name] and self.data.time-self.contact_changed.get(name,-1e9)>=minimum:
                self.signals[name]=value;self.contact_changed[name]=float(self.data.time)

    @staticmethod
    def _check_value(p,value):
        if p['type']=='BOOL' and type(value) is not bool:raise SceneError('BOOL required for '+p['name'])
        if p['type']=='INT' and (type(value) is not int or (p.get('range') and not p['range'][0]<=value<=p['range'][1])):
            raise SceneError('INT/range violation for '+p['name'])

    def _retire(self,item):
        item['active']=False;self.data.qpos[item['q']:item['q']+3]=[0,0,-20-item['bid']]
        self.data.qvel[item['v']:item['v']+6]=0
        for g in item['geoms']:self.model.geom_contype[g]=0;self.model.geom_conaffinity[g]=0

    def _spawn(self,cid,c):
        p=c['parameters'];phase=self.phase.get(cid,0)
        product=p['short_product_id'] if phase%2==0 else p['tall_product_id']
        item=next((i for i in self.products if i['product_id']==product and not i['active']),None)
        if item is None:return False
        position=np.asarray(c['world_transform'])[:3,3].copy()
        targets={edge['to'] for edge in self.meta.get('material_flow',[]) if edge['from']==cid}
        belts=[self.meta['components'][key] for key in targets if key in self.meta['components'] and self.meta['components'][key]['behavior']=='conveyor']
        if len(belts)!=1:raise SceneError('Source needs exactly one material-flow-connected conveyor','assembly')
        belt=belts[0]
        bp=belt['parameters'];belt_top=np.asarray(belt['world_transform'])[2,3]+bp['carrying_surface_offset_z_m']
        position[2]=belt_top+p['drop_clearance_m']+item['parameters']['size_z_m']/2
        self.data.qpos[item['q']:item['q']+7]=np.r_[position,[1,0,0,0]]
        self.data.qvel[item['v']:item['v']+6]=0;item['active']=True;item['generation']+=1
        for g in item['geoms']:self.model.geom_contype[g]=1;self.model.geom_conaffinity[g]=1
        self.phase[cid]=phase+1
        self.events.append({'time_s':float(self.data.time),'event':'spawn','item_id':item['body']+':'+str(item['generation']),
                            'category':item['category'],'source_id':cid})
        return True

    def step(self,outputs=None,external_inputs=None):
        for name,value in (outputs or {}).items():
            p=self.points.get(name)
            if p is None or p['dir']!='output':raise SceneError('PLC cannot write input '+name,'plc_logic')
            self._check_value(p,value);self.signals[name]=value
        self.set_inputs(external_inputs or {})
        self.data.xfrc_applied[:]=0
        self.robot.pre_step()
        for cid,c in self.meta['components'].items():
            p=c['parameters'];kind=c['behavior']
            if kind=='binary_prismatic':
                jid=p['driven_joint_id'];j=self.meta['joints'][jid]
                ji=mujoco.mj_name2id(self.model,mujoco.mjtObj.mjOBJ_JOINT,j['mjcf_joint']);q=float(self.data.qpos[self.model.jnt_qposadr[ji]])
                ai=mujoco.mj_name2id(self.model,mujoco.mjtObj.mjOBJ_ACTUATOR,j['actuator'])
                ei=mujoco.mj_name2id(self.model,mujoco.mjtObj.mjOBJ_EQUALITY,j['hold_equality'])
                if self._value(cid,'fault'):
                    if not self.data.eq_active[ei]:self.model.eq_data[ei,0]=q-j['initial']
                    self.data.eq_active[ei]=True;self.data.ctrl[ai]=q
                else:
                    self.data.eq_active[ei]=False
                    extend=self._value(cid,'extend_cmd')
                    target=p['command_true_target_m'] if extend else p['command_false_target_m'];origin=0 if extend else p['travel_m']
                    cushion=max(p['travel_m']*p['cushion_fraction'],p['minimum_cushion_m'])
                    factor=max(p['minimum_speed_factor'],min(1,abs(target-q)/cushion)*min(1,abs(q-origin)/cushion))
                    old=self.joint_targets[jid];step=p['nominal_speed_m_s']*factor*self.model.opt.timestep
                    self.joint_targets[jid]=old+float(np.clip(target-old,-step,step))
                    self.data.ctrl[ai]=self.joint_targets[jid]
            elif kind=='source':
                high=bool(self._value(cid,'emit_cmd'));rising=high and not self.edge.get(cid,False)
                if rising or self.trajectory.get(cid,False):self.trajectory[cid]=not self._spawn(cid,c)
                self.edge[cid]=high
            elif kind=='conveyor':
                run=self._value(cid,'run_cmd') and not self._value(cid,'fault')
                bid=mujoco.mj_name2id(self.model,mujoco.mjtObj.mjOBJ_BODY,c['body'])
                belt_geoms=set(range(self.model.body_geomadr[bid],self.model.body_geomadr[bid]+self.model.body_geomnum[bid]))
                direction=np.asarray(c['world_transform'])[:3,:3]@np.array([p['conveying_direction_'+a] for a in 'xyz'])
                direction=direction/np.linalg.norm(direction)
                for item in self.products:
                    if not item['active']:continue
                    touching=any((int(ct.geom1) in belt_geoms and int(ct.geom2) in item['geoms']) or
                                 (int(ct.geom2) in belt_geoms and int(ct.geom1) in item['geoms']) for ct in self.data.contact[:self.data.ncon])
                    if touching:
                        speed=float(self.data.qvel[item['v']:item['v']+3]@direction)
                        target=p['nominal_speed_m_s'] if run else 0
                        mass=float(self.model.body_mass[item['bid']]);limit=p['surface_friction']*mass*9.81
                        force=float(np.clip(mass*(target-speed)/.02,-limit,limit))
                        self.data.xfrc_applied[item['bid'],:3]+=force*direction
            elif kind in ('indicator','panel'):
                bid=mujoco.mj_name2id(self.model,mujoco.mjtObj.mjOBJ_BODY,c['body'])
                green=bool(self._value(cid,'green_cmd'));red=bool(self._value(cid,'red_cmd'))
                state={'green':green,'red':red}
                delay=self.ports.get((cid,'delay_setpoint'))
                if delay:state['delay_s']=self.signals[delay]*self.points[delay]['physical_per_plc_unit']
                self.properties[cid]=state
                for gid in range(self.model.body_geomadr[bid],self.model.body_geomadr[bid]+self.model.body_geomnum[bid]):
                    self.model.geom_rgba[gid,:3]=[.9 if red else .12,.9 if green else .12,.12]
        mujoco.mj_step(self.model,self.data)
        self.robot.post_step()
        for cid,c in self.meta['components'].items():
            p=c['parameters'];kind=c['behavior']
            if kind=='binary_prismatic':
                j=self.meta['joints'][p['driven_joint_id']];ji=mujoco.mj_name2id(self.model,mujoco.mjtObj.mjOBJ_JOINT,j['mjcf_joint'])
                q=float(self.data.qpos[self.model.jnt_qposadr[ji]])
                self._feedback(cid,'extended',q>=p['extended_threshold_m']);self._feedback(cid,'retracted',q<=p['retracted_threshold_m'])
            elif kind=='ray':
                t=np.asarray(c['world_transform']);origin=t[:3,3]+t[:3,:3]@np.array([p['beam_origin_local_'+a+'_m'] for a in 'xyz'])
                direction=t[:3,:3]@np.array([p['beam_direction_'+a] for a in 'xyz']);direction/=np.linalg.norm(direction)
                gid=np.zeros(1,dtype=np.int32)
                distance=mujoco.mj_ray(self.model,self.data,origin,direction,np.array([1,1,1,1,1,0],dtype=np.uint8),1,-1,gid)
                self._feedback(cid,'detect',bool(0<=distance<=p['range_m']))
            elif kind=='sink':
                t=np.asarray(c['world_transform']);half=np.array([p['zone_size_'+a+'_m'] for a in 'xyz'])/2
                for item in self.products:
                    if not item['active']:continue
                    # Intersect the actual transformed carton collision box with the zone.
                    ip=item['parameters'];extent=np.abs(self.data.xmat[item['bid']].reshape(3,3))@np.array([ip['size_'+a+'_m'] for a in 'xyz'])/2
                    center=self.data.xpos[item['bid']]
                    inside=np.all(np.abs(t[:3,:3].T@(center-t[:3,3]))<=half+np.abs(t[:3,:3].T)@extent)
                    if inside:
                        old=int(self._value(cid,'count',0));new=old+1
                        name=self.ports.get((cid,'count'))
                        if name and new>self.points[name]['range'][1]:raise SceneError('Counter range exhausted; no silent wrap')
                        self._feedback(cid,'count',new)
                        self.events.append({'time_s':float(self.data.time),'event':'outlet_entry','outlet_id':cid,
                            'item_id':item['body']+':'+str(item['generation']),'category':item['category']})
                        self._retire(item)
        if (not np.isfinite(self.data.qpos).all() or not np.isfinite(self.data.qvel).all()
                or any(w.number for w in self.data.warning)):
            raise SceneError('Physics diverged or MuJoCo reported an instability; auto-reset is not success')
        return {p['name']:self.signals[p['name']] for p in self.meta['io'] if p['dir']=='input'}

    def render(self,path):
        from PIL import Image
        camera=mujoco.MjvCamera();camera.lookat[:]=[1.5,-.1,.5];camera.distance=5;camera.azimuth=120;camera.elevation=-32
        renderer=mujoco.Renderer(self.model,height=480,width=640)
        try:
            option=mujoco.MjvOption();option.geomgroup[:]=1
            renderer.update_scene(self.data,camera=camera,scene_option=option);Image.fromarray(renderer.render()).save(path)
        finally:renderer.close()

    def freeze(self,directory,validation_report):
        if self.meta['candidate']:raise ApprovalRequired(['candidate scene cannot be frozen for PLC acceptance'])
        if validation_report.get('behavior_checks_complete') is not True:
            raise SceneError('Component and whole-device behavior checks required before freeze')
        if validation_report.get('status')!='passed' or not validation_report.get('checks') or not all(x.get('passed') for x in validation_report['checks']):
            raise SceneError('Model checks have not passed')
        if validation_report.get('scene_sha256')!=self.meta['model_xml_sha256']:raise SceneError('Checks belong to another model')
        if validation_report.get('definition_sha256')!=self.definition_sha256:raise SceneError('Checks belong to different device/IO behavior definitions')
        if self.meta.get('simulation_requirements_sha256') and not validation_report.get('simulation_tests',{}).get('complete'):
            raise SceneError('AML simulation test modules must all pass before freeze')
        # Re-verify approved bytes immediately before freezing.
        from src.agent.scene_agent.rules import review_digest,implementation_hashes
        from src.agent.scene_agent.common import read_json
        for r in self.meta['rules']:
            if r['approval']['review_sha256']!=review_digest(r['directory']):raise SceneError('Approved assets changed before freeze')
            if read_json(Path(r['directory'])/'validation.json')['implementation_sha256']!=implementation_hashes():
                raise SceneError('Behavior implementation changed before freeze')
        manifest={'status':'model_frozen','plc_acceptance':'not_run','scene_sha256':self.meta['model_xml_sha256'],
                  'device_ir_sha256':self.meta['device_ir_sha256'],'rules':self.meta['rules'],'checks':validation_report}
        write_json(Path(directory)/'model.freeze.json',manifest)
        return manifest
