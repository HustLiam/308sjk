"""BigModel(v4) OpenAI 兼容接口的轻量封装（框架沿用 308sjk_history/agent/client.py）。"""
import requests

from .config import API_URL


class BigModelClient:
    """负责与 BigModel /chat/completions 接口通信。"""

    def __init__(self, api_key: str, base_url: str = API_URL, timeout: int = 180):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def chat_completions(self, payload: dict) -> dict:
        """调用对话补全接口，返回原始 JSON；失败时抛 RuntimeError。"""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
        except requests.RequestException as exc:
            raise RuntimeError(f"网络请求失败：{exc}") from exc

        if resp.status_code != 200:
            raise RuntimeError(f"接口返回 HTTP {resp.status_code}：{resp.text}")
        return resp.json()
