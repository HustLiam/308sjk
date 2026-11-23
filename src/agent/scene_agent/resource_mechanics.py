"""Normalize explicitly declared mechanics without treating a mesh as a joint contract."""
import json
import math

import numpy as np

from .common import SceneError
from .resource_resolution import value, rigid_matrix

FORMAT = '308sjk.resource_mechanics/1'


def normalize(report, resources, aml):
    """Project extension: inline MechanicalDefinition or explicitly referenced JSON.

    Existing COLLADA kinematics are normalized separately from the imported nodes.
    Unsupported native/vendor declarations remain in the source ledger, not guessed.
    """
    result = {'format': FORMAT, 'components': [], 'issues': []}
    interfaces = {f['id']: f for f in report['interfaces']}
    components = {c['id']: c for c in report['components']}
    for component in report['components']:
        declarations = []
        raw = value(component, 'MechanicalDefinition')
        if raw:
            declarations.append((component['source']+'#MechanicalDefinition', raw))
        for fid in component['interfaces']:
            interface = interfaces[fid]
            if interface['class'].split('/')[-1] != 'MechanicalDefinitionInterface':
                continue
            uri = value(interface, 'refURI')
            try:
                path, fragment = resources.resolve(aml, uri or '')
                if fragment:
                    raise SceneError('机械 JSON 引用不接受 fragment', 'assembly')
                data = resources.blob(path)
                declarations.append((resources.relative(path), data.decode('utf-8-sig')))
            except (SceneError, UnicodeError) as exc:
                result['issues'].append({'code': 'mechanical_reference_missing', 'source': fid, 'detail': str(exc)})
        parsed = []; invalid = False
        for source, raw in declarations:
            try:
                definition = json.loads(raw)
                validate(definition, component['id'], components)
                parsed.append((source, definition))
            except (ValueError, TypeError, KeyError, SceneError) as exc:
                invalid = True
                result['issues'].append({'code': 'mechanical_definition_invalid', 'source': source, 'detail': str(exc)})
        if parsed and not invalid:
            if any(d != parsed[0][1] for _, d in parsed[1:]):
                result['issues'].append({'code': 'mechanical_definition_conflict', 'source': component['id'],
                                         'detail': '内联与外部机械声明不一致；未自动覆盖'})
                continue
            component['mechanical_definition'] = parsed[0][1]
            component['mechanical_sources'] = [s for s, _ in parsed]
            result['components'].append({'component_id': component['id'], 'definition': parsed[0][1],
                                         'sources': component['mechanical_sources']})
    return result


def validate(definition, cid, components):
    if not isinstance(definition, dict) or definition.get('format') != FORMAT:
        raise SceneError('机械定义格式必须为 '+FORMAT, 'assembly')
    if set(definition)-{'format', 'interfaces', 'connection'}:
        raise SceneError('机械定义存在未支持字段', 'assembly')
    frames = definition.get('interfaces')
    if not isinstance(frames, dict) or not frames:
        raise SceneError('组件必须声明安装接口完整坐标系', 'assembly')
    for frame in frames.values():
        rigid_matrix(frame)
    connection = definition.get('connection')
    if not isinstance(connection, dict):
        raise SceneError('组件必须明确 root、fixed、hinge 或 slide 连接', 'assembly')
    kind = connection.get('type')
    allowed = {'type'} if kind == 'root' else {'type', 'parent_component', 'parent_interface', 'child_interface',
                                               'range', 'initial', 'speed', 'acceleration'}
    if set(connection)-allowed or kind not in ('root', 'fixed', 'hinge', 'slide'):
        raise SceneError('连接类型或字段不支持', 'assembly')
    if kind == 'root':
        return
    parent = connection.get('parent_component')
    if parent not in components or parent == cid or connection.get('child_interface') not in frames:
        raise SceneError('连接必须明确父组件及子安装接口', 'assembly')
    if not isinstance(connection.get('parent_interface'), str):
        raise SceneError('连接必须明确父安装接口', 'assembly')
    if kind in ('hinge', 'slide'):
        limits = np.asarray(connection['range'], dtype=float)
        initial = float(connection['initial'])
        if limits.shape != (2,) or not np.isfinite(limits).all() or not limits[0] <= initial <= limits[1] or limits[0] >= limits[1]:
            raise SceneError('关节范围或初值不合法（单位 m/rad）', 'assembly')
        for name in ('speed', 'acceleration'):
            if not math.isfinite(float(connection[name])) or float(connection[name]) <= 0:
                raise SceneError('关节必须声明正的速度和加速度', 'assembly')


