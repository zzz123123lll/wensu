"""P0-4（统一 Revision 管道）+ P0-1（事务完整性）失败测试。

临时数据库，不触碰 data/workbench.db。
"""

import pytest
from fastapi.testclient import TestClient

from app import db, main

ORIGIN = "http://127.0.0.1:8766"


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "gateb_rev.db"
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


def _mk(client):
    pid_r = auth(client, "POST", "/api/projects", json={"name": "p"})
    pid = pid_r.json()["id"]
    aid_r = auth(client, "POST", f"/api/projects/{pid}/articles", json={"title": "t"})
    aid = aid_r.json()["id"]
    mid_r = auth(client, "POST", f"/api/projects/{pid}/materials",
                 json={"title": "素材", "content": "素材内容", "tags": []})
    return pid, aid, mid_r.json()["id"]


def _put(client, aid, blocks, base_version, reason, **extra):
    body = {"blocks": blocks, "base_version": base_version, "change_reason": reason}
    body.update(extra)
    return auth(client, "PUT", f"/api/articles/{aid}", json=body)


def _revs(aid):
    conn = main._conn()
    try:
        return db.list_revisions(conn, aid)
    finally:
        conn.close()


def _blocks(aid):
    conn = main._conn()
    try:
        return db.get_article(conn, aid)["blocks"]
    finally:
        conn.close()


# ==================== P0-4：素材/Ask 插入进入 Revision 管道 ====================

def test_p04_material_insert_creates_revision(client):
    pid, aid, mid = _mk(client)
    r = _put(client, aid, [{"id": "b1", "type": "paragraph", "text": "素材内容", "attrs": {}}],
             1, "material_insert", source_object_type="material", source_object_id=str(mid))
    assert r.status_code == 200
    revs = _revs(aid)
    assert len(revs) == 1
    assert revs[0]["reason"] == "material_insert"
    assert revs[0]["source_object_type"] == "material"
    assert revs[0]["source_object_id"] == str(mid)
    assert revs[0]["before_blocks"] == []
    assert revs[0]["after_blocks"][0]["text"] == "素材内容"
    # 使用关系已记录（同事务）
    conn = main._conn()
    usages = db.list_material_usages(conn, mid)
    conn.close()
    assert len(usages) == 1 and usages[0]["article_id"] == aid


def test_p04_ask_insert_creates_revision(client):
    pid, aid, _ = _mk(client)
    r = _put(client, aid, [{"id": "b1", "type": "paragraph", "text": "Ask 回答", "attrs": {}}],
             1, "ask_insert", source_object_type="ask", source_object_id="7")
    assert r.status_code == 200
    revs = _revs(aid)
    assert len(revs) == 1
    assert revs[0]["reason"] == "ask_insert"
    assert revs[0]["source_object_type"] == "ask"
    assert revs[0]["source_object_id"] == "7"


def test_p04_revision_before_after_correct(client):
    pid, aid, _ = _mk(client)
    r = _put(client, aid, [{"id": "b1", "type": "paragraph", "text": "原文", "attrs": {}}],
             1, "autosave")
    assert r.status_code == 200
    ver = r.json()["version"]
    r = _put(client, aid, [{"id": "b1", "type": "paragraph", "text": "原文+插入内容", "attrs": {}}],
             ver, "material_insert", source_object_type="material", source_object_id="1")
    assert r.status_code == 200
    revs = _revs(aid)
    assert len(revs) == 1
    assert revs[0]["before_blocks"][0]["text"] == "原文"
    assert revs[0]["after_blocks"][0]["text"] == "原文+插入内容"


def test_p04_autosave_no_revision(client):
    pid, aid, _ = _mk(client)
    r = _put(client, aid, [{"id": "b1", "type": "paragraph", "text": "普通保存", "attrs": {}}],
             1, "autosave")
    assert r.status_code == 200
    assert _revs(aid) == []


def test_p04_refresh_keeps_blocks_and_revision(client):
    pid, aid, mid = _mk(client)
    r = _put(client, aid, [{"id": "b1", "type": "paragraph", "text": "素材内容", "attrs": {}}],
             1, "material_insert", source_object_type="material", source_object_id=str(mid))
    assert r.status_code == 200
    # 模拟刷新：重新 GET
    r = auth(client, "GET", f"/api/articles/{aid}")
    assert r.status_code == 200
    assert r.json()["blocks"][0]["text"] == "素材内容"
    revs = _revs(aid)
    assert len(revs) == 1


def test_p04_restore_revision_restores_blocks(client):
    pid, aid, mid = _mk(client)
    r = _put(client, aid, [{"id": "b1", "type": "paragraph", "text": "初稿", "attrs": {}}],
             1, "autosave")
    ver1 = r.json()["version"]
    r = _put(client, aid, [{"id": "b1", "type": "paragraph", "text": "初稿+素材", "attrs": {}}],
             ver1, "material_insert", source_object_type="material", source_object_id=str(mid))
    assert r.status_code == 200
    rev = _revs(aid)[0]
    # 恢复到 after = 该版本正文
    r = auth(client, "POST", f"/api/articles/{aid}/revisions/{rev['version']}/restore")
    assert r.status_code == 200
    assert _blocks(aid)[0]["text"] == "初稿+素材"
    # 撤销插入（point=before）= 插入前正文
    r = auth(client, "POST", f"/api/articles/{aid}/revisions/{rev['version']}/restore?point=before")
    assert r.status_code == 200
    assert _blocks(aid)[0]["text"] == "初稿"
    # 恢复动作本身也留痕（revision_restore）
    revs = _revs(aid)
    assert any(x["reason"] == "revision_restore" for x in revs)


