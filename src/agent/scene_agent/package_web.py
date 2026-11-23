"""Read-only workbench projection of imported packages; not a DeviceIR contract."""
from .common import read_json, digest


FILES = ('package_manifest.json', 'assembly_checks.json', 'data_destinations.json',
         'logic_models.json', 'body_mapping.json', 'mesh_manifest.json', 'scene.xml',
         'resource_resolution.json', 'mechanical_definitions.json')


def state(record, folder, occupied, preview=None):
    root = folder/record['scene_kind']
    path = root/'package_manifest.json'
    report = read_json(path) if path.is_file() else {}
    assembly = report.get('assembly', {})
    scene_ready = bool(assembly.get('compiled') and assembly.get('actual_geoms', 0) > 0)
    preview_issue = None
    if report and assembly.get('compiled') and not scene_ready:
        preview_issue = {'code':'no_geometry', 'message':
            'AML 已解析，但没有加载到三维几何。单独上传 AML 不会上传它引用的 DAE、XSD 和 LogicXML 文件。'
            '请在左侧“从 AML 资源包导入组件”填写后端主机上原始 AML 的完整路径，保留资源相对目录后重新导入；'
            '若资源包本身缺少模型，请在检查结果中选择导入或临时生成。'}
    components = report.get('components', [])
    ir = None
    if components:
        ir = {'station':components[0]['name'], 'source':report['source'], 'facts_sha256':digest(report),
              'components':[{'id':c['id'], 'name':c['name'], 'type':c['role'].get('RefBaseRoleClassPath', 'AML').split('/')[-1],
                             'parent_id':c['parent_id'], 'parameters':c['attributes'], 'source_matrix':c['local'],
                             'source':{'locator':c['source']}}
                            for c in components],
              'interfaces':[dict(f, type='MechanicalMount' if 'AttachmentInterface' in f['class'] else f['class'].split('/')[-1]) for f in report['interfaces']],
              'connections':report['connections'], 'assemblies':report['connections'], 'joints':[], 'io':[]}
        for c in components:
            for name, matrix in c.get('mechanical_definition',{}).get('interfaces',{}).items():
                ir['interfaces'].append({'id':'mechanical_'+digest([c['id'],name])[:20], 'name':name,
                    'component_id':c['id'], 'type':'MechanicalMount', 'source_matrix':matrix,
                    'attributes':{'definition_sources':c.get('mechanical_sources',[])}, 'parameters':{}})
    checks = [{'name':'MuJoCo compile', 'passed':assembly.get('compiled', False),
               'details':{k:v for k,v in assembly.items() if k not in ('joint_records','attachment_checks','structural_checks')}}]
    if 'structural_checks_passed' in assembly:
        checks.append({'name':'Imported body / joint mapping', 'passed':assembly['structural_checks_passed'],
                       'details':{'count':len(assembly['structural_checks']), 'scope':'仅核对声明到实际 MuJoCo 的结构映射；不代表设备验收通过'}})
    checks.extend({'name':row['name'], 'passed':False, 'status':'not_evaluated', 'details':row} for row in assembly.get('attachment_checks', []))
    connected={l[k] for l in report.get('connections',[]) for k in ('a','b') if k in l}
    from .package_findings import io_binding, findings
    independent_io = io_binding(report, root, preview)
    pending = findings(report, independent_io)
    return {**record, 'occupied':occupied, 'resource_package':True, 'device_ir':ir,
            'source_sha256':report.get('source',{}).get('sha256'), 'candidates':[],
            'reports':{'assembly':{'checks':checks}}, 'scene_ready':scene_ready,
            'resource_preview_issue':preview_issue,
            'can_build':False, 'all_approved':False, 'model_frozen':False, 'geometry_only':True,
            'has_replay':False, 'has_preview':False, 'test_modules':[], 'test_plan':None,
            'plc_acceptance':'not_run', 'pending':pending, 'independent_io':independent_io,
            'package_counts':report.get('counts',{}), 'package_assembly':assembly,
            'resource_resolution':report.get('component_resolution',[]),
            'mechanical_definitions':report.get('mechanical_definition',{}),
            'files':['input.aml']+[record['scene_kind']+'/'+f for f in FILES if (root/f).is_file()],
            'interface_properties':{f['id']:{'connected':f['id'] in connected,'editable':False} for f in (ir['interfaces'] if ir else [])},
            'model_xml_sha256':assembly.get('model_xml_sha256')}


def model(folder, scene_kind='imported_scene'):
    import mujoco
    root = folder/scene_kind
    report = read_json(root/'assembly_checks.json')
    if not report.get('compiled'):
        from .common import SceneError
        raise SceneError('资源场景尚未完成编译', 'resource_import')
    if not report.get('actual_geoms', 0):
        from .common import SceneError
        raise SceneError('AML 已解析，但未加载三维几何；请导入包含关联模型的完整资源包', 'resource_import')
    m = mujoco.MjModel.from_xml_path(str(root/'scene.xml'))
    d = mujoco.MjData(m); mujoco.mj_forward(m,d)
    owners = {name:row['component_id'] for name,row in read_json(root/'body_mapping.json')['bodies'].items()}
    return m,d,owners,{'model_xml_sha256':report['model_xml_sha256'], 'candidate':True}
