"""Independent RobotCell IO experiment: PLC holding registers <-> MuJoCo qpos.

The generated PLCopen harness is the PLC source. Only the test driver writes
staging registers; the bridge reads PLC outputs and publishes model feedback.
This is position preview, not a torque controller or production acceptance.
"""
import argparse
import json
import math
from pathlib import Path
import re
import time
from urllib.parse import urlparse

import mujoco
import numpy as np

from .plc_test import PlcTestTransport, save_harness


DEFAULT_PROFILE = Path(__file__).resolve().parents[3] / 'examples/robotcell/010RB_100.io.json'


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def config_paths(profile=DEFAULT_PROFILE, adapter=None, motion=None):
    profile = Path(profile).resolve()
    base = profile.name.removesuffix('.io.json')
    return (profile, Path(adapter).resolve() if adapter else profile.with_name(base + '.adapter.json'),
            Path(motion).resolve() if motion else profile.with_name(base + '.motion.json'))


def component_id(config):
    ids = {p.get('component_id') for p in config['io_list']}
    if len(ids) != 1 or not all(isinstance(v, str) and v.strip() for v in ids):
        raise ValueError('Expected one device ID shared by all PLC variables')
    return next(iter(ids))


def validate_mapping(config):
    if not isinstance(config.get('io_list'), list) or not config['io_list']:
        raise ValueError('Expected a nonempty PLC variable mapping list')
    component_id(config)
    names, addresses, bindings = set(), set(), {}
    roles = []
    for p in config['io_list']:
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', p['name']) or p['name'].lower() in names:
            raise ValueError('Duplicate/invalid IO name')
        names.add(p['name'].lower())
        if p['type'] != 'INT' or not re.fullmatch(r'%QW\d+', p['address']):
            raise ValueError('Expected INT in OpenPLC holding-register area %QW')
        address = int(p['address'][3:])
        if address in addresses or address == 20 or not 0 <= address < 1024:
            raise ValueError('Duplicate/reserved/out-of-range IO address')
        addresses.add(address)
        role = p['role']
        if role not in ('target', 'actual', 'enable', 'feedback_sequence'):
            raise ValueError('Unknown IO role')
        if p['dir'] != ('output' if role in ('target', 'enable') else 'input'):
            raise ValueError('IO direction is from PLC perspective')
        roles.append(role)
        allowed = {'name', 'role', 'dir', 'type', 'address', 'component_id'}
        if role in ('target', 'actual'):
            allowed.update(('source_file', 'source_id', 'deg_per_count'))
            key = (p['component_id'], p['source_file'], p['source_id'])
            if not all(isinstance(k, str) and k for k in key):
                raise ValueError('Invalid device/source joint')
            scale = p['deg_per_count']
            if type(scale) not in (int, float) or not math.isfinite(scale) or scale <= 0:
                raise ValueError('Invalid angle scale')
            if role in bindings.setdefault(key, {}):
                raise ValueError('Duplicate joint binding')
            bindings[key][role] = p
        if set(p) != allowed:
            raise ValueError('Unexpected/missing PLC mapping fields: ' + p['name'])
    if roles.count('enable') != 1 or roles.count('feedback_sequence') != 1 or not bindings:
        raise ValueError('Expected enable, feedback_sequence and paired joints')
    for pair in bindings.values():
        if set(pair) != {'target', 'actual'}:
            raise ValueError('Each joint needs target and actual IO')
        if pair['target']['deg_per_count'] != pair['actual']['deg_per_count']:
            raise ValueError('Target and feedback encoding must match')
    return config


