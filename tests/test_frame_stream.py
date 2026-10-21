"""相机操作与实际状态保存；检查查看结果时不会推进物理。"""
import json
import os
import time
from types import SimpleNamespace

import mujoco
import numpy as np
import pytest

from runtime.frame_stream import FramePublisher, SnapshotRenderer
from runtime.studio_backend import RemoteCamera


@pytest.fixture
def captured_frame(tmp_path):
    model = mujoco.MjModel.from_xml_string('''<mujoco><worldbody>
        <geom type="plane" size="1 1 .1" rgba=".8 .8 .8 1"/>
        <body pos="0 0 .15"><joint type="slide" axis="1 0 0"/>
        <geom type="box" size=".12 .07 .15" mass="1" rgba=".7 .1 .1 1"/>
        </body></worldbody></mujoco>''')
    data = mujoco.MjData(model)
    data.qpos[0], data.qvel[0], data.time = .25, .12, 3.25
    mujoco.mj_forward(model, data)
    path = tmp_path / 'live_frame.npy'
    publisher = FramePublisher(model, path, (0, 0, .15))
    publisher.ink = [[.1, .1, .005], [.12, .1, .005]]
    try:
        publisher.publish(data)
        yield path, publisher, data
    finally:
        publisher.close()


def test_live_camera_changes_pixels_without_changing_physics(captured_frame):
    path, publisher, data = captured_frame
    before = np.load(path)
    position, velocity, stamp = data.qpos.copy(), data.qvel.copy(), data.time
    camera = RemoteCamera(path)
    camera.orbit(160, 200)
    camera.zoom(-1)
    publisher.next_frame = 0
    publisher.publish(data)
    assert publisher.cam.elevation == 20  # 可从俯视转到水平线以上。
    assert not np.array_equal(before, np.load(path))
    np.testing.assert_array_equal(data.qpos, position)
    np.testing.assert_array_equal(data.qvel, velocity)
    assert data.time == stamp


def test_result_rotation_preserves_actual_state_and_ink(captured_frame, monkeypatch):
    path, publisher, data = captured_frame
    position, stamp = data.qpos.copy(), data.time
    original_snapshot = path.with_suffix('.state.npz').read_bytes()
    monkeypatch.setattr(mujoco, 'mj_step', lambda *a: pytest.fail('查看结果不能推进物理'))
    viewer = SnapshotRenderer(path)
    try:
        np.testing.assert_array_equal(viewer.data.qpos, position)
        assert viewer.data.time == stamp
        assert viewer.publisher.ink == publisher.ink
        before = viewer.render().copy()
        camera = RemoteCamera(path)
        camera.orbit(200, -20)
        after = viewer.render().copy()
        assert not np.array_equal(before, after)
        np.testing.assert_array_equal(viewer.data.qpos, position)
        assert viewer.data.time == stamp
        camera.reset_camera()
        assert np.array_equal(viewer.render(), before)
        assert path.with_suffix('.state.npz').read_bytes() == original_snapshot
    finally:
        viewer.close()


def test_result_rejects_state_from_another_model(captured_frame):
    path, publisher, data = captured_frame
    path.with_suffix('.model.mjb').write_bytes(b'not the captured model')
    with pytest.raises(ValueError, match='不匹配'):
        SnapshotRenderer(path)


def test_finished_pipeline_can_orbit_and_release_renderer(captured_frame):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from runtime.csp_studio import MainWindow
    path, publisher, data = captured_frame
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.worker = SimpleNamespace(service=None, isRunning=lambda: False)
    window.frame_path = path
    try:
        window._pipeline_done('cancelled', str(path.parent))
        assert window.inspector is not None
        assert window.frame_path is None and not window.hmi.isEnabled()
        deadline = time.monotonic() + 5
        while window.view.img is None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.02)
        before = window.view.img.copy()
        window.view.thread.orbit(180, 20)
        deadline = time.monotonic() + 5
        while window.view.img == before and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.02)
        assert window.view.img != before
        inspector = window.inspector
        assert window._stop_manual_sim()
        assert not inspector.isRunning() and window.inspector is None
    finally:
        window.close()


@pytest.mark.parametrize('payload', [None, [], {'azimuth': 'bad'},
    {'azimuth': float('nan'), 'elevation': 0, 'distance': 2}])
def test_invalid_camera_does_not_break_publishing(captured_frame, payload):
    path, publisher, data = captured_frame
    path.with_suffix('.camera.json').write_text(json.dumps(payload), encoding='utf-8')
    publisher.next_frame = 0
    publisher.publish(data)
    assert np.load(path).shape == (480, 640, 3)
