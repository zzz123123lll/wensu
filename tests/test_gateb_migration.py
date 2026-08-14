"""迁移链测试（六.6）：空库 / v1 / v5 / v7 升级、重复执行、中途失败回滚、旧数据兼容。

全部使用临时/内存数据库，不触碰 data/workbench.db。
"""


import pytest

from app import db


def _fresh():
    conn = db.connect(":memory:")
    return conn


def test_migrate_empty_db(tmp_path):
    conn = db.connect(str(tmp_path / "empty.db"))
    db.migrate(conn)
    n = conn.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"]
    assert n == len(db.MIGRATIONS)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_migrate_idempotent():
    conn = _fresh()
    db.migrate(conn)
    db.migrate(conn)  # 重复执行
    n = conn.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"]
    assert n == len(db.MIGRATIONS)
    conn.close()


def test_upgrade_from_v1_with_old_data():
    """v1 库 + 旧数据 → 升级到最新：数据保留、新列有默认值、FK 无违规。"""
    conn = _fresh()
    # 只应用 v1（schema_migrations 表由 migrate 引导创建，旧库同样存在）
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    for stmt in db.MIGRATIONS[0]:
        conn.execute(stmt)
    conn.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (1, ?)", (db._now(),))
    conn.commit()
    # v1 时代数据
    pid = db.create_project(conn, "旧项目")
    aid = db.create_article(conn, pid, "旧文章")
    conn.execute(
        "UPDATE articles SET blocks_json = ? WHERE id = ?",
        ('[{"id": "b1", "type": "paragraph", "text": "旧正文", "attrs": {}}]', aid),
    )
    conn.commit()
    # 升级到最新
    db.migrate(conn)
    n = conn.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"]
    assert n == len(db.MIGRATIONS)
    # 数据保留
    art = db.get_article(conn, aid)
    assert art["title"] == "旧文章"
    assert art["blocks"][0]["text"] == "旧正文"
    # 新列默认值可用
    assert db.get_editor_state(conn, aid) == {}
    # 新表可写
    revs = db.list_revisions(conn, aid)
    assert revs == []
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_upgrade_from_v5_and_v7(tmp_path):
    """v5/v7 库升级：逐版本应用 v1..v5 / v1..v7 再补全，中间插入数据不破坏。"""
    for target in (5, 7):
        path = str(tmp_path / f"v{target}.db")
        conn = db.connect(path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        for i in range(target):
            for stmt in db.MIGRATIONS[i]:
                conn.execute(stmt)
            conn.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                         (i + 1, db._now()))
        conn.commit()
        # 目标版本下的数据（用目标版本已有的函数创建）
        pid = db.create_project(conn, f"p-v{target}")
        aid = db.create_article(conn, pid, f"a-v{target}")
        if target >= 3:
            sid = db.create_source(conn, pid, f"https://e.com/{target}", "来源", "证据", "web")
            cid = db.create_citation(conn, aid, "b1", sid, quote="引")
            if target >= 7:  # metadata_json 列 v7 才加
                db.set_citation_verification(conn, cid, "supported")
                db.create_material(conn, pid, "素材", "内容", sid)
        conn.close()
        # 升级到最新
        conn = db.connect(path)
        db.migrate(conn)
        n = conn.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"]
        assert n == len(db.MIGRATIONS)
        art = db.get_article(conn, aid)
        assert art is not None
        if target >= 3:
            assert len(db.list_citations(conn, aid)) == 1
        if target >= 7:
            assert len(db.list_materials(conn, pid)) == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        conn.close()


def test_upgrade_keeps_old_verification_status():
    """旧非法核验状态保留原值（不静默吞），由校验层暴露。"""
    conn = _fresh()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    for i in range(3):
        for stmt in db.MIGRATIONS[i]:
            conn.execute(stmt)
        conn.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                     (i + 1, db._now()))
    conn.commit()
    pid = db.create_project(conn, "p")
    aid = db.create_article(conn, pid, "a")
    sid = db.create_source(conn, pid, "https://e.com/x", "来源", "证据", "web")
    db.save_article(conn, aid, blocks=[{"id": "b1", "type": "paragraph", "text": "正文", "attrs": {}}], base_version=1)
    cid = db.create_citation(conn, aid, "b1", sid, quote="引")
    # 模拟旧非法状态（v7 之前可能存在的值）
    conn.execute("UPDATE citations SET verification_status = 'old_bogus' WHERE id = ?", (cid,))
    conn.commit()
    db.migrate(conn)  # 升级不吞状态、不崩溃
    row = conn.execute("SELECT verification_status FROM citations WHERE id = ?", (cid,)).fetchone()
    assert row["verification_status"] == "old_bogus"  # 原样保留，待人工处理
    conn.close()


def test_migrate_failure_keeps_article_intact(tmp_path):
    """中途失败回滚：注入坏 vN 迁移，已有文章数据不被破坏。"""
    path = str(tmp_path / "fail.db")
    conn = db.connect(path)
    db.migrate(conn)
    pid = db.create_project(conn, "p")
    aid = db.create_article(conn, pid, "t")
    db.save_article(conn, aid, blocks=[{"id": "b1", "type": "paragraph", "text": "正文", "attrs": {}}], base_version=1)
    # 注入坏迁移并重跑：失败不破坏已有数据、失败版本不记录
    monkey = db.MIGRATIONS + [["CREATE TABLE broken (id"]]
    orig = db.MIGRATIONS
    try:
        db.MIGRATIONS = monkey
        conn2 = db.connect(path)
        with pytest.raises(Exception):
            db.migrate(conn2)
        conn2.close()
    finally:
        db.MIGRATIONS = orig
    conn3 = db.connect(path)
    n = conn3.execute("SELECT COUNT(*) AS n FROM schema_migrations WHERE version > 8").fetchone()["n"]
    assert n == 0  # 失败版本未记录
    conn3.close()
    art = db.get_article(conn, aid)
    assert art["blocks"][0]["text"] == "正文"
    conn.close()


def test_legacy_revision_compat():
    """旧 Revision（无 before/新列）读取得出默认值，不崩溃。"""
    conn = _fresh()
    db.migrate(conn)
    pid = db.create_project(conn, "p")
    aid = db.create_article(conn, pid, "t")
    # 手工插入一条"旧式" revision（只填必填列）
    conn.execute(
        "INSERT INTO article_revisions (article_id, version, blocks_json, reason, created_at)"
        " VALUES (?, 2, ?, 'ai_rewrite', ?)",
        (aid, '[{"id": "b1", "type": "paragraph", "text": "x", "attrs": {}}]', db._now()),
    )
    conn.commit()
    revs = db.list_revisions(conn, aid)
    assert len(revs) == 1
    assert revs[0]["before_blocks"] == []  # 默认空快照
    assert revs[0]["status"] == "applied"
    conn.close()
