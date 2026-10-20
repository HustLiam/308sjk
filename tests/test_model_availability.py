"""Service failures retain evidence and never become repeated PLC repairs."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'src/pipeline'))
from agent.client import BigModelClient, ModelUnavailableError
from agent.closed_loop import record_api_blocked
from agent.orchestrator import Orchestrator
from test_test_plan import plan, spec


@pytest.mark.parametrize('status', [429, 500, 502, 503])
def test_transient_http_failure_blocks_repeated_calls(status, monkeypatch):
    client = BigModelClient('unit-test')
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(status_code=status, text='service failure')
    monkeypatch.setattr(client.session, 'post', post)
    with pytest.raises(ModelUnavailableError):
        client.chat_completions({})
    with pytest.raises(RuntimeError):
        client.chat_completions({})
    assert client.blocked_status == 'api_unavailable' and len(calls) == 1


def test_dns_failure_is_model_unavailability(monkeypatch):
    client = BigModelClient('unit-test')
    def post(*args, **kwargs):
        raise requests.ConnectionError('DNS unavailable')
    monkeypatch.setattr(client.session, 'post', post)
    with pytest.raises(ModelUnavailableError, match='DNS'):
        client.chat_completions({})
    assert client.blocked_status == 'api_unavailable'


def test_interrupted_stream_never_returns_partial_xml(monkeypatch):
    client = BigModelClient('unit-test')
    def lines(**kwargs):
        yield 'data: '+json.dumps({'choices': [{'delta': {'content': '<project/>'}}]})
        raise requests.exceptions.ChunkedEncodingError('stream disconnected')
    monkeypatch.setattr(client.session, 'post', lambda *a, **kw: SimpleNamespace(status_code=200, iter_lines=lines))
    with pytest.raises(ModelUnavailableError, match='不完整'):
        client.chat_completions({}, prefer_stream=True)
    assert client.blocked_status == 'api_unavailable'


def test_successful_stream_fallback_does_not_block_client(monkeypatch):
    client = BigModelClient('unit-test')
    def timed_out(payload):
        raise ModelUnavailableError('Read timed out')
    monkeypatch.setattr(client, '_post_json', timed_out)
    monkeypatch.setattr(client, '_post_stream', lambda *a, **kw: {'choices': [{'finish_reason': 'stop', 'message': {'content': 'ok'}}]})
    assert client.chat_completions({})['choices'][0]['message']['content'] == 'ok'
    assert client.blocked_error is None


def test_bad_payload_is_not_mislabeled_as_network(monkeypatch):
    client = BigModelClient('unit-test')
    monkeypatch.setattr(client.session, 'post', lambda *a, **kw: SimpleNamespace(status_code=400, text='invalid argument'))
    with pytest.raises(RuntimeError, match='400'):
        client.chat_completions({})
    assert client.blocked_status is None


def test_requirement_failure_writes_unavailable_manifest(tmp_path):
    client = SimpleNamespace(blocked_status='api_unavailable', calls=1)
    assert record_api_blocked(ModelUnavailableError('DNS failure'), tmp_path, client, 'requirement')
    manifest = json.loads((tmp_path / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['status'] == 'api_unavailable' and manifest['stage'] == 'requirement'
    assert manifest['seed_used'] is False


def test_generation_unavailability_keeps_best_and_stops_repair(tmp_path, monkeypatch):
    class Generator:
        client = SimpleNamespace(blocked_error=None, blocked_status=None)
        def generate(self, *args, **kwargs):
            return {'xml': (ROOT / 'src/plc/plotter_square_csp.xml').read_text(encoding='utf-8')}
        def repair(self, *args, **kwargs):
            self.client.blocked_error = 'DNS failure'
            self.client.blocked_status = 'api_unavailable'
            raise ModelUnavailableError('DNS failure')
    orch = Orchestrator(runs_root=tmp_path, project_root=ROOT, max_iters=6)
    monkeypatch.setattr(orch, 'acceptance_gate', lambda *a, **kw: ('failed', ['FAIL initial acceptance']))
    result = orch.solve(spec(), Generator(), acceptance='mujoco', test_plan=plan())
    assert result['status'] == 'api_unavailable' and result['iter'] == 1
    run = Path(result['run_dir'])
    assert (run / 'best/plcopen.xml').is_file() and not (run / 'iter_003').exists()


def test_benchmark_keeps_pending_cases_when_model_service_fails(tmp_path, monkeypatch):
    from agent import benchmark
    cases = [{'id': str(i), 'request': 'test'} for i in range(3)]
    monkeypatch.setattr(benchmark, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(benchmark, 'matrix', lambda: cases)
    dispatched = []
    class Process:
        def __init__(self, command, **kwargs):
            dispatched.append(command)
            task = command[command.index('--task-id')+1]
            run = tmp_path / 'runs' / task
            run.mkdir(parents=True)
            (run / 'manifest.json').write_text(json.dumps({'status': 'api_unavailable'}))
        def wait(self, **kwargs):
            return 2
    monkeypatch.setattr(benchmark.subprocess, 'Popen', Process)
    output = tmp_path / 'output'
    monkeypatch.setattr(sys, 'argv', ['benchmark', '--output', str(output)])
    assert benchmark.main() == 2 and len(dispatched) == 1
    summary = json.loads((output / 'summary.json').read_text(encoding='utf-8'))
    assert summary['completed'] == 1 and summary['planned'] == 3 and not summary['complete']
    assert not (output / '1/command.json').exists()
    monkeypatch.setenv('LLM_REASONING_EFFORT', 'changed-for-test')
    with pytest.raises(RuntimeError, match='Model configuration changed'):
        benchmark.main()
    assert len(dispatched) == 1