def test_p04_double_insert_order_and_versions(client):
    pid, aid, mid = _mk(client)
    v = 1
    for i, text in enumerate(["第一次插入", "第二次插入"]):
        r = _put(client, aid, [{"id": f"b{i}", "type": "paragraph", "text": text, "attrs": {}}],
                 v, "material_insert", source_object_type="material", source_object_id=str(mid))
        assert r.status_code == 200
        v = r.json()["version"]
    revs = _revs(aid)
    assert len(revs) == 2
    assert [x["version"] for x in revs] == [v, v - 1]  # DESC 排序
    assert revs[0]["after_blocks"][0]["text"] == "第二次插入"
    assert revs[1]["after_blocks"][0]["text"] == "第一次插入"


def test_p04_version_conflict_no_partial(client):
    """插入过程版本冲突：409，正文/Revision/Citation 均无半成品。"""
    pid, aid, mid = _mk(client)
    r = _put(client, aid, [{"id": "b1", "type": "paragraph", "text": "服务端已有", "attrs": {}}],
             1, "autosave")
    server_ver = r.json()["version"]
    # 客户端基于过期 base_version=1 再插一次 → 冲突
    r = _put(client, aid, [{"id": "b1", "type": "paragraph", "text": "客户端插入", "attrs": {}}],
             1, "material_insert", source_object_type="material", source_object_id=str(mid))
    assert r.status_code == 409
    # 正文仍是服务端版
    assert _blocks(aid)[0]["text"] == "服务端已有"
    # 无 revision、无 usage 残留
    assert _revs(aid) == []
    conn = main._conn()
    usages = db.list_material_usages(conn, mid)
    conn.close()
    assert usages == []


# ==================== P0-1：事务完整性 ====================

def _mk_cited(client):
    """建项目+草稿+来源+正文+Citation(supported)。返回 (aid, 当前版本)。"""
    pid = auth(client, "POST", "/api/projects", json={"name": "p"}).json()["id"]
    aid = auth(client, "POST", f"/api/projects/{pid}/articles", json={"title": "t"}).json()["id"]
    conn = main._conn()
    sid = db.create_source(conn, pid, "https://example.com/a", "来源A", "证据", "web")
    db.save_article(conn, aid, blocks=[
        {"id": "b1", "type": "paragraph", "text": "原始主张", "attrs": {}},
    ], base_version=1)
    cid = db.create_citation(conn, aid, "b1", sid, quote="引文")
    db.set_citation_verification(conn, cid, "supported", "手动核验")
    ver = db.get_article(conn, aid)["version"]
    conn.close()
    return aid, ver


def test_p01_save_conflict_keeps_citation(client):
    aid, ver = _mk_cited(client)
    # 冲突保存（过期 base_version）
    r = auth(client, "PUT", f"/api/articles/{aid}", json={
        "blocks": [{"id": "b1", "type": "paragraph", "text": "改过的", "attrs": {}}],
        "base_version": 1, "change_reason": "autosave",
    })
    assert r.status_code == 409
    conn = main._conn()
    cites = db.list_citations(conn, aid)
    conn.close()
    assert cites[0]["verification_status"] == "supported"
    assert cites[0]["status"] == "active"


def test_p01_transaction_failure_no_partial(client, monkeypatch):
    """失效步骤抛异常 → 正文、Citation、Revision 全部回滚，无半成品。"""
    aid, ver = _mk_cited(client)
    def boom(conn, aid, ids):
        raise RuntimeError("invalidate boom")
    monkeypatch.setattr(db, "_invalidate_citations", boom)
    # raise_server_exceptions=False：把服务端异常当作 500 响应断言
    c2 = TestClient(main.app, base_url="http://127.0.0.1:8766", raise_server_exceptions=False)
    c2.get("/api/session")
    r = c2.put(f"/api/articles/{aid}",
               headers={"Origin": ORIGIN, "X-Wensu-Token": main.SESSION_TOKEN},
               json={"blocks": [{"id": "b1", "type": "paragraph", "text": "修改后的主张", "attrs": {}}],
                     "base_version": ver, "change_reason": "autosave"})
    assert r.status_code == 500
    # 正文未变
    assert _blocks(aid)[0]["text"] == "原始主张"
    # citation 状态未变
    conn = main._conn()
    cites = db.list_citations(conn, aid)
    conn.close()
    assert cites[0]["verification_status"] == "supported"
    # 无 revision
    assert _revs(aid) == []


def test_p01_material_insert_failure_rolls_back(client, monkeypatch):
    """素材不存在 → 保存整体回滚（正文不写、无 revision）。"""
    pid, aid, _ = _mk(client)
    r = _put(client, aid, [{"id": "b1", "type": "paragraph", "text": "插入", "attrs": {}}],
             1, "material_insert", source_object_type="material", source_object_id="99999")
    assert r.status_code == 404
    assert _blocks(aid) == []
    assert _revs(aid) == []
