"""scenegen —— SceneSpec → USD 仿真环境生成模块。

详见《仿真环境与IO闭环详细设计》第 2 章：
  validate → build_usd → io_map（含 OpenPLC Modbus 地址分配）→ smoke。
"""

__version__ = "0.1.0"

from . import components, geom, iomap, validate  # noqa: F401

__all__ = ["components", "geom", "iomap", "validate", "__version__"]
