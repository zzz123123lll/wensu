"""SQLite 持久化层：projects + articles（blocks 以 JSON 存储）。

第一阶段最小实现：项目/草稿/正文块 三件事。
所有函数接收 conn（sqlite3.Connection），便于测试用 :memory:。
"""

import json
import os
import sqlite3
from datetime import datetime, timezone

# 默认数据库路径（可通过 main 或环境变量覆盖）
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "workbench.db")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(path: str | None = None) -> sqlite3.Connection:
    """打开连接；path 为 None 时用默认 DB_PATH。"""
    if path is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        path = DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS articles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id),
        title TEXT NOT NULL,
        blocks_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """)
    conn.commit()


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
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "title": row["title"],
        "blocks": json.loads(row["blocks_json"] or "[]"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def save_article_blocks(conn: sqlite3.Connection, aid: int, blocks: list) -> None:
    conn.execute(
        "UPDATE articles SET blocks_json = ?, updated_at = ? WHERE id = ?",
        (json.dumps(blocks, ensure_ascii=False), _now(), aid),
    )
    conn.commit()


def update_article_title(conn: sqlite3.Connection, aid: int, title: str) -> None:
    conn.execute(
        "UPDATE articles SET title = ?, updated_at = ? WHERE id = ?",
        (title, _now(), aid),
    )
    conn.commit()