def validate(config):
    validate_mapping(config)
    adapter, motion = config['adapter'], config['motion']
    if set(adapter) != {'cycle_hz', 'command_timeout_s', 'scene_dir', 'runtime'}:
        raise ValueError('Invalid adapter settings fields')
    if set(motion) != {'max_speed_deg_s'}:
        raise ValueError('Invalid model motion settings fields')
    for values, key in ((adapter, 'cycle_hz'), (adapter, 'command_timeout_s'), (motion, 'max_speed_deg_s')):
        value = values[key]
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError('Invalid ' + key)
    runtime = adapter['runtime']
    if set(runtime) != {'host', 'port', 'web_url'} or not isinstance(runtime['host'], str) or not runtime['host'].strip():
        raise ValueError('Invalid Runtime connection')
    if type(runtime['port']) is not int or not 1 <= runtime['port'] <= 65535:
        raise ValueError('Invalid Runtime port')
    url = urlparse(runtime['web_url'])
    if url.scheme not in ('http', 'https') or not url.hostname:
        raise ValueError('Invalid Runtime Web URL')
    if not isinstance(adapter['scene_dir'], str) or not adapter['scene_dir'].strip():
        raise ValueError('Invalid scene directory')
    return config


def load_config(profile=DEFAULT_PROFILE, adapter=None, motion=None):
    profile, adapter_path, motion_path = config_paths(profile, adapter, motion)
    mapping = read_json(profile)
    if not isinstance(mapping, dict) or set(mapping) != {'io_list'}:
        raise ValueError('IO file must contain only io_list')
    settings = read_json(adapter_path)
    config = validate({'io_list': mapping['io_list'], 'adapter': settings, 'motion': read_json(motion_path)})
    # Relative scene paths belong to the adapter file, independent of the IO file location.
    settings['scene_dir'] = str((adapter_path.parent / settings['scene_dir']).resolve())
    return config


def harness_ir(config):
    validate_mapping(config)
    # Identity describes wiring/encoding only, never connection or motion settings.
    # Zero is the disabled harness startup value, not a model joint initial pose.
    points = [{**p, 'initial': 0, 'range': [0, 1] if p['role'] == 'enable' else
               [0, 32767] if p['role'] == 'feedback_sequence' else [-32768, 32767]}
              for p in config['io_list']]
    return {'source': {'io_list': config['io_list']}, 'io': points}


class BoundModel:
    def __init__(self, config, model, data, owners, records):
        self.config = validate(config)
        self.model, self.data = model, data
        self.channels = {}
        resolved = set()
        for p in config['io_list']:
            if p['role'] not in ('target', 'actual'):
                continue
            b = p
            matches = [r for r in records if not r.get('locked')
                       and r.get('source_file') == b['source_file']
                       and r.get('source_id') == b['source_id']
                       and owners.get(r.get('body'), {}).get('component_id') == b['component_id']]
            if len(matches) != 1:
                raise ValueError('Missing/ambiguous source joint: ' + p['name'])
            record = matches[0]
            bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, record['body'])
            if bid < 0 or model.body_jntnum[bid] != 1:
                raise ValueError('Missing/ambiguous MuJoCo joint: ' + p['name'])
            jid = int(model.body_jntadr[bid])
            if model.jnt_type[jid] != mujoco.mjtJoint.mjJNT_HINGE or not model.jnt_limited[jid]:
                raise ValueError('Expected a limited hinge joint')
            qadr, dadr = int(model.jnt_qposadr[jid]), int(model.jnt_dofadr[jid])
            limits = np.degrees(model.jnt_range[jid])
            counts = [math.ceil(limits[0] / p['deg_per_count'] - 1e-7),
                      math.floor(limits[1] / p['deg_per_count'] + 1e-7)]
            if not -32768 <= counts[0] <= counts[1] <= 32767:
                raise ValueError('Model joint range exceeds signed INT16 encoding')
            if p['role'] == 'target':
                if jid in resolved:
                    raise ValueError('Different bindings resolve to the same joint')
                resolved.add(jid)
            self.channels[p['name']] = {'joint_id': jid, 'jnt_qposadr': qadr, 'jnt_dofadr': dadr,
                'joint_name': mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, jid),
                'body': record['body'], 'range_deg': limits.tolist(), 'range_counts': counts}
        self.targets = [{**p, 'range': self.channels[p['name']]['range_counts']}
                        for p in config['io_list'] if p['role'] == 'target']
        self.feedback = [p for p in config['io_list'] if p['role'] == 'actual']

    @classmethod
    def load(cls, config, folder):
        folder = Path(folder).resolve()
        model = mujoco.MjModel.from_xml_path(str(folder / 'scene.xml'))
        data = mujoco.MjData(model)
        mujoco.mj_kinematics(model, data)
        return cls(config, model, data, read_json(folder / 'body_mapping.json')['bodies'],
                   read_json(folder / 'assembly_checks.json')['joint_records'])

    def hold(self):
        for p in self.targets:
            self.data.qvel[self.channels[p['name']]['jnt_dofadr']] = 0

    def advance(self, values, dt, enabled):
        if not math.isfinite(dt) or not 0 < dt <= 1 / self.config['adapter']['cycle_hz'] * 1.01:
            raise ValueError('Invalid cycle interval')
        self.hold()
        if enabled:
            # Validate the whole command before changing any joint.
            for p in self.targets:
                value = values[p['name']]
                if type(value) is not int or not p['range'][0] <= value <= p['range'][1]:
                    raise ValueError('Target outside joint range: ' + p['name'])
            for p in self.targets:
                c = self.channels[p['name']]
                target = math.radians(values[p['name']] * p['deg_per_count'])
                old = float(self.data.qpos[c['jnt_qposadr']])
                step = math.radians(self.config['motion']['max_speed_deg_s']) * dt
                new = old + min(step, max(-step, target - old))
                self.data.qpos[c['jnt_qposadr']] = new
                self.data.qvel[c['jnt_dofadr']] = (new - old) / dt
        self.data.time += dt
        mujoco.mj_kinematics(self.model, self.data)
        return self.read_feedback()

    def read_feedback(self):
        return {p['name']: round(math.degrees(float(self.data.qpos[self.channels[p['name']]['jnt_qposadr']]))
                                / p['deg_per_count']) for p in self.feedback}


