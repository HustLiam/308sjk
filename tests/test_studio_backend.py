"""GUI 成功判定、无替代生成、进程停止与实际帧接线的回归。"""
import json
import os
from pathlib import Path
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from runtime.studio_backend import pipeline_command, verified_result, RemoteCamera, reset_cancelled_inputs


def evidence(path):
    (path / "final").mkdir()
    (path / "manifest.json").write_text(json.dumps({
        "status": "final", "generation_mode": "full_xml", "seed_used": False,
        "require_online": True}), encoding="utf-8")
    (path / "final/gate.json").write_text(json.dumps({"ok": True, "gates": {
        "deploy": {"state": "ok"}, "acceptance": {"state": "ok"}}}), encoding="utf-8")
    (path / "final/verdict.json").write_text(json.dumps({"verdict": "PASS", "details": [
        {"id": name, "pass": True} for name in ("AC1", "AC2", "AC3", "AC4", "PATH")]}), encoding="utf-8")


@pytest.mark.parametrize("corruption", ["exit", "seed", "skipped", "empty", "path_failure", "missing", "missing_path"])
def test_gui_never_labels_incomplete_evidence_success(tmp_path, corruption):
    evidence(tmp_path)
    assert verified_result(tmp_path, 0)
    code = 0
    if corruption == "exit":
        code = 1
    elif corruption == "missing":
        (tmp_path / "final/verdict.json").unlink()
    else:
        name = {"seed": "manifest.json", "skipped": "final/gate.json"}.get(corruption, "final/verdict.json")
        path = tmp_path / name
        data = json.loads(path.read_text(encoding="utf-8"))
        if corruption == "seed":
            data["seed_used"] = True
        elif corruption == "skipped":
            data["gates"]["acceptance"]["state"] = "skipped"
        elif corruption == "empty":
            data["details"] = []
        elif corruption == "missing_path":
            data["details"].pop()
        else:
            data["details"][-1]["pass"] = False
        path.write_text(json.dumps(data), encoding="utf-8")
    assert not verified_result(tmp_path, code)


def test_gui_strict_command_and_unsupported_scope():
    cmd = pipeline_command("device.aml", "画一个正方形", "studio_test")
    assert cmd[cmd.index("-m") + 1] == "src.agent.closed_loop"
    assert "--spec" not in cmd and "--seed" not in cmd
    assert Path(cmd[cmd.index("--frame-output") + 1]).parent.name == "studio_test"
    for text in ("画一个三角形", "画圆"):
        command = pipeline_command("device.aml", text, "studio_test")
        assert command[command.index("--request") + 1] == text
        assert "标准画方" not in " ".join(command)


def test_camera_does_not_precreate_strict_task_directory(tmp_path):
    path = tmp_path / "new_task/live_frame.npy"
    camera = RemoteCamera(path)
    camera.orbit(3, 4)
    assert not path.parent.exists()
    path.parent.mkdir()
    camera.zoom(1)
    assert path.with_suffix(".camera.json").is_file()


@pytest.mark.parametrize("identity", [4, 99])
def test_cancel_only_resets_inputs_of_matching_program(tmp_path, monkeypatch, identity):
    from types import SimpleNamespace
    import pymodbus.client
    folder = tmp_path / "requirements"
    folder.mkdir()
    (folder / "requirement_spec.json").write_text(json.dumps({"prog_id": 4, "io_list": [
        {"name": "run", "dir": "input", "type": "BOOL", "address": "%QX0.0"},
        {"name": "done", "dir": "output", "type": "BOOL", "address": "%QX2.2"}]}), encoding="utf-8")
    writes = []
    response = lambda **kw: SimpleNamespace(isError=lambda: False, **kw)
    client = SimpleNamespace(connect=lambda: True, close=lambda: None,
        read_holding_registers=lambda **kw: response(registers=[identity]),
        read_coils=lambda **kw: response(bits=[True] * kw["count"]),
        write_coils=lambda **kw: (writes.append(kw), response())[1])
    monkeypatch.setattr(pymodbus.client, "ModbusTcpClient", lambda *a, **kw: client)
    if identity == 99:
        with pytest.raises(RuntimeError, match="身份"):
            reset_cancelled_inputs(tmp_path)
        assert not writes
    else:
        reset_cancelled_inputs(tmp_path)
        assert len(writes) == 1 and len(writes[0]["values"]) == 32
        assert writes[0]["values"][0] is False
        assert all(writes[0]["values"][1:])


@pytest.fixture
def qt():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    return app


def test_failed_process_does_not_run_seed_fallback(qt, monkeypatch):
    from runtime import csp_studio as studio
    commands = []
    def command(*args):
        commands.append(args)
        return [sys.executable, "-c", "raise SystemExit(1)"]
    monkeypatch.setattr(studio, "pipeline_command", command)
    monkeypatch.setattr(studio.PipelineWorker, "_ensure_services", lambda self: None)
    worker = studio.PipelineWorker(studio.DEFAULT_AML, "画方")
    outcomes = []
    worker.task_done.connect(lambda status, path: outcomes.append(status))
    worker.start()
    assert worker.wait(5000)
    qt.processEvents()
    assert outcomes == ["failed"]
    assert len(commands) == 1


