"""证据数据层（v3）测试：sources / materials / citations + 机械检查 + 越权。"""

import pytest

from app import db


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.migrate(c)
    return c


def _setup(conn):
    pid = db.create_project(conn, "p1")
    aid = db.create_article(conn, pid, "t")
    return pid, aid


def test_source_create_and_dedupe(conn):
    pid, _ = _setup(conn)
    s1 = db.create_source(conn, pid, "https://a.com/x", "标题", "摘要", "web")
    s2 = db.create_source(conn, pid, "https://a.com/x", "标题2")  # 同 url 复用
    assert s1 == s2
    assert len(db.list_sources(conn, pid)) == 1


def test_material_roundtrip(conn):
    pid, _ = _setup(conn)
    mid = db.create_material(conn, pid, "素材", "内容")
    assert mid > 0
    mats = db.list_materials(conn, pid)
    assert len(mats) == 1
    assert mats[0]["title"] == "素材"


def test_citation_roundtrip_with_source(conn):
    pid, aid = _setup(conn)
    sid = db.create_source(conn, pid, "https://a.com", "来源", "摘要", "web")
    db.save_article(conn, aid, blocks=[{"id": "b1", "type": "paragraph", "text": "正文", "attrs": {}}], base_version=1)
    cid = db.create_citation(conn, aid, "b1", sid, quote="引文")
    assert cid > 0
    cites = db.list_citations(conn, aid)
    assert len(cites) == 1
    assert cites[0]["source_url"] == "https://a.com"
    assert cites[0]["status"] == "active"


def test_citation_cross_project_rejected(conn):
    pid1, aid1 = _setup(conn)
    pid2 = db.create_project(conn, "p2")
    sid = db.create_source(conn, pid2, "https://b.com", "别人的来源")
    with pytest.raises(db.NotFoundError):
        db.create_citation(conn, aid1, "b1", sid)


def test_citation_orphaned_when_block_deleted(conn):
    pid, aid = _setup(conn)
    sid = db.create_source(conn, pid, "https://a.com", "来源")
    db.save_article(conn, aid, blocks=[{"id": "b1", "type": "paragraph", "text": "正文", "attrs": {}}], base_version=1)
    db.create_citation(conn, aid, "b1", sid)
    # 删除 b1 块后重新保存
    db.save_article(conn, aid, blocks=[{"id": "b2", "type": "paragraph", "text": "新正文", "attrs": {}}], base_version=2)
    cites = db.list_citations(conn, aid)
    assert cites[0]["status"] == "orphaned"  # 机械检查落库


def test_delete_citation(conn):
    pid, aid = _setup(conn)
    sid = db.create_source(conn, pid, "https://a.com")
    db.save_article(conn, aid, blocks=[{"id": "b1", "type": "paragraph", "text": "x", "attrs": {}}], base_version=1)
    cid = db.create_citation(conn, aid, "b1", sid)
    assert db.delete_citation(conn, cid) is True
    assert db.delete_citation(conn, cid) is False
    assert db.list_citations(conn, aid) == []