class Bridge:
    """One connection, one model owner; no direct writes to PLC output channels."""
    def __init__(self, config, bound, client, clock=time.monotonic):
        self.config, self.bound, self.clock = config, bound, clock
        self.wire = PlcTestTransport(harness_ir(config), client)
        self.contract = self.wire.contract
        self.enable = next(p for p in config['io_list'] if p['role'] == 'enable')
        self.sequence = next(p for p in config['io_list'] if p['role'] == 'feedback_sequence')
        self.last_sequence, self.last_command, self.feedback_sequence = None, clock(), 0

    def stamp(self):
        return tuple(self.wire.read_word(self.contract[k]) for k in ('request', 'commit', 'ack'))

    def cycle(self):
        try:
            self.wire.verify()
            before = self.stamp()
            values = {p['name']: self.wire.read_point(p) for p in self.config['io_list'] if p['dir'] == 'output'}
            after = self.stamp()
            if before != after or len(set(before)) != 1:
                raise RuntimeError('PLC command is not scan-acknowledged')
            now = self.clock()
            if before[0] != self.last_sequence:
                self.last_sequence, self.last_command = before[0], now
            if now - self.last_command > self.config['adapter']['command_timeout_s']:
                raise TimeoutError('PLC command heartbeat expired; model held')
            enabled = values[self.enable['name']]
            if enabled not in (0, 1):
                raise ValueError('Invalid enable value')
            feedback = self.bound.advance(values, 1 / self.config['adapter']['cycle_hz'], bool(enabled))
            self.feedback_sequence = self.feedback_sequence % 32767 + 1
            feedback[self.sequence['name']] = self.feedback_sequence
            # Sequence is published last. The PLC copies inputs into separate
            # observed slots, allowing tests to prove a PLC scan consumed them.
            for p in self.config['io_list']:
                if p['role'] == 'actual':
                    self.wire.write_word(int(p['address'][3:]), feedback[p['name']])
            self.wire.write_word(int(self.sequence['address'][3:]), self.feedback_sequence)
            return {'command_sequence': before[0], 'outputs': values, 'feedback': feedback,
                    'qpos_rad': {p['name']: float(self.bound.data.qpos[self.bound.channels[p['name']]['jnt_qposadr']])
                                 for p in self.bound.targets}}
        except BaseException:
            self.bound.hold()
            raise


