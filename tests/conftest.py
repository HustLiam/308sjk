# -*- coding: utf-8 -*-
"""
pytest 全局隔离：任何未显式注入路径的 MemoryStore（编排器默认归因引擎等）
在测试期间写入 tmp——防 pytest 污染真实经验库/情景记忆（2026-09-09 实证：
全量测试向 workspace/experience/lessons.json 写入 21 条假经验）。
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_memory_defaults(tmp_path, monkeypatch):
    import agent.memory as mem
    monkeypatch.setattr(mem, "_LESSONS_PATH", tmp_path / "lessons.json")
    monkeypatch.setattr(mem, "_EPISODIC_PATH", tmp_path / "fixes.json")


@pytest.fixture(autouse=True)
def _isolate_deployment_http(monkeypatch,request):
    """Unit tests must not invoke live diagnostic deployments on the shared PLC."""
    import requests
    import agent.orchestrator as orch
    def offline(*args, **kwargs):
        raise requests.ConnectionError("unit tests have no live deployment HTTP access")
    monkeypatch.setattr(orch._LOCAL, 'request', offline)
    # This transport test installs its own read-only requests.Session fake.
    transport_test = request.node.originalname == 'test_runtime_status_only_reads_existing_deploy_service'
    if 'lx' not in request.fixturenames and not transport_test:
        from agent.runtime_control import LXRuntime,RuntimeControlError
        def offline_runtime(*args,**kwargs):
            raise RuntimeControlError('unit tests have no live Runtime access')
        monkeypatch.setattr(LXRuntime,'request',offline_runtime)
