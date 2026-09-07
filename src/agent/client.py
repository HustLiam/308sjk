"""BigModel(v4) OpenAI 兼容接口的轻量封装（框架沿用 308sjk_history/agent/client.py）。"""
import json

import requests

from .config import API_URL


class BigModelClient:
    """负责与 BigModel /chat/completions 接口通信。"""

    def __init__(self, api_key: str, base_url: str = API_URL, timeout: int = 180,
                 trust_env: bool = False):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # Windows 系统代理（注册表）会掐断长生成连接（ProxyError/RemoteDisconnected）；
        # 默认直连。确需代理的环境显式传 trust_env=True。
        self.session = requests.Session()
        self.session.trust_env = trust_env

    def chat_completions(self, payload: dict) -> dict:
        """调用对话补全接口，返回原始 JSON；失败时抛 RuntimeError。

        长生成（万 token 级完整工程输出）在非流式请求下，服务端生成期间连接
        无字节往返，常被中间层/读超时掐断（SSLEOFError / Read timed out）。
        此类失败自动降级为 **SSE 流式**重试（分片持续到达保活），并聚合成与
        非流式同构的响应。
        """
        try:
            return self._post_json(payload)
        except RuntimeError as exc:
            text = str(exc)
            if any(sig in text for sig in ("Read timed out", "SSLEOFError",
                                           "UNEXPECTED_EOF", "Connection reset")):
                return self._post_stream(payload)
            raise

    # ---------------- 内部：请求形态 ----------------
    def _headers(self):
        return {"Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"}

    def _post_json(self, payload: dict) -> dict:
        url = f"{self.base_url}/chat/completions"
        try:
            resp = self.session.post(url, headers=self._headers(), json=payload,
                                     timeout=self.timeout)
        except requests.RequestException as exc:
            raise RuntimeError(f"网络请求失败：{exc}") from exc
        if resp.status_code != 200:
            raise RuntimeError(f"接口返回 HTTP {resp.status_code}：{resp.text}")
        return resp.json()

    def _post_stream(self, payload: dict) -> dict:
        """SSE 流式请求；逐分片聚合为非流式同构响应。"""
        url = f"{self.base_url}/chat/completions"
        body = dict(payload)
        body["stream"] = True
        try:
            resp = self.session.post(url, headers=self._headers(), json=body,
                                     timeout=(10, 90), stream=True)
        except requests.RequestException as exc:
            raise RuntimeError(f"网络请求失败（stream）：{exc}") from exc
        if resp.status_code != 200:
            raise RuntimeError(f"接口返回 HTTP {resp.status_code}：{resp.text}")
        parts, finish = [], None
        try:
            for raw in resp.iter_lines(decode_unicode=True):
                if not raw or not raw.startswith("data:"):
                    continue
                data = raw[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                choice = (chunk.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    parts.append(delta["content"])
                finish = choice.get("finish_reason") or finish
        except requests.RequestException as exc:
            if not parts:
                raise RuntimeError(f"网络请求失败（stream）：{exc}") from exc
        return {"choices": [{"message": {"role": "assistant",
                                         "content": "".join(parts)},
                             "finish_reason": finish}]}
