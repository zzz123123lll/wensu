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
import sys
from datetime import datetime, timezone

from app.settings import _decrypt as settings_decrypt

# 默认数据库路径（基于文件位置，任意 CWD 可启动；WENSU_DB 环境变量可覆盖——测试隔离用）
# 冻结（PyInstaller 打包）模式：用户数据放 %APPDATA%\Wensu（安装目录不可写，卸载不丢数据）
def _default_db_dir() -> str:
    if getattr(sys, "frozen", False):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "Wensu")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def resolve_db_path(env: dict | None = None) -> str:
    env = os.environ if env is None else env
    return env.get("WENSU_DB") or os.path.join(_default_db_dir(), "workbench.db")


DB_PATH = resolve_db_path()

# 受控 Revision 原因（P0-4 统一契约）：AI 接受/核验/冲突恢复/素材插入/Ask 插入/版本恢复。
# 普通 autosave 不制造历史记录（v0.2 无历史 UI）。
REVISION_REASONS = ("ai_rewrite", "ai_check", "conflict_recovery", "material_insert", "ask_insert", "revision_restore")


class NotFoundError(Exception):
    def __init__(self, what: str):
        super().__init__(what)
        self.what = what


class VersionConflict(Exception):
    def __init__(self, article_id: int, current_version: int):
        super().__init__(f"version conflict: article {article_id} at {current_version}")
        self.article_id = article_id
        self.current_version = current_version