class TestDriver:
    """External test stimuli enter staging; the PLC program owns actual outputs."""
    __test__ = False

    def __init__(self, config, client):
        self.wire = PlcTestTransport(harness_ir(config), client)
        self.values = dict(self.wire.outputs)

    def command(self, updates, timeout=3):
        self.wire.verify()
        for name, value in updates.items():
            p = self.wire.points[name]
            if p['dir'] != 'output' or type(value) is not int or not p['range'][0] <= value <= p['range'][1]:
                raise ValueError('Invalid test command')
        self.values.update(updates)
        seq = self.wire.read_word(self.wire.contract['ack']) % 65535 + 1
        self.wire.write_word(self.wire.contract['request'], seq)
        for name, value in self.values.items():
            self.wire.write_word(self.wire.contract['staging'][name], value)
        self.wire.write_word(self.wire.contract['commit'], seq)
        self.wait(lambda: self.wire.read_word(self.wire.contract['ack']) == seq, timeout)

    @staticmethod
    def wait(predicate, timeout):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                raise TimeoutError('PLC scan acknowledgement timed out')
            time.sleep(.002)

    def observed(self, expected, timeout=3):
        def matches():
            return all(self.wire.read_word(self.wire.contract['observed'][name]) == value & 65535
                       for name, value in expected.items())
        self.wait(matches, timeout)
        return dict(expected)


