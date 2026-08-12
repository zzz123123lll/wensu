import pytest
from app import db


def test_create_project_and_article():
    conn = db.connect(":memory:")
    db.init(conn)
    pid = db.create_project(conn, "随笔")
    assert pid > 0
    aid = db.create_article(conn, pid, "第一篇")
    assert db.list_projects(conn) == [(pid, "随笔")]
    arts = db.list_articles(conn, pid)
    assert arts[0][1] == "第一篇"


def test_article_blocks_roundtrip():
    conn = db.connect(":memory:")
    db.init(conn)
    pid = db.create_project(conn, "p")
    aid = db.create_article(conn, pid, "t")
    blocks = [{"id": "b1", "type": "paragraph", "text": "你好", "attrs": {}}]
    db.save_article_blocks(conn, aid, blocks)
    assert db.get_article(conn, aid)["blocks"] == blocks


def test_get_article_returns_none_when_missing():
    conn = db.connect(":memory:")
    db.init(conn)
    assert db.get_article(conn, 999) is None


def test_project_name_update():
    conn = db.connect(":memory:")
    db.init(conn)
    pid = db.create_project(conn, "旧名")
    db.rename_project(conn, pid, "新名")
    assert db.list_projects(conn) == [(pid, "新名")]
