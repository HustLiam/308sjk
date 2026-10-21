"""Check declared terminal conditions without assuming device or signal names."""


class CompletionConflict(ValueError):
    pass


def validate_completion_contract(plan, spec):
    """Conservative interval proof; unknown relationships remain runtime checks.

    Only explicitly terminal path checks constrain the final snapshot. Intermediate
    event endpoints are tested against trace history, never forced to remain true.
    """
    points = {p['name']: p for p in spec['io_list']}
    written = set(plan['initial_inputs'])
    for action in plan['actions']:
        written.update(action.get('values', {}))
        if action.get('op') == 'pulse':
            written.add(action['signal'])
    complete = plan['completion']['all']
    if not any(points[c['signal']]['dir'] == 'output' or c['signal'] not in written for c in complete):
        raise CompletionConflict('完成条件必须观察输出或未被计划写入的反馈，不能只检查控制输入')
    domains = {}

    def constrain(name, low, high, source):
        point = points[name]
        initial = (0, 1) if point['type'] == 'BOOL' else tuple(point['range'])
        old = domains.get(name, initial)
        bounds = max(old[0], low), min(old[1], high)
        if bounds[0] > bounds[1]:
            raise CompletionConflict('终态条件冲突：%s 的 %s 与已有范围 %s 不相容' % (name, source, old))
        domains[name] = bounds

    for condition in complete:
        name = condition['signal']
        target = condition.get('equals', condition.get('target'))
        tolerance = condition.get('tolerance', 0)
        constrain(name, target - tolerance, target + tolerance, 'completion')
    from src.pipeline.region_contract import explicit_region_targets
    for criterion in spec.get('acceptance', []):
        if criterion.get('type') == 'region_containment':
            for name, target, tol in explicit_region_targets(criterion) or []:
                constrain(name, target-tol, target+tol, criterion['id'] + ' 终态区域')
    for check in plan['path_checks']:
        if check.get('terminal') is True:
            end = check['points'][-1]
            for name, value in end['values'].items():
                tol = end['tolerance'][name]
                constrain(name, value - tol, value + tol, check['id'] + ' 最终路点')
            for name, value in end['states'].items():
                constrain(name, value, value, check['id'] + ' 最终工艺状态')

    def forced(ref):
        name = ref['signal']
        point = points[name]
        low, high = domains.get(name, (0, 1) if point['type'] == 'BOOL' else tuple(point['range']))
        if 'equals' in ref:
            target, tolerance = ref['equals'], ref.get('tolerance', 0)
            return target - tolerance <= low <= high <= target + tolerance
        target = ref['value']
        return {'<': high < target, '<=': high <= target,
                '>': low > target, '>=': low >= target}[ref['op']]

    for criterion in spec.get('acceptance', []):
        if criterion['type'] == 'forbidden_state' and forced(criterion['when']) and forced(criterion['forbid']):
            raise CompletionConflict('终态条件必然违反验收 %s；修正测试计划，不通过重写PLC绕过' % criterion['id'])
