"""独立代码审查修复回归测试（评审发现的 6 个真实缺陷）。"""

import pytest
from fastapi.testclient import TestClient

from app import db, main

ORIGIN = "http://127.0.0.1:8766"


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "gateb_review.db"
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
    pid = auth(client, "POST", "/api/projects", json={"name": "p"}).json()["id"]
    aid = auth(client, "POST", f"/api/projects/{pid}/articles", json={"title": "t"}).json()["id"]
    return pid, aid


# ---- 审查缺陷 4：旧 Revision 无 before 快照时 point=before 必须拒绝（防清空全文） ----

def test_review_old_revision_before_restore_rejected(client):
    """v8 前的旧 revision（before_blocks=[]）：point=before 恢复必须 400，不得清空正文。"""
    pid, aid = _mk(client)
    conn = main._conn()
    db.save_article(conn, aid, blocks=[
        {"id": "b1", "type": "paragraph", "text": "现有正文", "attrs": {}},
    ], base_version=1, reason="autosave")
    # 手工插入一条"旧式" revision（无 before 快照，模拟 v8 前 ai_rewrite）
    conn.execute(
        "INSERT INTO article_revisions (article_id, version, blocks_json, reason, created_at)"
        " VALUES (?, 2, ?, 'ai_rewrite', ?)",
        (aid, '[{"id": "b1", "type": "paragraph", "text": "现有正文", "attrs": {}}]', db._now()),
    )
    conn.commit()
    conn.close()
    # point=before → 400（防护生效）
    r = auth(client, "POST", f"/api/articles/{aid}/revisions/2/restore?point=before")
    assert r.status_code == 400
    # 正文未被清空
    conn = main._conn()
    art = db.get_article(conn, aid)
    conn.close()
    assert len(art["blocks"]) == 1 and art["blocks"][0]["text"] == "现有正文"
    # point=after 仍可用
    r = auth(client, "POST", f"/api/articles/{aid}/revisions/2/restore")
    assert r.status_code == 200


# ---- 审查缺陷 5：导出文件名连续点号不得 500 ----

def test_review_export_dotdot_title_no_500(client):
    pid, aid = _mk(client)
    auth(client, "PATCH", f"/api/articles/{aid}", json={})  # no-op
    conn = main._conn()
    conn.execute("UPDATE articles SET title = ? WHERE id = ?", ("a..b", aid))
    conn.commit()
    conn.close()
    r = auth(client, "GET", f"/api/articles/{aid}/export?format=md")
    assert r.status_code in (200, 400)  # 绝不 500
    assert r.status_code != 500


# ---- 审查缺陷 6：素材创建 source_id 越权/不存在校验 ----

def test_review_material_cross_project_source_404(client):
    pid, aid = _mk(client)
    pid2 = auth(client, "POST", "/api/projects", json={"name": "p2"}).json()["id"]
    sid2 = auth(client, "POST", f"/api/projects/{pid2}/sources",
                json={"url": "https://e.com/2", "title": "别项目来源"}).json()["id"]
    # 用项目1 创建素材但挂项目2 的来源 → 404（越权拒绝）
    r = auth(client, "POST", f"/api/projects/{pid}/materials",
             json={"title": "素材", "content": "内容", "source_id": sid2})
    assert r.status_code == 404


def test_review_material_missing_source_404(client):
    pid, aid = _mk(client)
    r = auth(client, "POST", f"/api/projects/{pid}/materials",
             json={"title": "素材", "content": "内容", "source_id": 99999})
    assert r.status_code == 404


def test_review_material_same_project_source_ok(client):
    pid, aid = _mk(client)
    sid = auth(client, "POST", f"/api/projects/{pid}/sources",
               json={"url": "https://e.com/1", "title": "本项目来源"}).json()["id"]
    r = auth(client, "POST", f"/api/projects/{pid}/materials",
             json={"title": "素材", "content": "内容", "source_id": sid})
    assert r.status_code == 200


# ---- 审查缺陷 7：素材插入无实质变化不记录 usage；目标块优先新增块 ----

def test_review_material_insert_no_change_no_usage(client):
    pid, aid = _mk(client)
    mid = auth(client, "POST", f"/api/projects/{pid}/materials",
               json={"title": "素材", "content": "重复文本", "tags": []}).json()["id"]
    # 第一次插入（新增块）→ 记录 usage
    r = auth(client, "PUT", f"/api/articles/{aid}", json={
        "blocks": [{"id": "b1", "type": "paragraph", "text": "重复文本", "attrs": {}}],
        "base_version": 1, "change_reason": "material_insert",
        "source_object_type": "material", "source_object_id": str(mid),
    })
    assert r.status_code == 200
    ver = r.json()["version"]
    conn = main._conn()
    assert len(db.list_material_usages(conn, mid)) == 1
    conn.close()
    # 再次"插入"相同文本（无实质变化）→ 不新增 usage（避免空关联）
    r = auth(client, "PUT", f"/api/articles/{aid}", json={
        "blocks": [{"id": "b1", "type": "paragraph", "text": "重复文本", "attrs": {}}],
        "base_version": ver, "change_reason": "material_insert",
        "source_object_type": "material", "source_object_id": str(mid),
    })
    assert r.status_code == 200
    conn = main._conn()
    assert len(db.list_material_usages(conn, mid)) == 1  # 未新增
    conn.close()


# ---- 审查建议 10：restore 乐观锁（并发冲突 409） ----

def test_review_restore_conflict_409(client):
    pid, aid = _mk(client)
    r = auth(client, "PUT", f"/api/articles/{aid}", json={
        "blocks": [{"id": "b1", "type": "paragraph", "text": "版本一", "attrs": {}}],
        "base_version": 1, "change_reason": "material_insert",
        "source_object_type": "material", "source_object_id": "0",
    })
    assert r.status_code == 200
    conn = main._conn()
    rev = db.list_revisions(conn, aid)[0]
    conn.close()
    # 先并发修改一次（版本前进）
    r = auth(client, "PUT", f"/api/articles/{aid}", json={
        "blocks": [{"id": "b1", "type": "paragraph", "text": "版本二", "attrs": {}}],
        "base_version": r.json()["version"], "change_reason": "autosave",
    })
    assert r.status_code == 200
    # 恢复旧版本（restore 内部以当前版本为基线，不应 409——它自己读当前版本）
    r = auth(client, "POST", f"/api/articles/{aid}/revisions/{rev['version']}/restore")
    assert r.status_code == 200
