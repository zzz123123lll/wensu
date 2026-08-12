"""AI API 路由测试：未配置拦截 + 已配置成功路径（mock ai_service）。"""

from fastapi.testclient import TestClient

from app import ai_service, db, main
from app.llm import LLMError


def _client(tmp_path):
    db.DB_PATH = str(tmp_path / "ai.db")
    return TestClient(main.app)


def test_ask_requires_config(tmp_path):
    c = _client(tmp_path)
    r = c.post("/api/ai/ask", json={"prompt": "hi", "context": "ctx"})
    assert r.status_code == 400
    assert "设置" in r.json()["detail"]


def test_ask_success(tmp_path, monkeypatch):
    c = _client(tmp_path)
    monkeypatch.setattr(ai_service, "ask", lambda conn, p, ctx: "真回答")
    r = c.post("/api/ai/ask", json={"prompt": "怎么改", "context": "正文"})
    assert r.status_code == 200
    assert r.json()["reply"] == "真回答"


def test_rewrite_success(tmp_path, monkeypatch):
    c = _client(tmp_path)
    monkeypatch.setattr(ai_service, "rewrite", lambda conn, t: [{"label": "方案一", "text": "改"}])
    r = c.post("/api/ai/rewrite", json={"text": "原文"})
    assert r.status_code == 200
    assert r.json()["candidates"][0]["text"] == "改"


def test_insight_success(tmp_path, monkeypatch):
    c = _client(tmp_path)
    monkeypatch.setattr(ai_service, "insight", lambda conn, t, b: {
        "insight": {"summary": "s", "gap": "g"}, "suggestions": []})
    r = c.post("/api/ai/insight", json={"title": "t", "blocks": [{"text": "b"}]})
    assert r.status_code == 200
    assert r.json()["insight"]["summary"] == "s"


def test_llm_error_maps_to_502(tmp_path, monkeypatch):
    c = _client(tmp_path)
    def boom(conn, p, ctx):
        raise LLMError("接口错误", "http")
    monkeypatch.setattr(ai_service, "ask", boom)
    r = c.post("/api/ai/ask", json={"prompt": "p", "context": "c"})
    assert r.status_code == 502


# ---------- search / check 端点 ----------

def test_search_success(tmp_path, monkeypatch):
    c = _client(tmp_path)
    monkeypatch.setattr(ai_service, "search", lambda conn, q: [{"title": "T", "url": "u", "snippet": "s"}])
    r = c.post("/api/ai/search", json={"query": "写作"})
    assert r.status_code == 200
    assert r.json()["results"][0]["title"] == "T"


def test_search_empty_query_400(tmp_path):
    c = _client(tmp_path)
    r = c.post("/api/ai/search", json={"query": "   "})
    assert r.status_code == 400


def test_check_success(tmp_path, monkeypatch):
    c = _client(tmp_path)
    monkeypatch.setattr(ai_service, "check", lambda conn, claim: {"status": "ok", "reason": "r", "suggestion": ""})
    r = c.post("/api/ai/check", json={"claim": "地球是圆的"})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_check_empty_claim_400(tmp_path):
    c = _client(tmp_path)
    r = c.post("/api/ai/check", json={"claim": ""})
    assert r.status_code == 400
