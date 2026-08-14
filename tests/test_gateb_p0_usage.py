"""P0-6 失败测试：Material 显式使用关系（不再靠共享 source_id 推断）。

临时数据库，不触碰 data/workbench.db。
"""

import pytest
from fastapi.testclient import TestClient

from app import db, main

ORIGIN = "http://127.0.0.1:8766"


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "gateb_usage.db"
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


def _mk(client, n_materials=1, articles=1):
    """建项目 + n 个共享同一 source 的素材 + articles 篇草稿。"""
    pid_r = auth(client, "POST", "/api/projects", json={"name": "p"})
    pid = pid_r.json()["id"]
    conn = main._conn()
    sid = db.create_source(conn, pid, "https://example.com/shared", "共享来源", "证据", "web")
    conn.close()
    mids = []
    for i in range(n_materials):
        r = auth(client, "POST", f"/api/projects/{pid}/materials",
                 json={"title": f"素材{i}", "content": f"内容{i}", "tags": []})
        mids.append(r.json()["id"])
    aids = []
    for i in range(articles):
        r = auth(client, "POST", f"/api/projects/{pid}/articles", json={"title": f"稿{i}"})
        aids.append(r.json()["id"])
    return pid, sid, mids, aids


def test_p06_same_source_materials_no_crosstalk(client):
    """同一 Source 下两条 Material：各自使用状态独立，不因共享 source_id 互相污染。"""
    _, _, mids, aids = _mk(client, n_materials=2)
    # 只有素材0 被插入到草稿
    conn = main._conn()
    db.record_material_usage(conn, mids[0], aids[0], block_id="b1", usage_type="insert")
    conn.close()
    u0 = auth(client, "GET", f"/api/materials/{mids[0]}/usage").json()
    u1 = auth(client, "GET", f"/api/materials/{mids[1]}/usage").json()
    assert len(u0["usages"]) == 1 and u0["articles"] == [aids[0]]
    assert len(u1["usages"]) == 0 and u1["articles"] == []


def test_p06_one_material_used_by_multiple_drafts(client):
    _, _, mids, aids = _mk(client, n_materials=1, articles=2)
    conn = main._conn()
    db.record_material_usage(conn, mids[0], aids[0], block_id="b1")
    db.record_material_usage(conn, mids[0], aids[1], block_id="b2")
    conn.close()
    u = auth(client, "GET", f"/api/materials/{mids[0]}/usage").json()
    assert sorted(u["articles"]) == sorted(aids)
    assert len(u["usages"]) == 2


def test_p06_unlink_only_keeps_material(client):
    """unlink_only=1：只解除 Material—Draft 关系，Material/Citation/正文都保留。"""
    _, _, mids, aids = _mk(client, n_materials=1)
    conn = main._conn()
    db.record_material_usage(conn, mids[0], aids[0], block_id="b1")
    sid = db.create_source(conn, 1, "https://example.com/shared")
    conn.close()
    # 建正文 + citation（模拟素材正文已被引用）
    conn = main._conn()
    db.save_article(conn, aids[0], blocks=[{"id": "b1", "type": "paragraph", "text": "正文", "attrs": {}}], base_version=1)
    db.create_citation(conn, aids[0], "b1", sid, quote="引")
    conn.close()
    r = auth(client, "DELETE", f"/api/materials/{mids[0]}?unlink_only=1")
    assert r.status_code == 200
    body = r.json()
    assert body.get("unlinked") is True
    assert body.get("kept_material") is True
    # 素材仍在
    r = auth(client, "GET", f"/api/materials/{mids[0]}")
    assert r.status_code == 200
    # 关系已解除
    u = auth(client, "GET", f"/api/materials/{mids[0]}/usage").json()
    assert len(u["usages"]) == 0
    # 正文和 Citation 都在
    conn = main._conn()
    cites = db.list_citations(conn, aids[0])
    conn.close()
    assert len(cites) == 1 and cites[0]["status"] == "active"


