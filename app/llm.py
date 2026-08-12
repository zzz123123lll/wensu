"""统一 LLM 客户端：OpenAI 兼容 chat/completions 协议。

支持任意兼容端点（DeepSeek / OpenAI / 通义 / Kimi / 自定义）。
错误统一映射为 LLMError(kind)：auth / timeout / network / http / empty。
transport 可注入（测试用 httpx.MockTransport）。
"""

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
