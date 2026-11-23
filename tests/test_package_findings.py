"""Resource findings must not turn a local IO configuration into a scene pass."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from agent.scene_agent import plc_preview
from agent.scene_agent.package_findings import findings, io_binding
from agent.scene_agent.package_web import state
from test_plc_preview import make_preview
from test_robotcell_io import PROFILE, load_config, component_id


@pytest.fixture
def binding(tmp_path, monkeypatch):
    config = load_config(PROFILE)
    monkeypatch.setattr(plc_preview, 'load_profile', lambda: deepcopy(config))
    cid = component_id(config)
    records = [{**p, 'body': 'axis' + str(i), 'locked': False}
               for i, p in enumerate(config['io_list'][:6])]
    owners = {'axis' + str(i): {'component_id': cid} for i in range(6)}
    (tmp_path/'body_mapping.json').write_text(json.dumps({'bodies': owners}), encoding='utf8')
    report = {'source': {}, 'components': [{'id': cid, 'name': 'Robot', 'parent_id': None,
               'role': {}, 'attributes': {}, 'local': [], 'source': 'Station/Robot'}],
              'interfaces': [], 'connections': [], 'assembly': {'joint_records': records},
              'issues': [], 'gaps': [{'code': 'io_contract_missing', 'required': 'old text'}]}
    return config, report, tmp_path


def test_configuration_alone_is_not_a_model_or_communication_pass(binding):
    config, report, root = binding
    result = io_binding(report, root)
    assert result['status'] == 'configured'
    assert result['components'][0]['variable_count'] == 14
    assert result['components'][0]['joint_count'] == 6
    assert result['scene_acceptance'] == 'not_run'
    assert result['communication'] == 'not_checked'
    (root/'package_manifest.json').write_text(json.dumps(report), encoding='utf8')
    projected = state({'scene_kind': '.'}, root, False)
    assert projected['plc_acceptance'] == 'not_run' and not projected['model_frozen']
    gap = next(x for x in projected['pending'] if x['code'] == 'io_contract_missing')
    assert gap['title'] == '整场景 IO 验收尚未完成'
    assert 'IO 文件只保存变量映射' in gap['action']
    assert gap['raw']['required'] == 'old text'  # Preserve the original audit evidence.


def test_bound_status_requires_current_mujoco_binding_without_network(binding):
    config, report, root = binding
    preview = make_preview(config, lambda: pytest.fail('Report must not connect to PLC'))
    try:
        assert io_binding(report, root, preview)['status'] == 'bound'
        config['motion']['max_speed_deg_s'] += 1
        assert io_binding(report, root, preview)['status'] == 'mismatch'
    finally:
        preview.close()


@pytest.mark.parametrize('change', ['absent', 'locked', 'ambiguous', 'owner'])
def test_changed_scene_cannot_inherit_old_binding_status(binding, change):
    _, report, root = binding
    records = report['assembly']['joint_records']
    if change == 'absent':
        records.pop()
    elif change == 'locked':
        records[0]['locked'] = True
    elif change == 'ambiguous':
        records.append(deepcopy(records[0]))
    else:
        (root/'body_mapping.json').write_text(json.dumps({'bodies': {}}), encoding='utf8')
    assert io_binding(report, root)['status'] == 'mismatch'


def test_missing_config_and_other_scene_remain_unbound(binding, monkeypatch):
    _, report, root = binding
    report['components'] = []
    assert io_binding(report, root)['status'] == 'not_applicable'
    monkeypatch.setattr(plc_preview, 'load_profile', lambda: None)
    assert io_binding(report, root)['status'] == 'unavailable'


def test_issue_counts_names_unknown_codes_and_raw_evidence_are_preserved():
    report = {'components': [{'id': 'c', 'name': 'Post', 'source': 'Station/Fence/Post'}],
              'connections': [{'id': 'link', 'name': 'Mount'}],
              'issues': [{'code': 'mechanical_definition_missing', 'source': 'c', 'detail': 'missing fixed'},
                         {'code': 'mechanical_definition_missing', 'source': 'another'},
                         {'code': 'attachment_frame_unspecified', 'source': 'link'},
                         {'code': 'future_issue', 'source': 'unknown', 'detail': 'retain me'}]}
    before = deepcopy(report)
    rows = findings(report, {'status': 'unavailable'})
    assert report == before
    assert rows[0]['count'] == 2 and '不是缺失模型数' in rows[0]['message']
    assert rows[0]['affected_examples'][0]['path'] == 'Station/Fence/Post'
    assert rows[1]['affected_examples'][0]['name'] == 'Mount'
    assert rows[-1]['raw']['examples'][0]['detail'] == 'retain me'
