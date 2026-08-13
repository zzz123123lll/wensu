"""P1-4 失败测试：Ask 历史再利用（删除/重新提问/存素材/插入的 API 支撑）。"""

import pytest
from fastapi.testclient import TestClient

from app import db, main

ORIGIN = "http://127.0.0.1:8766"


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "gateb_ask.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    c = TestClient(main.app, base_url="http://127.0.0.1:8766")
    conn = db.connect()
    db.migrate(conn)
    conn.close()
    c.get("/api/session")
    return c


def auth(client, method, url, **kw):
    headers = dict(kw.pop("headers", {}))
    headers.setdefault("Origin", ORIGIN)
    return client.request(method, url, headers=headers, **kw)


def test_p14_ask_delete(client):
    pid = auth(client, "POST", "/api/projects", json={"name": "p"}).json()["id"]
    aid = auth(client, "POST", f"/api/projects/{pid}/articles", json={"title": "t"}).json()["id"]
    conn = main._conn()
    ask_id = db.record_ask(conn, aid, "问题", "回答", "deepseek-x")
    conn.close()
    r = auth(client, "DELETE", f"/api/asks/{ask_id}")
    assert r.status_code == 200
    conn = main._conn()
    assert db.get_ask(conn, ask_id) is None
    conn.close()


def test_p14_ask_delete_missing_404(client):
    r = auth(client, "DELETE", "/api/asks/99999")
    assert r.status_code == 404


def test_p14_ask_history_has_metadata_for_reuse(client):
    """历史条目含模型/时间/使用状态，且可重新提问（prompt 保留）与插入正文。"""
    pid = auth(client, "POST", "/api/projects", json={"name": "p"}).json()["id"]
    aid = auth(client, "POST", f"/api/projects/{pid}/articles", json={"title": "t"}).json()["id"]
    conn = main._conn()
    ask_id = db.record_ask(conn, aid, "可复用的提问", "可复用的回答", "e2e-model")
    db.set_ask_usage(conn, ask_id, "inserted_to_body")
    conn.close()
    r = auth(client, "GET", f"/api/articles/{aid}/asks")
    assert r.status_code == 200
    h = r.json()["asks"][0]
    assert h["prompt"] == "可复用的提问"
    assert h["response"] == "可复用的回答"
    assert h["model"] == "e2e-model"
    assert h["metadata"]["usage"] == "inserted_to_body"
    assert h["created_at"]
    # 插入正文（历史 → ask_insert revision）
    r = auth(client, "PUT", f"/api/articles/{aid}", json={
        "blocks": [{"id": "b1", "type": "paragraph", "text": "可复用的回答", "attrs": {}}],
        "base_version": 1, "change_reason": "ask_insert",
        "source_object_type": "ask", "source_object_id": str(ask_id),
    })
    assert r.status_code == 200
    revs = db.list_revisions(main._conn(), aid)
    assert any(x["reason"] == "ask_insert" and x["source_object_id"] == str(ask_id) for x in revs)
