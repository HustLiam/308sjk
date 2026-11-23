"""Joint controls use compiled indices and real forward kinematics, without file writes."""
import hashlib
import json
import threading
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))

import mujoco
import numpy as np
import pytest
import requests

from agent.scene_agent.common import SceneError, write_json
from agent.scene_agent.joint_preview import JointPreview
from agent.scene_agent.web import SceneWorkbench, SceneWebError


XML = '''<mujoco><compiler angle="radian"/><worldbody>
<body name="unrelated" pos="3 0 0"><freejoint name="floating"/><geom type="sphere" size=".1"/></body>
<body name="arm"><joint name="axis" type="hinge" axis="0 0 1" range="-1 1"/>
<geom type="box" size=".1 .05 .05" pos=".2 0 0"/>
<body name="tool" pos=".4 0 0"><joint name="jaw" type="slide" axis="1 0 0" range="0 .1"/>
<geom type="sphere" size=".02"/></body></body></worldbody></mujoco>'''
COMPONENTS = [{'id':'root','name':'Robot','parent_id':None},
              {'id':'arm','name':'Arm','parent_id':'root'},
              {'id':'tool','name':'Tool','parent_id':'arm'},
              {'id':'other','name':'Other','parent_id':None}]
OWNERS = {'arm':'arm','tool':'tool','unrelated':'other'}
CLIENT = 'a' * 32


@pytest.fixture
def preview():
    model = mujoco.MjModel.from_xml_string(XML)
    data = mujoco.MjData(model)
    now = [10.]
    return JointPreview(model, data, OWNERS, COMPONENTS, clock=lambda: now[0]), now


def test_compiled_address_not_joint_id_and_aml_subtree(preview):
    p, _ = preview
    snapshot = p.snapshot(CLIENT, ['root'], metadata=True)
    assert [(j['joint_name'], j['joint_id'], j['jnt_qposadr']) for j in snapshot['joints']] == [('axis', 1, 7), ('jaw', 2, 8)]
    assert p.snapshot(CLIENT, ['other'], metadata=True)['joints'][0]['qpos_width'] == 7
    assert not p.snapshot(CLIENT, ['other'], metadata=True)['joints'][0]['controllable']


def test_move_uses_real_fk_and_smooth_timing_stop_and_reset(preview):
    p, clock = preview
    initial = p.snapshot(CLIENT, ['root'])
    p.command(CLIENT, ['root'], 'move', [{'joint_name':'axis','position':1}, {'joint_name':'jaw','position':.1}], 2)
    clock[0] += 1
    halfway = p.snapshot(CLIENT, ['root'])
    assert halfway['positions'] == {'axis':[.5], 'jaw':[.05]}
    assert halfway['active']
    assert not np.allclose(initial['transforms'], halfway['transforms'])
    # The attached tool follows the arm's rotation as well as its own displacement.
    assert np.allclose(halfway['transforms'][2][:3], [.45*np.cos(.5), .45*np.sin(.5), 0])
    stopped = p.command(CLIENT, ['root'], 'stop')
    clock[0] += 5
    assert p.snapshot(CLIENT, ['root'])['positions'] == stopped['positions']
    p.command(CLIENT, ['root'], 'reset', duration=2)
    clock[0] += 2
    final = p.snapshot(CLIENT, ['root'])
    assert not final['active']
    assert final['positions'] == initial['positions']
    assert np.allclose(final['transforms'], initial['transforms'])


@pytest.mark.parametrize('targets', [
    [{'joint_name':'axis','position':1.1}], [{'joint_name':'axis','position':float('nan')}],
    [{'joint_name':'axis','position':float('inf')}], [{'joint_name':'axis','position':True}],
    [{'joint_name':'axis','position':0}, {'joint_name':'axis','position':.1}],
    [{'joint_name':'floating','position':0}], [{'joint_name':'missing','position':0}], []])
def test_bad_batch_is_rejected_before_state_mutation(preview, targets):
    p, _ = preview
    before = p.snapshot(CLIENT, ['root'])
    with pytest.raises(SceneError):p.command(CLIENT, ['root'], 'move', targets)
    assert p.snapshot(CLIENT, ['root']) == before


def test_selection_session_isolation_and_bad_duration(preview):
    p, clock = preview
    with pytest.raises(SceneError):p.command(CLIENT, ['tool'], 'move', [{'joint_name':'axis','position':.5}])
    with pytest.raises(SceneError):p.command(CLIENT, ['root'], 'reset', duration=0)
    with pytest.raises(SceneError):p.snapshot(CLIENT, ['unknown'])
    p.command(CLIENT, ['root'], 'move', [{'joint_name':'axis','position':.5}], 1)
    clock[0] += 2
    assert p.snapshot(CLIENT, ['root'])['positions']['axis'] == [.5]
    assert p.snapshot('b'*32, ['root'])['positions']['axis'] == [0.]


