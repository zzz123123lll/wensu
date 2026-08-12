"""AI API 路由测试：未配置拦截 + 已配置成功路径（mock ai_service）。"""

from fastapi.testclient import TestClient

from app import ai_service, db, main
from app.llm import LLMError


def _client(tmp_path):
    db.DB_PATH = str(tmp_path / "ai.db")
    return TestClient(main.app, base_url="http://127.0.0.1:8766")


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


# ---------- 锚点回显 ----------

def test_rewrite_echoes_anchor(tmp_path, monkeypatch):
    c = _client(tmp_path)
    monkeypatch.setattr(ai_service, "rewrite", lambda conn, t: [{"label": "方案一", "text": "改"}])
    sel = {"text": "选中文字", "start_utf16": 3, "end_utf16": 7}
    r = c.post("/api/ai/rewrite", json={
        "text": "原文", "article_id": 7, "target_block_id": "b1", "selection": sel,
    })
    assert r.status_code == 200
    a = r.json()["anchor"]
    assert a["article_id"] == 7
    assert a["target_block_id"] == "b1"
    assert a["selection"] == sel


def test_search_echoes_anchor(tmp_path, monkeypatch):
    c = _client(tmp_path)
    monkeypatch.setattr(ai_service, "search", lambda conn, q: [])
    sel = {"text": "x", "start_utf16": 0, "end_utf16": 1}
    r = c.post("/api/ai/search", json={"query": "q", "article_id": 3, "selection": sel})
    assert r.status_code == 200
    assert r.json()["anchor"]["selection"] == sel
    assert r.json()["anchor"]["article_id"] == 3


def test_check_echoes_anchor(tmp_path, monkeypatch):
    c = _client(tmp_path)
    monkeypatch.setattr(ai_service, "check", lambda conn, claim: {"status": "ok", "reason": "r", "suggestion": ""})
    r = c.post("/api/ai/check", json={"claim": "c", "target_block_id": "b9"})
    assert r.status_code == 200
    assert r.json()["anchor"]["target_block_id"] == "b9"


def test_anchor_optional_no_anchor_in_response(tmp_path, monkeypatch):
    c = _client(tmp_path)
    monkeypatch.setattr(ai_service, "rewrite", lambda conn, t: [{"label": "方案一", "text": "改"}])
    r = c.post("/api/ai/rewrite", json={"text": "原文"})
    assert r.status_code == 200
    assert r.json()["anchor"]["article_id"] is None