def test_p06_delete_unused_material_ok(client):
    _, _, mids, _ = _mk(client, n_materials=1)
    r = auth(client, "DELETE", f"/api/materials/{mids[0]}")
    assert r.status_code == 200
    r = auth(client, "GET", f"/api/materials/{mids[0]}")
    assert r.status_code == 404


def test_p06_delete_used_material_shows_impact_409(client):
    """删除被使用素材：默认 409 并展示真实影响，不静默删除。"""
    _, _, mids, aids = _mk(client, n_materials=1)
    conn = main._conn()
    db.record_material_usage(conn, mids[0], aids[0], block_id="b1")
    conn.close()
    r = auth(client, "DELETE", f"/api/materials/{mids[0]}")
    assert r.status_code == 409
    d = r.json()
    assert d.get("usages") or d.get("detail")  # 影响信息可见
    # 素材仍在
    r = auth(client, "GET", f"/api/materials/{mids[0]}")
    assert r.status_code == 200


def test_p06_force_delete_used_material(client):
    """force=1：删素材 + 解除关系；正文与 Citation 保留。"""
    _, _, mids, aids = _mk(client, n_materials=1)
    conn = main._conn()
    db.record_material_usage(conn, mids[0], aids[0], block_id="b1")
    sid = db.create_source(conn, 1, "https://example.com/shared")
    db.save_article(conn, aids[0], blocks=[{"id": "b1", "type": "paragraph", "text": "正文", "attrs": {}}], base_version=1)
    db.create_citation(conn, aids[0], "b1", sid, quote="引")
    conn.close()
    r = auth(client, "DELETE", f"/api/materials/{mids[0]}?force=1")
    assert r.status_code == 200
    r = auth(client, "GET", f"/api/materials/{mids[0]}")
    assert r.status_code == 404
    conn = main._conn()
    cites = db.list_citations(conn, aids[0])
    conn.close()
    assert len(cites) == 1


def test_p06_rename_material_keeps_usage(client):
    """重命名素材不破坏使用关系。"""
    _, _, mids, aids = _mk(client, n_materials=1)
    conn = main._conn()
    db.record_material_usage(conn, mids[0], aids[0], block_id="b1")
    conn.close()
    r = auth(client, "PATCH", f"/api/materials/{mids[0]}",
             json={"title": "新名字", "content": "新内容", "tags": ["x"]})
    assert r.status_code == 200
    u = auth(client, "GET", f"/api/materials/{mids[0]}/usage").json()
    assert len(u["usages"]) == 1 and u["articles"] == [aids[0]]


def test_p06_deleted_source_lifecycle(client):
    """Source 生命周期：被 Citation 引用的 Source 不可删（FK 保护），先删 Citation 才可删。"""
    pid, sid, _, aids = _mk(client, n_materials=1)
    conn = main._conn()
    db.save_article(conn, aids[0], blocks=[{"id": "b1", "type": "paragraph", "text": "正文", "attrs": {}}], base_version=1)
    cid = db.create_citation(conn, aids[0], "b1", sid, quote="引")
    conn.close()
    # 被引用 → 删除被 FK 拒绝
    conn = main._conn()
    with pytest.raises(Exception):
        conn.execute("DELETE FROM sources WHERE id = ?", (sid,))
    conn.rollback()
    # 删除 Citation 后可删
    conn.execute("DELETE FROM citations WHERE id = ?", (cid,))
    conn.commit()
    conn.execute("DELETE FROM sources WHERE id = ?", (sid,))
    conn.commit()
    conn.close()


def test_p06_legacy_material_no_usage(client):
    """旧数据兼容：没有 material_usages 记录的旧素材显示为未使用，不伪造关系。"""
    pid, _, mids, _ = _mk(client, n_materials=1)
    # 手工删掉 usage 记录模拟旧数据
    conn = main._conn()
    conn.execute("DELETE FROM material_usages")
    conn.commit()
    conn.close()
    u = auth(client, "GET", f"/api/materials/{mids[0]}/usage").json()
    assert u["material"] is not None
    assert u["usages"] == [] and u["articles"] == []
