"""Browser-session PLC control: ownership, actual feedback, stop and failures."""
from copy import deepcopy
import math
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import numpy as np
import pytest

from agent.scene_agent.common import SceneError
from agent.scene_agent.joint_preview import JointPreview
from test_robotcell_io import PROFILE, ScanClient, load_config, component_id, small_model, scan_tcp


CLIENT = 'a' * 32


def make_preview(config, connect):
    bound = small_model(config, prefix=True)
    records = [{**p, 'body': 'axis' + str(i), 'locked': False}
               for i, p in enumerate(bound.targets)]
    owners = {'axis' + str(i): component_id(config) for i in range(6)}
    owners['extra'] = 'unbound'
    components = [{'id': component_id(config), 'name': 'Robot', 'parent_id': None},
                  {'id': 'unbound', 'name': 'Other', 'parent_id': None}]
    return JointPreview(bound.model, bound.data, owners, components, records,
                        plc_config=config, plc_connect=connect)


def finished(preview, ids):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        frame = preview.snapshot(CLIENT, ids)
        if not frame['active']:
            return frame
        time.sleep(.01)
    raise AssertionError('PLC preview did not finish')


@pytest.fixture
def rig():
    config = load_config(PROFILE)
    scan = ScanClient(config)
    preview = make_preview(config, lambda: scan)
    try:
        yield preview, [component_id(config)], scan
    finally:
        preview.close()


def test_unbound_and_mixed_selection_are_disabled_and_rejected(rig):
    p, ids, scan = rig
    assert p.snapshot(CLIENT, ids)['plc']['available']
    for chosen in [['unbound'], ids + ['unbound']]:
        assert not p.snapshot(CLIENT, chosen)['plc']['available']
        with pytest.raises(SceneError, match='IO 绑定'):
            p.command(CLIENT, chosen, 'plc', [{'joint_name': 'j0', 'position': 1}], .1)
    assert not scan.writes


def test_incomplete_binding_is_disabled():
    config = load_config(PROFILE)
    connect = lambda: pytest.fail('unbound model must not connect')
    p = make_preview(config, connect)
    p.close()
    # Keep the compiled model/source records and change only the persisted binding.
    bad = deepcopy(config)
    bad['io_list'][0]['source_id'] = 'missing'
    bad['io_list'][6]['source_id'] = 'missing'
    owners = {'axis'+str(i): component_id(config) for i in range(6)}
    records = [{**r, 'body': 'axis'+str(i), 'locked': False} for i, r in enumerate(config['io_list'][:6])]
    disabled = JointPreview(p.model, small_model(config, prefix=True).data, owners,
                            list(p.components.values()), records, plc_config=bad, plc_connect=connect)
    assert not disabled.snapshot(CLIENT, [component_id(config)])['plc']['available']


def test_plc_drives_display_session_and_only_checked_joint(rig):
    p, ids, scan = rig
    before = p.snapshot(CLIENT, ids)
    original = p.session(CLIENT)['data'].qpos.copy()
    value = before['positions']['j0'][0] + math.radians(3)
    frame = p.command(CLIENT, ids, 'plc', [{'joint_name': 'j0', 'position': value}], .15)
    assert frame['plc']['active']
    result = finished(p, ids)
    assert not result['plc']['error']
    assert result['positions']['j0'][0] == pytest.approx(value)
    assert not np.allclose(result['transforms'], before['transforms'])
    current = p.session(CLIENT)['data'].qpos
    assert np.array_equal(current[:7], original[:7])
    assert np.array_equal(current[8:], original[8:])
    event = result['plc']['feedback']
    assert event['feedback'] == event['plc_observed_inputs']
    assert scan.registers[106] == 0


def test_stop_holds_pose_clears_enable_and_releases_runtime(rig):
    p, ids, scan = rig
    value = p.snapshot(CLIENT, ids)['positions']['j0'][0] + .5
    p.command(CLIENT, ids, 'plc', [{'joint_name': 'j0', 'position': value}], 5)
    time.sleep(.1)
    stopped = p.command(CLIENT, ids, 'stop')
    assert not stopped['active']
    assert scan.registers[106] == 0
    time.sleep(.08)
    assert p.snapshot(CLIENT, ids)['positions'] == stopped['positions']
    p.command(CLIENT, ids, 'move', [{'joint_name': 'j0', 'position': value}], .1)
    assert p.snapshot(CLIENT, ids)['active']


