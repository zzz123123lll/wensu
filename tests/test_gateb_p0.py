"""Gate B 回归测试（阶段 A 失败测试，先 RED 后 GREEN）。

覆盖 P0-1（正文变化 Citation 失效）、P0-2（素材 PATCH 500）、P0-3（模型连接测试 500）。

所有测试使用临时数据库（monkeypatch db.DB_PATH），不触碰 data/workbench.db。
"""

import pytest
from fastapi.testclient import TestClient

from app import db, main
from app.llm import LLMError

ORIGIN = "http://127.0.0.1:8766"


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "gateb.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    c = TestClient(main.app, base_url="http://127.0.0.1:8766")
    conn = db.connect()
    db.migrate(conn)
    conn.close()
    # 建立 session cookie（守卫强化后浏览器同源写请求需要它）
    r = c.get("/api/session")
    assert r.status_code == 200
    return c


def auth(client, method, url, **kw):
    """带同源 Origin + session 的请求。"""
    headers = dict(kw.pop("headers", {}))
    headers.setdefault("Origin", ORIGIN)
    return client.request(method, url, headers=headers, **kw)


def _create_project(client, name="p"):
    r = auth(client, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 200
    return r.json()["id"]


def _create_article(client, pid, title="t"):
    r = auth(client, "POST", f"/api/projects/{pid}/articles", json={"title": title})
    assert r.status_code == 200
    return r.json()["id"]


def _setup_supported_citation(client, block_id="b1", text="原始主张"):
    """建项目+草稿+来源+正文+Citation(supported)。返回 (aid, 当前版本)。"""
    pid = _create_project(client)
    aid = _create_article(client, pid)
    conn = main._conn()
    sid = db.create_source(conn, pid, "https://example.com/a", "来源A", "证据原文片段", "web")
    db.save_article(conn, aid, blocks=[
        {"id": block_id, "type": "paragraph", "text": text, "attrs": {}},
    ], base_version=1)
    cid = db.create_citation(conn, aid, block_id, sid, quote="引文")
    db.set_citation_verification(conn, cid, "supported", "手动核验")
    ver = db.get_article(conn, aid)["version"]
    conn.close()
    return aid, ver


def _current_version(aid):
    conn = main._conn()
    try:
        return db.get_article(conn, aid)["version"]
    finally:
        conn.close()


def _citation(aid):
    conn = main._conn()
    try:
        return db.list_citations(conn, aid)[0]
    finally:
        conn.close()


# ==================== P0-1：正文变化后 Citation 失效 ====================

def test_p01_edit_supported_citation_becomes_needs_recheck(client):
    aid, ver = _setup_supported_citation(client)
    r = auth(client, "PUT", f"/api/articles/{aid}", json={
        "blocks": [{"id": "b1", "type": "paragraph", "text": "修改后的主张", "attrs": {}}],
        "base_version": ver, "change_reason": "autosave",
    })
    assert r.status_code == 200
    assert _citation(aid)["verification_status"] == "needs_recheck"


def test_p01_unchanged_save_keeps_supported(client):
    aid, ver = _setup_supported_citation(client)
    r = auth(client, "PUT", f"/api/articles/{aid}", json={
        "blocks": [{"id": "b1", "type": "paragraph", "text": "原始主张", "attrs": {}}],
        "base_version": ver, "change_reason": "autosave",
    })
    assert r.status_code == 200
    assert _citation(aid)["verification_status"] == "supported"


def test_p01_edit_unrelated_block_keeps_other_citation(client):
    aid, ver = _setup_supported_citation(client)
    conn = main._conn()
    sid = db.create_source(conn, 1, "https://example.com/b", "来源B")
    db.create_citation(conn, aid, "b2", sid, quote="引文2")
    conn.close()
    # 保存：b1 不变，新增 b2、b3（b2 被引用，但只改 b3）
    r = auth(client, "PUT", f"/api/articles/{aid}", json={
        "blocks": [
            {"id": "b1", "type": "paragraph", "text": "原始主张", "attrs": {}},
            {"id": "b2", "type": "paragraph", "text": "第二段", "attrs": {}},
            {"id": "b3", "type": "paragraph", "text": "第三段", "attrs": {}},
        ],
        "base_version": ver, "change_reason": "autosave",
    })
    assert r.status_code == 200
    cites = db.list_citations(main._conn(), aid)
    conn = main._conn()
    try:
        cites = db.list_citations(conn, aid)
        assert cites[0]["verification_status"] == "supported"  # b1 未变
    finally:
        conn.close()


def test_p01_delete_referenced_block_orphans_citation(client):
    aid, ver = _setup_supported_citation(client)
    r = auth(client, "PUT", f"/api/articles/{aid}", json={
        "blocks": [],  # b1 被删除
        "base_version": ver, "change_reason": "autosave",
    })
    assert r.status_code == 200
    c = _citation(aid)
    assert c["status"] == "orphaned"


def test_p01_multi_citation_selective_invalidation(client):
    """多个 Citation 分别绑定多个 Block：只失效被改的。"""
    pid = _create_project(client)
    aid = _create_article(client, pid)
    conn = main._conn()
    sid = db.create_source(conn, pid, "https://example.com/c", "来源C")
    db.save_article(conn, aid, blocks=[
        {"id": "b1", "type": "paragraph", "text": "主张一", "attrs": {}},
        {"id": "b2", "type": "paragraph", "text": "主张二", "attrs": {}},
    ], base_version=1)
    c1 = db.create_citation(conn, aid, "b1", sid, quote="引1")
    c2 = db.create_citation(conn, aid, "b2", sid, quote="引2")
    db.set_citation_verification(conn, c1, "supported")
    db.set_citation_verification(conn, c2, "supported")
    ver = db.get_article(conn, aid)["version"]
    conn.close()
    # 只改 b1
    r = auth(client, "PUT", f"/api/articles/{aid}", json={
        "blocks": [
            {"id": "b1", "type": "paragraph", "text": "主张一（改）", "attrs": {}},
            {"id": "b2", "type": "paragraph", "text": "主张二", "attrs": {}},
        ],
        "base_version": ver, "change_reason": "autosave",
    })
    assert r.status_code == 200
    conn = main._conn()
    try:
        by_id = {c["block_id"]: c["verification_status"] for c in db.list_citations(conn, aid)}
        assert by_id["b1"] == "needs_recheck"
        assert by_id["b2"] == "supported"
    finally:
        conn.close()


# ==================== P0-2：素材标签编辑（PATCH） ====================

def _create_material(client, pid, title="素材", content="内容", tags=None):
    r = auth(client, "POST", f"/api/projects/{pid}/materials",
             json={"title": title, "content": content, "tags": tags or []})
    assert r.status_code == 200
    return r.json()["id"]


def test_p02_patch_material_tags_ok(client):
    pid = _create_project(client)
    mid = _create_material(client, pid, tags=["旧"])
    r = auth(client, "PATCH", f"/api/materials/{mid}",
             json={"title": "素材", "content": "内容", "tags": ["新标签1", "新标签2"]})
    assert r.status_code == 200
    # 刷新后仍在
    r = auth(client, "GET", f"/api/materials/{mid}")
    assert r.status_code == 200
    assert r.json()["material"]["tags"] == ["新标签1", "新标签2"]


def test_p02_patch_material_cleans_tags(client):
    pid = _create_project(client)
    mid = _create_material(client, pid)
    r = auth(client, "PATCH", f"/api/materials/{mid}",
             json={"title": "素材", "content": "内容", "tags": [" 甲 ", "乙", "", " 甲 ", "丙"]})
    assert r.status_code == 200
    r = auth(client, "GET", f"/api/materials/{mid}")
    assert r.json()["material"]["tags"] == ["甲", "乙", "丙"]


def test_p02_patch_material_overlong_tag_422(client):
    pid = _create_project(client)
    mid = _create_material(client, pid)
    r = auth(client, "PATCH", f"/api/materials/{mid}",
             json={"title": "素材", "content": "内容", "tags": ["x" * 40]})
    assert r.status_code == 422


def test_p02_patch_material_too_many_tags_422(client):
    pid = _create_project(client)
    mid = _create_material(client, pid)
    r = auth(client, "PATCH", f"/api/materials/{mid}",
             json={"title": "素材", "content": "内容", "tags": [f"t{i}" for i in range(21)]})
    assert r.status_code == 422


def test_p02_patch_material_not_found_404(client):
    r = auth(client, "PATCH", "/api/materials/99999",
             json={"title": "x", "content": "y", "tags": []})
    assert r.status_code == 404


def test_p02_patch_material_invalid_body_422(client):
    pid = _create_project(client)
    mid = _create_material(client, pid)
    r = auth(client, "PATCH", f"/api/materials/{mid}", json={"title": "", "content": ""})
    assert r.status_code == 422


def test_p02_patch_material_malicious_origin_403(client):
    pid = _create_project(client)
    mid = _create_material(client, pid)
    r = client.request("PATCH", f"/api/materials/{mid}",
                       headers={"Origin": "http://evil.example.com"},
                       json={"title": "x", "content": "y", "tags": []})
    assert r.status_code == 403


# ==================== P0-3：模型连接测试 ====================

class _FakeLLMClient:
    """传输层替身：可配置成功/异常。"""

    def __init__(self, base_url, api_key, model, **kw):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.kw = kw

    def chat(self, messages, **kw):
        if getattr(_FakeLLMClient, "error", None):
            raise _FakeLLMClient.error
        return "pong"


def _create_profile(client, name="模型", with_key=True):
    body = {"name": name, "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat",
            "capabilities": "json_mode,stream"}
    if with_key:
        body["api_key"] = "sk-test-1234567890"
    r = auth(client, "POST", "/api/profiles", json=body)
    assert r.status_code == 200
    return r.json()["id"]


@pytest.fixture
def fake_llm(monkeypatch):
    _FakeLLMClient.error = None
    # raising=False：修复前 main 根本没有 LLMClient 属性（这正是 P0-3 根因）
    monkeypatch.setattr(main, "LLMClient", _FakeLLMClient, raising=False)
    return _FakeLLMClient


def test_p03_profile_test_success(client, fake_llm):
    pid = _create_profile(client)
    r = auth(client, "POST", f"/api/profiles/{pid}/test")
    assert r.status_code == 200
    assert r.json().get("ok") is True


def test_p03_profile_test_not_found_404(client, fake_llm):
    r = auth(client, "POST", "/api/profiles/99999/test")
    assert r.status_code == 404


def test_p03_profile_test_missing_key_400(client, fake_llm):
    pid = _create_profile(client, with_key=False)
    r = auth(client, "POST", f"/api/profiles/{pid}/test")
    assert r.status_code == 400


def test_p03_profile_test_timeout(client, fake_llm):
    pid = _create_profile(client)
    fake_llm.error = LLMError("请求超时", "timeout")
    r = auth(client, "POST", f"/api/profiles/{pid}/test")
    assert r.status_code in (502, 504)


def test_p03_profile_test_auth_error(client, fake_llm):
    pid = _create_profile(client)
    fake_llm.error = LLMError("API Key 无效", "auth")
    r = auth(client, "POST", f"/api/profiles/{pid}/test")
    assert r.status_code in (401, 502)


def test_p03_profile_test_response_no_api_key(client, fake_llm):
    pid = _create_profile(client)
    r = auth(client, "POST", f"/api/profiles/{pid}/test")
    assert r.status_code == 200
    assert "sk-test-1234567890" not in r.text
    assert "sk-" not in r.text
