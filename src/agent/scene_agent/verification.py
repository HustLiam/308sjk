"""Versioned model/IO facts contract. No executable programs from the author."""
import copy
import math

import numpy as np

from .common import SceneError, digest, transform
from .device_ir import values, facts_digest

PROFILE = 'fixed_model_io/1'
FORMAT = '308sjk.verification_parameters/1'
SIMPLIFICATION = '允许简化模型外观、非关键结构及几何细节，必须保留关键尺寸、位姿、层级、安装接口偏置、运动副及速度、加速度参数，并保持碰撞检查所需的几何特征和 IO 绑定关系。'
PHYSICS = {'version':'308sjk.basic_physics/1', 'body_mass_kg':1., 'position_kp':5000.,
           'damping':100., 'gravity_compensation':1., 'friction':[.5,.005,.0001]}
PHYSICAL_KEYS = {'mass_kg','position_kp','damping','gravity_compensation','inertia','inertia_kg_m2',
                 'density','collision_friction','surface_friction','mass_center_x_m','mass_center_y_m','mass_center_z_m'}


def enabled(ir):
    return ir.get('verification_profile') == PROFILE


def configure(ir):
    """Keep original AML facts; defaults are explicit host configuration."""
    result=copy.deepcopy(ir)
    result['verification_profile']=PROFILE
    result['facts_sha256']=facts_digest(result)
    return result


def interface_frame(frame):
    p=values(frame)
    if not all('position_'+a+'_m' in p for a in 'xyz'):
        raise SceneError('安装接口缺少局部位置：'+frame['id'],'assembly')
    angles=[]
    for a in 'xyz':
        rec=frame['parameters'].get('rotation_'+a+'_deg',{})
        angles.append(rec.get('value',0) if rec.get('original_unit') else math.radians(rec.get('value',0)))
    return transform([p['position_'+a+'_m'] for a in 'xyz'],angles)


def joint_from_interfaces(ir, joint, *, required=False):
    """Interface +Z is the positive joint axis, expressed in the child frame."""
    interfaces={f['id']:f for f in ir['interfaces']};ends={}
    for link in ir['connections']:
        a,b=interfaces[link['a']],interfaces[link['b']]
        for local,remote in ((a,b),(b,a)):
            if local['component_id']==joint['id'] and local['name'] in ('parent_body','child_body'):
                if local['name'] in ends:raise SceneError('运动副接口连接重复','assembly')
                ends[local['name']]=remote
    if set(ends)!={'parent_body','child_body'}:
        if required:raise SceneError('运动副需要父/子组件的完整安装接口','assembly')
        return None
    if any(not all('position_'+a+'_m' in values(f) for a in 'xyz') for f in ends.values()):
        if required:raise SceneError('运动副连接的接口缺少局部坐标','assembly')
        return None
    parent,child=(interface_frame(ends[k]) for k in ('parent_body','child_body'))
    return {'parent_body':ends['parent_body']['component_id'],'child_body':ends['child_body']['component_id'],
            'axis':child[:3,2].tolist(),'parent_anchor_m':parent[:3,3].tolist(),
            'child_anchor_m':child[:3,3].tolist(),'parent_interface':ends['parent_body']['id'],
            'child_interface':ends['child_body']['id'],'frame_convention':'interface_positive_z/1'}


def apply_physics(rule):
    """Host-owned numeric approximation; authored geometry/behavior stay intact."""
    result=copy.deepcopy(rule);model=result['model']
    result['parameter_bounds']={k:v for k,v in result['parameter_bounds'].items() if k not in PHYSICAL_KEYS}
    colliders=[g for g in model.get('geoms',[])+model.get('blender_bodies',[]) if g.get('collision',True)]
    for g in model.get('geoms',[])+model.get('blender_bodies',[]):
        g.pop('mass',None);g.pop('density',None)
        if g.get('collision',True):
            g['friction']=list(PHYSICS['friction'])
            # Density also supports authored internal bodies and mesh colliders.
            g['density']=1000.
    roots=[g for g in colliders if g.get('body','root')=='root']
    model['physical_parameters']={}
    if roots:
        model['physical_parameters'].update(position_kp=PHYSICS['position_kp'],damping=PHYSICS['damping'])
        if all(g['type']!='mesh' for g in roots):
            model['physical_parameters']['mass_kg']=PHYSICS['body_mass_kg']
            for g in roots:g.pop('density',None)
    for j in model.get('joints',[]):j['damping']=PHYSICS['damping']
    return result


def parameters(ir):
    """Deterministic extraction used by the Agent, independently rechecked by host."""
    components=[]
    joint_ids={j['id'] for j in ir['joints']}
    for c in ir['components']:
        if c['id'] in joint_ids:continue
        components.append({k:copy.deepcopy(c[k]) for k in ('id','name','type','parent_id','pose','interfaces')})
        components[-1]['parameters']={k:copy.deepcopy(v) for k,v in c['parameters'].items() if k not in PHYSICAL_KEYS}
    req=ir.get('simulation_requirements')
    return {'format':FORMAT,'profile':PROFILE,'device_ir_sha256':facts_digest(ir),
            'source':copy.deepcopy(ir['source']),'units':copy.deepcopy(ir['units']),
            'components':components,'interfaces':copy.deepcopy(ir['interfaces']),
            'connections':copy.deepcopy(ir['connections']),'assemblies':copy.deepcopy(ir.get('assemblies',[])),
            'joints':copy.deepcopy(ir['joints']),'couplings':copy.deepcopy(ir.get('couplings',[])),
            'io':copy.deepcopy(ir['io']),'requirements':copy.deepcopy(req),
            'simplification':SIMPLIFICATION,'physics_profile':copy.deepcopy(PHYSICS)}


def validate_parameters(ir, candidate):
    if digest(candidate)!=digest(parameters(ir)):
        raise SceneError('校验参数 JSON 与 AML 事实不一致，不能用于判定','verification_parameters')
    return candidate


def dimensions(component):
    p=values(component)
    for names in (('size_x_m','size_y_m','size_z_m'),('width_m','depth_m','height_m')):
        if all(k in p for k in names):
            result=[p[k] for k in names]
            if any(type(x) not in (int,float) or not math.isfinite(x) or x<=0 for x in result):
                raise SceneError('组件关键尺寸必须为正的有限数','verification_parameters')
            return result
    return None
