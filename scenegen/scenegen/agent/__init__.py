"""③ 模块的 LLM 部分：requirement_spec.json → SceneSpec（JSON）。

流水线（对应详细设计 2.1）：
  Prompt 组装（组件目录自动从 REGISTRY 导出）
    → LLM 生成 JSON
    → scenegen.validate + io_list 契约检查
    → 失败则把错误定向反馈给 LLM 重试，直至通过或达上限
"""

from .core import SceneGenAgent, contract_errors  # noqa: F401
from .llm import LLM, MockLLM, OpenAICompatLLM, from_env  # noqa: F401
