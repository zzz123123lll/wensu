"""review 数据访问：review_sessions / issues / variant_patches / exports / 规则覆盖。"""

import json
import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- Session ----------

def create_session(conn, article_id: int, article_version: int, blocks: list,
                   citations: list, snapshot_hash: str, profile_json: dict) -> int:
    cur = conn.execute(
        "INSERT INTO review_sessions (article_id, article_version, blocks_json, citations_json,"
        " snapshot_hash, profile_json, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'draft', ?)",
        (article_id, article_version, json.dumps(blocks, ensure_ascii=False),
         json.dumps(citations, ensure_ascii=False), snapshot_hash,
         json.dumps(profile_json, ensure_ascii=False), _now()),
    )
    conn.commit()
    return cur.lastrowid


def get_session(conn, review_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM review_sessions WHERE id = ?", (review_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["blocks"] = json.loads(d.pop("blocks_json") or "[]")
    d["citations"] = json.loads(d.pop("citations_json") or "[]")
    d["profile"] = json.loads(d.pop("profile_json") or "{}")
    return d


def set_session_status(conn, review_id: int, status: str, error: str = "") -> None:
    conn.execute("UPDATE review_sessions SET status = ?, error = ? WHERE id = ?",
                 (status, error[:500], review_id))
    conn.commit()


def list_sessions(conn, article_id: int, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        "SELECT id, article_version, status, error, created_at FROM review_sessions"
        " WHERE article_id = ? ORDER BY id DESC LIMIT ?", (article_id, limit)
    ).fetchall()
    return [dict(r) for r in rows]


# ---------- Issue ----------

def add_issues(conn, review_id: int, issues: list[dict]) -> None:
    for i in issues:
        conn.execute(
            "INSERT INTO review_issues (review_id, fingerprint, rule_id, severity, anchor_json,"
            " suggestion_json, reason, source_type, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (review_id, i["fingerprint"], i["rule_id"], i["severity"],
             json.dumps(i.get("anchor", {}), ensure_ascii=False),
             json.dumps(i.get("suggestion", ""), ensure_ascii=False),
             i.get("reason", ""), i.get("source_type", "system"), _now()),
        )
    conn.commit()


def list_issues(conn, review_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM review_issues WHERE review_id = ? ORDER BY id", (review_id,)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["anchor"] = json.loads(d.pop("anchor_json") or "{}")
        d["suggestion"] = json.loads(d.pop("suggestion_json") or "{}")
        out.append(d)
    return out


def get_issue(conn, review_id: int, issue_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM review_issues WHERE review_id = ? AND id = ?", (review_id, issue_id)
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["anchor"] = json.loads(d.pop("anchor_json") or "{}")
    d["suggestion"] = json.loads(d.pop("suggestion_json") or "{}")
    return d


def set_issue_state(conn, issue_id: int, state: str) -> bool:
    cur = conn.execute("UPDATE review_issues SET state = ? WHERE id = ?", (state, issue_id))
    conn.commit()
    return cur.rowcount > 0


# ---------- Variant Patch ----------

def create_patch(conn, review_id: int, target: str, rule_id: str, block_id: str,
                 selection: dict, original_hash: str, replacement: str) -> int:
    cur = conn.execute(
        "INSERT INTO review_variant_patches (review_id, target, rule_id, block_id, selection_json,"
        " original_hash, replacement, status) VALUES (?, ?, ?, ?, ?, ?, ?, 'proposed')",
        (review_id, target, rule_id, block_id, json.dumps(selection, ensure_ascii=False),
         original_hash, replacement),
    )
    conn.commit()
    return cur.lastrowid


def activate_patch(conn, patch_id: int) -> bool:
    cur = conn.execute(
        "UPDATE review_variant_patches SET status = 'active', confirmed_at = ? WHERE id = ?",
        (_now(), patch_id),
    )
    conn.commit()
    return cur.rowcount > 0


def list_patches(conn, review_id: int, target: str | None = None) -> list[dict]:
    if target:
        rows = conn.execute(
            "SELECT * FROM review_variant_patches WHERE review_id = ? AND target = ? ORDER BY id",
            (review_id, target),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM review_variant_patches WHERE review_id = ? ORDER BY id", (review_id,)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["selection"] = json.loads(d.pop("selection_json") or "{}")
        out.append(d)
    return out


def mark_patches_stale(conn, review_id: int) -> int:
    cur = conn.execute(
        "UPDATE review_variant_patches SET status = 'stale' WHERE review_id = ? AND status = 'active'",
        (review_id,),
    )
    conn.commit()
    return cur.rowcount


# ---------- 规则覆盖 ----------

def get_override(conn, rule_id: str) -> dict | None:
    row = conn.execute("SELECT patch_json FROM review_rule_overrides WHERE rule_id = ?", (rule_id,)).fetchone()
    return json.loads(row["patch_json"]) if row else None


def set_override(conn, rule_id: str, patch: dict) -> None:
    conn.execute(
        "INSERT INTO review_rule_overrides (rule_id, patch_json, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT(rule_id) DO UPDATE SET patch_json = excluded.patch_json, updated_at = excluded.updated_at",
        (rule_id, json.dumps(patch, ensure_ascii=False), _now()),
    )
    conn.commit()


def delete_override(conn, rule_id: str) -> bool:
    cur = conn.execute("DELETE FROM review_rule_overrides WHERE rule_id = ?", (rule_id,))
    conn.commit()
    return cur.rowcount > 0


def list_overrides(conn) -> dict[str, dict]:
    rows = conn.execute("SELECT rule_id, patch_json FROM review_rule_overrides").fetchall()
    return {r["rule_id"]: json.loads(r["patch_json"]) for r in rows}


def add_custom_rule(conn, rule_json: dict) -> int:
    cur = conn.execute(
        "INSERT INTO review_custom_rules (rule_json, created_at) VALUES (?, ?)",
        (json.dumps(rule_json, ensure_ascii=False), _now()),
    )
    conn.commit()
    return cur.lastrowid


def list_custom_rules(conn) -> list[dict]:
    rows = conn.execute("SELECT id, rule_json, enabled, created_at FROM review_custom_rules ORDER BY id").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["rule"] = json.loads(d.pop("rule_json"))
        out.append(d)
    return out


# ---------- Exports ----------

def create_export(conn, review_id: int, article_version: int, target: str, manifest: dict) -> int:
    cur = conn.execute(
        "INSERT INTO review_exports (review_id, article_version, target, manifest_json, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (review_id, article_version, target, json.dumps(manifest, ensure_ascii=False), _now()),
    )
    conn.commit()
    return cur.lastrowid
