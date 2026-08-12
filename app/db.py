"""SQLite 持久化层：项目/草稿/正文块 + 显式迁移 + 版本乐观锁 + 最小修订日志。

- 连接启用 foreign_keys / busy_timeout / WAL（文件库）
- migrate() 幂等迁移，schema_migrations 记录已应用版本
- save_article 原子：标题 + 正文 + version 递增 + 必要 revision 同一事务
- 乐观锁：base_version 不匹配抛 VersionConflict（409）
"""

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone

# 默认数据库路径（基于文件位置，任意 CWD 可启动）
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "workbench.db")

# 仅 AI 接受/核验修订/冲突恢复等明确边界写入最小不可变修订日志；
# 普通 autosave 不制造历史记录（v0.2 无历史 UI）。
REVISION_REASONS = ("ai_rewrite", "ai_check", "conflict_recovery")


class NotFoundError(Exception):
    def __init__(self, what: str):
        super().__init__(what)
        self.what = what


class VersionConflict(Exception):
    def __init__(self, article_id: int, current_version: int):
        super().__init__(f"version conflict: article {article_id} at {current_version}")
        self.article_id = article_id
        self.current_version = current_version


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# 迁移脚本：每个版本是语句列表（逐条执行，整体一个事务）
MIGRATIONS: list[list[str]] = [
    # v1：初始表（幂等；旧库已有则无操作）
    [
        "CREATE TABLE IF NOT EXISTS projects ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " name TEXT NOT NULL,"
        " created_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS articles ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " project_id INTEGER NOT NULL REFERENCES projects(id),"
        " title TEXT NOT NULL,"
        " blocks_json TEXT NOT NULL DEFAULT '[]',"
        " created_at TEXT NOT NULL,"
        " updated_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS settings ("
        " id INTEGER PRIMARY KEY CHECK (id = 1),"
        " base_url TEXT NOT NULL DEFAULT '',"
        " model TEXT NOT NULL DEFAULT '',"
        " api_key_enc BLOB)",
    ],
    # v2：version 乐观锁 + 索引 + 最小修订日志
    [
        "ALTER TABLE articles ADD COLUMN version INTEGER NOT NULL DEFAULT 1",
        "CREATE INDEX IF NOT EXISTS idx_articles_project_updated ON articles(project_id, updated_at)",
        "CREATE TABLE IF NOT EXISTS article_revisions ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " article_id INTEGER NOT NULL REFERENCES articles(id),"
        " version INTEGER NOT NULL,"
        " blocks_json TEXT NOT NULL,"
        " reason TEXT NOT NULL DEFAULT '',"
        " created_at TEXT NOT NULL)",
    ],
    # v3：Source / evidence snapshot / Material / Citation 证据数据层
    [
        "CREATE TABLE IF NOT EXISTS sources ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " project_id INTEGER NOT NULL REFERENCES projects(id),"
        " url TEXT NOT NULL,"
        " canonical_url TEXT NOT NULL DEFAULT '',"
        " title TEXT NOT NULL DEFAULT '',"
        " snippet TEXT NOT NULL DEFAULT '',"
        " provider TEXT NOT NULL DEFAULT '',"
        " verification_status TEXT NOT NULL DEFAULT 'unknown',"
        " metadata_json TEXT NOT NULL DEFAULT '{}',"
        " created_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS evidence_snapshots ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " source_id INTEGER NOT NULL REFERENCES sources(id),"
        " requested_url TEXT NOT NULL,"
        " final_url TEXT NOT NULL DEFAULT '',"
        " fetched_at TEXT NOT NULL,"
        " mime TEXT NOT NULL DEFAULT '',"
        " content_hash TEXT NOT NULL DEFAULT '',"
        " excerpt TEXT NOT NULL DEFAULT '',"
        " fetch_status TEXT NOT NULL DEFAULT 'ok',"
        " created_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS materials ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " project_id INTEGER NOT NULL REFERENCES projects(id),"
        " source_id INTEGER REFERENCES sources(id),"
        " title TEXT NOT NULL,"
        " content TEXT NOT NULL DEFAULT '',"
        " created_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS citations ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " article_id INTEGER NOT NULL REFERENCES articles(id),"
        " block_id TEXT NOT NULL,"
        " source_id INTEGER NOT NULL REFERENCES sources(id),"
        " evidence_snapshot_id INTEGER REFERENCES evidence_snapshots(id),"
        " quote TEXT NOT NULL DEFAULT '',"
        " locator TEXT NOT NULL DEFAULT '',"
        " display_label TEXT NOT NULL DEFAULT '',"
        " verification_status TEXT NOT NULL DEFAULT 'unknown',"
        " status TEXT NOT NULL DEFAULT 'active',"
        " created_at TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS idx_citations_article ON citations(article_id)",
        "CREATE INDEX IF NOT EXISTS idx_sources_project ON sources(project_id)",
    ],
]