class RevisionNoBefore(Exception):
    """point=before 但该 Revision 无修改前快照（旧数据兼容防护）。"""

    def __init__(self, what: str):
        super().__init__(what)
        self.what = what


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
    # v4：回收站（软删除）
    [
        "ALTER TABLE articles ADD COLUMN deleted_at TEXT",
        "ALTER TABLE projects ADD COLUMN deleted_at TEXT",
    ],
    # v5：Ask 历史 / 作者记忆 / 多模型 profile
    [
        "CREATE TABLE IF NOT EXISTS article_asks ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " article_id INTEGER NOT NULL REFERENCES articles(id),"
        " prompt TEXT NOT NULL,"
        " response TEXT NOT NULL DEFAULT '',"
        " model TEXT NOT NULL DEFAULT '',"
        " created_at TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS idx_asks_article ON article_asks(article_id, id)",
        "CREATE TABLE IF NOT EXISTS author_prefs ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " key TEXT NOT NULL UNIQUE,"
        " content TEXT NOT NULL,"
        " source TEXT NOT NULL DEFAULT 'user',"
        " created_at TEXT NOT NULL,"
        " updated_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS model_profiles ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " name TEXT NOT NULL,"
        " base_url TEXT NOT NULL,"
        " model TEXT NOT NULL,"
        " api_key_enc BLOB,"
        " capabilities TEXT NOT NULL DEFAULT 'json_mode,stream',"
        " enabled INTEGER NOT NULL DEFAULT 1,"
        " created_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS task_bindings ("
        " task TEXT PRIMARY KEY,"
        " profile_id INTEGER NOT NULL REFERENCES model_profiles(id))",
    ],
    # v6：成稿检查（review_sessions/issues/variant_patches/exports/规则覆盖）
    [
        "CREATE TABLE IF NOT EXISTS review_rule_overrides ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " rule_id TEXT NOT NULL UNIQUE,"
        " patch_json TEXT NOT NULL,"
        " updated_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS review_custom_rules ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " rule_json TEXT NOT NULL,"
        " enabled INTEGER NOT NULL DEFAULT 1,"
        " created_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS review_sessions ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " article_id INTEGER NOT NULL REFERENCES articles(id),"
        " article_version INTEGER NOT NULL,"
        " blocks_json TEXT NOT NULL,"
        " citations_json TEXT NOT NULL DEFAULT '[]',"
        " snapshot_hash TEXT NOT NULL,"
        " profile_json TEXT NOT NULL,"
        " status TEXT NOT NULL DEFAULT 'draft',"
        " error TEXT NOT NULL DEFAULT '',"
        " created_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS review_issues ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " review_id INTEGER NOT NULL REFERENCES review_sessions(id),"
        " fingerprint TEXT NOT NULL,"
        " rule_id TEXT NOT NULL,"
        " severity TEXT NOT NULL,"
        " anchor_json TEXT NOT NULL,"
        " suggestion_json TEXT NOT NULL DEFAULT '{}',"
        " reason TEXT NOT NULL DEFAULT '',"
        " source_type TEXT NOT NULL DEFAULT 'system',"
        " state TEXT NOT NULL DEFAULT 'open',"
        " created_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS review_variant_patches ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " review_id INTEGER NOT NULL REFERENCES review_sessions(id),"
        " target TEXT NOT NULL,"
        " rule_id TEXT NOT NULL,"
        " block_id TEXT NOT NULL,"
        " selection_json TEXT NOT NULL,"
        " original_hash TEXT NOT NULL,"
        " replacement TEXT NOT NULL,"
        " status TEXT NOT NULL DEFAULT 'proposed',"
        " confirmed_at TEXT)",
        "CREATE TABLE IF NOT EXISTS review_exports ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " review_id INTEGER NOT NULL REFERENCES review_sessions(id),"
        " article_version INTEGER NOT NULL,"
        " target TEXT NOT NULL,"
        " manifest_json TEXT NOT NULL,"
        " created_at TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS idx_review_issues_review ON review_issues(review_id)",
        "CREATE INDEX IF NOT EXISTS idx_review_patches_review ON review_variant_patches(review_id)",
    ],
    # v7：下一阶段进化方案 阶段1 对象扩展
    # - materials：标签/元数据（访问时间、保存方式、使用状态、相关草稿）
    # - article_asks：元数据（来源范围、是否已转素材/正文）
    # - citations：核验状态语义化（待核验/支持/支持不足/冲突/来源失效/正文变化需复查）
    [
        "ALTER TABLE materials ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE materials ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE article_asks ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE citations ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'",
        "CREATE INDEX IF NOT EXISTS idx_materials_project ON materials(project_id)",
    ],
    # v8（Gate B）：Material 显式使用关系 + Revision 契约扩展
    # - material_usages：Material↔Draft/Citation 的真实关系（不再靠共享 source_id 推断）
    # - article_revisions：before 快照 / 作用范围 / 来源对象 / 处理状态（统一 Revision 契约）
    [
        "CREATE TABLE IF NOT EXISTS material_usages ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " material_id INTEGER NOT NULL REFERENCES materials(id) ON DELETE CASCADE,"
        " article_id INTEGER NOT NULL REFERENCES articles(id),"
        " block_id TEXT,"
        " claim_id INTEGER REFERENCES citations(id),"
        " usage_type TEXT NOT NULL DEFAULT 'insert',"
        " created_at TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS idx_mat_usage_material ON material_usages(material_id)",
        "CREATE INDEX IF NOT EXISTS idx_mat_usage_article ON material_usages(article_id)",
        "ALTER TABLE article_revisions ADD COLUMN before_blocks_json TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE article_revisions ADD COLUMN scope TEXT NOT NULL DEFAULT 'blocks'",
        "ALTER TABLE article_revisions ADD COLUMN source_object_type TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE article_revisions ADD COLUMN source_object_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE article_revisions ADD COLUMN status TEXT NOT NULL DEFAULT 'applied'",
        # P1-5：继续写位置（本地写作状态；不进模型上下文，不随正文保存）
        "ALTER TABLE articles ADD COLUMN editor_state_json TEXT NOT NULL DEFAULT '{}'",
    ],
    # v9（发布三件套）：publish_targets（配置 DPAPI 加密落盘）+ publish_logs（发布历史，诚实记录）
    [
        "CREATE TABLE IF NOT EXISTS publish_targets ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " name TEXT NOT NULL UNIQUE,"
        " kind TEXT NOT NULL CHECK (kind IN ('webhook','local')),"
        " config_enc BLOB NOT NULL,"
        " enabled INTEGER NOT NULL DEFAULT 1,"
        " created_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS publish_logs ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " article_id INTEGER,"
        " target_id INTEGER,"
        " fmt TEXT NOT NULL,"
        " status TEXT NOT NULL,"
        " message TEXT NOT NULL DEFAULT '',"
        " created_at TEXT NOT NULL)",
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
    """返回 [(id, name)]，按创建时间排序（不含回收站）。"""
    rows = conn.execute(
        "SELECT id, name FROM projects WHERE deleted_at IS NULL ORDER BY id"
    ).fetchall()
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
    """返回 [(id, title, updated_at)]（不含回收站）。"""
    rows = conn.execute(
        "SELECT id, title, updated_at FROM articles WHERE project_id = ? AND deleted_at IS NULL"
        " ORDER BY updated_at DESC",
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


def diff_block_texts(old_blocks: list, new_blocks: list) -> tuple[set, set]:
    """正文块 diff：返回 (changed_ids, deleted_ids)。

    - changed：文本/类型实质变化的既有 block，以及新增 block（防御性：若被引用则失效）
    - deleted：旧正文存在但新正文缺失的 block（对应 Citation 进入 orphaned）
    只比较 id/text/type——attrs 变化不视为正文主张实质变化。
    """
    old_map = {b.get("id"): b for b in (old_blocks or []) if b.get("id")}
    new_map = {b.get("id"): b for b in (new_blocks or []) if b.get("id")}
    changed = {
        bid for bid, nb in new_map.items()
        if bid not in old_map
        or old_map[bid].get("text") != nb.get("text")
        or old_map[bid].get("type") != nb.get("type")
    }
    deleted = {bid for bid in old_map if bid not in new_map}
    return changed, deleted


def save_article(
    conn: sqlite3.Connection,
    aid: int,
    title: str | None = None,
    blocks: list | None = None,
    base_version: int | None = None,
    reason: str = "autosave",
    before_blocks: list | None = None,
    source_object_type: str = "",
    source_object_id: str = "",
    scope: str = "blocks",
    material_usage: tuple | None = None,
) -> int:
    """原子保存：标题/正文 + version 递增 + Revision + Citation 失效/孤立 同一事务。

    - 文章不存在 → NotFoundError（写前抛出，零副作用）
    - base_version 与当前版本不符 → VersionConflict（写前抛出，零副作用）
    - before_blocks：覆盖前的旧正文快照（调用方先读再写）；据此计算
      changed/deleted block，同事务内把相关活动 Citation 置 needs_recheck /
      orphaned——任何异常整体回滚，不产生半成品。
    - material_usage=(material_id, block_id)：素材插入正文时同事务记录显式使用关系
      （P0-6；素材不存在/跨项目 → NotFoundError，整体回滚）
    - 返回新 version
    """
    row = conn.execute("SELECT version FROM articles WHERE id = ?", (aid,)).fetchone()
    if row is None:
        raise NotFoundError(f"article {aid}")
    if base_version is not None and base_version != row["version"]:
        raise VersionConflict(aid, row["version"])

    new_version = row["version"] + 1
    changed_ids: set[str] = set()
    deleted_ids: set[str] = set()
    if blocks is not None:
        # before_blocks 未显式传入时：事务内读覆盖前的数据库快照（兼容旧调用路径）
        if before_blocks is None:
            old_row = conn.execute(
                "SELECT blocks_json FROM articles WHERE id = ?", (aid,)
            ).fetchone()
            try:
                before_blocks = json.loads(old_row["blocks_json"] or "[]") if old_row else []
            except ValueError:
                before_blocks = []
        changed_ids, deleted_ids = diff_block_texts(before_blocks, blocks)

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

    # 统一 Revision 契约（P0-4）：受控原因 + 正文实质变化才记录；before/after 都在
    if blocks is not None and reason in REVISION_REASONS and (changed_ids or deleted_ids):
        conn.execute(
            "INSERT INTO article_revisions (article_id, version, blocks_json, before_blocks_json,"
            " reason, scope, source_object_type, source_object_id, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (aid, new_version, json.dumps(blocks, ensure_ascii=False),
             json.dumps(before_blocks or [], ensure_ascii=False),
             reason, scope, source_object_type, source_object_id, "applied", _now()),
        )

    # 正文主张实质变化 → 相关活动 Citation 自动 needs_recheck（同一事务，P0-1）
    if changed_ids:
        _invalidate_citations(conn, aid, changed_ids)
    # 被删除 Block 的 Citation → 明确 orphaned（同一事务）
    if deleted_ids:
        _orphan_citations(conn, aid, deleted_ids)

    # 素材插入 → 显式使用关系（同一事务；同项目校验）
    if material_usage is not None:
        mid_, bid_ = material_usage
        row = conn.execute(
            "SELECT m.project_id AS mp, a.project_id AS ap FROM materials m, articles a"
            " WHERE m.id = ? AND a.id = ?", (mid_, aid),
        ).fetchone()
        if row is None:
            conn.rollback()
            raise NotFoundError(f"素材 {mid_} 或草稿 {aid} 不存在")
        if row["mp"] != row["ap"]:
            conn.rollback()
            raise NotFoundError("素材与草稿不属于同一项目")
        conn.execute(
            "INSERT INTO material_usages (material_id, article_id, block_id, usage_type, created_at)"
            " VALUES (?, ?, ?, 'insert', ?)",
            (mid_, aid, bid_, _now()),
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


def create_material(conn, project_id: int, title: str, content: str = "", source_id: int | None = None, tags: list[str] | None = None) -> int:
    """创建素材；source_id 提供时必须存在且属于同一项目（P1-2 越权防护）。"""
    if source_id is not None:
        row = conn.execute(
            "SELECT project_id FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"来源 {source_id} 不存在")
        if row["project_id"] != project_id:
            raise NotFoundError("来源不属于该素材项目")
    meta = {"saved_via": "search", "accessed_at": _now(), "usage": "unused"}
    cur = conn.execute(
        "INSERT INTO materials (project_id, source_id, title, content, tags, metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (project_id, source_id, title[:200], content, json.dumps(tags or [], ensure_ascii=False), json.dumps(meta, ensure_ascii=False), _now()),
    )
    conn.commit()
    return cur.lastrowid


def list_materials(conn, project_id: int | None = None, q: str = "", tag: str = "", source_type: str = "") -> list[dict]:
    sql = ("SELECT m.*, s.url, s.canonical_url, s.title AS source_title, s.provider "
           "FROM materials m LEFT JOIN sources s ON s.id = m.source_id WHERE 1=1")
    args: list = []
    if project_id is not None:
        sql += " AND m.project_id = ?"
        args.append(project_id)
    if q:
        sql += " AND (m.title LIKE ? OR m.content LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    if tag:
        sql += " AND m.tags LIKE ?"
        args.append(f'%"{tag}"%')
    if source_type:
        sql += " AND COALESCE(s.provider, '') = ?"
        args.append(source_type)
    sql += " ORDER BY m.id DESC"
    rows = conn.execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["tags"] = json.loads(d.get("tags") or "[]")
        except Exception:
            d["tags"] = []
        try:
            d["metadata"] = json.loads(d.get("metadata_json") or "{}")
        except Exception:
            d["metadata"] = {}
        out.append(d)
    return out


def get_material(conn, material_id: int) -> dict | None:
    """单素材读取：只查一行（避免 list_materials 全表扫描+LEFT JOIN）。"""
    row = conn.execute(
        "SELECT m.*, s.url, s.canonical_url, s.title AS source_title, s.provider"
        " FROM materials m LEFT JOIN sources s ON s.id = m.source_id"
        " WHERE m.id = ?", (material_id,),
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    try:
        d["tags"] = json.loads(d.get("tags") or "[]")
    except Exception:
        d["tags"] = []
    try:
        d["metadata"] = json.loads(d.get("metadata_json") or "{}")
    except Exception:
        d["metadata"] = {}
    return d


def record_material_usage(conn: sqlite3.Connection, material_id: int, article_id: int,
                          block_id: str | None = None, claim_id: int | None = None,
                          usage_type: str = "insert") -> int:
    """记录 Material→Draft 显式使用关系（P0-6：不再靠共享 source_id 推断）。

    - 同项目校验：素材与草稿必须属于同一 Project（P1-2），否则 NotFoundError
    - usage_type：insert（素材插入正文）| citation（素材关联引用）
    """
    row = conn.execute(
        "SELECT m.project_id AS mp, a.project_id AS ap FROM materials m, articles a"
        " WHERE m.id = ? AND a.id = ?", (material_id, article_id),
    ).fetchone()
    if row is None:
        raise NotFoundError("素材或草稿不存在")
    if row["mp"] != row["ap"]:
        raise NotFoundError("素材与草稿不属于同一项目")
    cur = conn.execute(
        "INSERT INTO material_usages (material_id, article_id, block_id, claim_id, usage_type, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (material_id, article_id, block_id, claim_id, usage_type, _now()),
    )
    conn.commit()
    return cur.lastrowid


def list_material_usages(conn: sqlite3.Connection, material_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT mu.id, mu.article_id, mu.block_id, mu.claim_id, mu.usage_type, mu.created_at,"
        " a.title AS article_title"
        " FROM material_usages mu JOIN articles a ON a.id = mu.article_id"
        " WHERE mu.material_id = ? ORDER BY mu.id", (material_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def unlink_material(conn: sqlite3.Connection, material_id: int) -> int:
    """解除素材的全部使用关系（保留 Material/Citation/正文）。返回解除条数。"""
    cur = conn.execute("DELETE FROM material_usages WHERE material_id = ?", (material_id,))
    conn.commit()
    return cur.rowcount


def material_usage(conn, material_id: int) -> dict:
    """真实影响范围：基于 material_usages 显式关系表。

    - material 不存在 → {"material": None, ...}
    - usages：素材被哪些草稿/引用使用（旧数据无记录 → 空列表，不伪造）
    """
    m = conn.execute("SELECT * FROM materials WHERE id = ?", (material_id,)).fetchone()
    if m is None:
        return {"material": None, "usages": [], "citations": [], "articles": []}
    usages = list_material_usages(conn, material_id)
    citations: list[dict] = []
    if usages:
        claim_ids = [u["claim_id"] for u in usages if u["claim_id"]]
        if claim_ids:
            marks = ",".join("?" * len(claim_ids))
            rows = conn.execute(
                "SELECT c.*, s.url AS source_url, s.title AS source_title"
                " FROM citations c JOIN sources s ON s.id = c.source_id"
                " WHERE c.id IN (" + marks + ")", claim_ids,
            ).fetchall()
            citations = [dict(r) for r in rows]
    return {
        "material": dict(m),
        "usages": usages,
        "citations": citations,
        "articles": sorted({u["article_id"] for u in usages}),
    }


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


def create_evidence_snapshot(conn, source_id: int, requested_url: str, final_url: str = "",
                             mime: str = "", content_hash: str = "", excerpt: str = "",
                             fetch_status: str = "ok") -> int:
    cur = conn.execute(
        "INSERT INTO evidence_snapshots (source_id, requested_url, final_url, fetched_at,"
        " mime, content_hash, excerpt, fetch_status, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (source_id, requested_url, final_url, _now(), mime[:100], content_hash[:64], excerpt[:2000],
         fetch_status, _now()),
    )
    conn.commit()
    return cur.lastrowid


def get_source(conn, sid: int) -> dict | None:
    row = conn.execute("SELECT * FROM sources WHERE id = ?", (sid,)).fetchone()
    return dict(row) if row else None


# ---------- 回收站 / 历史 / 导出（v4） ----------

def soft_delete_article(conn, aid: int) -> bool:
    cur = conn.execute("UPDATE articles SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL", (_now(), aid))
    conn.commit()
    return cur.rowcount > 0


def restore_article(conn, aid: int) -> bool:
    # 恢复文章时若原项目已被删，一并恢复（否则文章成为孤儿：数据在但 UI 不可见，dogfood Bug#11）
    row = conn.execute("SELECT project_id FROM articles WHERE id = ?", (aid,)).fetchone()
    if row is None:
        return False
    conn.execute(
        "UPDATE projects SET deleted_at = NULL WHERE id = ? AND deleted_at IS NOT NULL",
        (row["project_id"],),
    )
    cur = conn.execute("UPDATE articles SET deleted_at = NULL WHERE id = ?", (aid,))
    conn.commit()
    return cur.rowcount > 0


def list_trash(conn, project_id: int | None = None) -> list[dict]:
    sel = "SELECT id, project_id, title, deleted_at, updated_at FROM articles"
    if project_id is not None:
        rows = conn.execute(
            sel + " WHERE deleted_at IS NOT NULL AND project_id = ? ORDER BY deleted_at DESC",
            (project_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            sel + " WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def soft_delete_project(conn, pid: int) -> bool:
    cur = conn.execute("UPDATE projects SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL", (_now(), pid))
    conn.commit()
    if cur.rowcount:
        conn.execute("UPDATE articles SET deleted_at = ? WHERE project_id = ? AND deleted_at IS NULL", (_now(), pid))
        conn.commit()
        return True
    return False


def list_revisions(conn, aid: int) -> list[dict]:
    """版本记录（统一 Revision 契约字段：before/after、作用范围、来源对象、状态）。"""
    rows = conn.execute(
        "SELECT id, version, blocks_json, before_blocks_json, reason, scope,"
        " source_object_type, source_object_id, status, created_at"
        " FROM article_revisions WHERE article_id = ? ORDER BY version DESC", (aid,)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["after_blocks"] = json.loads(d.pop("blocks_json") or "[]")
        d["before_blocks"] = json.loads(d.pop("before_blocks_json") or "[]")
        out.append(d)
    return out


def get_revision(conn, aid: int, version: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM article_revisions WHERE article_id = ? AND version = ?", (aid, version)
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["blocks"] = json.loads(d.pop("blocks_json") or "[]")
    d["before_blocks"] = json.loads(d.pop("before_blocks_json") or "[]")
    return d


def restore_revision(conn, aid: int, version: int, point: str = "after") -> int:
    """恢复历史版本并升新版本。

    - point=after：恢复到该 Revision 的 after 快照（默认，等同该版本正文）
    - point=before：恢复到该 Revision 的 before 快照（= 撤销这一次插入/改写）
    - reason=revision_restore（受控枚举，恢复动作本身也留 Revision，可再恢复）
    - 恢复会触发正文变化检测：引用该正文的 Citation 自动 needs_recheck
    - 防护：旧数据 Revision 无 before 快照（v8 前 ai_rewrite 等未记录）时，
      point=before 拒绝（400 语义），防止把全文恢复成空数组清空文章
    """
    rev = get_revision(conn, aid, version)
    if rev is None:
        raise NotFoundError(f"版本 {version} 不存在")
    if point == "before" and not rev["before_blocks"]:
        raise RevisionNoBefore(f"版本 {version} 没有修改前快照（旧数据），无法撤销；请改用恢复此版本")
    target = rev["before_blocks"] if point == "before" else rev["blocks"]
    # 乐观锁：以当前版本为基线，避免与并发 autosave 竞争静默覆盖（审查建议）
    cur_row = conn.execute("SELECT version FROM articles WHERE id = ?", (aid,)).fetchone()
    cur_version = cur_row["version"] if cur_row else None
    return save_article(
        conn, aid, blocks=target, base_version=cur_version,
        reason="revision_restore", source_object_type="revision",
        source_object_id=f"{version}:{point}",
    )


# ---------- Phase 7：Ask 历史 / 作者记忆 / 多模型 ----------

ASK_KEEP = 30   # checkpoint：超过即裁剪，保留最近 30 条

def delete_ask(conn: sqlite3.Connection, ask_id: int) -> bool:
    """删除一条 Ask 历史。"""
    cur = conn.execute("DELETE FROM article_asks WHERE id = ?", (ask_id,))
    conn.commit()
    return cur.rowcount > 0


def get_ask(conn: sqlite3.Connection, ask_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM article_asks WHERE id = ?", (ask_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    try:
        d["metadata"] = json.loads(d.get("metadata_json") or "{}")
    except ValueError:
        d["metadata"] = {}
    return d


def record_ask(conn, article_id: int, prompt: str, response: str = "", model: str = "", metadata: dict | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO article_asks (article_id, prompt, response, model, metadata_json, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (article_id, prompt[:4000], response[:8000], model, json.dumps(metadata or {}, ensure_ascii=False), _now()),
    )
    conn.commit()
    n = conn.execute("SELECT COUNT(*) AS n FROM article_asks WHERE article_id = ?", (article_id,)).fetchone()["n"]
    if n > ASK_KEEP:
        conn.execute(
            "DELETE FROM article_asks WHERE article_id = ? AND id NOT IN ("
            " SELECT id FROM article_asks WHERE article_id = ? ORDER BY id DESC LIMIT ?)",
            (article_id, article_id, ASK_KEEP),
        )
        conn.commit()
    return cur.lastrowid


def list_asks(conn, article_id: int, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        "SELECT id, prompt, response, model, metadata_json, created_at FROM article_asks"
        " WHERE article_id = ? ORDER BY id DESC LIMIT ?", (article_id, limit)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["metadata"] = json.loads(d.get("metadata_json") or "{}")
        except Exception:
            d["metadata"] = {}
        out.append(d)
    return out


def set_ask_usage(conn, ask_id: int, usage: str) -> bool:
    """标记 Ask 结果的使用状态：saved_as_material / inserted_to_body。方案 B 动作命名固定。"""
    row = conn.execute("SELECT metadata_json FROM article_asks WHERE id = ?", (ask_id,)).fetchone()
    if row is None:
        return False
    try:
        meta = json.loads(row["metadata_json"] or "{}")
    except Exception:
        meta = {}
    meta["usage"] = usage
    conn.execute("UPDATE article_asks SET metadata_json = ? WHERE id = ?",
                 (json.dumps(meta, ensure_ascii=False), ask_id))
    conn.commit()
    return True


VERIF_PENDING, VERIF_SUPPORTED, VERIF_INSUFFICIENT, VERIF_CONFLICTING, VERIF_SOURCE_DEAD, VERIF_NEEDS_RECHECK = (
    "pending", "supported", "insufficient", "conflicting", "source_dead", "needs_recheck")


def set_citation_verification(conn, citation_id: int, status: str, note: str = "") -> bool:
    meta = {"note": note, "checked_at": _now()}
    cur = conn.execute(
        "UPDATE citations SET verification_status = ?, metadata_json = ? WHERE id = ?",
        (status, json.dumps(meta, ensure_ascii=False), citation_id),
    )
    conn.commit()
    return cur.rowcount > 0


def _invalidate_citations(conn: sqlite3.Connection, aid: int, changed_block_ids: set[str]) -> None:
    """事务内原语：正文实质变化 → 活动 Citation 置 needs_recheck（不提交，由外层事务控制）。"""
    if not changed_block_ids:
        return
    marks = ",".join("?" * len(changed_block_ids))
    conn.execute(
        f"UPDATE citations SET verification_status = '{VERIF_NEEDS_RECHECK}'"
        " WHERE article_id = ? AND block_id IN (" + marks + ") AND status = 'active'"
        " AND verification_status != '" + VERIF_NEEDS_RECHECK + "'",
        (aid, *changed_block_ids),
    )


def _orphan_citations(conn: sqlite3.Connection, aid: int, deleted_block_ids: set[str]) -> None:
    """事务内原语：被删除 Block 的 Citation → 明确 orphaned（不提交，由外层事务控制）。"""
    if not deleted_block_ids:
        return
    marks = ",".join("?" * len(deleted_block_ids))
    conn.execute(
        "UPDATE citations SET status = 'orphaned'"
        " WHERE article_id = ? AND block_id IN (" + marks + ") AND status = 'active'",
        (aid, *deleted_block_ids),
    )


def invalidate_citations_on_edit(conn, aid: int, changed_block_ids: set[str]) -> int:
    """兼容入口（旧测试直接调用）：正文块实质变化 → 活动 Citation 失效并提交。"""
    if not changed_block_ids:
        return 0
    _invalidate_citations(conn, aid, changed_block_ids)
    conn.commit()
    # 返回受影响行数
    marks = ",".join("?" * len(changed_block_ids))
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM citations WHERE article_id = ? AND block_id IN ("
        + marks + ") AND status = 'active'",
        (aid, *changed_block_ids),
    ).fetchone()
    return row["n"]


def save_editor_state(conn: sqlite3.Connection, aid: int, state: dict) -> bool:
    """保存本地写作状态（继续写位置：block_id/offset/scroll_top）。

    与正文分离：位置是本地写作状态，不泄漏到模型上下文，也不随正文保存。
    """
    cur = conn.execute(
        "UPDATE articles SET editor_state_json = ? WHERE id = ?",
        (json.dumps(state, ensure_ascii=False), aid),
    )
    conn.commit()
    return cur.rowcount > 0


def get_editor_state(conn: sqlite3.Connection, aid: int) -> dict:
    row = conn.execute(
        "SELECT editor_state_json FROM articles WHERE id = ?", (aid,)
    ).fetchone()
    if not row:
        return {}
    try:
        state = json.loads(row["editor_state_json"] or "{}")
    except ValueError:
        return {}
    return state if isinstance(state, dict) else {}


def add_pref(conn, key: str, content: str, source: str = "user") -> int:
    conn.execute(
        "INSERT INTO author_prefs (key, content, source, created_at, updated_at) VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(key) DO UPDATE SET content = excluded.content, updated_at = excluded.updated_at",
        (key, content[:500], source, _now(), _now()),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM author_prefs WHERE key = ?", (key,)).fetchone()
    return row["id"]


def list_prefs(conn) -> list[dict]:
    rows = conn.execute("SELECT id, key, content, source, updated_at FROM author_prefs ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def delete_pref(conn, key: str) -> bool:
    cur = conn.execute("DELETE FROM author_prefs WHERE key = ?", (key,))
    conn.commit()
    return cur.rowcount > 0


def get_prefs_map(conn) -> dict[str, str]:
    rows = conn.execute("SELECT key, content FROM author_prefs").fetchall()
    return {r["key"]: r["content"] for r in rows}


def create_profile(conn, name: str, base_url: str, model: str, api_key_enc: bytes | None,
                   capabilities: str = "json_mode,stream") -> int:
    cur = conn.execute(
        "INSERT INTO model_profiles (name, base_url, model, api_key_enc, capabilities, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (name[:50], base_url, model, api_key_enc, capabilities, _now()),
    )
    conn.commit()
    return cur.lastrowid


def list_profiles(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM model_profiles ORDER BY id").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["has_key"] = bool(d.pop("api_key_enc"))
        out.append(d)
    return out


def delete_profile(conn, pid: int) -> bool:
    conn.execute("DELETE FROM task_bindings WHERE profile_id = ?", (pid,))
    cur = conn.execute("DELETE FROM model_profiles WHERE id = ?", (pid,))
    conn.commit()
    return cur.rowcount > 0


def set_binding(conn, task: str, profile_id: int) -> None:
    conn.execute(
        "INSERT INTO task_bindings (task, profile_id) VALUES (?, ?)"
        " ON CONFLICT(task) DO UPDATE SET profile_id = excluded.profile_id",
        (task, profile_id),
    )
    conn.commit()


def get_bindings(conn) -> dict[str, int]:
    rows = conn.execute("SELECT task, profile_id FROM task_bindings").fetchall()
    return {r["task"]: r["profile_id"] for r in rows}


def get_profile(conn, pid: int) -> dict | None:
    row = conn.execute("SELECT * FROM model_profiles WHERE id = ?", (pid,)).fetchone()
    return dict(row) if row else None


def get_profile_key(conn, pid: int) -> str:
    row = conn.execute("SELECT api_key_enc FROM model_profiles WHERE id = ?", (pid,)).fetchone()
    if not row or not row["api_key_enc"]:
        return ""
    return settings_decrypt(bytes(row["api_key_enc"]))


# ---------- 发布目标与日志（v9） ----------

def create_publish_target(conn, name: str, kind: str, config_enc: bytes) -> int:
    """创建发布目标；重名抛 ValueError（先查后插，API 可映射 400）。"""
    dup = conn.execute("SELECT id FROM publish_targets WHERE name = ?", (name,)).fetchone()
    if dup:
        raise ValueError(f"目标名已存在：{name}")
    cur = conn.execute(
        "INSERT INTO publish_targets (name, kind, config_enc, created_at) VALUES (?, ?, ?, ?)",
        (name, kind, config_enc, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return cur.lastrowid


def list_publish_targets(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM publish_targets ORDER BY id").fetchall()]


def get_publish_target(conn, tid: int) -> dict | None:
    row = conn.execute("SELECT * FROM publish_targets WHERE id = ?", (tid,)).fetchone()
    return dict(row) if row else None


def delete_publish_target(conn, tid: int) -> None:
    conn.execute("DELETE FROM publish_targets WHERE id = ?", (tid,))
    conn.commit()


def record_publish_log(conn, article_id: int | None, target_id: int | None,
                       fmt: str, status: str, message: str) -> int:
    cur = conn.execute(
        "INSERT INTO publish_logs (article_id, target_id, fmt, status, message, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (article_id, target_id, fmt, status, message, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return cur.lastrowid


def list_publish_logs(conn, limit: int = 20) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT l.*, t.name AS target_name FROM publish_logs l"
        " LEFT JOIN publish_targets t ON t.id = l.target_id"
        " ORDER BY l.id DESC LIMIT ?", (limit,)).fetchall()]
