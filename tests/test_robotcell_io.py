"""Binding/encoding/ownership tests; ScanClient is explicitly not a real PLC."""
from copy import deepcopy
from contextlib import contextmanager
import math
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import mujoco
import numpy as np
import pytest

from src.agent.scene_agent.robotcell_io import (
    BoundModel, Bridge, TestDriver, exercise, harness_ir, read_json, validate, load_config, component_id, config_paths,
)
from src.agent.scene_agent.plc_test import harness


PROFILE = Path(__file__).resolve().parents[1] / 'examples/robotcell/010RB_100.io.json'


class ScanClient:
    """Deterministic model of the generated ST, used only for offline tests."""
    def __init__(self, config):
        self.config = config
        self.contract, _ = harness(harness_ir(config))
        self.registers = {}
        self.writes = []
        self.failed = False
        self.scan()

    def scan(self):
        c = self.contract
        for i, address in enumerate(c['identity']):
            self.registers[address] = int(c['identity_sha256'][i * 4:i * 4 + 4], 16)
        if self.registers.get(c['request'], 0) == self.registers.get(c['commit'], 0):
            for p in self.config['io_list']:
                address = int(p['address'][3:])
                if p['dir'] == 'output':
                    self.registers[address] = self.registers.get(c['staging'][p['name']], 0)
                else:
                    self.registers[c['observed'][p['name']]] = self.registers.get(address, 0)
            self.registers[c['ack']] = self.registers.get(c['request'], 0)

    def read_holding_registers(self, *, address, count):
        return SimpleNamespace(registers=[self.registers.get(a, 0) for a in range(address, address + count)],
                               isError=lambda: self.failed)

    def write_register(self, *, address, value):
        self.writes.append((address, value))
        if not self.failed:
            self.registers[address] = value
            self.scan()
        return SimpleNamespace(isError=lambda: self.failed)

    def close(self):
        pass


@contextmanager
def scan_tcp(config):
    """Real localhost Modbus TCP, with a scan substitute behind the server."""
    import asyncio
    import threading
    from pymodbus.client import ModbusTcpClient
    from pymodbus.datastore import ModbusSlaveContext, ModbusServerContext, ModbusSequentialDataBlock
    from pymodbus.server import ModbusTcpServer

    scan = ScanClient(config)
    class Store(ModbusSlaveContext):
        def getValues(self, fc_as_hex, address, count=1):
            return [scan.registers.get(a, 0) for a in range(address, address + count)]

        def setValues(self, fc_as_hex, address, values):
            for i, value in enumerate(values):
                scan.write_register(address=address + i, value=value)

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    clients = []
    server = None
    async def start():
        context = ModbusServerContext(slaves=Store(hr=ModbusSequentialDataBlock(0, [0] * 2048)), single=True)
        s = ModbusTcpServer(context, address=('127.0.0.1', 0))
        await s.serve_forever(background=True)
        return s
    try:
        server = asyncio.run_coroutine_threadsafe(start(), loop).result(timeout=5)
        port = server.transport.sockets[0].getsockname()[1]
        def connect():
            c = ModbusTcpClient('127.0.0.1', port=port, timeout=1, retries=0)
            clients.append(c)
            assert c.connect()
            return c
        yield connect, scan
    finally:
        for c in clients:
            c.close()
        if server:
            asyncio.run_coroutine_threadsafe(server.shutdown(), loop).result(timeout=5)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()


def small_model(config, prefix=False):
    targets = [p for p in config['io_list'] if p['role'] == 'target']
    bodies = ['<body name="extra"><freejoint/><geom size=".1"/></body>'] if prefix else []
    records, owners = [], {}
    for i, p in enumerate(targets):
        name = 'axis' + str(i)
        lo, hi = [(-185, 185), (-140, -5), (-120, 155), (-350, 350), (-122.5, 122.5), (-350, 350)][i]
        initial = [17.58, -134.38, 103.60, 77.84, -66.74, 96.68][i]
        bodies.append(f'<body name="{name}"><joint name="j{i}" type="hinge" range="{lo} {hi}" ref="{initial}"/><geom size=".1"/></body>')
        records.append({**p, 'body': name, 'locked': False})
        owners[name] = {'component_id': component_id(config)}
    model = mujoco.MjModel.from_xml_string('<mujoco><worldbody>' + ''.join(bodies) + '</worldbody></mujoco>')
    return BoundModel(config, model, mujoco.MjData(model), owners, records)


@pytest.fixture
def config():
    return load_config(PROFILE)


def test_rebuild_rebinds_qpos_instead_of_storing_offsets(config):
    original, rebuilt = small_model(config), small_model(config, prefix=True)
    for name, row in original.channels.items():
        assert rebuilt.channels[name]['jnt_qposadr'] == row['jnt_qposadr'] + 7
        assert rebuilt.channels[name]['joint_id'] == row['joint_id'] + 1
    assert 'jnt_qposadr' not in PROFILE.read_text()


