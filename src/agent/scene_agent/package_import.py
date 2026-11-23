"""AML resource-first assembly. Import never authors missing facts or grants approval."""
from collections import Counter
import hashlib
import math
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET

import numpy as np

from .common import SceneError, digest, quaternion, transform, write_json
from .resources import PackageResources, attributes, child, children, tag
from .collada_import import ColladaImporter

FORMAT = '308sjk.aml_resource_package/1'


def frame(attrs):
    fields = attrs.get('Frame', {}).get('children', {})
    result = []
    for name in ('x', 'y', 'z', 'rx', 'ry', 'rz'):
        field = fields.get(name, {}); unit = field.get('unit')
        factors = {'m': 1., 'mm': .001, 'cm': .01} if len(name) == 1 else {'deg': math.pi/180, 'rad': 1.}
        default = 'm' if len(name) == 1 else 'deg'
        if unit is not None and unit not in factors:
            raise SceneError('Frame 单位不支持：' + str(unit), 'resource_import')
        result.append(float(field.get('value') or 0)*factors[unit or default])
    if not np.isfinite(result).all():
        raise SceneError('Frame 包含非有限数值', 'resource_import')
    return transform(result[:3], result[3:])


def inspect_package(aml, aliases=None):
    aml = Path(aml).resolve(); resources = PackageResources(aml.parent, aliases)
    root, _ = resources.document(aml)
    hierarchy = child(root, 'InstanceHierarchy')
    if tag(root) != 'CAEXFile' or hierarchy is None:
        raise SceneError('资源包入口必须是包含 InstanceHierarchy 的 CAEXFile', 'resource_import')
    components = []; interfaces = []; links = []; index = {}; interface_index = {}; issues = []
    schema = root.get('{http://www.w3.org/2001/XMLSchema-instance}noNamespaceSchemaLocation')
    schema_result = {'status': 'not_declared'}
    if schema:
        try:
            sp, _ = resources.resolve(aml, schema); resources.document(sp)
            from lxml import etree
            parser = etree.XMLParser(resolve_entities=False, no_network=True)
            sd = etree.fromstring(sp.read_bytes(), parser)
            if sd.findall('.//{*}include') or sd.findall('.//{*}import'):
                raise SceneError('XSD 外部依赖需要显式导入适配', 'resource_import')
            validator = etree.XMLSchema(sd)
            valid = validator.validate(etree.fromstring(aml.read_bytes(), parser))
            schema_result = {'status': 'passed' if valid else 'failed', 'path': resources.relative(sp),
                             'errors': [str(e) for e in validator.error_log]}
            if not valid:
                issues.append({'code': 'xsd_invalid', 'detail': schema_result['errors']})
        except (SceneError, ImportError) as exc:
            schema_result = {'status': 'blocked', 'error': str(exc)}
            issues.append({'code': 'xsd_unverified', 'detail': str(exc)})
    def walk(n, parent, locator):
        cid = n.get('ID') or 'generated_'+digest(locator)[:16]
        if cid in index:
            raise SceneError('AML 组件 ID 重复', 'resource_import')
        attrs = attributes(n); local = frame(attrs)
        world = (index[parent]['world'] if parent else np.eye(4)) @ local
        role = child(n, 'RoleRequirements')
        record = {'id': cid, 'name': n.get('Name', ''), 'parent_id': parent, 'source': locator,
                  'attributes': attrs, 'role': dict(role.attrib) if role is not None else {},
                  'xml_attributes': dict(n.attrib), 'local': local, 'world': world, 'interfaces': []}
        index[cid] = record; components.append(record)
        for f in children(n, 'ExternalInterface'):
            fid = f.get('ID') or 'generated_'+digest(locator+'#'+str(f.get('Name')))[:16]
            rec = {'id': fid, 'name': f.get('Name'), 'component_id': cid,
                   'class': f.get('RefBaseClassPath', ''), 'attributes': attributes(f), 'xml_attributes': dict(f.attrib)}
            if fid in interface_index:
                raise SceneError('AML 接口 ID 重复', 'resource_import')
            interfaces.append(rec); interface_index[fid] = rec; record['interfaces'].append(fid)
        for l in children(n, 'InternalLink'):
            links.append({'id': l.get('ID') or 'link_'+digest(locator+'/'+str(l.attrib))[:16],
                          'owner': cid, 'name': l.get('Name'), 'attributes': attributes(l), 'xml_attributes': dict(l.attrib)})
        for n2 in children(n, 'InternalElement'):
            walk(n2, cid, locator+'/'+str(n2.get('Name')))
    for n in children(hierarchy, 'InternalElement'):
        walk(n, None, str(n.get('Name')))
    def endpoint(value):
        if value in interface_index:
            return value
        match = re.fullmatch(r'\[([^\]]+)\]:\[([^\]]+)\]', value or '')
        if match:
            found = [f['id'] for f in interfaces if f['component_id'] == match[1] and f['name'] == match[2]]
            if len(found) == 1:
                return found[0]
        raise SceneError('连接端点无法解析：' + str(value), 'resource_import')
    for l in links:
        try:
            l['a'] = endpoint(l['xml_attributes'].get('RefPartnerSideA'))
            l['b'] = endpoint(l['xml_attributes'].get('RefPartnerSideB'))
        except SceneError as exc:
            issues.append({'code': 'unresolved_link', 'source': l['id'], 'detail': str(exc)})
    return resources, {'format': FORMAT, 'source': {'file': str(aml), 'sha256': resources.records[aml.name]['sha256']},
        'caex_version': root.get('SchemaVersion'), 'schema_validation': schema_result,
        'frame_convention': {'length_default': 'm', 'angle_default': 'deg', 'rotation': 'Rz Ry Rx', 'up': 'Z'},
        'components': components, 'interfaces': interfaces, 'connections': links,
        'class_libraries': [{'kind': tag(n), 'name': n.get('Name'), 'xml': ET.tostring(n, encoding='unicode')}
                            for n in root if tag(n).endswith('ClassLib')],
        'document_metadata': [{'kind': tag(n), 'xml': ET.tostring(n, encoding='unicode')} for n in root
                              if tag(n) in ('AdditionalInformation', 'Version', 'SourceDocumentInformation')],
        'issues': issues}


