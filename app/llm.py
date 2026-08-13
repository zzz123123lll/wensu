"""统一 LLM 客户端：OpenAI 兼容 chat/completions 协议。

支持任意兼容端点（DeepSeek / OpenAI / 通义 / Kimi / 自定义）。
错误统一映射为 LLMError(kind)：auth / timeout / network / http / empty。
transport 可注入（测试用 httpx.MockTransport）。
"""

import json

import httpx


class LLMError(Exception):
    def __init__(self, message: str, kind: str):
        super().__init__(message)
        self.kind = kind


class LLMClient:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 60.0, transport=None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._transport = transport

    def chat(self, messages: list, json_mode: bool = False, temperature: float | None = None, max_tokens: int | None = None) -> str:
        url = self.base_url + "/chat/completions"
        payload: dict = {"model": self.model, "messages": messages}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        try:
            with httpx.Client(transport=self._transport, timeout=self.timeout) as client:
                resp = client.post(
                    url,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
        except httpx.TimeoutException:
            raise LLMError("请求超时：模型响应过慢或网络异常", "timeout")
        except httpx.RequestError as e:
            raise LLMError(f"网络错误：{e}", "network")

        if resp.status_code == 401:
            raise LLMError("API Key 无效，请在设置中检查", "auth")
        if resp.status_code >= 400:
            detail = ""
            try:
                err = resp.json()
                err_obj = err.get("error")
                if isinstance(err_obj, dict):
                    detail = err_obj.get("message") or ""
                elif isinstance(err_obj, str):
                    detail = err_obj
                else:
                    detail = err.get("detail") or ""
            except ValueError:
                pass
            hint = f"（{detail}）" if detail else ""
            if resp.status_code == 404:
                raise LLMError(f"接口地址或模型名不存在（404）{hint}", "http")
            raise LLMError(f"模型接口拒绝请求（{resp.status_code}）{hint}", "http")

        try:
            data = resp.json()
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        except (ValueError, IndexError, AttributeError):
            raise LLMError("模型返回格式无法解析", "http")

        content = content.strip()
        if not content:
            raise LLMError("模型未返回内容，请重试", "empty")
        return content

    def chat_stream(self, messages: list, json_mode: bool = False,
                    temperature: float | None = None, max_tokens: int | None = None):
        """生成器：逐块产出文本增量（SSE 解析）。

        - 非 200/网络错误抛 LLMError（在迭代时抛出）
        - 兼容退化：提供商忽略 stream=true 返回普通 JSON 时，整体解析后作为单个块产出
        """
        url = self.base_url + "/chat/completions"
        payload: dict = {"model": self.model, "messages": messages, "stream": True}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        def _parse_non_stream(data):
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
            return content.strip()

        try:
            with httpx.Client(transport=self._transport, timeout=self.timeout) as client:
                with client.stream(
                    "POST", url,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                ) as resp:
                    if resp.status_code == 401:
                        raise LLMError("API Key 无效，请在设置中检查", "auth")
                    if resp.status_code >= 400:
                        detail = ""
                        try:
                            body = resp.json()
                            err_obj = (body or {}).get("error") or {}
                            detail = err_obj.get("message") if isinstance(err_obj, dict) else str(err_obj)
                        except (ValueError, KeyError):
                            pass
                        hint = f"（{detail}）" if detail else ""
                        raise LLMError(f"模型接口拒绝请求（{resp.status_code}）{hint}", "http")
                    emitted = 0
                    raw_lines: list[str] = []
                    for line in resp.iter_lines():
                        raw_lines.append(line)
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            return
                        try:
                            obj = json.loads(data)
                        except ValueError:
                            continue
                        delta = ((obj.get("choices") or [{}])[0].get("delta") or {}).get("content")
                        if delta:
                            emitted += 1
                            yield delta
                    if emitted == 0:
                        # 提供商忽略 stream：整段回退为单块（迭代已消费响应，需用缓冲原文解析）
                        try:
                            text = _parse_non_stream(json.loads("\n".join(raw_lines)))
                        except (ValueError, AttributeError):
                            text = ""
                        if text:
                            yield text
        except httpx.TimeoutException:
            raise LLMError("请求超时：模型响应过慢或网络异常", "timeout")
        except httpx.RequestError as e:
            raise LLMError(f"网络错误：{e}", "network")
