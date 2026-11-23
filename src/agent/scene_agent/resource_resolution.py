"""User-selected resource completion. No implicit generation or task cache reuse."""
import hashlib
import math
from pathlib import Path
import shutil

import numpy as np

from .common import SceneError, digest, read_json, write_json

SIMPLIFICATIONS = ('允许简化外观、非关键结构及几何细节；保留关键尺寸、位姿、层级、'
                  '安装接口偏置、运动副、速度与加速度、碰撞所需几何和 IO 绑定。')


def value(component, name, default=None):
    return component.get('attributes', {}).get(name, {}).get('value') or default


def dimensions(component):
    result = []
    for axis in 'xyz':
        field = component.get('attributes', {}).get('size_'+axis+'_m', {})
        if not field.get('value'):
            return None
        factor = {'m': 1., 'mm': .001, 'cm': .01}.get(field.get('unit') or 'm')
        if factor is None:
            return None
        result.append(float(field['value'])*factor)
    return result if np.isfinite(result).all() and min(result) > 0 else None


def inventory(report, nodes, resolutions):
    """Explicit representation/role wins; ambiguous nodes need a human decision."""
    rows = []
    mapping = report.get('resource_mapping', [])
    for c in report['components']:
        cid = c['id']; decision = resolutions.get(cid, {})
        refs = [r for r in mapping if r['component_id'] == cid and r.get('geometry')]
        owned = [n for n in nodes.values() if n['component_id'] == cid and n.get('meshes')]
        bound = any(r.get('status') == 'bound_existing_node' for r in refs)
        role = c['role'].get('RefBaseRoleClassPath', '').split('/')[-1]
        declared = value(c, 'geometry_role')
        structural = declared == 'structural' or role in ('Structure', 'Frame', 'Interface')
        # A successfully imported empty visual scene may be a frame/container.
        # A missing resource does require a model choice; an empty representation
        # without physical facts requires purpose confirmation first.
        known_physical = declared == 'physical' or any(r.get('status') == 'blocked' for r in refs) or dimensions(c) is not None
        state = ('resolved' if owned or bound else 'structural' if structural else
                 'missing_geometry' if known_physical else 'needs_classification')
        if decision.get('action') == 'structural' and not known_physical:
            state = 'structural'
        if decision.get('action') == 'physical' and state == 'needs_classification':
            state = 'missing_geometry'
        rows.append({'component_id': cid, 'name': c['name'], 'status': state,
                     'options': ['import', 'temporary'] if state == 'missing_geometry' else
                                ['physical', 'structural'] if state == 'needs_classification' else [],
                     'source': decision.get('action', 'aml'), 'scope': decision.get('scope'),
                     'dimensions_m': dimensions(c), 'decision': decision,
                     'representation_empty': bool(refs) and not owned and not bound,
                     'simplifications': SIMPLIFICATIONS})
    return rows


def rigid_matrix(raw):
    matrix = np.asarray(raw, dtype=float)
    if (matrix.shape != (4, 4) or not np.isfinite(matrix).all() or
            not np.allclose(matrix[3], [0, 0, 0, 1]) or
            not np.allclose(matrix[:3, :3].T@matrix[:3, :3], np.eye(3), atol=1e-8) or
            not np.isclose(np.linalg.det(matrix[:3, :3]), 1)):
        raise SceneError('模型到组件的映射必须是明确的刚体变换，不能缩放或拉伸', 'resource_import')
    return matrix


def obj_mesh(path, unit, up):
    """Geometry only: mechanics always come from AML or its explicit references."""
    scale = {'m': 1., 'mm': .001, 'cm': .01}.get(unit)
    if scale is None or up not in ('X_UP', 'Y_UP', 'Z_UP'):
        raise SceneError('OBJ 必须明确长度单位和向上轴', 'resource_import')
    from .collada_import import rotation
    basis = {'Z_UP': np.eye(4), 'Y_UP': rotation([1, 0, 0], math.pi/2),
             'X_UP': rotation([0, 1, 0], -math.pi/2)}[up][:3, :3]
    vertices, faces = [], []
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        parts = line.split()
        if not parts or parts[0].startswith('#'):
            continue
        if parts[0] == 'v':
            if len(parts) != 4:
                raise SceneError('OBJ 顶点必须有三个坐标', 'resource_import')
            vertices.append((basis@np.array([float(v) for v in parts[1:]])*scale).tolist())
        elif parts[0] == 'f':
            if len(parts) != 4:
                raise SceneError('OBJ 仅接受三角面，请先三角化', 'resource_import')
            indices = [int(v.split('/')[0]) for v in parts[1:]]
            if any(i == 0 for i in indices):
                raise SceneError('OBJ 面索引不能为零', 'resource_import')
            faces.append([i-1 if i > 0 else len(vertices)+i for i in indices])
    if (len(vertices) < 3 or not faces or not np.isfinite(vertices).all() or
            min(min(f) for f in faces) < 0 or max(max(f) for f in faces) >= len(vertices)):
        raise SceneError('OBJ 网格为空或包含无效索引/数值', 'resource_import')
    return {'vertices': vertices, 'faces': faces}


