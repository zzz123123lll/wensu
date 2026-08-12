"""LLM 客户端测试：httpx MockTransport 模拟 OpenAI 兼容端点，不真调外部 API。"""

import json

import httpx
import pytest

from app.llm import LLMClient, LLMError


def make_transport(handler):
    return httpx.MockTransport(handler)


def json_response(data, status=200):
    return httpx.Response(status, json=data)


def test_chat_normal_reply():
    calls = {}

    def handler(request):
        calls["url"] = str(request.url)
        calls["auth"] = request.headers.get("Authorization")
        body = json.loads(request.content)
        calls["model"] = body["model"]
        calls["messages"] = body["messages"]
        return json_response({"choices": [{"message": {"content": "你好"}}]})

    c = LLMClient("https://api.deepseek.com/v1", "sk-1", "deepseek-chat", transport=make_transport(handler))
    out = c.chat([{"role": "user", "content": "hi"}])
    assert out == "你好"
    assert calls["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert calls["auth"] == "Bearer sk-1"
    assert calls["model"] == "deepseek-chat"
    assert calls["messages"][0]["content"] == "hi"


def test_chat_json_mode_sends_response_format():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return json_response({"choices": [{"message": {"content": '{"a":1}'}}]})

    c = LLMClient("https://api.test/v1", "k", "m", transport=make_transport(handler))
    out = c.chat([{"role": "user", "content": "json"}], json_mode=True)
    assert json.loads(out) == {"a": 1}
    assert seen["body"]["response_format"] == {"type": "json_object"}


def test_chat_401_maps_to_auth_error():
    def handler(request):
        return json_response({"error": "invalid key"}, status=401)

    c = LLMClient("https://api.test/v1", "bad", "m", transport=make_transport(handler))
    with pytest.raises(LLMError) as ei:
        c.chat([{"role": "user", "content": "x"}])
    assert ei.value.kind == "auth"


def test_chat_empty_reply_raises():
    def handler(request):
        return json_response({"choices": [{"message": {"content": "   "}}]})

    c = LLMClient("https://api.test/v1", "k", "m", transport=make_transport(handler))
    with pytest.raises(LLMError) as ei:
        c.chat([{"role": "user", "content": "x"}])
    assert ei.value.kind == "empty"


def test_chat_timeout_maps():
    def handler(request):
        raise httpx.ReadTimeout("boom")

    c = LLMClient("https://api.test/v1", "k", "m", transport=make_transport(handler))
    with pytest.raises(LLMError) as ei:
        c.chat([{"role": "user", "content": "x"}])
    assert ei.value.kind == "timeout"


def test_chat_http_500_maps():
    def handler(request):
        return json_response({"error": "server"}, status=500)

    c = LLMClient("https://api.test/v1", "k", "m", transport=make_transport(handler))
    with pytest.raises(LLMError) as ei:
        c.chat([{"role": "user", "content": "x"}])
    assert ei.value.kind == "http"
