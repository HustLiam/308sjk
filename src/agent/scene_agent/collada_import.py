"""Deterministic COLLADA mesh/scene/kinematics import (metres, radians, Z-up)."""
import math
from pathlib import Path

import numpy as np

from .common import SceneError
from .resources import child, children, tag, text


def numbers(value, count=None):
    result = np.array([float(v) for v in value.split()], dtype=float)
    if not np.isfinite(result).all() or (count is not None and len(result) != count):
        raise SceneError('几何数值非有限或维度错误', 'resource_import')
    return result


def rotation(axis, angle):
    axis = np.asarray(axis, dtype=float)
    norm = np.linalg.norm(axis)
    if norm < 1e-12:
        raise SceneError('旋转轴为零', 'resource_import')
    x, y, z = axis / norm
    skew = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    result = np.eye(4)
    result[:3, :3] += math.sin(angle)*skew + (1-math.cos(angle))*(skew@skew)
    return result


def transforms(node, meter):
    result = np.eye(4)
    for n in node:
        kind = tag(n)
        if kind not in ('matrix', 'translate', 'rotate', 'scale', 'lookat', 'skew'):
            continue
        v = numbers(n.text or '')
        t = np.eye(4)
        if kind == 'matrix' and len(v) == 16:
            t = v.reshape(4, 4).copy()  # COLLADA text is written row by row.
            t[:3, 3] *= meter
        elif kind == 'translate' and len(v) == 3:
            t[:3, 3] = v*meter
        elif kind == 'rotate' and len(v) == 4:
            t = rotation(v[:3], math.radians(v[3]))
        elif kind == 'scale' and len(v) == 3:
            t[:3, :3] = np.diag(v)
        else:
            raise SceneError('暂不支持的 COLLADA 变换：' + kind, 'resource_import')
        if not np.allclose(t[3], [0, 0, 0, 1]):
            raise SceneError('无效仿射矩阵', 'resource_import')
        result = result @ t
    return result


