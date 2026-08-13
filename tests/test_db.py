import sqlite3

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


# ---------- 迁移 ----------

def test_migrate_idempotent():
    conn = db.connect(":memory:")
    db.migrate(conn)
    v1 = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()["v"]
    assert v1 == len(db.MIGRATIONS)
    db.migrate(conn)  # 重复执行幂等
    v2 = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()["v"]
    assert v2 == v1
    # 表结构就位
    conn.execute("SELECT version FROM articles LIMIT 0")
    conn.execute("SELECT article_id, version, reason FROM article_revisions LIMIT 0")


def test_migrate_old_schema_upgrades():
    """旧库（无 version 列）升级：v1 建表（已存在跳过），v2 加列不丢数据。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE projects (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, created_at TEXT NOT NULL)")
    conn.execute("CREATE TABLE articles (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, title TEXT NOT NULL, blocks_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
    conn.execute("INSERT INTO projects (name, created_at) VALUES ('旧项目', '2026-01-01')")
    conn.execute("INSERT INTO articles (project_id, title, blocks_json, created_at, updated_at) VALUES (1, '旧稿', '[{\"id\":\"b1\",\"type\":\"paragraph\",\"text\":\"旧内容\",\"attrs\":{}}]', '2026-01-01', '2026-01-01')")
    conn.commit()
    db.migrate(conn)
    art = db.get_article(conn, 1)
    assert art["title"] == "旧稿"
    assert art["blocks"][0]["text"] == "旧内容"
    assert art["version"] == 1  # 默认 1，不丢


def test_migrate_failure_rolls_back(monkeypatch):
    """迁移失败回滚：失败的版本不记录，已应用版本保留。"""
    conn = db.connect(":memory:")
    db.migrate(conn)
    # 注入一个会失败的 v7 迁移（语法错误）
    monkeypatch.setattr(db, "MIGRATIONS", [
        db.MIGRATIONS[0], db.MIGRATIONS[1], db.MIGRATIONS[2], db.MIGRATIONS[3], db.MIGRATIONS[4], db.MIGRATIONS[5],
        ["CREATE TABLE broken_table (id"],  # 未闭合，必然失败
    ])
    with pytest.raises(Exception):
        db.migrate(conn)
    # 失败的 v7 未记录
    n = conn.execute("SELECT COUNT(*) AS n FROM schema_migrations WHERE version = 7").fetchone()["n"]
    assert n == 0
    # v1-v6 记录仍在，后续可重试
    n2 = conn.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"]
    assert n2 == 6


# ---------- 乐观锁与原子性 ----------

def test_save_article_version_conflict():
    conn = db.connect(":memory:")
    db.init(conn)
    pid = db.create_project(conn, "p")
    aid = db.create_article(conn, pid, "t")
    db.save_article(conn, aid, blocks=[], base_version=1)
    import pytest
    with pytest.raises(db.VersionConflict):
        db.save_article(conn, aid, blocks=[], base_version=1)


def test_save_article_not_found():
    conn = db.connect(":memory:")
    db.init(conn)
    import pytest
    with pytest.raises(db.NotFoundError):
        db.save_article(conn, 999, blocks=[])


def test_save_article_revision_only_for_ai():
    conn = db.connect(":memory:")
    db.init(conn)
    pid = db.create_project(conn, "p")
    aid = db.create_article(conn, pid, "t")
    db.save_article(conn, aid, blocks=[], base_version=1, reason="autosave")
    db.save_article(conn, aid, blocks=[], base_version=2, reason="ai_rewrite")
    rows = conn.execute("SELECT reason FROM article_revisions").fetchall()
    assert len(rows) == 1
    assert rows[0]["reason"] == "ai_rewrite"


def test_create_article_orphan_rejected():
    conn = db.connect(":memory:")
    db.init(conn)
    import pytest
    with pytest.raises(db.NotFoundError):
        db.create_article(conn, 999, "孤儿")


def test_foreign_keys_enforced():
    """PRAGMA foreign_keys=ON：插入不存在 project 的 article 必须失败。"""
    conn = db.connect(":memory:")
    db.init(conn)
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO articles (project_id, title, blocks_json, created_at, updated_at) VALUES (999, 'x', '[]', 't', 't')"
        )


def test_blocks_hash_stable():
    b1 = [{"id": "b1", "type": "paragraph", "text": "你好", "attrs": {}}]
    b2 = [{"attrs": {}, "text": "你好", "type": "paragraph", "id": "b1"}]  # 键序不同
    assert db.blocks_hash(b1) == db.blocks_hash(b2)
    b3 = [{"id": "b1", "type": "paragraph", "text": "你好！", "attrs": {}}]
    assert db.blocks_hash(b1) != db.blocks_hash(b3)
