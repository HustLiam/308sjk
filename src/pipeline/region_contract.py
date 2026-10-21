"""Explicit numeric feedback bindings and satisfiability of terminal regions."""
import math
import re


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def explicit_region_targets(criterion):
    """Return ordered (signal, target, tolerance); None denotes the legacy format."""
    if 'signals' not in criterion:
        return None
    signals, center, tolerance = (criterion.get(k) for k in ('signals', 'region_center', 'tolerance'))
    if (not isinstance(signals, list) or not 1 <= len(signals) <= 64
            or any(not isinstance(n, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', n) for n in signals)
            or len(set(signals)) != len(signals)):
        raise ValueError('signals须为1~64个不同的有效反馈信号名')
    if not isinstance(center, (list, tuple)) or len(center) != len(signals) or not all(map(_number, center)):
        raise ValueError('region_center须与signals逐项对应，长度相等且为有限数值')
    if not _number(tolerance) or tolerance <= 0:
        raise ValueError('tolerance须为有限正数')
    return list(zip(signals, center, [tolerance] * len(signals)))


def terminal_region_problems(spec):
    """Only reject proven contradictions; never infer coordinates from asset labels."""
    def array(key):
        value = spec.get(key)
        return value if isinstance(value, list) else []
    points = {p.get('name'): p for p in array('io_list') if isinstance(p, dict) and isinstance(p.get('name'), str)}
    axes = {a.get('axis') for a in array('axes') if isinstance(a, dict) and isinstance(a.get('axis'), str)}
    problems, domains, sources = [], {}, {}
    for criterion in array('acceptance'):
        if not isinstance(criterion, dict) or criterion.get('type') != 'region_containment':
            continue
        label = 'acceptance[%s]' % criterion.get('id', '?')
        try:
            targets = explicit_region_targets(criterion)
        except ValueError as exc:
            problems.append('%s: %s' % (label, exc))
            continue
        if targets is None:
            if isinstance(criterion.get('asset'), str) and criterion['asset'] in axes:
                problems.append('%s: 单轴asset不选择数值分量；须用signals显式引用反馈，不能给其他轴补0' % label)
            continue
        for signal, target, tolerance in targets:
            point = points.get(signal)
            if not point or point.get('dir') != 'input' or point.get('type') != 'INT':
                problems.append('%s.signals: %s须为已声明的input INT反馈，不能引用命令输出或BOOL' % (label, signal))
                continue
            limits = point.get('range')
            if not isinstance(limits, (list, tuple)) or len(limits) != 2 or not all(map(_number, limits)):
                continue  # The IO validator reports malformed ranges.
            old = domains.get(signal, limits)
            low, high = max(old[0], target-tolerance), min(old[1], target+tolerance)
            if low > high or math.ceil(low) > math.floor(high):
                problems.append('%s: %s终态区域与%s或量程无共同INT值，不可通过重写PLC满足' %
                                (label, signal, sources.get(signal, '设备')))
            else:
                domains[signal] = low, high
                sources[signal] = label
    return problems