def test_runtime_is_exclusive_across_preview_sessions(rig):
    p, ids, scan = rig
    target = [{'joint_name': 'j0', 'position': 1.2}]
    p.command(CLIENT, ids, 'plc', target, 2)
    with pytest.raises(SceneError, match='正在控制'):
        p.command('b'*32, ids, 'plc', target, 2)
    p2 = make_preview(load_config(PROFILE), lambda: scan)
    try:
        with pytest.raises(SceneError, match='另一个预览'):
            p2.command(CLIENT, ids, 'plc', target, .1)
    finally:
        p2.close()


def test_wrong_program_fails_without_writing_or_moving(rig):
    p, ids, scan = rig
    before = p.snapshot(CLIENT, ids)
    scan.registers[scan.contract['identity'][0]] ^= 1
    p.command(CLIENT, ids, 'plc', [{'joint_name': 'j0', 'position': 1.2}], .1)
    result = finished(p, ids)
    assert 'PLC 控制失败' in result['plc']['error']
    assert result['positions'] == before['positions']
    assert not scan.writes


def test_disconnect_and_browser_expiry_hold_model(rig):
    p, ids, scan = rig
    p.command(CLIENT, ids, 'plc', [{'joint_name': 'j0', 'position': 1.8}], 3)
    time.sleep(.08)
    scan.failed = True
    result = finished(p, ids)
    assert result['plc']['error']
    scan.failed = False
    p.command(CLIENT, ids, 'plc', [{'joint_name': 'j0', 'position': 1.8}], 3)
    p.plc.job['last_seen'] = time.monotonic() - 6
    # Do not poll the session: polling would correctly renew the browser lease.
    p.plc.job['thread'].join(timeout=3)
    result = p.snapshot(CLIENT, ids)
    assert not result['active']
    assert '预览连接已中断' in result['plc']['error']
    assert scan.registers[106] == 0


def test_real_tcp_controller_updates_session():
    config = load_config(PROFILE)
    with scan_tcp(config) as (connect, scan):
        p = make_preview(config, connect)
        try:
            ids = [component_id(config)]
            target = p.snapshot(CLIENT, ids)['positions']['j1'][0] + math.radians(1)
            p.command(CLIENT, ids, 'plc', [{'joint_name':'j1', 'position':target}], .1)
            frame = finished(p, ids)
            assert not frame['plc']['error']
            assert frame['positions']['j1'][0] == pytest.approx(target)
            assert scan.registers[106] == 0
        finally:
            p.close()


def test_stop_during_feedback_keeps_last_acknowledged_pose(rig, monkeypatch):
    from agent.scene_agent.plc_preview import TestDriver
    p, ids, scan = rig
    observed = TestDriver.observed
    count = [0]
    def stop_after_observation(driver, expected, timeout=3):
        result = observed(driver, expected, timeout)
        count[0] += 1
        if count[0] == 2:
            p.plc.job['cancel'].set()
        return result
    monkeypatch.setattr(TestDriver, 'observed', stop_after_observation)
    p.command(CLIENT, ids, 'plc', [{'joint_name': 'j0', 'position': 1.4}], .1)
    result = finished(p, ids)
    event = result['plc']['feedback']
    assert event['feedback']['rb100_feedback_seq'] == 2
    assert math.degrees(result['positions']['j0'][0]) == pytest.approx(event['feedback']['rb100_j1_actual'] * .02)


def test_stop_preserves_failed_disable_warning(rig):
    p, ids, scan = rig
    p.command(CLIENT, ids, 'plc', [{'joint_name': 'j0', 'position': 1.4}], 2)
    time.sleep(.08)
    scan.failed = True
    stopped = p.command(CLIENT, ids, 'stop')
    assert not stopped['active']
    assert '未确认 PLC 使能清零' in stopped['plc']['error']