def snapshot_asset(path, destination):
    """Copy only a successfully parsed asset and its resolved XML dependencies."""
    from .resources import PackageResources
    from .collada_import import ColladaImporter
    path = Path(path).resolve(); destination = Path(destination)
    if not path.is_file() or path.suffix.lower() not in ('.dae', '.obj'):
        raise SceneError('请选择已有的 DAE 或三角化 OBJ 文件', 'resource_import')
    if path.stat().st_size > 64*1024*1024:
        raise SceneError('单个补件资源不能超过 64 MiB', 'resource_import')
    files = [path]
    if path.suffix.lower() == '.dae':
        resources = PackageResources(path.parent)
        ColladaImporter(resources).scene(path, path.name)
        resources.verify()
        files = [resources.root/name for name in resources.records]
    for source in files:
        target = destination/source.relative_to(path.parent)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return destination/path.name


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def generate_box(component, proposal, path):
    """Fixed tool executes an Agent selection; dimensions cannot be invented."""
    expected = {'tool': 'box', 'size_parameters': ['size_x_m', 'size_y_m', 'size_z_m']}
    if proposal != expected:
        raise SceneError('临时生成只支持引用 AML 尺寸的 box 工具，不接受自由代码或新参数', 'component_model')
    size = dimensions(component)
    if size is None or value(component, 'primitive_shape') != 'box':
        raise SceneError('临时方盒需 AML 明确 primitive_shape=box 及三个关键尺寸', 'component_model')
    if value(component, 'geometry_origin') != 'center':
        raise SceneError('临时方盒需 AML 明确 geometry_origin=center，不能猜测几何原点', 'component_model')
    xyz = [[x*size[0]/2, y*size[1]/2, z*size[2]/2]
           for z in (-1, 1) for y in (-1, 1) for x in (-1, 1)]
    faces = [(1, 3, 2), (2, 3, 4), (5, 6, 7), (6, 8, 7), (1, 2, 5), (2, 6, 5),
             (3, 7, 4), (4, 7, 8), (1, 5, 3), (3, 5, 7), (2, 4, 6), (4, 8, 6)]
    Path(path).write_text(''.join('v '+' '.join(map(str, v))+'\n' for v in xyz)+
                          ''.join('f '+' '.join(map(str, f))+'\n' for f in faces), encoding='utf-8')
    return size


class ResourceLibrary:
    """Versioned opt-in assets. A task-local temporary model never enters here implicitly."""
    def __init__(self, root):
        self.root = Path(root)

    def entries(self):
        return [read_json(p) for p in sorted(self.root.glob('*/entry.json'))]

    def add(self, source, decision, component, version, scope):
        import re
        if not re.fullmatch(r'\d+\.\d+\.\d+', version or '') or not str(scope or '').strip():
            raise SceneError('加入共享库必须指定版本（如 1.0.0）和适用范围', 'resource_import')
        key = digest({'decision': decision, 'version': version, 'scope': scope})[:32]
        root = self.root/key
        if root.exists():
            raise SceneError('该组件版本已存在', 'resource_import')
        asset = snapshot_asset(source, root/'asset')
        entry = {'id': key, 'name': component['name'], 'version': version, 'scope': scope,
                 'kind': 'mechanism' if asset.suffix.lower() == '.dae' else 'rigid_body',
                 'asset': asset.relative_to(root).as_posix(), 'sha256': file_hash(asset),
                 'mapping': {k: decision[k] for k in ('unit', 'up_axis', 'model_to_component') if k in decision},
                 'dimensions_m': dimensions(component), 'interfaces': component['interfaces'],
                 'mechanics': component.get('mechanical_definition'), 'source_task_only': False}
        entry['files'] = {p.relative_to(root).as_posix():file_hash(p) for p in (root/'asset').rglob('*') if p.is_file()}
        if asset.suffix.lower() == '.dae':
            from .resources import PackageResources
            from .collada_import import ColladaImporter
            imported = ColladaImporter(PackageResources(asset.parent)).scene(asset, asset.name)
            entry['kind'] = 'mechanism' if any(n.get('joint') for n in imported) else 'rigid_body'
            entry['internal_joints'] = [n['joint'] for n in imported if n.get('joint')]
        write_json(root/'entry.json', entry)
        return entry

    def get(self, ident):
        import re
        if not isinstance(ident, str) or not re.fullmatch('[a-f0-9]{32}', ident):
            raise SceneError('组件库 ID 无效', 'resource_import')
        root = self.root/ident
        entry = read_json(root/'entry.json')
        path = (root/entry['asset']).resolve()
        if not path.is_relative_to(root.resolve()) or file_hash(path) != entry['sha256']:
            raise SceneError('共享组件文件已改变，需要重新登记版本', 'resource_import')
        for name, sha in entry.get('files', {}).items():
            part = (root/name).resolve()
            if not part.is_relative_to(root.resolve()) or not part.is_file() or file_hash(part) != sha:
                raise SceneError('共享组件依赖已改变，需要重新登记版本', 'resource_import')
        return entry, path