def apply(nodes, report):
    """Apply only complete explicit relations; never override imported internal joints."""
    definitions = {c['id']: c.get('mechanical_definition') for c in report['components']}
    issues = report['issues']
    for cid, definition in definitions.items():
        if not definition:
            continue
        connection = definition['connection']; kind = connection['type']
        if kind == 'root':
            nodes[cid]['parent'] = None
            continue
        parent = connection['parent_component']; parent_definition = definitions[parent]
        if not parent_definition or connection['parent_interface'] not in parent_definition['interfaces']:
            issues.append({'code': 'mechanical_parent_interface_missing', 'source': cid})
            continue
        # Source world transforms remain authoritative. Measure assembly residuals,
        # rather than silently relocating a component to make it appear correct.
        parent_frame = rigid_matrix(parent_definition['interfaces'][connection['parent_interface']])
        child_frame = rigid_matrix(definition['interfaces'][connection['child_interface']])
        a = nodes[parent]['world']@parent_frame; b = nodes[cid]['world']@child_frame
        if kind in ('hinge', 'slide'):
            from .collada_import import rotation
            motion = rotation([0, 0, 1], float(connection['initial'])) if kind == 'hinge' else np.eye(4)
            if kind == 'slide':
                motion[2, 3] = float(connection['initial'])
            a = a@motion
        residual = np.linalg.inv(a)@b
        if not np.allclose(residual, np.eye(4), atol=1e-6):
            issues.append({'code': 'mechanical_installation_mismatch', 'source': cid,
                           'position_error_m': float(np.linalg.norm(residual[:3, 3])),
                           'detail': '声明位姿与接口安装关系不一致，保留原位姿，等待修正'})
            continue
        nodes[cid]['parent'] = parent
        nodes[cid]['mechanical_source'] = 'aml_explicit_definition'
        if kind in ('hinge', 'slide'):
            nodes[cid]['joint'] = {'name': cid, 'type': kind, 'axis': child_frame[:3, 2].tolist(),
                                  'pos': child_frame[:3, 3].tolist(), 'range': connection['range'],
                                  'initial': connection['initial'], 'locked': False,
                                  'speed': connection['speed'], 'acceleration': connection['acceleration'],
                                  'source_file': 'AML MechanicalDefinition'}
    # Detect cycles before emitting MuJoCo, including explicit root overrides.
    for cid in nodes:
        seen = set(); key = cid
        while key:
            if key in seen:
                raise SceneError('显式机械连接存在环；闭环需要专用约束适配', 'assembly')
            seen.add(key); key = nodes[key]['parent']


def ledger(report, nodes, normalized):
    declared = {c['component_id'] for c in normalized['components']}
    external = []
    for node in nodes.values():
        if node.get('joint') and node['component_id'] not in declared:
            external.append({'component_id': node['component_id'], 'node_id': node['id'],
                             'definition': node['joint'], 'source': 'explicit_COLLADA_kinematics'})
    normalized['external_joints'] = external
    normalized['interfaces'] = report['interfaces']
    normalized['connections'] = report['connections']
    normalized['policy'] = 'Explicit references count as declarations; visual meshes alone do not declare mechanics.'
    return normalized
