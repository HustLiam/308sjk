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
        self.blocked_error = None
        self.output_limited = False
        # Windows 系统代理（注册表）会掐断长生成连接（ProxyError/RemoteDisconnected）；
        # 默认直连。确需代理的环境显式传 trust_env=True。
        self.session = requests.Session()
        self.session.trust_env = trust_env

    def chat_completions(self, payload: dict, on_chunk=None, prefer_stream=False) -> dict:
        """调用对话补全接口，返回原始 JSON；失败时抛 RuntimeError。

        长生成（万 token 级完整工程输出）在非流式请求下，服务端生成期间连接
        无字节往返，常被中间层/读超时掐断（SSLEOFError / Read timed out）。
        此类失败自动降级为 **SSE 流式**重试（分片持续到达保活），并聚合成与
        非流式同构的响应。

        on_chunk(text, total_chars)：流式模式下每收到一个增量分片回调一次
        （供上层实时播报生成进度）；prefer_stream=True 直接走流式（预期长
        输出的调用省去先超时再降级的两分钟）。
        """
        if self.blocked_error is not None:
            raise RuntimeError(self.blocked_error)
        payload = self.prepare_payload(payload)
        if prefer_stream:
            result = self._post_stream(payload, on_chunk=on_chunk)
        else:
            try:
                result = self._post_json(payload)
            except RuntimeError as exc:
                text = str(exc)
                if any(sig in text for sig in ("Read timed out", "SSLEOFError",
                                               "UNEXPECTED_EOF", "Connection reset")):
                    result = self._post_stream(payload, on_chunk=on_chunk)
                else:
                    raise
        if (result.get("choices") or [{}])[0].get("finish_reason") == "length":
            self.output_limited = True
        return result

    def prepare_payload(self, payload):
        """On subsequent allowed retries reserve the output budget for the artifact.

        No extra call, token-budget increase, or source/acceptance change is made.
        The DeepSeek reasoning profile is only adjusted after a recorded length limit.
        """
        prepared = dict(payload)
        if (self.output_limited and "deepseek" in str(payload.get("model", "")).lower()
                and payload.get("thinking", {}).get("type") == "enabled"):
            prepared["reasoning_effort"] = "low"
        return prepared

    # ---------------- 内部：请求形态 ----------------
    def _headers(self):
        return {"Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"}

    def _check_status(self, response):
        if response.status_code != 200:
            error = "接口返回 HTTP %s：%s" % (response.status_code, response.text)
            if response.status_code in (401, 402):
                self.blocked_error = error
            raise RuntimeError(error)

    def _post_json(self, payload: dict) -> dict:
        url = f"{self.base_url}/chat/completions"
        try:
            resp = self.session.post(url, headers=self._headers(), json=payload,
                                     timeout=self.timeout)
        except requests.RequestException as exc:
            raise RuntimeError(f"网络请求失败：{exc}") from exc
        self._check_status(resp)
        return resp.json()

    def _post_stream(self, payload: dict, on_chunk=None) -> dict:
        """SSE 流式请求；逐分片聚合为非流式同构响应。

        on_chunk(delta_text, total_chars)：每分片回调（上层节流后播报进度）。
        """
        url = f"{self.base_url}/chat/completions"
        body = dict(payload)
        body["stream"] = True
        try:
            resp = self.session.post(url, headers=self._headers(), json=body,
                                     timeout=(10, 90), stream=True)
        except requests.RequestException as exc:
            raise RuntimeError(f"网络请求失败（stream）：{exc}") from exc
        self._check_status(resp)
        parts, finish = [], None
        metadata, usage = {}, None
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
                for key in ("id", "model", "created"):
                    if key in chunk:
                        metadata[key] = chunk[key]
                if chunk.get("usage"):
                    usage = chunk["usage"]
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    piece = delta["content"]
                    parts.append(piece)
                    if on_chunk:
                        try:
                            on_chunk(piece, sum(len(p) for p in parts))
                        except Exception:
                            pass   # 播报异常不影响生成
                finish = choice.get("finish_reason") or finish
        except requests.RequestException as exc:
            raise RuntimeError(f"流式响应中断，拒绝使用不完整生成结果：{exc}") from exc
        if finish is None:
            raise RuntimeError("流式响应未完整结束，拒绝使用不完整生成结果")
        return {**metadata, "usage": usage, "choices": [{"message": {"role": "assistant",
                                         "content": "".join(parts)},
                             "finish_reason": finish}]}
