"""AML simulation requirements. Facts and oracles never come from test scripts."""
import math

from .common import SceneError

FORMAT = '308sjk.simulation_requirements/1'
KINDS = {'mechanical_joint', 'fixed_mount', 'io_binding', 'joint_coupling'}


def parse_requirements(hierarchy, attributes, pending):
    records = attributes(hierarchy, 'InstanceHierarchy', pending)
    record = records.get('SimulationRequirements')
    if record is None:
        return None
    def unpack(rec):
        return {k: unpack(v) if v['children'] else v['value'] for k, v in rec['children'].items()}
    result = unpack(record)
    if not isinstance(result.get('checks'),dict) or not isinstance(result.get('allowed_simplifications'),dict):
        raise SceneError('SimulationRequirements checks/allowed_simplifications must be CAEX attribute groups','device_description')
    if any(not isinstance(value,dict) for value in result['checks'].values()):
        raise SceneError('SimulationRequirements checks must contain kind/target/description','device_description')
    result['checks'] = [{'id': key, **value} for key, value in result.get('checks', {}).items()]
    result['allowed_simplifications'] = list(result.get('allowed_simplifications', {}).values())
    result['source'] = {'locator': 'InstanceHierarchy/SimulationRequirements', 'kind': 'aml'}
    return result


def validate_requirements(ir):
    req = ir.get('simulation_requirements')
    if req is None:
        return
    if not isinstance(req, dict) or req.get('format') != FORMAT:
        raise SceneError('Unsupported SimulationRequirements format', 'device_description')
    allowed = {'format', 'description', 'position_tolerance_m', 'angle_tolerance_rad',
               'response_timeout_s', 'allowed_simplifications', 'checks', 'source'}
    if set(req) - allowed:
        raise SceneError('Unknown SimulationRequirements fields: '+str(sorted(set(req)-allowed)), 'device_description')
    for name, upper in [('position_tolerance_m', .05), ('angle_tolerance_rad', .1), ('response_timeout_s', 30)]:
        value = req.get(name)
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= upper:
            raise SceneError('Invalid simulation requirement '+name, 'device_description')
    if not isinstance(req.get('description'), str) or not req['description'].strip():
        raise SceneError('SimulationRequirements description required', 'device_description')
    if not isinstance(req.get('allowed_simplifications'), list) or any(not isinstance(v, str) for v in req['allowed_simplifications']):
        raise SceneError('Invalid allowed_simplifications', 'device_description')
    targets = {'mechanical_joint': {j['id'] for j in ir['joints']},
               'fixed_mount': {a['id'] for a in ir.get('assemblies', [])},
               'io_binding': {p['name'] for p in ir['io']},
               'joint_coupling': {c['id'] for c in ir.get('couplings', [])}}
    checks = req.get('checks')
    if not isinstance(checks, list) or not checks or len(checks) > 512:
        raise SceneError('SimulationRequirements needs 1..512 checks', 'device_description')
    seen = set()
    for check in checks:
        if (not isinstance(check, dict) or set(check) != {'id', 'kind', 'target', 'description'}
                or not isinstance(check['id'], str) or not check['id'] or check['id'] in seen
                or check['kind'] not in KINDS or (check['target']!='*' and check['target'] not in targets[check['kind']])
                or not isinstance(check['description'], str) or not check['description'].strip()):
            raise SceneError('Invalid simulation check/reference: '+str(check), 'device_description')
        seen.add(check['id'])


def validate_couplings(ir):
    joints = {j['id']: j for j in ir['joints']}
    followers = set()
    for c in ir.get('couplings', []):
        if c['leader'] not in joints or c['follower'] not in joints or c['leader'] == c['follower'] or c['follower'] in followers:
            raise SceneError('Invalid/duplicate joint coupling', 'assembly')
        followers.add(c['follower'])
        if any(joints[k]['type'] not in ('slide', 'hinge') for k in (c['leader'], c['follower'])):
            raise SceneError('Coupling requires scalar joints', 'assembly')
        if any(type(c[k]) not in (int, float) or not math.isfinite(c[k]) for k in ('ratio', 'offset')) or c['ratio'] == 0:
            raise SceneError('Invalid coupling ratio/offset', 'assembly')
        leader, follower = joints[c['leader']], joints[c['follower']]
        if abs(follower['initial']-(c['offset']+c['ratio']*leader['initial'])) > 1e-9:
            raise SceneError('Coupling initial states disagree', 'assembly')
        for q in leader['range']:
            if not follower['range'][0] <= c['offset']+c['ratio']*q <= follower['range'][1]:
                raise SceneError('Coupling exceeds follower range', 'assembly')
    graph = {c['follower']: c['leader'] for c in ir.get('couplings', [])}
    for node in graph:
        seen = set()
        while node in graph:
            if node in seen:
                raise SceneError('Cyclic joint coupling', 'assembly')
            seen.add(node); node = graph[node]