def test_world_anchors_follow_compiled_joints_and_attachment_tree(preview):
    p, clock = preview
    assert p.mechanical_children == {'arm': ['tool']}
    p.command(CLIENT, ['root'], 'move', [{'joint_name': 'axis', 'position': 1}], 1)
    clock[0] += 1
    result = p.snapshot(CLIENT, ['root'])
    assert np.allclose(result['anchors']['axis'], [0, 0, 0])
    assert np.allclose(result['anchors']['jaw'], [.4 * np.cos(1), .4 * np.sin(1), 0])


def create_task(manager):
    ident = 'c'*32
    folder = manager.directory(ident)
    root = folder/'imported_scene'
    root.mkdir(parents=True)
    (root/'scene.xml').write_text(XML, encoding='utf-8')
    sha = hashlib.sha256((root/'scene.xml').read_bytes()).hexdigest()
    write_json(root/'assembly_checks.json', {'compiled':True,'actual_geoms':3,'model_xml_sha256':sha,'joint_records':[]})
    write_json(root/'body_mapping.json', {'bodies':{k:{'component_id':v,'name':v} for k,v in OWNERS.items()}})
    write_json(root/'package_manifest.json', {'components':COMPONENTS})
    write_json(folder/'web.json', {'id':ident,'resource_package':True,'scene_kind':'imported_scene','busy':False,'revision':1})
    return ident, root


def test_workbench_rebinds_and_rejects_old_version_without_mutating_sources(tmp_path):
    manager = SceneWorkbench(tmp_path)
    try:
        ident, root = create_task(manager)
        before = {p.name:p.read_bytes() for p in root.iterdir()}
        geometry = manager.geometry(ident)
        version = geometry['joint_preview_version']
        args = (ident, CLIENT, ['root'], version)
        assert manager.joint_preview(*args, metadata=True)['joints'][0]['jnt_qposadr'] == 7
        manager.joint_preview(*args, command={'action':'reset'})
        assert {p.name:p.read_bytes() for p in root.iterdir()} == before
        assert manager.geometry(ident)['joint_preview_version'] == version
        # Remove the unrelated free joint: actual qpos offsets shift by seven.
        (root/'scene.xml').write_text(XML.replace('<freejoint name="floating"/>', ''), encoding='utf-8')
        with pytest.raises(SceneWebError, match='版本'):manager.joint_preview(*args, command={'action':'reset'})
        new = manager.geometry(ident)['joint_preview_version']
        assert new != version
        assert manager.joint_preview(ident, CLIENT, ['root'], new, metadata=True)['joints'][0]['jnt_qposadr'] == 0
        with pytest.raises(SceneWebError, match='版本'):manager.joint_preview(*args)
    finally:manager.close()


def test_http_joint_controls_require_token_and_return_updated_positions(tmp_path):
    from agent.web import SVGWorkbench, make_server
    app = SVGWorkbench(tmp_path/'app')
    ident, root = create_task(app.device_scenes())
    server = make_server(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
    client = requests.Session();client.trust_env=False
    url = 'http://127.0.0.1:'+str(server.server_port)
    try:
        result = client.get(url+'/api/device-scene/view', params={'task':ident}, timeout=10)
        assert result.status_code == 200, result.text
        version = result.json()['joint_preview_version']
        args = {'task':ident,'client':CLIENT,'components':['root'],'version':version,'action':'move',
                'targets':[{'joint_name':'axis','position':.5}],'duration':.1}
        assert client.post(url+'/api/device-scene/joint-command', json=args, timeout=10).status_code == 403
        response = client.post(url+'/api/device-scene/joint-command', json=args, headers={'X-Agent-Token':app.token}, timeout=10)
        assert response.status_code == 200, response.text
        assert response.json()['active']
        response = client.get(url+'/api/device-scene/joint-state', params={'task':ident,'client':CLIENT,'component':'root','version':version,'metadata':'1'}, timeout=10)
        assert response.status_code == 200, response.text
        assert len(response.json()['joints']) == 2
        assert client.get(url+'/joint_preview.js',timeout=10).status_code == 200
    finally:
        server.shutdown();server.server_close();app.close();client.close();thread.join(timeout=5)
