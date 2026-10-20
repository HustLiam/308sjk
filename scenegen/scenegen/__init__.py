"""scenegen —— SceneSpec → USD 仿真环境生成模块。

详见《仿真环境与IO闭环详细设计》第 2 章：
  validate → build_usd → io_map（含 OpenPLC Modbus 地址分配）→ smoke。
"""

__version__ = "0.1.0"

def __getattr__(name):
    """MJCF/地址分配路径不依赖 USD；仅实际使用 USD 组件时才导入 pxr。"""
    if name in {"components", "geom", "iomap", "validate", "build_usd", "smoke"}:
        import importlib
        module = importlib.import_module("." + name, __name__)
        globals()[name] = module
        return module
    raise AttributeError(name)

__all__ = ["components", "geom", "iomap", "validate", "__version__"]
