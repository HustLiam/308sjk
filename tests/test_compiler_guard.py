import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from src.pipeline.compiler_guard import (
    CompilerConfigurationError, INSTALL, MARKER, guarded_script, ensure_local_compiler,
)


SCRIPT = '#!/bin/bash\necho "Optimizing ST program..."\n./st_optimizer ./st_files/"$1" ./st_files/"$1"\n./iec2c ./st_files/"$1"\n'


def test_guard_keeps_compiler_and_is_idempotent():
    result = guarded_script(SCRIPT)
    assert MARKER in result and './st_optimizer' not in result
    assert './iec2c ./st_files/"$1"' in result
    assert guarded_script(result) == result


@pytest.mark.parametrize("script", ["#!/bin/bash\necho unknown\n", SCRIPT + SCRIPT])
def test_unknown_script_stops_without_mutation(script):
    with pytest.raises(CompilerConfigurationError):
        guarded_script(script)


def test_atomic_installer_preserves_backup_and_rejects_stale_source(tmp_path):
    import base64
    path = tmp_path / 'compile.sh'
    path.write_text(SCRIPT)
    payload = {'path': str(path), 'expected_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
               'replacement': base64.b64encode(guarded_script(SCRIPT).encode()).decode()}
    p = subprocess.run([sys.executable, '-c', INSTALL], input=json.dumps(payload),
                       text=True, capture_output=True)
    assert p.returncode == 0, p.stderr
    assert Path(str(path) + '.pre_agent_optimizer_guard').read_text() == SCRIPT
    before = path.read_bytes()
    p = subprocess.run([sys.executable, '-c', INSTALL], input=json.dumps(payload),
                       text=True, capture_output=True)
    assert p.returncode != 0
    assert path.read_bytes() == before


def test_remote_runtime_does_not_modify_local_container(monkeypatch):
    monkeypatch.setattr('src.pipeline.compiler_guard._docker',
                        lambda *a, **k: pytest.fail('remote runtime must not touch local Docker'))
    assert ensure_local_compiler('http://plc.example:8080')['status'] == 'external_unverified'


def test_wrong_local_port_does_not_modify_container(monkeypatch):
    info = [{'State': {'Running': True}, 'NetworkSettings': {'Ports': {
        '8080/tcp': [{'HostPort': '8080', 'HostIp': '0.0.0.0'}]}}}]
    calls = []
    def docker(args, input=None):
        calls.append(args)
        return json.dumps(info).encode()
    monkeypatch.setattr('src.pipeline.compiler_guard._docker', docker)
    assert ensure_local_compiler('http://localhost:9999')['status'] == 'unmanaged_unverified'
    assert calls == [['inspect', 'openplc']]
