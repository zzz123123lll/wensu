"""回收站 / 历史 / 导出（v4）测试。"""

import pytest

from app import blocks, db


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.migrate(c)
    return c


def _mk(conn):
    pid = db.create_project(conn, "p")
    aid = db.create_article(conn, pid, "t")
    return pid, aid


# ---------- 回收站 ----------

def test_soft_delete_and_restore_article(conn):
    pid, aid = _mk(conn)
    assert len(db.list_articles(conn, pid)) == 1
    assert db.soft_delete_article(conn, aid) is True
    assert db.list_articles(conn, pid) == []  # 列表过滤
    trash = db.list_trash(conn, pid)
    assert len(trash) == 1 and trash[0]["id"] == aid
    assert db.restore_article(conn, aid) is True
    assert len(db.list_articles(conn, pid)) == 1


def test_soft_delete_project_cascades(conn):
    pid, aid = _mk(conn)
    assert db.soft_delete_project(conn, pid) is True
    assert db.list_projects(conn) == []
    assert db.list_articles(conn, pid) == []
    assert len(db.list_trash(conn)) == 1  # 全局回收站含该草稿


def test_delete_twice_fails(conn):
    pid, aid = _mk(conn)
    assert db.soft_delete_article(conn, aid) is True
    assert db.soft_delete_article(conn, aid) is False


# ---------- 历史 ----------

def test_revision_roundtrip_and_restore(conn):
    pid, aid = _mk(conn)
    db.save_article(conn, aid, blocks=[{"id": "b1", "type": "paragraph", "text": "初稿", "attrs": {}}], base_version=1, reason="ai_rewrite")
    db.save_article(conn, aid, blocks=[{"id": "b1", "type": "paragraph", "text": "第二版", "attrs": {}}], base_version=2, reason="ai_rewrite")
    revs = db.list_revisions(conn, aid)
    assert len(revs) == 2
    assert revs[0]["version"] == 3  # 最新在前（初始 version=1，两次保存后 2/3）
    # 恢复到 v2（初稿内容，revision 只记录保存产生的版本）
    new_v = db.restore_revision(conn, aid, 2)
    art = db.get_article(conn, aid)
    assert art["blocks"][0]["text"] == "初稿"
    assert new_v > 3  # 恢复产生新版本


def test_restore_missing_revision_fails(conn):
    pid, aid = _mk(conn)
    with pytest.raises(db.NotFoundError):
        db.restore_revision(conn, aid, 99)


def test_autosave_does_not_create_revision(conn):
    pid, aid = _mk(conn)
    db.save_article(conn, aid, blocks=[{"id": "b1", "type": "paragraph", "text": "x", "attrs": {}}], base_version=1)  # reason=autosave
    assert db.list_revisions(conn, aid) == []


# ---------- 导出 ----------

def test_export_markdown(conn):
    pid, aid = _mk(conn)
    db.save_article(conn, aid, title="标题",
                    blocks=[
                        {"id": "b1", "type": "paragraph", "text": "第一段", "attrs": {}},
                        {"id": "b2", "type": "heading", "text": "小标题", "attrs": {}},
                        {"id": "b3", "type": "paragraph", "text": "引用自", "attrs": {}},
                    ], base_version=1)
    art = db.get_article(conn, aid)
    md = blocks.serialize_blocks(art["blocks"])
    assert "第一段" in md
    assert "小标题" in md