@pytest.mark.parametrize('mutation', ['address', 'direction', 'scale', 'duplicate', 'component', 'pair', 'enable'])
def test_invalid_profiles_fail(config, mutation):
    p = config['io_list'][0]
    if mutation == 'address': p['address'] = config['io_list'][1]['address']
    if mutation == 'direction': p['dir'] = 'input'
    if mutation == 'scale': p['deg_per_count'] = float('nan')
    if mutation == 'duplicate': config['io_list'][1].update({k:p[k] for k in ('component_id', 'source_file', 'source_id')})
    if mutation == 'component': p['component_id'] = 'other'
    if mutation == 'pair': config['io_list'].pop(0)
    if mutation == 'enable': config['io_list'][-2]['role'] = 'feedback_sequence'
    with pytest.raises(ValueError): validate(config)


def test_missing_ambiguous_source_and_limits_fail(config):
    bound = small_model(config)
    p = config['io_list'][0]
    record = {**p, 'body': 'axis0', 'locked': False}
    owners = {'axis0': {'component_id': component_id(config)}}
    for records in ([], [record, record]):
        with pytest.raises(ValueError, match='source joint'):
            BoundModel(config, bound.model, bound.data, owners, records)
    for p in config['io_list']:
        if p['role'] in ('target', 'actual'): p['deg_per_count'] = .001
    with pytest.raises(ValueError, match='range exceeds'):
        BoundModel(config, bound.model, bound.data, owners, [record])


def test_identity_includes_bindings_and_scale(config):
    before, _ = harness(harness_ir(config))
    changed = deepcopy(config)
    changed['io_list'][0]['source_id'] += '_changed'
    changed['io_list'][6]['source_id'] += '_changed'
    after, _ = harness(harness_ir(changed))
    assert before['identity_sha256'] != after['identity_sha256']


def test_bidirectional_io_feedback_comes_from_qpos(config):
    bound = small_model(config, prefix=True)
    client = ScanClient(config)
    bridge, driver = Bridge(config, bound, client), TestDriver(config, client)
    result = exercise(config, bound, bridge, driver)
    assert result['status'] == 'passed'
    assert result['unbound_qpos_unchanged']
    # Finite speed: the first feedback cannot be an echo of the target.
    event = next(e for e in result['events'] if e['case'] == 'move_1')
    assert event['outputs']['rb100_j1_target'] != event['feedback']['rb100_j1_actual']
    assert event['feedback']['rb100_j2_actual'] < 0
    assert event['plc_observed_inputs'] == event['feedback']
    assert client.registers[106] == 0


def test_bridge_never_writes_plc_outputs_or_staging(config):
    bound, client = small_model(config), ScanClient(config)
    bridge, driver = Bridge(config, bound, client), TestDriver(config, client)
    driver.command({})
    client.writes.clear()
    bridge.cycle()
    assert {address for address, _ in client.writes} == {110, 111, 112, 113, 114, 115, 116}


def test_actual_modbus_tcp_frames_bidirectional(config):
    bound = small_model(config)
    with scan_tcp(config) as (connect, _):
        bridge = Bridge(config, bound, connect())
        driver = TestDriver(config, connect())
        result = exercise(config, bound, bridge, driver)
    assert result['status'] == 'passed'
    assert result['events'][-1]['feedback']['rb100_j2_actual'] < 0


def test_faults_hold_model_and_wrong_identity_does_not_write(config):
    bound, client = small_model(config), ScanClient(config)
    now = [0.]
    bridge = Bridge(config, bound, client, clock=lambda: now[0])
    initial = {p['name']: round(math.degrees(bound.data.qpos[bound.channels[p['name']]['jnt_qposadr']]) / p['deg_per_count']) for p in bound.targets}
    TestDriver(config, client).command({**initial, 'rb100_enable': 1})
    bridge.cycle()
    baseline = bound.data.qpos.copy()
    now[0] = 3
    with pytest.raises(TimeoutError): bridge.cycle()
    assert np.array_equal(bound.data.qpos, baseline)
    assert np.all(bound.data.qvel == 0)
    client.registers[bridge.contract['identity'][0]] ^= 1
    client.writes.clear()
    with pytest.raises(Exception, match='OpenPLC'): bridge.cycle()
    assert not client.writes
    client.failed = True
    with pytest.raises(Exception): bridge.cycle()
    assert np.array_equal(bound.data.qpos, baseline)


def test_partial_command_and_out_of_range_never_move(config):
    bound, client = small_model(config), ScanClient(config)
    bridge = Bridge(config, bound, client)
    baseline = bound.data.qpos.copy()
    client.registers[bridge.contract['request']] = 1
    with pytest.raises(RuntimeError, match='scan-acknowledged'): bridge.cycle()
    client.scan()
    client.registers[bridge.contract['commit']] = 1
    client.scan()
    client.registers[106] = 1
    client.registers[100] = 32767
    with pytest.raises(ValueError, match='range'): bridge.cycle()
    assert np.array_equal(bound.data.qpos, baseline)


def test_signed_encoding_covers_350_degrees(config):
    bound = small_model(config)
    p = next(p for p in bound.targets if p['name'] == 'rb100_j4_target')
    assert p['range'] == [-17500, 17500]
    assert math.isclose(p['range'][1] * p['deg_per_count'], 350)


