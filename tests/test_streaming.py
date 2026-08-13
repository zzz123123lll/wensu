"""P1-④ 流式测试：llm.chat_stream（SSE/回退）+ ai_service.ask_stream/rewrite_stream + API 端点。"""

import json

import pytest

from app import ai_service, db, llm
from app.llm import LLMError


class FakeStreamClient:
    def __init__(self, chunks=None, error=None):
        self.chunks = list(chunks or [])
        self.error = error
        self.calls = []

    def chat_stream(self, messages, json_mode=False, **kwargs):
        self.calls.append((messages, json_mode))
        yield from self.chunks
        if self.error:
            raise self.error


# ---------- llm.chat_stream ----------

def _sse(*chunks):
    lines = []
    for c in chunks:
        lines.append("data: " + json.dumps({"choices": [{"delta": {"content": c}}]}, ensure_ascii=False))
        lines.append("")
    lines.append("data: [DONE]")
    lines.append("")
    return "\n".join(lines).encode("utf-8")


def test_chat_stream_yields_sse_chunks():
    import httpx
    body = _sse("你好", "，", "文序")

    def handler(request):
        return httpx.Response(200, content=body)

    client = llm.LLMClient("http://x/v1", "k", "m", transport=httpx.MockTransport(handler))
    out = list(client.chat_stream([{"role": "user", "content": "hi"}]))
    assert out == ["你好", "，", "文序"]


def test_chat_stream_non_sse_falls_back_single_chunk():
    import httpx
    body = json.dumps({"choices": [{"message": {"content": "整段回答"}}]}, ensure_ascii=False).encode("utf-8")

    def handler(request):
        return httpx.Response(200, content=body)

    client = llm.LLMClient("http://x/v1", "k", "m", transport=httpx.MockTransport(handler))
    assert list(client.chat_stream([{"role": "user", "content": "hi"}])) == ["整段回答"]


def test_chat_stream_401_raises_auth():
    import httpx

    def handler(request):
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    client = llm.LLMClient("http://x/v1", "k", "m", transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError) as ei:
        list(client.chat_stream([{"role": "user", "content": "hi"}]))
    assert ei.value.kind == "auth"


# ---------- ai_service.ask_stream / rewrite_stream ----------

def test_ask_stream_events_and_history(monkeypatch):
    c = db.connect(":memory:")
    db.migrate(c)
    pid = db.create_project(c, "p")
    aid = db.create_article(c, pid, "t")
    fake = FakeStreamClient(["答案", "第二段"])
    monkeypatch.setattr(ai_service, "_require_client", lambda conn, task="ask": fake)
    monkeypatch.setattr(ai_service, "model_name_for", lambda conn, task: "deepseek-x")
    events = list(ai_service.ask_stream(c, "问", "上下文", article_id=aid))
    assert [e["type"] for e in events] == ["token", "token", "result"]
    assert events[-1]["reply"] == "答案第二段"
    assert events[-1]["model"] == "deepseek-x"
    assert events[-1]["ask_id"]
    rows = db.list_asks(c, aid, 5)
    assert len(rows) == 1 and rows[0]["response"] == "答案第二段"


def test_ask_stream_empty_reply_error_event(monkeypatch):
    fake = FakeStreamClient([])
    monkeypatch.setattr(ai_service, "_require_client", lambda conn, task="ask": fake)
    events = list(ai_service.ask_stream(None, "问", "ctx"))
    assert events[-1]["type"] == "error"
    assert "未返回内容" in events[-1]["message"]


def test_ask_stream_llm_error_event(monkeypatch):
    fake = FakeStreamClient(["半截"], error=LLMError("接口错误", "http"))
    monkeypatch.setattr(ai_service, "_require_client", lambda conn, task="ask": fake)
    events = list(ai_service.ask_stream(None, "问", "ctx"))
    assert events[0] == {"type": "token", "text": "半截"}
    assert events[-1]["type"] == "error"


def test_rewrite_stream_parses_candidates(monkeypatch):
    raw = '{"candidates": [{"label": "方案一", "text": "改后文字"}]}'
    fake = FakeStreamClient([raw[:10], raw[10:]])
    monkeypatch.setattr(ai_service, "_require_client", lambda conn, task="rewrite": fake)
    events = list(ai_service.rewrite_stream(None, "原文"))
    assert events[0]["type"] == "token"
    assert events[-1]["type"] == "result"
    assert events[-1]["candidates"][0]["text"] == "改后文字"


def test_rewrite_stream_llm_error_event(monkeypatch):
    fake = FakeStreamClient(error=LLMError("超时", "timeout"))
    monkeypatch.setattr(ai_service, "_require_client", lambda conn, task="rewrite": fake)
    events = list(ai_service.rewrite_stream(None, "原文"))
    assert events[-1]["type"] == "error"


# ---------- API 端点 ----------

@pytest.fixture(autouse=True)
def _fake_stream_client(monkeypatch):
    fake = FakeStreamClient(["流", "式", "回答"])
    monkeypatch.setattr(ai_service, "_require_client", lambda conn, task="ask": fake)
    monkeypatch.setattr(ai_service, "model_name_for", lambda conn, task: "deepseek-x")
    return fake


def test_api_ask_stream_ndjson(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    conn = db.connect()
    db.migrate(conn)
    pid = db.create_project(conn, "p")
    aid = db.create_article(conn, pid, "t")
    conn.close()
    client = TestClient(main.app, base_url="http://127.0.0.1:8766")
    r = client.post("/api/ai/ask/stream", json={"prompt": "q", "context": "c", "article_id": aid})
    assert r.status_code == 200
    lines = [json.loads(x) for x in r.text.strip().split("\n") if x.strip()]
    types = [e["type"] for e in lines]
    assert types == ["token", "token", "token", "result"]
    assert lines[-1]["reply"] == "流式回答"
    assert lines[-1]["ask_id"]


def test_api_rewrite_stream_ndjson(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    fake = FakeStreamClient(['{"candidates": [{"label": "方案一", "text": "改"}]}'])
    monkeypatch.setattr(ai_service, "_require_client", lambda conn, task="rewrite": fake)
    client = TestClient(main.app, base_url="http://127.0.0.1:8766")
    r = client.post("/api/ai/rewrite/stream", json={"text": "原文", "flavor": "de-ai"})
    assert r.status_code == 200
    lines = [json.loads(x) for x in r.text.strip().split("\n") if x.strip()]
    assert lines[-1]["type"] == "result"
    assert lines[-1]["candidates"][0]["text"] == "改"


def test_api_rewrite_stream_bad_flavor_400(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    client = TestClient(main.app, base_url="http://127.0.0.1:8766")
    r = client.post("/api/ai/rewrite/stream", json={"text": "x", "flavor": "evil"})
    assert r.status_code == 400
