"""Presentation of import findings and current, local IO binding evidence."""
from collections import Counter

from .common import digest, read_json


# These describe evidence gaps, not automatic repair decisions or acceptance.
DESCRIPTIONS = {
    'attachment_requires_constraint_review': ('安装约束待确认', '连接涉及闭环或多点安装，当前保留源位姿和连接记录。', '补充固定连接或运动副的约束定义。'),
    'mechanical_definition_missing': ('组件机械关系未声明完整', '已有几何不等于已确认固定或运动关系；此数量不是缺失模型数。', '为受影响组件明确安装接口及固定关系或运动副。'),
    'needs_classification': ('组件用途待确认', '尚不能确定是结构／参考节点，还是需要补模型的实体。', '在“组件资源补全”中逐项确认用途。'),
    'missing_geometry': ('实体组件缺少三维模型', '已识别为实体，但未得到可用几何。', '在“组件资源补全”中导入模型，或提供临时生成所需声明。'),
    'attachment_frame_unspecified': ('安装坐标系待补充', '连接端点缺少明确安装坐标系，当前以组件原点代替；偏差不能直接判为装配错误。', '补充安装点的位置、方向和接口偏移。'),
    'connection_type_unspecified': ('连接类型待确认', '源连接未明确固定关系或运动副类型。', '明确连接类型及所需约束。'),
    'joint_limits_or_coupling_missing': ('关节限位或耦合关系待补充', '相关源关节暂按固定关系用于预览，不作为可控运动副。', '补充关节限位或耦合关系后重新检查。'),
    'unsupported_source_joint': ('源运动副尚未支持', '源文件包含当前导入器未完整支持的运动副声明。', '核对原始声明并补充对应的解析或机构定义。'),
    'logic_resource_missing': ('行为资源缺失', '引用的行为资源未完整解析，不能据此执行整机行为。', '补充引用的行为文件或依赖。'),
    'verification_requirements_missing': ('整场景验收要求未定义', '当前没有完整的检查目标、容差和响应超时，不能判定整场景验收通过。', '在验收设置中定义检查目标与通过条件。'),
    'collision_geometry_unreviewed': ('碰撞与动力学尚未验收', '可见外观网格尚未完成碰撞用途审查。', '确认碰撞体、接触排除规则及动力学参数，并执行验证。'),
    'runtime_behavior_mapping_missing': ('整机行为尚未接入执行', '导入的 PLCopen 行为已保留，尚未完成到运行内核的映射。', '补充整机行为映射与运动执行策略。'),
}


def io_binding(report, root, preview=None):
    """No network requests, model loading, motion, or historical pass claims."""
    from .plc_preview import load_profile
    from .robotcell_io import component_id

    result = {'status': 'unavailable', 'scope': 'component', 'scene_acceptance': 'not_run',
              'communication': 'not_checked', 'components': []}
    config = load_profile()
    if config is None:
        return {**result, 'message': '未读取到有效的独立 IO 配置及配套设置。'}
    cid = component_id(config)
    component = next((c for c in report.get('components', []) if c['id'] == cid), None)
    if component is None:
        return {**result, 'status': 'not_applicable', 'message': '现有独立 IO 配置未指向当前场景的组件。'}
    targets = [p for p in config['io_list'] if p['role'] == 'target']
    result['components'] = [{'id': cid, 'name': component['name'], 'joint_count': len(targets),
                             'variable_count': len(config['io_list'])}]
    try:
        owners = read_json(root/'body_mapping.json')['bodies']
        records = report.get('assembly', {}).get('joint_records', [])
        for point in targets:
            matches = [r for r in records if not r.get('locked')
                       and r.get('source_file') == point['source_file']
                       and r.get('source_id') == point['source_id']
                       and owners.get(r.get('body'), {}).get('component_id') == cid]
            if len(matches) != 1:
                raise ValueError('unresolved source joint')
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {**result, 'status': 'mismatch', 'message': '独立 IO 配置与当前场景的源关节不匹配，请检查绑定。'}
    if preview is None:
        return {**result, 'status': 'configured', 'message': '独立 IO 配置已匹配源关节；加载场景后核对实际模型绑定。'}
    plc = preview.plc
    if (plc is None or digest(plc.config) != digest(config)
            or not plc.available([cid], preview.selected([cid]))):
        return {**result, 'status': 'mismatch', 'message': '当前 MuJoCo 模型未通过独立 IO 绑定检查。'}
    return {**result, 'status': 'bound', 'message': '独立 IO 已绑定到当前 MuJoCo 模型，可对该组件进行 PLC 手动控制。'}


def findings(report, io):
    components = {c['id']: c for c in report.get('components', [])}
    connections = {c['id']: c for c in report.get('connections', [])}
    issues = report.get('issues', [])
    rows = [{'code': code, 'count': count, 'examples': [i for i in issues if i['code'] == code][:2],
             'details': '完整明细见 package_manifest.json'} for code, count in Counter(i['code'] for i in issues).items()]
    rows.extend(report.get('gaps', []))
    output = []
    for raw in rows:
        code = raw['code']
        if code == 'io_contract_missing':
            title = '整场景 IO 验收尚未完成'
            message = ('已识别组件级独立 IO 配置，其覆盖范围见上方绑定状态。'
                       if io['status'] in ('configured', 'bound') else '尚无可确认的当前模型独立 IO 绑定。')
            description = (title, message + '组件绑定与整场景验收分别记录。',
                           'IO 文件只保存变量映射；连接、周期和指令超时在适配器设置中，初态、限位与速度来自模型及运动配置，响应阈值来自验收设置。')
        else:
            description = DESCRIPTIONS.get(code, ('其他导入待确认项 · ' + code,
                                                 '此项尚无专用说明，请查看原始记录。', '核对原始记录中的来源和缺项。'))
        examples = []
        for item in raw.get('examples', []):
            source = item.get('source', '')
            entity = components.get(source) or connections.get(source) or {}
            examples.append({'name': entity.get('name', source), 'path': entity.get('source', ''),
                             'detail': item.get('detail', '')})
        output.append({**raw, 'title': description[0], 'message': description[1], 'action': description[2],
                       'affected_examples': examples, 'raw': raw})
    return output