def connect(path: str | None = None) -> sqlite3.Connection:
    """打开连接；path 为 None 时用默认 DB_PATH。启用 FK / busy_timeout / WAL。"""
    if path is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        path = DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """幂等迁移：每个版本在独立事务中应用并记录；失败整体回滚。"""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY,"
        " applied_at TEXT NOT NULL)"
    )
    applied = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
    for i, stmts in enumerate(MIGRATIONS, start=1):
        if i in applied:
            continue
        try:
            for stmt in stmts:
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (i, _now()),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def init(conn: sqlite3.Connection) -> None:
    """兼容别名：等价于 migrate。"""
    migrate(conn)


def blocks_hash(blocks: list) -> str:
    return hashlib.sha256(
        json.dumps(blocks, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def create_project(conn: sqlite3.Connection, name: str) -> int:
    cur = conn.execute(
        "INSERT INTO projects (name, created_at) VALUES (?, ?)", (name, _now())
    )
    conn.commit()
    return cur.lastrowid


def rename_project(conn: sqlite3.Connection, pid: int, name: str) -> None:
    conn.execute("UPDATE projects SET name = ? WHERE id = ?", (name, pid))
    conn.commit()


def list_projects(conn: sqlite3.Connection) -> list[tuple]:
    """返回 [(id, name)]，按创建时间排序。"""
    rows = conn.execute("SELECT id, name FROM projects ORDER BY id").fetchall()
    return [(r["id"], r["name"]) for r in rows]


def create_article(conn: sqlite3.Connection, project_id: int, title: str) -> int:
    """创建草稿前验证项目存在；不存在抛 NotFoundError（不产生孤儿草稿）。"""
    row = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"project {project_id}")
    now = _now()
    cur = conn.execute(
        "INSERT INTO articles (project_id, title, blocks_json, created_at, updated_at) VALUES (?, ?, '[]', ?, ?)",
        (project_id, title, now, now),
    )
    conn.commit()
    return cur.lastrowid


def list_articles(conn: sqlite3.Connection, project_id: int) -> list[tuple]:
    """返回 [(id, title, updated_at)]。"""
    rows = conn.execute(
        "SELECT id, title, updated_at FROM articles WHERE project_id = ? ORDER BY updated_at DESC",
        (project_id,),
    ).fetchall()
    return [(r["id"], r["title"], r["updated_at"]) for r in rows]


def get_article(conn: sqlite3.Connection, aid: int) -> dict | None:
    row = conn.execute("SELECT * FROM articles WHERE id = ?", (aid,)).fetchone()
    if row is None:
        return None
    blocks = json.loads(row["blocks_json"] or "[]")
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "title": row["title"],
        "blocks": blocks,
        "version": row["version"],
        "blocks_hash": blocks_hash(blocks),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def save_article(
    conn: sqlite3.Connection,
    aid: int,
    title: str | None = None,
    blocks: list | None = None,
    base_version: int | None = None,
    reason: str = "autosave",
) -> int:
    """原子保存：标题/正文 + version 递增 + 必要 revision 同一事务。

    - 文章不存在 → NotFoundError
    - base_version 与当前版本不符 → VersionConflict（服务端当前版本随异常携带）
    - 返回新 version
    """
    row = conn.execute("SELECT version FROM articles WHERE id = ?", (aid,)).fetchone()
    if row is None:
        raise NotFoundError(f"article {aid}")
    if base_version is not None and base_version != row["version"]:
        raise VersionConflict(aid, row["version"])

    new_version = row["version"] + 1
    sets: list[str] = []
    params: list = []
    if title is not None and title.strip():
        sets.append("title = ?")
        params.append(title.strip())
    if blocks is not None:
        sets.append("blocks_json = ?")
        params.append(json.dumps(blocks, ensure_ascii=False))
    sets.append("version = ?")
    params.append(new_version)
    sets.append("updated_at = ?")
    params.append(_now())
    params.append(aid)

    cur = conn.execute(f"UPDATE articles SET {', '.join(sets)} WHERE id = ?", params)
    if cur.rowcount == 0:
        conn.rollback()
        raise NotFoundError(f"article {aid}")

    if blocks is not None and reason in REVISION_REASONS:
        conn.execute(
            "INSERT INTO article_revisions (article_id, version, blocks_json, reason, created_at) VALUES (?, ?, ?, ?, ?)",
            (aid, new_version, json.dumps(blocks, ensure_ascii=False), reason, _now()),
        )
    conn.commit()
    return new_version


def save_article_blocks(conn: sqlite3.Connection, aid: int, blocks: list) -> None:
    """兼容薄封装：仅正文保存（不校验版本），供旧调用/测试使用。"""
    save_article(conn, aid, blocks=blocks)


def update_article_title(conn: sqlite3.Connection, aid: int, title: str) -> None:
    """兼容薄封装：仅标题保存（不校验版本），供旧调用/测试使用。"""
    save_article(conn, aid, title=title)


# ---------- 证据数据层（v3）：sources / materials / citations ----------

def create_source(conn, project_id: int, url: str, title: str = "", snippet: str = "", provider: str = "") -> int:
    """创建来源；同 project 同 url 复用已有（不重复建）。"""
    row = conn.execute(
        "SELECT id FROM sources WHERE project_id = ? AND url = ?", (project_id, url)
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO sources (project_id, url, canonical_url, title, snippet, provider, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (project_id, url, url, title[:200], snippet[:500], provider, _now()),
    )
    conn.commit()
    return cur.lastrowid


def list_sources(conn, project_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM sources WHERE project_id = ? ORDER BY id DESC", (project_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def create_material(conn, project_id: int, title: str, content: str = "", source_id: int | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO materials (project_id, source_id, title, content, created_at) VALUES (?, ?, ?, ?, ?)",
        (project_id, source_id, title[:200], content, _now()),
    )
    conn.commit()
    return cur.lastrowid


def list_materials(conn, project_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM materials WHERE project_id = ? ORDER BY id DESC", (project_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def create_citation(conn, article_id: int, block_id: str, source_id: int,
                    quote: str = "", locator: str = "", display_label: str = "") -> int:
    # 越权校验：来源必须属于该文章所在项目
    row = conn.execute(
        "SELECT a.project_id AS ap, s.project_id AS sp FROM articles a, sources s"
        " WHERE a.id = ? AND s.id = ?",
        (article_id, source_id),
    ).fetchone()
    if row is None:
        raise NotFoundError("文章或来源不存在")
    if row["ap"] != row["sp"]:
        raise NotFoundError("来源不属于该文章项目")
    row = conn.execute(
        "SELECT id FROM citations WHERE article_id = ? AND block_id = ? AND source_id = ?",
        (article_id, block_id, source_id),
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO citations (article_id, block_id, source_id, quote, locator, display_label, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (article_id, block_id, source_id, quote[:500], locator[:200], display_label[:200], _now()),
    )
    conn.commit()
    return cur.lastrowid


def list_citations(conn, article_id: int) -> list[dict]:
    """引用列表（含来源信息）；机械检查：Block 已删 → 标记 orphaned（落库）。"""
    rows = conn.execute(
        "SELECT c.*, s.url AS source_url, s.title AS source_title, s.provider AS source_provider,"
        " s.verification_status AS source_verification"
        " FROM citations c JOIN sources s ON s.id = c.source_id"
        " WHERE c.article_id = ? ORDER BY c.id", (article_id,)
    ).fetchall()
    art = get_article(conn, article_id)
    block_ids = {b["id"] for b in (art["blocks"] if art else [])}
    out = []
    for r in rows:
        d = dict(r)
        if d["status"] == "active" and d["block_id"] not in block_ids:
            conn.execute("UPDATE citations SET status = 'orphaned' WHERE id = ?", (d["id"],))
            d["status"] = "orphaned"
        out.append(d)
    if out:
        conn.commit()
    return out


def delete_citation(conn, cid: int) -> bool:
    cur = conn.execute("DELETE FROM citations WHERE id = ?", (cid,))
    conn.commit()
    return cur.rowcount > 0
