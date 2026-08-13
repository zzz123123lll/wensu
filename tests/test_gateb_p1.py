"""Gate B P1 回归测试（阶段 A 失败测试）。

P1-1：标题层级往返；P1-3：Verification 状态受控；P1-6：安全守卫覆盖 PATCH/session。
临时数据库，不触碰 data/workbench.db。
"""

import pytest
from fastapi.testclient import TestClient

from app import db, main

ORIGIN = "http://127.0.0.1:8766"


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "gateb_p1.db"
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


def _project(client):
    r = auth(client, "POST", "/api/projects", json={"name": "p"})
    return r.json()["id"]


def _article(client, pid):
    r = auth(client, "POST", f"/api/projects/{pid}/articles", json={"title": "t"})
    return r.json()["id"]


# ==================== P1-1：Block 类型完整往返 ====================

def test_p11_heading_levels_roundtrip(client):
    """H1~H4 保存→读取→再保存，层级不变（不降级为 paragraph）。"""
    pid = _project(client)
    aid = _article(client, pid)
    blocks = [
        {"id": "b1", "type": "heading", "text": "一级标题", "attrs": {}},
        {"id": "b2", "type": "heading2", "text": "二级标题", "attrs": {}},
        {"id": "b3", "type": "heading3", "text": "三级标题", "attrs": {}},
        {"id": "b4", "type": "heading4", "text": "四级标题", "attrs": {}},
    ]
    r = auth(client, "PUT", f"/api/articles/{aid}", json={
        "blocks": blocks, "base_version": 1, "change_reason": "autosave",
    })
    assert r.status_code == 200
    r = auth(client, "GET", f"/api/articles/{aid}")
    got = {b["id"]: b["type"] for b in r.json()["blocks"]}
    assert got == {"b1": "heading", "b2": "heading2", "b3": "heading3", "b4": "heading4"}
    # 再保存一次（模拟编辑后自动保存）仍不变
    ver = r.json()["version"]
    r = auth(client, "PUT", f"/api/articles/{aid}", json={
        "blocks": blocks, "base_version": ver, "change_reason": "autosave",
    })
    assert r.status_code == 200
    r = auth(client, "GET", f"/api/articles/{aid}")
    got = {b["id"]: b["type"] for b in r.json()["blocks"]}
    assert got == {"b1": "heading", "b2": "heading2", "b3": "heading3", "b4": "heading4"}


def test_p11_declared_block_types_roundtrip(client):
    """Schema 声明的 block 类型保存/读取不降级。"""
    pid = _project(client)
    aid = _article(client, pid)
    blocks = [
        {"id": "b1", "type": "paragraph", "text": "段落", "attrs": {}},
        {"id": "b2", "type": "blockquote", "text": "引用", "attrs": {}},
        {"id": "b3", "type": "unordered_list", "text": "项目一\n项目二", "attrs": {}},
        {"id": "b4", "type": "ordered_list", "text": "1. 甲\n2. 乙", "attrs": {}},
        {"id": "b5", "type": "code", "text": "print(1)", "attrs": {}},
        {"id": "b6", "type": "divider", "text": "", "attrs": {}},
    ]
    r = auth(client, "PUT", f"/api/articles/{aid}", json={
        "blocks": blocks, "base_version": 1, "change_reason": "autosave",
    })
    assert r.status_code == 200
    r = auth(client, "GET", f"/api/articles/{aid}")
    got = {b["id"]: b["type"] for b in r.json()["blocks"]}
    assert got == {b["id"]: b["type"] for b in blocks}


# ==================== P1-3：Verification 状态受控 ====================

def test_p13_invalid_verification_status_422(client):
    pid = _project(client)
    aid = _article(client, pid)
    conn = main._conn()
    sid = db.create_source(conn, pid, "https://example.com/x", "来源")
    db.save_article(conn, aid, blocks=[{"id": "b1", "type": "paragraph", "text": "主张", "attrs": {}}], base_version=1)
    cid = db.create_citation(conn, aid, "b1", sid, quote="引")
    conn.close()
    r = auth(client, "POST", f"/api/citations/{cid}/verification", json={"status": "totally_bogus"})
    assert r.status_code == 422


def test_p13_valid_verification_status_ok(client):
    pid = _project(client)
    aid = _article(client, pid)
    conn = main._conn()
    sid = db.create_source(conn, pid, "https://example.com/x", "来源")
    db.save_article(conn, aid, blocks=[{"id": "b1", "type": "paragraph", "text": "主张", "attrs": {}}], base_version=1)
    cid = db.create_citation(conn, aid, "b1", sid, quote="引")
    conn.close()
    r = auth(client, "POST", f"/api/citations/{cid}/verification", json={"status": "supported", "note": "核验"})
    assert r.status_code == 200


# ==================== P1-6：安全守卫 ====================

def test_p16_patch_without_session_403(client):
    """PATCH 也是状态改变方法：缺失 session 必须 403（现在 PATCH 完全绕过守卫）。"""
    c2 = TestClient(main.app, base_url="http://127.0.0.1:8766")  # 未建立 session
    # 显式空 token 覆盖 conftest 自动补头
    r = c2.request("PATCH", "/api/materials/1", headers={"Origin": ORIGIN, "X-Wensu-Token": ""},
                   json={"title": "x", "content": "y", "tags": []})
    assert r.status_code == 403


def test_p16_write_without_origin_403(client):
    """写请求缺失 Origin 必须拒绝（浏览器总是带 Origin；无 Origin 的匿名写不允许）。"""
    c2 = TestClient(main.app, base_url="http://127.0.0.1:8766")
    c2.get("/api/session")
    r = c2.post("/api/projects", json={"name": "no-origin"}, headers={"Origin": ""})
    assert r.status_code == 403


def test_p16_write_missing_session_403(client):
    """有 Origin 但无 session cookie 的写请求必须 403。"""
    c2 = TestClient(main.app, base_url="http://127.0.0.1:8766")
    r = c2.post("/api/projects", json={"name": "x"}, headers={"Origin": ORIGIN, "X-Wensu-Token": ""})
    assert r.status_code == 403


def test_p16_browser_session_write_ok(client):
    """浏览器同源 + 有效 session 的写请求正常。"""
    r = auth(client, "POST", "/api/projects", json={"name": "ok"})
    assert r.status_code == 200


def test_p16_static_and_health_unaffected(client):
    r = client.get("/")
    assert r.status_code == 200
    r = client.get("/api/session")
    assert r.status_code == 200