def exercise(config, bound, bridge, driver):
    initial = {p['name']: round(math.degrees(bound.data.qpos[bound.channels[p['name']]['jnt_qposadr']])
                               / p['deg_per_count']) for p in bound.targets}
    baseline = bound.data.qpos.copy()
    selected = {c['jnt_qposadr'] for c in bound.channels.values()}
    untouched = [i for i in range(bound.model.nq) if i not in selected]
    events = []
    def sample(label):
        started = time.monotonic()
        event = bridge.cycle()
        event['plc_observed_inputs'] = driver.observed(event['feedback'])
        event['case'] = label
        if not np.array_equal(bound.data.qpos[untouched], baseline[untouched]):
            raise AssertionError('Unbound joint moved')
        events.append(event)
        time.sleep(max(0, 1 / config['adapter']['cycle_hz'] - (time.monotonic() - started)))
        return event
    try:
        driver.command({**initial, bridge.enable['name']: 0})
        sample('disabled')
        if not np.array_equal(bound.data.qpos, baseline):
            raise AssertionError('Disabled model moved')
        for direction in (1, -1, 0):
            targets = {p['name']: max(p['range'][0], min(p['range'][1], initial[p['name']]
                       + direction * (1 if i % 2 else -1) * round(3 / p['deg_per_count'])))
                       for i, p in enumerate(bound.targets)}
            driver.command({**targets, bridge.enable['name']: 1})
            start = heartbeat = time.monotonic()
            while True:
                if time.monotonic() - heartbeat >= config['adapter']['command_timeout_s'] / 3:
                    driver.command({})
                    heartbeat = time.monotonic()
                event = sample('move_' + str(direction))
                if event['outputs'] != {**targets, bridge.enable['name']: 1}:
                    raise AssertionError('PLC output differs from staging command')
                if all(abs(event['qpos_rad'][p['name']] - math.radians(targets[p['name']] * p['deg_per_count'])) < 1e-9
                       for p in bound.targets):
                    break
                if time.monotonic() - start > 10:
                    raise TimeoutError('Joint failed to reach target')
        return {'status': 'passed', 'mode': 'kinematic_position_io', 'channels': bound.channels,
                'events': events, 'unbound_qpos_unchanged': True}
    finally:
        bound.hold()
        driver.command({bridge.enable['name']: 0})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('export', 'deploy', 'inspect', 'run', 'test'))
    parser.add_argument('--config', default=str(DEFAULT_PROFILE))
    parser.add_argument('--adapter', help='Adapter settings JSON; defaults to adjacent .adapter.json')
    parser.add_argument('--motion', help='Model motion settings JSON; defaults to adjacent .motion.json')
    parser.add_argument('--scene-dir')
    parser.add_argument('--out', default='workspace/2026-11-17-robotcell-modbus')
    parser.add_argument('--host')
    parser.add_argument('--port', type=int)
    parser.add_argument('--web-url')
    parser.add_argument('--seconds', type=float, default=30)
    args = parser.parse_args()
    config = load_config(args.config, args.adapter, args.motion)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.action in ('export', 'deploy'):
        contract = save_harness(harness_ir(config), out)
        if args.action == 'deploy':
            from src.pipeline.xml2st import convert
            from src.pipeline.openplc_client import OpenPLCClient
            ok, st, problems = convert(out / 'io_test.xml')
            if not ok:
                raise ValueError(str(problems))
            url = args.web_url or config['adapter']['runtime']['web_url']
            plc = OpenPLCClient(url)
            result = {'status': 'failed', 'url': url, 'identity': contract['identity_sha256']}
            try:
                plc.login()
                status = plc.status()
                if status == 'RUNNING':
                    status = plc.stop()
                if status != 'STOPPED':
                    raise RuntimeError('Runtime not stopped: ' + status)
                result['program'] = plc.upload_and_compile(st, 'robotcell_rb100_io')
                ok, log = plc.wait_compilation()
                (out / 'compilation.log').write_text(log, encoding='utf-8')
                if not ok:
                    raise RuntimeError('matiec compilation failed; see compilation.log')
                result['runtime_status'] = plc.start()
                if result['runtime_status'] != 'RUNNING':
                    raise RuntimeError('Runtime did not start')
                result['status'] = 'deployed'
            except Exception as exc:
                result['error'] = str(exc)
                raise
            finally:
                write_json(out / 'deployment.json', result)
                plc.session.close()
        print(json.dumps({'xml': str(out / 'io_test.xml'), 'identity': contract['identity_sha256']}))
        return
    folder = args.scene_dir or config['adapter']['scene_dir']
    print('Loading MuJoCo scene...', flush=True)
    bound = BoundModel.load(config, folder)
    write_json(out / 'resolved_bindings.json', bound.channels)
    if args.action == 'inspect':
        print(json.dumps(bound.channels, indent=2))
        return
    from pymodbus.client import ModbusTcpClient
    clients = []
    def client():
        c = ModbusTcpClient(args.host or config['adapter']['runtime']['host'],
                           port=args.port or config['adapter']['runtime']['port'], timeout=1, retries=0)
        clients.append(c)
        if not c.connect():
            raise ConnectionError('OpenPLC Modbus endpoint unavailable')
        return c
    try:
        connection = client()
        bridge = Bridge(config, bound, connection)
        if args.action == 'test':
            result = exercise(config, bound, bridge, TestDriver(config, connection))
        else:
            deadline = time.monotonic() + args.seconds
            events = []
            while time.monotonic() < deadline:
                start = time.monotonic()
                events.append(bridge.cycle())
                time.sleep(max(0, 1 / config['adapter']['cycle_hz'] - (time.monotonic() - start)))
            result = {'status': 'completed', 'events': events}
        write_json(out / (args.action + '_report.json'), result)
        if args.action == 'test':
            from .binding_registry import certify
            certify(args.config, config, out/'test_report.json')
        print(json.dumps({'status': result['status'], 'cycles': len(result['events'])}))
    except Exception as exc:
        write_json(out / (args.action + '_report.json'), {'status': 'failed', 'error': str(exc)})
        raise
    finally:
        bound.hold()
        for c in clients:
            c.close()


if __name__ == '__main__':
    main()