def serializable(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, dict):
        return {k: serializable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [serializable(v) for v in value]
    return value


def attach_components(nodes, manifest, explicit_components):
    """Make attachment-connected imported roots follow their host frame.

    InternalLink is symmetric. Prefer the moving endpoint as host; for a static
    pair use source endpoint order. Retain authored relative pose, recording
    interface residuals separately: a link does not supply a mating tolerance.
    Closed loops and competing attachments are not silently converted to trees.
    """
    interfaces = {f['id']:f for f in manifest['interfaces']}
    components = {c['id']:c for c in manifest['components']}
    assigned = set()
    containing=set(explicit_components)
    for cid in explicit_components:
        parent=components[cid]['parent_id']
        while parent:
            containing.add(parent);parent=components[parent]['parent_id']
    def ancestry(key):
        result=[]
        while key:
            if key in result:raise SceneError('装配层级存在环', 'assembly')
            result.append(key);key=nodes[key]['parent']
        return result
    def owner(key):
        while key and key not in explicit_components:
            if key in containing and components[key]['role'].get('RefBaseRoleClassPath','').endswith('/Structure'):return key
            key=components[key]['parent_id']
        return key
    for link in manifest['connections']:
        if not all(k in link for k in ('a','b')):continue
        a,b=(interfaces[link[k]] for k in ('a','b'))
        if not all('AttachmentInterface' in f['class'] for f in (a,b)):
            link['assembly_status']='preserved_nonmechanical';continue
        if not any(components[f['component_id']].get('mechanical_definition') for f in (a,b)):
            manifest['issues'].append({'code':'connection_type_unspecified','source':link['id'],
                'detail':'AttachmentInterface 链接保留安装关系；未明确 fixed/运动副语义，当前仅用于预览'})
        endpoints=[f['component_id'] for f in (a,b)];owners=[owner(k) for k in endpoints]
        if not all(owners):
            link['assembly_status']='unresolved_component'
            manifest['issues'].append({'code':'attachment_component_missing','source':link['id']})
            continue
        if owners[0]==owners[1]:
            link['assembly_status']='internal_constraint_preserved';continue
        scores=[sum(bool(nodes[k].get('joint') and not nodes[k]['joint']['locked']) for k in ancestry(e)) for e in endpoints]
        order=(0,1) if scores[0]>=scores[1] else (1,0)
        attached=False
        for host in order:
            target=owners[1-host];anchor=endpoints[host]
            if target in assigned or target in ancestry(anchor):continue
            nodes[target]['source_parent']=nodes[target]['parent']
            nodes[target]['parent']=anchor
            assigned.add(target);attached=True
            link.update(assembly_status='attached_preserving_source_pose',host_frame=anchor,attached_component=target)
            break
        if not attached:
            link['assembly_status']='closed_loop_or_multiple_attachment'
            manifest['issues'].append({'code':'attachment_requires_constraint_review','source':link['id'],
                                       'detail':'闭环或多点安装需补充约束语义，保留连接记录，不强制改写位姿'})


def import_package(aml, output, *, aliases=None, progress=None, resolutions=None, preview=None):
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise SceneError('导入输出必须为空目录', 'resource_import')
    output.mkdir(parents=True, exist_ok=True)
    progress = progress or (lambda message: None)
    resources, report = inspect_package(aml, aliases)
    from . import resource_mechanics
    from .resource_resolution import inventory, obj_mesh, rigid_matrix, dimensions
    resolutions = resolutions or {}
    normalized = resource_mechanics.normalize(report, resources, Path(aml).resolve())
    report['issues'].extend(normalized['issues'])
    importer = ColladaImporter(resources); issues = report['issues']
    components = {c['id']: c for c in report['components']}; interfaces = {f['id']: f for f in report['interfaces']}
    nodes = {}; scene_sets = {}; mapping = []; logic = []
    for c in report['components']:
        nodes[c['id']] = {'id': c['id'], 'parent': c['parent_id'], 'world': c['world'],
                          'name': c['name'], 'meshes': [], 'component_id': c['id']}
    if preview:preview(report, nodes, importer.meshes)
    for c in report['components']:
        for fid in c['interfaces']:
            f = interfaces[fid]; attrs = {k:v['value'] for k,v in f['attributes'].items()}
            uri = attrs.get('refURI')
            if not uri:
                continue
            item = {'component_id': c['id'], 'interface_id': fid, 'uri': uri, 'ref_type': attrs.get('refType'), 'target': attrs.get('target'),
                    'geometry': 'COLLADAInterface' in f['class']}
            mapping.append(item)
            if 'COLLADAInterface' in f['class'] and attrs.get('refType', 'explicit') == 'explicit':
                if resolutions.get(c['id'], {}).get('asset'):
                    item.update(status='user_replaced', destination='resource_resolution.json')
                    continue
                progress('导入组件：'+c['name'])
                try:
                    imported = importer.scene(Path(aml).resolve(), uri)
                    names = {n['id']: fid+'_'+n['id'] for n in imported}
                    for n in imported:
                        rec = {**n, 'id': names[n['id']], 'parent': names[n['parent']] if n['parent'] is not None else c['id'],
                               'world': c['world']@n['world'], 'component_id': c['id']}
                        nodes[rec['id']] = rec
                    scene_sets[c['id']] = [names[n['id']] for n in imported]
                    item.update(status='imported', destination='scene.xml', node_count=len(imported))
                    if preview:preview(report, nodes, importer.meshes)
                except (SceneError, ValueError, KeyError) as exc:
                    item.update(status='blocked', detail=str(exc))
                    issues.append({'code':'component_import_failed', 'source':fid, 'detail':str(exc)})
            elif 'PLCopenXMLInterface' in f['class']:
                try:
                    path, element = resources.element(Path(aml).resolve(), uri)
                    logic.append({'interface_id':fid, 'source':resources.relative(path), 'fragment':element.get('globalId'),
                                  'pou_name':element.get('name'), 'xml':ET.tostring(element, encoding='unicode'),
                                  'status':'preserved_not_executed'})
                    item.update(status='preserved', destination='logic_models.json')
                except SceneError as exc:
                    item.update(status='blocked', detail=str(exc));issues.append({'code':'logic_resource_missing', 'source':fid, 'detail':str(exc)})
            elif 'COLLADAInterface' not in f['class']:
                item.update(status='preserved', destination='package_manifest.json')
    # Only user-selected missing components can reach this path. All geometry is
    # task-local; it does not modify the AML or the shared component rule library.
    for cid, decision in resolutions.items():
        if not decision.get('asset'):
            continue
        c = components[cid]
        item = {'component_id': cid, 'interface_id': None, 'uri': decision['asset'], 'geometry': True,
                'ref_type': 'user_selected', 'source': decision['action'], 'scope': decision['scope']}
        mapping.append(item)
        try:
            path, _ = resources.resolve(Path(aml).resolve(), decision['asset'])
            if decision['action'] == 'temporary':
                for filename in ('request.json','response.json','proposal.json'):
                    evidence=path.with_name(filename)
                    if evidence.is_file():resources.blob(evidence)
            transform_model = rigid_matrix(decision['model_to_component'])
            if path.suffix.lower() == '.obj':
                resources.blob(path)
                key = resources.relative(path)
                importer.meshes[key] = obj_mesh(path, decision.get('unit'), decision.get('up_axis'))
                imported = [{'id': 'geometry', 'parent': None, 'world': np.eye(4), 'meshes': [key],
                             'name': c['name'], 'source_file': key, 'source_id': '', 'sid': ''}]
            else:
                imported = importer.scene(Path(aml).resolve(), decision['asset'])
            names = {n['id']: 'resolved_'+cid+'_'+n['id'] for n in imported}
            for n in imported:
                rec = {**n, 'id': names[n['id']], 'parent': names[n['parent']] if n['parent'] is not None else cid,
                       'world': c['world']@transform_model@n['world'], 'component_id': cid}
                nodes[rec['id']] = rec
            scene_sets[cid] = list(names.values())
            if preview:preview(report, nodes, importer.meshes)
            item.update(status='imported', destination='scene.xml', node_count=len(imported))
            points = []
            for n in imported:
                for key in n['meshes']:
                    vertices = np.asarray(importer.meshes[key]['vertices'])
                    matrix = transform_model@n['world']
                    points.extend(vertices@matrix[:3,:3].T+matrix[:3,3])
            expected = dimensions(c)
            actual = np.ptp(points, axis=0).tolist() if points else None
            item.update(expected_dimensions_m=expected, actual_dimensions_m=actual)
            if expected is not None and (actual is None or not np.allclose(expected, actual, atol=1e-6, rtol=1e-5)):
                issues.append({'code': 'component_dimensions_mismatch', 'source': cid,
                               'expected_m': expected, 'actual_m': actual, 'detail': '保留导入尺寸，不自动拉伸'})
        except (SceneError, ValueError, KeyError) as exc:
            item.update(status='blocked', detail=str(exc))
            issues.append({'code': 'component_import_failed', 'source': cid, 'detail': str(exc)})
    # Implicit COLLADA nodes identify existing imported bodies/frames; do not duplicate meshes.
    for item in mapping:
        if item['ref_type'] != 'implicit':
            continue
        c = components[item['component_id']]
        try:
            uri_used = item['uri']
            ancestor = c['parent_id']
            while ancestor:
                replacement = resolutions.get(ancestor, {}).get('asset')
                originals = [r for r in mapping if r['component_id']==ancestor and r.get('status')=='user_replaced']
                if replacement and replacement.lower().endswith('.dae') and any(
                        r['uri'].split('#')[0].removeprefix('./')==item['uri'].split('#')[0].removeprefix('./') for r in originals):
                    uri_used = replacement + ('#'+item['uri'].split('#',1)[1] if '#' in item['uri'] else '')
                    item['resolution_uri'] = uri_used
                    break
                ancestor = components[ancestor]['parent_id']
            file, fragment = resources.resolve(Path(aml).resolve(), uri_used)
            root, ids = resources.document(file)
            selected = ids.get(fragment)
            if selected is None:
                raise SceneError('隐式引用 fragment 不存在', 'resource_import')
            target = item['target']
            if target:
                for segment in target.removeprefix('./').split('/'):
                    found = [n for n in selected if n.get('sid') == segment or n.get('id') == segment]
                    if len(found) != 1:
                        raise SceneError('隐式引用 target 缺失或不唯一：'+segment, 'resource_import')
                    selected = found[0]
            ancestor = c['parent_id']; candidates = []
            while ancestor:
                candidates = [nodes[k] for k in scene_sets.get(ancestor, []) if nodes[k]['source_file'] == resources.relative(file)
                              and ((selected.get('id') and nodes[k]['source_id'] == selected.get('id')) or
                                   (selected.get('sid') and nodes[k]['sid'] == selected.get('sid')))]
                if candidates:
                    break
                ancestor = components[ancestor]['parent_id']
            if len(candidates) != 1:
                raise SceneError('隐式引用未匹配到唯一已导入实例', 'resource_import')
            target_node = candidates[0]
            # AML Frame describes this component relative to its referenced frame.
            nodes[c['id']]['parent'] = target_node['id']
            old_world=nodes[c['id']]['world'].copy()
            nodes[c['id']]['world'] = target_node['world'] @ c['local']
            delta=nodes[c['id']]['world']@np.linalg.inv(old_world)
            descendants={c['id']}
            changed=True
            while changed:
                changed=False
                for key,node in nodes.items():
                    if key not in descendants and node['parent'] in descendants:
                        descendants.add(key);node['world']=delta@node['world'];changed=True
            c['world'] = nodes[c['id']]['world']
            item.update(status='bound_existing_node', destination='scene.xml/site', body=target_node['id'])
        except (SceneError, KeyError) as exc:
            item.update(status='blocked', detail=str(exc));issues.append({'code':'implicit_reference_failed', 'source':item['interface_id'], 'detail':str(exc)})
    issues.extend(importer.issues)
    progress('正在生成 MuJoCo 装配与源数据对账')
    report['resource_mapping'] = mapping
    report['resource_policy'] = 'explicit resources first; missing components are reported, never sent to an author automatically'
    for cid,c in components.items():c['world']=nodes[cid]['world']
    attach_components(nodes,report,set(scene_sets))
    resource_mechanics.apply(nodes, report)
    if preview:preview(report, nodes, importer.meshes)
    report['component_resolution'] = inventory(report, nodes, resolutions)
    report['completion_policy'] = 'user_choice_per_missing_component; temporary_task_only; no_automatic_cache'
    report['mechanical_definition'] = resource_mechanics.ledger(report, nodes, normalized)
    externally_defined = {r['component_id'] for r in report['mechanical_definition']['external_joints']}
    for row in report['component_resolution']:
        if row['status'] in ('missing_geometry', 'needs_classification'):
            issues.append({'code': row['status'], 'source': row['component_id'], 'detail': '等待用户选择补件方式或确认组件用途'})
        if row['status'] != 'structural' and not components[row['component_id']].get('mechanical_definition') and row['component_id'] not in externally_defined:
            issues.append({'code':'mechanical_definition_missing','source':row['component_id'],
                           'detail':'未定位到明确的安装接口及 fixed/运动副声明；几何可预览，不能据此验收机械关系'})
    write_json(output/'resource_resolution.json', report['component_resolution'])
    write_json(output/'mechanical_definitions.json', serializable(report['mechanical_definition']))
    write_json(output/'data_destinations.json', {'format':FORMAT,'resources':mapping,'nodes':serializable(list(nodes.values()))})
    scene_report = build_scene(nodes, importer.meshes, report, output)
    report['assembly'] = scene_report
    report['resources'] = list(resources.records.values());report['references'] = resources.references
    report['source_inventory']=[{'path':name,'elements':dict(Counter(tag(n) for n in doc[0].iter()))}
                                for name,doc in resources.documents.items()]
    report['unmapped_data']=[
        {'group':'materials/textures/normals','destination':'source/*.dae','processing':'源 XML 保留；预览使用统一材质并重算法线'},
        {'group':'articulated_system speed/acceleration, extra, vendor attributes','destination':'source/*.dae + source_inventory','processing':'保留声明；尚未映射到驱动器'},
        {'group':'PLCopen schema dependencies','destination':'source/LogicXML','processing':'行为 XML 保留；包内未提供 tc6_xml_v201.xsd 与 AML_addData.xsd，未完成行为 XSD 校验'}]
    report['counts'] = {'components':len(components), 'interfaces':len(interfaces), 'connections':len(report['connections']),
                        'explicit_references':sum(i['ref_type']=='explicit' for i in mapping),
                        'implicit_references':sum(i['ref_type']=='implicit' for i in mapping),
                        'imported_meshes':len(importer.meshes), 'resource_files':len(resources.records),
                        'logic_models':len(logic)}
    report['gaps'] = [
        {'code':'io_contract_missing', 'required':'逐点 IO 名称、方向、类型、地址、初值、范围、对象/物理量、换算与响应阈值', 'effect':'不能执行 OpenPLC IO 校验'},
        {'code':'verification_requirements_missing', 'required':'检查目标、容差、响应超时', 'effect':'不能判定设备验收通过'},
        {'code':'collision_geometry_unreviewed', 'required':'适用于接触检查的碰撞体与接触排除规则', 'effect':'导入网格仅作视觉，碰撞与动力学未验收'},
        {'code':'runtime_behavior_mapping_missing', 'required':'PLCopen 行为到运行内核的映射及速度/加速度策略', 'effect':'保留逻辑模型，不执行整机行为'}]
    partial=any(i['code'] in ('component_import_failed','implicit_reference_failed','unresolved_link','missing_geometry','needs_classification') for i in issues)
    report['status'] = ('partial_assembly' if partial else 'assembled_with_gaps') if scene_report.get('compiled') else 'blocked'
    report['model_frozen'] = False;report['simulation_acceptance'] = 'blocked'
    resources.verify()
    for relative in resources.records:
        target = output/'source'/relative;target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(resources.root/relative, target)
    write_json(output/'package_manifest.json', serializable(report))
    write_json(output/'logic_models.json', logic)
    write_json(output/'data_destinations.json', {'format':FORMAT, 'resources':mapping, 'nodes':serializable(list(nodes.values())),
        'groups': {'identity_hierarchy':'package_manifest.json/components + scene.xml/body',
                   'Frame':'components/local,world + scene.xml/body transforms (m/rad)',
                   'class_libraries':'package_manifest.json/class_libraries (preserved)',
                   'COLLADA_geometry':'meshes/*.obj + scene.xml/asset,geom',
                   'COLLADA_kinematics':'data_destinations.json/nodes/joint + scene.xml/joint or fixed body',
                   'AttachmentInterface_InternalLink':'assembly report + scene.xml/site; ambiguous constraints remain blocked',
                   'PLCopenXML':'logic_models.json + source/LogicXML (not executed)',
                   'unmapped_extensions':'source snapshots (preserved, not assumed implemented)'}})
    return report


def build_scene(nodes, meshes, manifest, output):
    import mujoco
    def nums(v):
        return ' '.join(format(float(x), '.12g') for x in v)
    root = ET.Element('mujoco', model='AML_resource_assembly')
    ET.SubElement(root, 'compiler', angle='radian', inertiafromgeom='false', fusestatic='false')
    option = ET.SubElement(root, 'option', gravity='0 0 0')
    ET.SubElement(option, 'flag', contact='disable')
    ET.SubElement(root, 'size', memory='128M')
    asset = ET.SubElement(root, 'asset'); world = ET.SubElement(root, 'worldbody')
    names = {key:'b'+str(i) for i,key in enumerate(nodes)};mesh_names = {};mesh_manifest=[]
    for i,(key, mesh) in enumerate(meshes.items()):
        name = 'm'+str(i);mesh_names[key]=name;file=output/'meshes'/(name+'.obj');file.parent.mkdir(exist_ok=True)
        vertices = mesh['vertices']; faces = mesh['faces']
        # MuJoCo requires >=4 vertices even for a visual-only triangle. Subdivide,
        # preserving the exact surface rather than inventing thickness.
        if len(vertices) == 3:
            vertices = vertices + [np.mean(vertices, axis=0).tolist()]
            faces = [[f[0], f[1], 3] for f in faces] + [[f[1], f[2], 3] for f in faces] + [[f[2], f[0], 3] for f in faces]
        with file.open('w', encoding='utf-8') as stream:
            for v in vertices:stream.write('v '+nums(v)+'\n')
            for f in faces:stream.write('f '+' '.join(str(j+1) for j in f)+'\n')
        ET.SubElement(asset,'mesh',name=name,file='meshes/'+name+'.obj',inertia='shell')
        points=np.asarray(mesh['vertices'])
        mesh_manifest.append({'name':name,'source':key,'file':'meshes/'+name+'.obj','unit':'m',
                              'min':points.min(axis=0).tolist(),'max':points.max(axis=0).tolist(),
                              'dimensions':np.ptp(points,axis=0).tolist(),'vertices':len(vertices),'faces':len(faces),
                              'visual_only':True,'triangle_subdivision':len(mesh['vertices'])==3})
    built={};active=set();checks=[];joints=[]
    def build(key):
        if key in built:return built[key]
        if key in active:raise SceneError('装配层级存在环', 'assembly')
        active.add(key);n=nodes[key];parent=n['parent']
        p=build(parent) if parent else world
        local=np.linalg.inv(nodes[parent]['world'])@n['world'] if parent else n['world']
        # Source text rotations have rounding noise; retain the source matrix in the ledger.
        u,_,vt=np.linalg.svd(local[:3,:3]);r=u@vt
        if np.linalg.det(r)<0 or not np.allclose(local[:3,:3],r,atol=2e-5):
            raise SceneError('装配变换包含缩放/反射，需显式几何转换：'+n['name'], 'assembly')
        body=ET.SubElement(p,'body',name=names[key],pos=nums(local[:3,3]),quat=nums(quaternion(r)))
        ET.SubElement(body,'inertial',pos='0 0 0',mass='1',diaginertia='0.01 0.01 0.01')
        if n.get('joint'):
            j=n['joint'];joints.append({**j,'body':names[key]})
            if not j['locked']:
                ET.SubElement(body,'joint',name='j'+names[key],type=j['type'],pos=nums(j['pos']),axis=nums(j['axis']),
                              limited='true',range=nums(j['range']),ref=str(j['initial']),damping='100')
        for mesh in n['meshes']:
            ET.SubElement(body,'geom',type='mesh',mesh=mesh_names[mesh],contype='0',conaffinity='0',mass='0',rgba='.55 .65 .72 1')
        if key in {c['id'] for c in manifest['components']}:
            ET.SubElement(body,'site',name='s'+names[key],size='.002',rgba='0 .6 .6 0')
        built[key]=body;active.remove(key);return body
    for key in nodes:build(key)
    interface_sites=[]
    for c in manifest['components']:
        for name,matrix in c.get('mechanical_definition',{}).get('interfaces',{}).items():
            matrix=np.asarray(matrix);site='interface_'+str(len(interface_sites))
            ET.SubElement(built[c['id']],'site',name=site,pos=nums(matrix[:3,3]),quat=nums(quaternion(matrix[:3,:3])),
                          size='.003',rgba='0 .6 .6 1')
            interface_sites.append({'name':site,'component_id':c['id'],'interface':name,'local':matrix.tolist()})
    by_interface={f['id']:f for f in manifest['interfaces']}
    for link in manifest['connections']:
        if 'a' not in link or 'b' not in link:continue
        a,b=(by_interface[link[k]] for k in ('a','b'))
        ta=nodes[a['component_id']]['world']@frame(a['attributes']);tb=nodes[b['component_id']]['world']@frame(b['attributes'])
        delta=np.linalg.inv(ta)@tb
        checks.append({'id':link['id'],'name':link['name'],'position_residual_m':float(np.linalg.norm(delta[:3,3])),
                       'rotation_residual_rad':float(math.acos(np.clip((np.trace(delta[:3,:3])-1)/2,-1,1))),
                       'status':'measured_no_declared_tolerance','assembly_status':link.get('assembly_status'),
                       'a':a['id'],'b':b['id']})
        source_components={c['id']:c for c in manifest['components']}
        checks[-1]['frame_sources']=[('collada_node' if nodes[f['component_id']].get('parent') not in source_components
                                      else 'aml_frame' if 'Frame' in source_components[f['component_id']]['attributes'] or 'Frame' in f['attributes']
                                      else 'identity_default_no_frame_declared') for f in (a,b)]
        if 'identity_default_no_frame_declared' in checks[-1]['frame_sources']:
            manifest['issues'].append({'code':'attachment_frame_unspecified','source':link['id'],
                                       'detail':'连接端点未提供局部 Frame 或 COLLADA 节点定位；预览采用所属组件原点，安装偏置待补充'})
    xml=ET.tostring(root,encoding='unicode');(output/'scene.xml').write_text(xml,encoding='utf-8')
    result={'compiled':False,'body_count':len(nodes),'joint_declarations':len(joints),
            'movable_joints':sum(not j['locked'] for j in joints),'locked_joints':sum(j['locked'] for j in joints),
            'source_locked_joints':sum(j['locked'] and not j.get('provisional_fixed') for j in joints),
            'preview_only_fixed_joints':sum(bool(j.get('provisional_fixed')) for j in joints),
            'joint_records':joints,'attachment_checks':checks,'interface_sites':interface_sites,'collision_validation':'not_run',
            'physics_profile':'import_preview_uniform_mass_inertia_zero_gravity',
            'model_xml_sha256':hashlib.sha256(xml.encode()).hexdigest()}
    try:
        m=mujoco.MjModel.from_xml_path(str(output/'scene.xml'));d=mujoco.MjData(m);mujoco.mj_forward(m,d)
        errors=[]
        structure=[]
        for key,n in nodes.items():
            bid=mujoco.mj_name2id(m,mujoco.mjtObj.mjOBJ_BODY,names[key])
            errors.append(float(np.linalg.norm(d.xpos[bid]-n['world'][:3,3])))
            expected_parent=mujoco.mj_name2id(m,mujoco.mjtObj.mjOBJ_BODY,names[n['parent']]) if n['parent'] else 0
            structure.append({'node':key,'check':'body_parent','passed':int(m.body_parentid[bid])==expected_parent})
            if n.get('joint') and not n['joint']['locked']:
                jid=mujoco.mj_name2id(m,mujoco.mjtObj.mjOBJ_JOINT,'j'+names[key]);j=n['joint']
                passed=(jid>=0 and int(m.jnt_type[jid])==int(mujoco.mjtJoint.mjJNT_HINGE if j['type']=='hinge' else mujoco.mjtJoint.mjJNT_SLIDE)
                        and np.allclose(m.jnt_axis[jid],j['axis'],atol=1e-8) and np.allclose(m.jnt_range[jid],j['range'],atol=1e-8)
                        and np.allclose(m.jnt_pos[jid],j['pos'],atol=1e-8))
                structure.append({'node':key,'check':'joint_definition','passed':bool(passed)})
        # Read compiled mesh vertices in their actual geom frame. MuJoCo may
        # recenter/reorient mesh assets, so raw mesh_vert alone is insufficient.
        from .resource_resolution import dimensions
        body_owners={mujoco.mj_name2id(m,mujoco.mjtObj.mjOBJ_BODY,names[k]):n['component_id'] for k,n in nodes.items()}
        expected_components={c['id']:dimensions(c) for c in manifest['components'] if dimensions(c) is not None}
        actual_points={cid:[] for cid in expected_components}
        for gid in range(m.ngeom):
            cid=body_owners[int(m.geom_bodyid[gid])]
            if cid not in actual_points:continue
            mesh=int(m.geom_dataid[gid]);start=int(m.mesh_vertadr[mesh]);count=int(m.mesh_vertnum[mesh])
            vertices=m.mesh_vert[start:start+count].astype(float)
            world_points=vertices@d.geom_xmat[gid].reshape(3,3).T+d.geom_xpos[gid]
            component_world=nodes[cid]['world']
            actual_points[cid].extend((world_points-component_world[:3,3])@component_world[:3,:3])
        for cid,expected in expected_components.items():
            actual=np.ptp(actual_points[cid],axis=0).tolist() if actual_points[cid] else None
            structure.append({'node':cid,'check':'geometry_dimensions','expected_m':expected,'actual_m':actual,
                'passed':bool(actual is not None and np.allclose(actual,expected,atol=1e-6,rtol=1e-5)),
                'scope':'导入尺寸对账；容差用于数值转换，不作为设备验收判据'})
        result.update(compiled=True,actual_bodies=m.nbody,actual_joints=m.njnt,actual_geoms=m.ngeom,
                      max_body_position_error_m=max(errors,default=0),finite=bool(np.isfinite(d.xpos).all()),
                      structural_checks=structure,structural_checks_passed=all(s['passed'] for s in structure))
    except (ValueError, mujoco.FatalError) as exc:
        result['compile_error']=str(exc)
    write_json(output/'assembly_checks.json', result)
    write_json(output/'mesh_manifest.json',mesh_manifest)
    write_json(output/'body_mapping.json', {'bodies':{names[k]:{'component_id':n['component_id'],'name':n['name']} for k,n in nodes.items()}})
    return result
