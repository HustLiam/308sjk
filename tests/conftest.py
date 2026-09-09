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
