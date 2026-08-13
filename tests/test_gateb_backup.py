"""备份恢复演练（六.7-8）：SQLite backup API 完整备份→恢复→数量与关系校验。

临时数据库，不触碰 data/workbench.db。
"""

import sqlite3

from app import db


def _mk_rich_db(path):
    """建一个数据齐全的库：项目/草稿/版本/素材/来源/引用/核验/Ask/回收站。"""
    conn = db.connect(path)
    db.migrate(conn)
    pid = db.create_project(conn, "项目A")
    aid = db.create_article(conn, pid, "文章")
    db.save_article(conn, aid, blocks=[
        {"id": "b1", "type": "paragraph", "text": "正文", "attrs": {}},
    ], base_version=1, reason="material_insert", before_blocks=[],
        source_object_type="material", source_object_id="1")
    sid = db.create_source(conn, pid, "https://example.com/s", "来源", "证据", "web")
    mid = db.create_material(conn, pid, "素材", "内容", sid, tags=["标签"])
    db.record_material_usage(conn, mid, aid, block_id="b1")
    cid = db.create_citation(conn, aid, "b1", sid, quote="引文")
    db.set_citation_verification(conn, cid, "supported")
    db.record_ask(conn, aid, "问题", "回答", "deepseek-x")
    # 回收站：删一个项目
    pid2 = db.create_project(conn, "要删的项目")
    aid2 = db.create_article(conn, pid2, "被删文章")
    conn.execute("UPDATE articles SET deleted_at = ? WHERE id = ?", (db._now(), aid2))
    conn.commit()
    conn.close()
    return {"pid": pid, "aid": aid, "sid": sid, "mid": mid, "cid": cid}


def _counts(path):
    conn = db.connect(path)
    try:
        c = {}
        for t in ("projects", "articles", "article_revisions", "sources", "materials",
                  "material_usages", "citations", "article_asks", "schema_migrations"):
            c[t] = conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        return c, fk
    finally:
        conn.close()


def test_backup_restore_full_integrity(tmp_path):
    src = str(tmp_path / "src.db")
    info = _mk_rich_db(src)
    before, fk_before = _counts(src)
    assert fk_before == []

    # SQLite backup API → 备份文件
    backup_path = str(tmp_path / "backup.db")
    s = sqlite3.connect(src)
    b = sqlite3.connect(backup_path)
    s.backup(b)
    b.close()
    s.close()

    # 备份文件能打开且 quick_check ok
    chk = sqlite3.connect(backup_path)
    assert chk.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    chk.close()

    # 恢复演练：删掉原库，从备份恢复
    import os
    os.remove(src)
    s2 = sqlite3.connect(src)
    b2 = sqlite3.connect(backup_path)
    b2.backup(s2)
    s2.close()
    b2.close()

    after, fk_after = _counts(src)
    assert fk_after == []
    assert after == before

    # 具体对象与关系都在
    conn = db.connect(src)
    art = db.get_article(conn, info["aid"])
    assert art is not None and art["blocks"][0]["text"] == "正文"
    revs = db.list_revisions(conn, info["aid"])
    assert len(revs) == 1 and revs[0]["reason"] == "material_insert"
    cites = db.list_citations(conn, info["aid"])
    assert len(cites) == 1 and cites[0]["verification_status"] == "supported"
    usage = db.material_usage(conn, info["mid"])
    assert len(usage["usages"]) == 1
    mats = db.list_materials(conn, info["pid"])
    assert mats[0]["tags"] == ["标签"]
    asks = db.list_asks(conn, info["aid"])
    assert len(asks) == 1
    # 回收站仍在
    trash = db.list_trash(conn)
    assert len(trash) == 1
    conn.close()