def test_mapping_identity_ignores_adapter_and_motion_settings(config):
    original, _ = harness(harness_ir(config))
    raw, _ = harness(harness_ir(read_json(PROFILE)))
    assert original['identity_sha256'] == raw['identity_sha256']
    changed = deepcopy(config)
    changed['adapter'].update(cycle_hz=15, command_timeout_s=4, scene_dir='another/model')
    changed['adapter']['runtime']['host'] = '127.0.0.1'
    changed['motion']['max_speed_deg_s'] = 10
    after, _ = harness(harness_ir(changed))
    assert after['identity_sha256'] == original['identity_sha256']
    # Encoding is part of the mapping and must still invalidate the PLC identity.
    for p in changed['io_list']:
        if p['role'] in ('target', 'actual'):
            p['deg_per_count'] = .04
    assert harness(harness_ir(changed))[0]['identity_sha256'] != original['identity_sha256']


def test_model_supplies_limits_and_motion_settings_supply_speed(config):
    slow = deepcopy(config)
    slow['motion']['max_speed_deg_s'] = 3
    bound = small_model(slow)
    values = {p['name']: round(math.degrees(bound.data.qpos[bound.channels[p['name']]['jnt_qposadr']])
                              / p['deg_per_count']) for p in bound.targets}
    first = bound.targets[0]
    adr = bound.channels[first['name']]['jnt_qposadr']
    start = bound.data.qpos[adr]
    values[first['name']] += 150
    bound.advance(values, 1 / slow['adapter']['cycle_hz'], True)
    assert math.degrees(bound.data.qpos[adr] - start) == pytest.approx(.1)
    # Rebinding a model with a different limit uses the compiled range, not IO JSON.
    bound.model.jnt_range[0] = np.radians([10, 20])
    records = [{**p, 'body': 'axis'+str(i), 'locked': False} for i, p in enumerate(config['io_list'][:6])]
    owners = {'axis'+str(i): {'component_id': component_id(config)} for i in range(6)}
    rebound = BoundModel(slow, bound.model, bound.data, owners, records)
    assert rebound.targets[0]['range'] == [500, 1000]
    before = rebound.data.qpos.copy()
    values[first['name']] = 1050
    with pytest.raises(ValueError, match='range'):
        rebound.advance(values, 1 / slow['adapter']['cycle_hz'], True)
    assert np.array_equal(rebound.data.qpos, before)


def test_separate_files_resolve_paths_and_invalidate_preview(tmp_path, monkeypatch):
    from src.agent.scene_agent import plc_preview
    from src.agent.scene_agent.robotcell_io import write_json
    io = tmp_path / 'mapping' / 'robot.io.json'
    io.parent.mkdir()
    io.write_bytes(PROFILE.read_bytes())
    adapter = tmp_path / 'connection' / 'settings.json'
    motion = tmp_path / 'models' / 'motion.json'
    settings = read_json(config_paths(PROFILE)[1])
    settings['scene_dir'] = '../models/scene'
    write_json(adapter, settings)
    write_json(motion, {'max_speed_deg_s': 12})
    loaded = load_config(io, adapter, motion)
    assert Path(loaded['adapter']['scene_dir']) == tmp_path / 'models' / 'scene'
    assert loaded['motion']['max_speed_deg_s'] == 12
    # The browser's default adjacent sidecars participate in cache invalidation.
    _, adjacent_adapter, adjacent_motion = config_paths(io)
    write_json(adjacent_adapter, settings)
    write_json(adjacent_motion, {'max_speed_deg_s': 12})
    monkeypatch.setattr(plc_preview, 'PROFILE', io)
    before = plc_preview.profile_signature()
    write_json(adjacent_motion, {'max_speed_deg_s': 123})
    assert plc_preview.profile_signature() != before
    assert plc_preview.load_profile()['motion']['max_speed_deg_s'] == 123
    before = plc_preview.profile_signature()
    adjacent_adapter.unlink()
    assert plc_preview.profile_signature() != before
    assert plc_preview.load_profile() is None
    with pytest.raises(FileNotFoundError):
        load_config(io)


@pytest.mark.parametrize('section,key,value', [
    ('adapter', 'cycle_hz', 0), ('adapter', 'cycle_hz', True),
    ('adapter', 'command_timeout_s', float('nan')), ('adapter', 'scene_dir', ''),
    ('motion', 'max_speed_deg_s', -1), ('motion', 'max_speed_deg_s', True),
])
def test_invalid_separate_settings_rejected(config, section, key, value):
    config[section][key] = value
    with pytest.raises(ValueError):
        validate(config)


def test_io_file_rejects_non_mapping_settings(tmp_path):
    from src.agent.scene_agent.robotcell_io import write_json
    io = tmp_path / 'bad.io.json'
    raw = read_json(PROFILE)
    raw['cycle_hz'] = 30
    write_json(io, raw)
    with pytest.raises(ValueError, match='only io_list'):
        load_config(io)