class ColladaImporter:
    def __init__(self, resources):
        self.resources = resources
        self.meshes = {}
        self.issues = []

    def settings(self, path):
        root, _ = self.resources.document(path)
        if tag(root) != 'COLLADA':
            raise SceneError('引用不是 COLLADA：' + str(path), 'resource_import')
        asset = child(root, 'asset')
        unit = child(asset, 'unit') if asset is not None else None
        meter = float(unit.get('meter', '1')) if unit is not None else 1.
        if not math.isfinite(meter) or meter <= 0:
            raise SceneError('COLLADA 单位无效', 'resource_import')
        up = text(asset, 'up_axis', 'Y_UP') if asset is not None else 'Y_UP'
        basis = {'Z_UP': np.eye(4), 'Y_UP': rotation([1, 0, 0], math.pi/2),
                 'X_UP': rotation([0, 1, 0], -math.pi/2)}.get(up)
        if basis is None:
            raise SceneError('未知 COLLADA up_axis', 'resource_import')
        return root, meter, basis

    def mesh(self, path, geometry):
        key = self.resources.relative(path) + '#' + geometry.get('id', '')
        if key in self.meshes:
            return key
        _, meter, basis = self.settings(path)
        node = child(geometry, 'mesh')
        if node is None:
            raise SceneError('仅支持 COLLADA mesh 几何：' + key, 'resource_import')
        sources = {}
        for src in children(node, 'source'):
            arr = child(src, 'float_array')
            if arr is None:
                continue
            common = child(src, 'technique_common'); accessor = child(common, 'accessor') if common is not None else None
            if accessor is None:
                raise SceneError('几何 source 缺少 accessor', 'resource_import')
            stride = int(accessor.get('stride', '1')); offset = int(accessor.get('offset', '0')); count = int(accessor.get('count', '0'))
            data = numbers(arr.text or '')
            if stride < 1 or count < 1 or offset < 0 or offset+stride*count > len(data):
                raise SceneError('几何 accessor 越界', 'resource_import')
            sources['#'+src.get('id')] = data[offset:offset+stride*count].reshape(count, stride)[:, :3]
        vertices = {('#'+v.get('id')): next((i.get('source') for i in children(v, 'input') if i.get('semantic') == 'POSITION'), None)
                    for v in children(node, 'vertices')}
        out_v = []; out_f = []
        for primitive in node:
            kind = tag(primitive)
            if kind in ('source', 'vertices', 'extra'):
                continue
            if kind not in ('triangles', 'trifans', 'tristrips', 'polylist', 'polygons'):
                raise SceneError('暂不支持的网格图元：' + kind, 'resource_import')
            inputs = children(primitive, 'input')
            vin = next((i for i in inputs if i.get('semantic') in ('VERTEX', 'POSITION')), None)
            if vin is None:
                raise SceneError('网格缺少顶点输入', 'resource_import')
            source = vertices.get(vin.get('source'), vin.get('source'))
            if source not in sources:
                raise SceneError('顶点源无法解析', 'resource_import')
            v = sources[source]*meter
            if v.shape[1] != 3:
                raise SceneError('POSITION 必须为三维坐标', 'resource_import')
            v = v @ basis[:3, :3].T
            start = len(out_v); out_v.extend(v.tolist())
            stride = max(int(i.get('offset', '0')) for i in inputs)+1; offset = int(vin.get('offset', '0'))
            polys = []
            for pn in children(primitive, 'p'):
                raw = np.array([int(i) for i in (pn.text or '').split()], dtype=int)
                if len(raw) % stride:
                    raise SceneError('网格索引步长不匹配', 'resource_import')
                ix = raw.reshape(-1, stride)[:, offset]
                if len(ix) and (ix.min() < 0 or ix.max() >= len(v)):
                    raise SceneError('网格索引越界', 'resource_import')
                if kind == 'triangles':
                    if len(ix) % 3:
                        raise SceneError('三角面索引不完整', 'resource_import')
                    polys.extend(ix.reshape(-1, 3).tolist())
                elif kind == 'tristrips':
                    polys.extend(([ix[j+1], ix[j], ix[j+2]] if j % 2 else ix[j:j+3].tolist()) for j in range(len(ix)-2))
                elif kind in ('trifans', 'polygons'):
                    if kind == 'polygons' and len(ix) != 3:
                        raise SceneError('一般多边形需先三角化，不能假定为凸多边形', 'resource_import')
                    polys.extend([ix[0], ix[j], ix[j+1]] for j in range(1, len(ix)-1))
                else:
                    counts = [int(i) for i in text(primitive, 'vcount').split()]
                    if sum(counts) != len(ix) or any(c != 3 for c in counts):
                        raise SceneError('polylist 需先三角化', 'resource_import')
                    polys.extend(ix.reshape(-1, 3).tolist())
            if polys:
                faces = np.asarray(polys, dtype=int)
                areas = np.linalg.norm(np.cross(v[faces[:,1]]-v[faces[:,0]], v[faces[:,2]]-v[faces[:,0]]), axis=1)
                out_f.extend((faces[areas > 1e-16]+start).tolist())
        if not out_v or not out_f:
            raise SceneError('资源没有有效三角面：' + key, 'resource_import')
        self.meshes[key] = {'vertices': out_v, 'faces': out_f, 'source': key}
        return key

    def scene(self, owner, uri):
        path, selected = self.resources.element(owner, uri)
        root, _, basis = self.settings(path)
        if tag(selected) == 'COLLADA':
            scene = child(root, 'scene'); inst = child(scene, 'instance_visual_scene') if scene is not None else None
            if inst is None:
                raise SceneError('COLLADA 缺少活动 visual_scene', 'resource_import')
            path, selected = self.resources.element(path, inst.get('url', ''))
        nodes = []; stack = set()
        def walk(file, n, parent, prefix):
            _, meter, basis = self.settings(file)
            local = basis @ transforms(n, meter) @ np.linalg.inv(basis)
            name = str(len(nodes)); key = (str(file), id(n))
            if key in stack:
                raise SceneError('COLLADA instance_node 循环引用', 'resource_import')
            if len(nodes) >= 30000:
                raise SceneError('场景节点超过预算', 'resource_import')
            stack.add(key)
            world = (nodes[int(parent)]['world'] if parent is not None else np.eye(4)) @ local
            record = {'id': name, 'parent': parent, 'world': world, 'local': local,
                      'source_file': self.resources.relative(file), 'source_id': n.get('id'),
                      'sid': n.get('sid'), 'name': n.get('name', n.get('id', 'node')), 'meshes': []}
            nodes.append(record)
            for g in children(n, 'instance_geometry'):
                gf, gn = self.resources.element(file, g.get('url', ''))
                record['meshes'].append(self.mesh(gf, gn))
            for sub in n:
                if tag(sub) == 'node':
                    walk(file, sub, name, prefix)
                elif tag(sub) == 'instance_node':
                    sf, sn = self.resources.element(file, sub.get('url', ''))
                    walk(sf, sn, name, prefix)
                elif tag(sub) in ('instance_controller', 'lookat', 'skew'):
                    raise SceneError('暂不支持的场景元素：' + tag(sub), 'resource_import')
            stack.remove(key)
        walk(path, selected, None, '')
        self.kinematics(nodes)
        return nodes

    def kinematics(self, nodes):
        """Reparent bound visual bodies using the kinematic link tree, retaining authored pose.

        Source visual matrices already include the bind value in this AML example.
        MJCF ref keeps that initial pose; joint axes/anchors come from attachment_full.
        """
        files = sorted({n['source_file'] for n in nodes})
        for relative in files:
            path = self.resources.root/relative
            root, meter, basis = self.settings(path)
            models = root.findall('./{*}library_kinematics_models/{*}kinematics_model')
            if not models:
                continue
            ids = self.resources.document(path)[1]
            params = {n.get('sid'): n for n in root.iter() if tag(n) == 'newparam'}
            symbols = {n.get('symbol'): child(n, 'param').get('ref') for n in root.iter()
                       if tag(n) == 'bind' and child(n, 'param') is not None}
            def parameter(name):
                seen = set()
                while name in symbols:
                    if name in seen:
                        raise SceneError('运动学参数循环引用', 'resource_import')
                    seen.add(name); name = symbols[name]
                return params.get(name)
            bindings = {}
            for bind in root.findall('./{*}scene/{*}instance_kinematics_scene/{*}bind_joint_axis'):
                axis = child(bind, 'axis'); value = child(bind, 'value')
                p = parameter(text(axis, 'param')) if axis is not None else None
                sidref = text(p, 'SIDREF') if p is not None else ''
                vp = parameter(text(value, 'param')) if value is not None else None
                initial = float(text(vp, 'float', '0')) if vp is not None else float(text(value, 'float', '0')) if value is not None else 0.
                if len(sidref.split('/')) >= 2:
                    bindings[sidref.split('/')[-2]] = (bind.get('target', '').rsplit('/', 1)[0], initial)
            roots = root.findall('./{*}scene/{*}instance_kinematics_scene/{*}bind_kinematics_model')
            for model in models:
                common = child(model, 'technique_common')
                if common is None:
                    continue
                joints = {j.get('sid'): ids.get(j.get('url', '').lstrip('#')) for j in children(common, 'instance_joint')}
                def find_node(source_id):
                    found = [n for n in nodes if n['source_file'] == relative and n['source_id'] == source_id]
                    if len(found) != 1:
                        raise SceneError('运动学节点绑定缺失或不唯一：' + source_id, 'resource_import')
                    return found[0]
                if len(models) != 1 or len(roots) != 1:
                    raise SceneError('目前需要每个 DAE 场景明确绑定一个运动学模型', 'resource_import')
                root_node = find_node(roots[0].get('node', ''))
                def link(node, visual):
                    for attachment in children(node, 'attachment_full'):
                        alias = attachment.get('joint', '').rsplit('/', 1)[-1]
                        declared = joints.get(alias)
                        if declared is None or alias not in bindings:
                            raise SceneError('运动副缺少定义或视觉绑定：' + alias, 'resource_import')
                        target, initial_raw = bindings[alias]; moving = find_node(target)
                        kinds = [x for x in declared if tag(x) in ('revolute', 'prismatic')]
                        if len(kinds) != 1:
                            raise SceneError('目前只支持单轴转动副/移动副：' + alias, 'resource_import')
                        axis_node = kinds[0]; factor = meter if tag(axis_node) == 'prismatic' else math.pi/180
                        limits = child(axis_node, 'limits')
                        bounds = [float(text(limits, v))*factor for v in ('min', 'max')] if limits is not None else None
                        initial = initial_raw*factor
                        provisional = bounds is None
                        if provisional:
                            self.issues.append({'code':'joint_limits_or_coupling_missing', 'source':relative+'#'+alias,
                                                'effect':'preview retains initial pose; motion disabled'})
                            bounds = [initial, initial]
                        if not np.isfinite(bounds+[initial]).all() or bounds[0] > bounds[1]:
                            raise SceneError('运动副限位缺失或无效：' + alias, 'resource_import')
                        anchor = basis @ transforms(attachment, meter) @ np.linalg.inv(basis)
                        local = np.linalg.inv(visual['world']) @ moving['world']
                        frame = np.linalg.inv(local) @ anchor
                        axis = basis[:3, :3] @ numbers(text(axis_node, 'axis'), 3)
                        axis = frame[:3, :3] @ axis
                        if np.linalg.norm(axis) < 1e-12:
                            raise SceneError('运动副轴为零：' + alias, 'resource_import')
                        moving['parent'] = visual['id']; moving['local'] = local
                        moving['joint'] = {'source_file': relative, 'source_id': declared.get('id'), 'name': alias,
                            'type': 'slide' if tag(axis_node) == 'prismatic' else 'hinge',
                            'axis': (axis/np.linalg.norm(axis)).tolist(), 'pos': frame[:3, 3].tolist(),
                            'range': bounds, 'initial': initial, 'locked': abs(bounds[1]-bounds[0]) < 1e-12,
                            'provisional_fixed': provisional,
                            'source_unsupported': 'UnsupportedType' in declared.get('name', '')}
                        if moving['joint']['source_unsupported']:
                            self.issues.append({'code':'unsupported_source_joint', 'source':relative+'#'+alias,
                                                'effect':'source converter joint semantics require review'})
                        if not bounds[0]-1e-8 <= initial <= bounds[1]+1e-8:
                            self.issues.append({'code': 'initial_outside_range', 'source': relative+'#'+alias,
                                                'initial': initial, 'range': bounds})
                        for sub in children(attachment, 'link'):
                            link(sub, moving)
                for top in children(common, 'link'):
                    link(top, root_node)
