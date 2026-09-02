"""智能体侧全局配置：API 地址、模型、密钥（框架沿用 308sjk_history/agent/config.py）。"""
import os
from pathlib import Path

# 仓库根目录（src/agent/ 的上两级）
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 智谱 BigModel 开放平台 API 地址（OpenAI 兼容）
API_URL = "https://open.bigmodel.cn/api/coding/paas/v4"

# 使用的模型
MODEL = "glm-5.3"

# 从环境变量读取 API Key 时依次尝试的变量名
API_KEY_ENV_VARS = ("ZHIPUAI_API_KEY", "BIGMODEL_API_KEY", "API_KEY")

# PLCopen XML 交付物目录（模式库种子所在）
PLC_DIR = PROJECT_ROOT / "src" / "plc"

# 编排器产物根目录（gc 文档 §4：每轮落盘 runs/<task>/iter_NNN/，全量入 git）
RUNS_DIR = PROJECT_ROOT / "runs"


def _load_dotenv(path: Path) -> None:
    """极简 .env 加载器：读取 KEY=VALUE 行写入环境变量（不覆盖已有值）。"""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv(PROJECT_ROOT / ".env")


def get_api_key() -> str:
    """从环境变量读取 API Key，未配置时返回空字符串。"""
    for name in API_KEY_ENV_VARS:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def has_api_key() -> bool:
    return bool(get_api_key())
