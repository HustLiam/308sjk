"""Resource task operations: select, snapshot, rebuild, and explicitly publish."""
from pathlib import Path
import shutil
import uuid

import numpy as np

from .common import SceneError, read_json, write_json, digest
from .package_import import import_package
from .resource_resolution import (ResourceLibrary, snapshot_asset, file_hash, rigid_matrix,
                                  generate_box, dimensions, value, SIMPLIFICATIONS)


def current_report(workbench, record):
    return read_json(workbench.directory(record['id'])/record['scene_kind']/'package_manifest.json')


def persist_source(folder, scene):
    source = folder/'package_source'
    shutil.copytree(scene/'source', source, dirs_exist_ok=True)
    return source


def resolve(workbench, data):
    from .web import SceneWebError
    ident = data.get('task'); action = data.get('action'); cid = data.get('component_id')
    with workbench.lock:
        record = workbench._record(ident)
        if not record.get('resource_package'):
            raise SceneWebError('此操作仅用于 AML 资源装配任务', 409)
        if type(data.get('revision')) is not int or data['revision'] != record['revision']:
            raise SceneWebError('场景版本已更新，请刷新后再操作', 409)
        if workbench.active or record['busy']:
            raise SceneWebError('请等待当前场景操作完成', 409)
        report = current_report(workbench, record)
        row = next((r for r in report.get('component_resolution', []) if r['component_id'] == cid), None)
        if row is None:
            raise SceneWebError('组件不存在')
        component = next(c for c in report['components'] if c['id'] == cid)
        library = ResourceLibrary(workbench.root/'resource_library')
        folder = workbench.directory(ident)
        choices_file = folder/'resource_choices.json'
        choices = read_json(choices_file) if choices_file.exists() else {}
        if action == 'publish':
            decision = choices.get(cid, {})
            if not decision.get('asset') or row['status'] != 'resolved':
                raise SceneWebError('仅可将已有几何的补件加入共享库')
            # Library publication records applicability, not simulation acceptance.
            entry = library.add(folder/'package_source'/decision['asset'], decision, component,
                                data.get('version'), data.get('scope'))
            decision['published_library_id'] = entry['id']; choices[cid] = decision
            write_json(choices_file, choices)
            workbench._log(ident, '用户将组件加入共享库：'+component['name']+' '+entry['version'])
            return workbench.state(ident)
        if action not in row['options'] and not (action == 'library' and 'import' in row['options']):
            raise SceneWebError('当前组件不需要该补件操作；已有几何不能重复生成', 409)
        if action in ('physical', 'structural'):
            decision = {'action': action, 'scope': 'task'}
        else:
            decision = {'action': action, 'scope': 'task', 'model_to_component': data.get('model_to_component'),
                        'unit': data.get('unit'), 'up_axis': data.get('up_axis')}
            if action == 'library':
                entry, path = library.get(data.get('library_id'))
                decision.update(entry['mapping'], library_id=entry['id'], scope='library')
            elif action == 'import':
                path = Path(str(data.get('path', ''))).expanduser().resolve()
                if not path.is_file():
                    raise SceneWebError('请填写补充模型的实际文件路径')
                if path.suffix.lower() == '.obj' and (decision['unit'] not in ('m', 'mm', 'cm') or decision['up_axis'] not in ('X_UP','Y_UP','Z_UP')):
                    raise SceneWebError('OBJ 必须选择长度单位和向上轴')
            else:
                if (dimensions(component) is None or value(component, 'primitive_shape') != 'box' or
                        value(component, 'geometry_origin') != 'center' or not component.get('mechanical_definition')):
                    raise SceneWebError('暂不能生成该组件：需明确方盒形状、三个关键尺寸、中心原点、安装接口和机械连接；可导入模型或补齐 AML')
                decision.update(unit='m', up_axis='Z_UP', model_to_component=np.eye(4).tolist())
            rigid_matrix(decision['model_to_component'])
        def run():
            decision_id = uuid.uuid4().hex
            destination = folder/'package_source'/'_resolved'/decision_id
            if action in ('import', 'library'):
                asset = snapshot_asset(path, destination)
                decision.update(asset=asset.relative_to(folder/'package_source').as_posix(), sha256=file_hash(asset))
            elif action == 'temporary':
                destination.mkdir(parents=True)
                author = workbench.author_factory(workbench.control)
                proposal = author.author_resource_component(component, destination)
                asset = destination/'component.obj'
                generate_box(component, proposal, asset)
                decision.update(asset=asset.relative_to(folder/'package_source').as_posix(), sha256=file_hash(asset),
                                generation_id=decision_id, task_id=ident, simplifications=SIMPLIFICATIONS)
            choices[cid] = decision
            scene_name = 'imported_scene_'+decision_id
            aml = folder/'package_source'/Path(report['source']['file']).name
            result = import_package(aml, folder/scene_name, resolutions=choices, aliases=record.get('resource_aliases'),
                                    progress=lambda message: workbench._log(ident, message),
                                    preview=workbench.assembly_publisher(ident))
            write_json(choices_file, choices)
            if action in ('import', 'library') and next(r for r in result['component_resolution'] if r['component_id'] == cid)['status'] == 'resolved':
                bindings_file = workbench.root/'resource_bindings.json'
                bindings = read_json(bindings_file) if bindings_file.exists() else {}
                bindings[digest([report['source']['sha256'], cid])] = {
                    'decision': {k:v for k,v in decision.items() if k not in ('asset','scope')},
                    'path': str(path), 'sha256': file_hash(path), 'source': 'explicit_user_selection',
                    'files': {p.relative_to(destination).as_posix():file_hash(p) for p in destination.rglob('*') if p.is_file()}}
                write_json(bindings_file, bindings)
            workbench._update(ident, scene_kind=scene_name, preview_revision=record.get('preview_revision',0)+1)
            workbench._log(ident, '补件已隔离装配；结构差异和未完成验证见检查结果')
        workbench._start(record, 'resolving', run)
        return workbench.state(ident)


def restore_bindings(workbench, folder, report):
    """Only explicitly saved imported/library bindings; temporary choices are excluded."""
    bindings_file = workbench.root/'resource_bindings.json'
    if not bindings_file.exists():
        return {}
    bindings = read_json(bindings_file); choices = {}
    for row in report.get('component_resolution', []):
        if row['status'] != 'missing_geometry':
            continue
        cid = row['component_id']; binding = bindings.get(digest([report['source']['sha256'], cid]))
        if not binding or binding['decision'].get('action') not in ('import', 'library'):
            continue
        path = Path(binding['path'])
        changed = not path.is_file() or file_hash(path) != binding['sha256']
        for name, sha in binding.get('files', {}).items():
            dependency = (path.parent/name).resolve()
            if not dependency.is_relative_to(path.parent.resolve()) or not dependency.is_file() or file_hash(dependency) != sha:
                changed = True
        if changed:
            workbench._log(folder.name, '已保存的模型绑定失效，需重新选择：'+row['name'])
            continue
        asset = snapshot_asset(path, folder/'package_source'/'_resolved'/uuid.uuid4().hex)
        choices[cid] = {**binding['decision'], 'asset': asset.relative_to(folder/'package_source').as_posix(),
                        'scope': 'library' if binding['decision']['action'] == 'library' else 'task',
                        'binding_reused': True}
    return choices
