"""P1-5 失败测试：继续写位置保存/恢复。

临时数据库，不触碰 data/workbench.db。
"""

import pytest
from fastapi.testclient import TestClient

from app import db, main

ORIGIN = "http://127.0.0.1:8766"


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "gateb_pos.db"
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


def _mk(client, blocks=True):
    pid = auth(client, "POST", "/api/projects", json={"name": "p"}).json()["id"]
    aid = auth(client, "POST", f"/api/projects/{pid}/articles", json={"title": "长文"}).json()["id"]
    if blocks:
        conn = main._conn()
        db.save_article(conn, aid, blocks=[
            {"id": "b1", "type": "paragraph", "text": "第一段", "attrs": {}},
            {"id": "b2", "type": "heading2", "text": "第二节", "attrs": {}},
            {"id": "b3", "type": "paragraph", "text": "第三段内容", "attrs": {}},
        ], base_version=1)
        conn.close()
    return pid, aid


def test_p15_save_and_get_position(client):
    _, aid = _mk(client)
    r = auth(client, "PUT", f"/api/articles/{aid}/position",
             json={"block_id": "b2", "offset": 3, "scroll_top": 240})
    assert r.status_code == 200
    r = auth(client, "GET", f"/api/articles/{aid}/continue")
    assert r.status_code == 200
    pos = r.json()["position"]
    assert pos["block_id"] == "b2"
    assert pos["offset"] == 3
    assert pos["scroll_top"] == 240
    assert r.json()["next_step"]  # 一句可解释的"下一步"


def test_p15_position_does_not_change_blocks(client):
    _, aid = _mk(client)
    conn = main._conn()
    before = db.get_article(conn, aid)["blocks"]
    conn.close()
    r = auth(client, "PUT", f"/api/articles/{aid}/position",
             json={"block_id": "b1", "offset": 1, "scroll_top": 100})
    assert r.status_code == 200
    conn = main._conn()
    after = db.get_article(conn, aid)["blocks"]
    conn.close()
    assert after == before
    # 版本不递增
    conn = main._conn()
    ver_before = db.get_article(conn, aid)["version"]
    conn.close()
    assert ver_before == 2  # 建文 v1 + 正文保存 v2，position 不 bump


def test_p15_invalid_block_position_falls_back(client):
    """保存的位置指向已删除 block → continue 安全回退（不返回失效位置）。"""
    _, aid = _mk(client)
    auth(client, "PUT", f"/api/articles/{aid}/position",
         json={"block_id": "ghost-block", "offset": 5, "scroll_top": 1})
    r = auth(client, "GET", f"/api/articles/{aid}/continue")
    assert r.status_code == 200
    assert r.json()["position"] == {}  # 失效位置不返回，前端回退最近块


def test_p15_continue_missing_article_404(client):
    r = auth(client, "GET", "/api/articles/99999/continue")
    assert r.status_code == 404


def test_p15_position_missing_article_404(client):
    r = auth(client, "PUT", "/api/articles/99999/position",
             json={"block_id": "b1", "offset": 0, "scroll_top": 0})
    assert r.status_code == 404


def test_p15_needs_recheck_count(client):
    """待复查 Citation 计数出现在 continue 响应。"""
    pid, aid = _mk(client)
    conn = main._conn()
    sid = db.create_source(conn, pid, "https://example.com/x", "来源")
    cid = db.create_citation(conn, aid, "b1", sid, quote="引")
    db.set_citation_verification(conn, cid, "needs_recheck")
    conn.close()
    r = auth(client, "GET", f"/api/articles/{aid}/continue")
    assert r.json()["needs_recheck"] == 1
    assert "复查" in r.json()["next_step"]