def test_stop_terminates_owned_worker_process(qt, monkeypatch):
    from runtime import csp_studio as studio
    monkeypatch.setattr(studio, "pipeline_command", lambda *args: [
        sys.executable, "-c", "import time; print('started', flush=True); time.sleep(60)"])
    monkeypatch.setattr(studio.PipelineWorker, "_ensure_services", lambda self: None)
    worker = studio.PipelineWorker(studio.DEFAULT_AML, "画方")
    outcomes = []
    worker.task_done.connect(lambda status, path: outcomes.append(status))
    worker.start()
    deadline = time.monotonic() + 5
    while worker.proc is None and time.monotonic() < deadline:
        qt.processEvents()
        time.sleep(0.01)
    try:
        assert worker.proc is not None
        worker.cancel()
        assert worker.wait(5000)
        qt.processEvents()
        assert worker.proc.poll() is not None
        assert outcomes == ["cancelled"]
    finally:
        if worker.isRunning():
            worker.cancel()
            worker.wait(5000)


def test_gui_displays_frame_and_disables_hmi_during_run(qt, tmp_path):
    import numpy as np
    from runtime.csp_studio import MainWindow
    window = MainWindow()
    try:
        if os.name == "nt":
            from PySide6.QtGui import QFontMetrics
            assert QFontMetrics(window.font()).inFontUcs4(ord("画"))
        window._set_busy(True)
        assert not window.hmi.isEnabled() and not window.go_button.isEnabled()
        path = tmp_path / "live_frame.npy"
        np.save(path, np.full((24, 32, 3), 80, dtype=np.uint8))
        window.frame_path = path
        window._read_live_frame()
        assert window.received_frames == 1 and not window.view.img.isNull()
        window._read_live_frame()
        assert window.received_frames == 1
    finally:
        window.close()


@pytest.mark.parametrize("grid", [False, True])
def test_hmi_short_click_has_minimum_pulse_and_run_tracks_plc(qt, tmp_path, monkeypatch, grid):
    from types import SimpleNamespace
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QPlainTextEdit
    from runtime.csp_studio import HmiPanel
    elements = [
        {"kind": "BUTTON", "signal": "run", "address": "%QX0.0", "type": "toggle"},
        {"kind": "BUTTON", "signal": "draw", "address": "%QX2.0", "type": "trigger"}]
    if grid:
        for col, e in enumerate(elements):
            e["pos"] = [0, col]
    path = tmp_path / "hmi.json"
    path.write_text(json.dumps({"elements": elements}), encoding="utf-8")
    panel = HmiPanel(str(path), QPlainTextEdit())
    values, writes = {0: False, 16: False}, []
    def write(bit, value):
        values[bit] = value
        writes.append((bit, value, time.monotonic()))
    panel.io = SimpleNamespace(read=lambda bit: values[bit], write=write)
    monkeypatch.setattr(panel, "_connect", lambda: True)
    panel.show()
    try:
        assert panel.coil_span == 32
        QTest.mouseClick(panel.button_widgets["run"], Qt.LeftButton)
        assert values[0] and panel.button_widgets["run"].isChecked()
        values[0] = False
        panel.poll()
        assert not panel.button_widgets["run"].isChecked()
        panel.button_widgets["run"].setChecked(True)
        monkeypatch.setattr(panel, "_connect", lambda: False)
        panel._toggle("run", 0)
        assert not panel.button_widgets["run"].isChecked()
        monkeypatch.setattr(panel, "_connect", lambda: True)
        QTest.mouseClick(panel.button_widgets["draw"], Qt.LeftButton)
        assert values[16] is True
        QTest.qWait(70)
        assert values[16] is True
        QTest.qWait(160)
        assert values[16] is False
        pulse = [w for w in writes if w[0] == 16]
        assert pulse[-1][2] - pulse[-2][2] >= 0.14
        QTest.mouseClick(panel.button_widgets["draw"], Qt.LeftButton)
        QTest.qWait(40)
        QTest.mousePress(panel.button_widgets["draw"], Qt.LeftButton)
        QTest.qWait(190)
        assert values[16] is True
        QTest.mouseRelease(panel.button_widgets["draw"], Qt.LeftButton)
        assert values[16] is False
    finally:
        panel.disconnect()
        panel.close()


def test_manual_failed_deploy_keeps_hmi_disabled(qt):
    from types import SimpleNamespace
    from runtime.csp_studio import MainWindow
    window = MainWindow()
    window.deployer = SimpleNamespace(isRunning=lambda: False, ok=False)
    window.sim = SimpleNamespace(isRunning=lambda: True, link=SimpleNamespace(online=True))
    try:
        window._manual_deploy_finished()
        assert not window.hmi.isEnabled()
        window._manual_connection(window.sim, True, "connected")
        assert not window.hmi.isEnabled()
    finally:
        window.sim = None
        window.close()


@pytest.mark.parametrize("bad_step", ["read", "write"])
def test_safe_coil_io_rejects_modbus_error_responses(bad_step):
    from types import SimpleNamespace
    from modbus_io import SafeCoilIO
    client = SimpleNamespace(
        read_coils=lambda **kw: SimpleNamespace(bits=[False] * 32, isError=lambda: bad_step == "read"),
        write_coils=lambda **kw: SimpleNamespace(isError=lambda: bad_step == "write"))
    with pytest.raises(RuntimeError, match="Modbus"):
        SafeCoilIO(client, span=32).write(16, True)
