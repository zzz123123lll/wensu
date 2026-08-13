"""review service：Session 编排、快照、确定性检查运行、主稿/变体应用、复检、双版本导出。

原则：快照运行（不可变）、失效即停止（stale gate）、逐项确认、主稿/变体分离。
"""

import hashlib
import json
import os

from app import db
from app.review import deterministic, repository, resolver


def _snapshot_hash(blocks, citations) -> str:
    raw = json.dumps({"blocks": blocks, "citations": citations}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def _build_profile(profile_selection: dict, conn) -> dict:
    """合并四层规则；返回含 conflicts 的 profile。"""
    overrides = repository.list_overrides(conn)
    customs = [c["rule"] for c in repository.list_custom_rules(conn) if c.get("enabled")]
    return resolver.resolve_profile(profile_selection, overrides=overrides, custom_rules=customs)


def _collect_citations(conn, article_id: int) -> list[dict]:
    """收集文章引用的来源信息（供证据机械检查）。"""
    rows = conn.execute(
        "SELECT c.id, c.block_id, c.quote, c.status, s.title AS source_title, s.url AS source_url"
        " FROM citations c LEFT JOIN sources s ON s.id = c.source_id"
        " WHERE c.article_id = ?", (article_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def run_ai_and_evidence(conn, review_id: int) -> tuple[list, list]:
    """同步运行 AI 语义 + 证据阶段（创建检查时调用）。

    - 任一阶段失败（无模型/坏输出）→ 记录诊断，不阻塞确定性结果
    - 幂等：库中已有 ai/evidence issue（或 evidence 存在）→ 跳过模型调用
    - 返回 (新增 issues, 诊断 warnings)
    """
    from app.review import ai_checker, evidence_checker
    s = repository.get_session(conn, review_id)
    if s is None:
        return [], []
    existing = repository.list_issues(conn, review_id)
    ev_done = any(i.get("source_type") == "evidence" for i in existing)
    ai_done = any(i.get("source_type") == "ai" for i in existing) or ev_done
    added = []
    warns = []
    ai_rules = [r for r in s["profile"].get("rules", []) if r.get("engine") == "ai"]
    if ai_rules and not ai_done:
        ai_issues = ai_checker.run_ai_checks(conn, s, ai_rules)
        repository.add_issues(conn, review_id, ai_issues)
        added.extend(ai_issues)
        if ai_checker.last_diagnostics:
            warns.append({"stage": "content", "message": "部分 AI 检查项被丢弃（缺字段/锚点不符）",
                          "detail": ai_checker.last_diagnostics[-1]})
    if not ev_done:
        ev_issues = evidence_checker.run_evidence_checks(conn, s)
        repository.add_issues(conn, review_id, ev_issues)
        added.extend(ev_issues)
    return added, warns


def create_review(conn, article_id: int, profile_selection: dict) -> dict:
    """保存未保存内容由前端负责；此处创建不可变快照 + 运行确定性检查。"""
    art = db.get_article(conn, article_id)
    if art is None:
        raise db.NotFoundError(f"article {article_id}")
    citations = _collect_citations(conn, article_id)
    profile = _build_profile(profile_selection, conn)
    snap_hash = _snapshot_hash(art["blocks"], citations)

    review_id = repository.create_session(
        conn, article_id, art["version"], art["blocks"], citations, snap_hash, profile,
    )
    repository.set_session_status(conn, review_id, "running")
    try:
        run_deterministic(conn, review_id)
        repository.set_session_status(conn, review_id, "completed")
        return {"review_id": review_id, "issues": repository.list_issues(conn, review_id), "profile": profile}
    except Exception as e:
        repository.set_session_status(conn, review_id, "failed", str(e))
        raise


def run_deterministic(conn, review_id: int) -> list[dict]:
    """确定性检查：写 issues 并返回（相同快照 → 相同结果）。"""
    s = repository.get_session(conn, review_id)
    if s is None:
        raise db.NotFoundError(f"review {review_id}")
    det_rules = resolver.rules_for_engine(s["profile"], "deterministic")
    issues = deterministic.run_all({"blocks": s["blocks"], "citations": s["citations"]}, det_rules)
    repository.add_issues(conn, review_id, issues)
    return issues


def get_review(conn, review_id: int) -> dict:
    s = repository.get_session(conn, review_id)
    if s is None:
        raise db.NotFoundError(f"review {review_id}")
    return {
        "review": {k: s[k] for k in ("id", "article_id", "article_version", "snapshot_hash", "status", "error", "created_at")},
        "profile": s["profile"],
        "issues": repository.list_issues(conn, review_id),
        "patches": repository.list_patches(conn, review_id),
    }


def accept_issue(conn, review_id: int, issue_id: int) -> dict:
    """逐项采用。

    - scope=master（通用/文章类型规则）：直接写入主稿（save_article, reason=review_accept），
      必须匹配快照版本（base_version=snapshot 版本），冲突 409 由调用方处理。
    - scope=variant（渠道规则）：创建 proposed patch；客户端确认后 activate（Phase 3 预览）。
    """
    s = repository.get_session(conn, review_id)
    if s is None:
        raise db.NotFoundError(f"review {review_id}")
    issue = repository.get_issue(conn, review_id, issue_id)
    if issue is None:
        raise db.NotFoundError(f"issue {issue_id}")
    if issue["state"] != "open":
        raise ValueError(f"issue {issue_id} 已处理（{issue['state']}）")
    if s["status"] != "completed":
        raise ValueError("检查未完成，不能采用")

    rule = next((r for r in s["profile"]["rules"] if r["id"] == issue["rule_id"]), None)
    if (rule or {}).get("fix_mode") == "advisory":
        raise ValueError("该问题仅为提示（advisory），不可直接采用")
    scope = (rule or {}).get("scope", "master")
    anchor = issue["anchor"]
    suggestion = issue.get("suggestion") or ""
    block_id = anchor.get("block_id", "")

    if scope == "master":
        if not suggestion or not block_id:
            raise ValueError("该问题没有可应用的修改候选")
        blocks = _apply_exact(s["blocks"], block_id, anchor, suggestion)
        if blocks is None:
            raise ValueError("主稿已变化，补丁失效：请复检")
        new_version = db.save_article(
            conn, s["article_id"], blocks=blocks, base_version=s["article_version"],
            reason="review_accept",
        )
        repository.set_issue_state(conn, issue_id, "accepted")
        return {"action": "master", "new_version": new_version, "block_id": block_id}

    # variant：创建渠道补丁（proposed），由用户在渠道预览确认后激活
    target = (rule or {}).get("pack_id", "channel")
    original_text = anchor.get("original_text", "")
    if not block_id or original_text is None:
        raise ValueError("渠道问题缺少精确锚点")
    import hashlib
    orig_hash = hashlib.sha1(original_text.encode("utf-8")).hexdigest()[:16]
    patch_id = repository.create_patch(
        conn, review_id, target, issue["rule_id"], block_id,
        {"start_utf16": anchor.get("start_utf16", 0), "end_utf16": anchor.get("end_utf16", 0),
         "original_text": original_text},
        orig_hash, suggestion,
    )
    repository.set_issue_state(conn, issue_id, "accepted")
    return {"action": "variant", "patch_id": patch_id, "status": "proposed"}


def _apply_exact(blocks, block_id: str, anchor: dict, replacement: str):
    """唯一精确原文匹配替换；零次或多次匹配 → None（stale）。"""
    original = anchor.get("original_text")
    found = 0
    for b in blocks:
        if b.get("id") != block_id:
            continue
        text = b.get("text", "")
        start = anchor.get("start_utf16", 0)
        end = anchor.get("end_utf16", len(text))
        if start <= len(text) and end <= len(text) and text[start:end] == original:
            found += 1
            b["text"] = text[:start] + replacement + text[end:]
    if found != 1:
        return None
    return blocks


def recheck(conn, review_id: int) -> dict:
    """基于当前文章与同一 Profile 创建新 Session。"""
    s = repository.get_session(conn, review_id)
    if s is None:
        raise db.NotFoundError(f"review {review_id}")
    return create_review(conn, s["article_id"], _selection_from_profile(s["profile"]))


def _selection_from_profile(profile: dict) -> dict:
    """从已解析 profile 反推包选择（common/type/channel/personal 各取 pack_id 集合）。"""
    sel = {"common": [], "type": [], "channel": [], "personal": []}
    for r in profile.get("rules", []):
        layer = r.get("layer", "common")
        if r.get("pack_id") and r["pack_id"] not in sel.get(layer, []):
            if layer in sel:
                sel[layer].append(r["pack_id"])
    return sel


# ---------- 双版本导出（Phase 3） ----------

EXPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "exports")


def export_review(conn, review_id: int, target: str | None = None) -> dict:
    """生成通用版 + 渠道版 Markdown 与摘要 manifest，落库并写文件。

    target 为渠道包 id（如 wechat-mini）；None 表示无渠道（只出通用版）。
    stale 补丁不会静默应用：渠道版基于未应用 stale 的 blocks 生成，
    并在 manifest 中记录 stale；前端必须向用户确认后导出。
    """
    from app.review import exporter
    s = repository.get_session(conn, review_id)
    if s is None:
        raise db.NotFoundError(f"review {review_id}")
    art = db.get_article(conn, s["article_id"])
    title = art["title"] if art else "文章"
    issues = repository.list_issues(conn, review_id)
    patches = repository.list_patches(conn, review_id)

    general_md = exporter.render_markdown(s["blocks"], s["citations"])
    general_file = exporter.safe_filename(title, "通用版")

    channel_file = None
    channel_md = None
    stale = []
    active_targets = {p.get("target") for p in patches if p.get("status") == "active"}
    if target and target in active_targets:
        target_patches = [p for p in patches if p.get("target") == target]
        # stale gate：相对【当前主稿】判定（快照里原文总在，无法暴露陈旧）
        current_blocks = (db.get_article(conn, s["article_id"]) or {}).get("blocks", [])
        fresh = []
        for p in target_patches:
            sel = p.get("selection", {})
            original = sel.get("original_text", "")
            blk = next((b for b in current_blocks if b.get("id") == p.get("block_id")), None)
            txt = (blk or {}).get("text", "")
            if blk is None or txt[sel.get("start_utf16", 0):sel.get("end_utf16", len(original))] != original:
                stale.append({**p, "stale_reason": "当前主稿中原文已变化"})
                continue
            fresh.append(p)
        # 渠道版基于快照应用 fresh 补丁（可重现），stale 补丁不静默跳过
        ch_blocks, apply_stale = exporter.apply_patches(s["blocks"], fresh)
        stale.extend(apply_stale)
        channel_md = exporter.render_markdown(ch_blocks, s["citations"])
        channel_file = exporter.safe_filename(title, target.replace("-", ""), existing=[general_file])

    os.makedirs(EXPORT_DIR, exist_ok=True)
    general_path = os.path.join(EXPORT_DIR, general_file)
    with open(general_path, "w", encoding="utf-8") as f:
        f.write(general_md)
    if channel_md is not None:
        channel_path = os.path.join(EXPORT_DIR, channel_file)
        with open(channel_path, "w", encoding="utf-8") as f:
            f.write(channel_md)

    manifest = exporter.build_manifest(
        {"article_id": s["article_id"], "article_version": s["article_version"],
         "snapshot_hash": s["snapshot_hash"]},
        s["profile"], issues, patches, stale, general_md, channel_md,
        general_file, channel_file,
    )
    export_id = repository.create_export(conn, review_id, s["article_version"],
                                         target or "general", manifest)
    return {"export_id": export_id, "general_file": general_file, "channel_file": channel_file,
            "stale": stale, "manifest": manifest}
