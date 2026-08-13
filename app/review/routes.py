"""review API 路由（APIRouter；不继续膨胀 app/main.py）。"""

import json
import os
import secrets

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from app import db
from app.review import models, pack_loader, repository, service

router = APIRouter(prefix="/api", tags=["review"])


class ReviewCreateIn(BaseModel):
    article_id: int
    profile_selection: dict


class RuleOverrideIn(BaseModel):
    patch: dict


class ReviewIssueActionIn(BaseModel):
    pass


class ExportIn(BaseModel):
    target: str | None = None  # 渠道包 id（如 wechat-mini）；None = 仅通用版


class ImportIn(BaseModel):
    content: str = ""  # .wensu-rules.json 内容
    confirm_token: str | None = None  # 二阶段确认 token


@router.get("/review/packs")
def list_packs():
    """列出内置/自定义规则包、版本、来源概览。"""
    out = []
    for pid in pack_loader.BUILTIN_PACK_IDS:
        try:
            pack = pack_loader.load_pack_file(pid)
            out.append({
                "pack_id": pack.pack_id, "pack_version": pack.pack_version,
                "name": pack.name, "description": pack.description,
                "rule_count": len(pack.rules), "builtin": True,
            })
        except Exception:
            continue
    conn = db.connect()
    try:
        customs = repository.list_custom_rules(conn)
    finally:
        conn.close()
    for c in customs:
        out.append({"pack_id": "custom", "pack_version": "user", "name": "我的规则",
                    "description": "用户自定义规则", "rule_count": 1, "builtin": False,
                    "custom_id": c["id"]})
    return {"packs": out}


@router.get("/review/rules/{rule_id}")
def get_rule(rule_id: str):
    """读取规则（内置定义 + 用户 override 状态）。"""
    conn = db.connect()
    try:
        override = repository.get_override(conn, rule_id)
    finally:
        conn.close()
    rule = None
    for pid in pack_loader.BUILTIN_PACK_IDS:
        try:
            pack = pack_loader.load_pack_file(pid)
            rule = next((r for r in pack.rules if r.id == rule_id), None)
            if rule:
                break
        except Exception:
            continue
    if rule is None and override is None:
        raise HTTPException(404, "规则不存在")
    base = rule.model_dump() if rule else {}
    return {"rule": base, "override": override}


@router.put("/review/rules/{rule_id}")
def put_rule_override(rule_id: str, body: RuleOverrideIn):
    """保存用户 override（不改内置包）。"""
    allowed = {"params", "severity", "enabled", "fix_mode"}
    bad = set(body.patch.keys()) - allowed
    if bad:
        raise HTTPException(400, f"不允许覆盖字段：{sorted(bad)}")
    conn = db.connect()
    try:
        repository.set_override(conn, rule_id, body.patch)
    finally:
        conn.close()
    return {"ok": True}


@router.delete("/review/rules/{rule_id}")
def delete_rule_override(rule_id: str):
    """删除 override = 恢复内置默认（回滚）。"""
    conn = db.connect()
    try:
        ok = repository.delete_override(conn, rule_id)
        if not ok:
            raise HTTPException(404, "没有该规则的覆盖")
        return {"ok": True}
    finally:
        conn.close()


@router.post("/review/custom-rules")
def add_custom_rule(body: dict):
    """新增自定义规则（通过完整 schema 校验）。"""
    try:
        models.validate_rule(body)
    except models.ReviewRuleError as e:
        raise HTTPException(400, str(e))
    conn = db.connect()
    try:
        cid = repository.add_custom_rule(conn, body)
        return {"id": cid}
    finally:
        conn.close()


# ---------- 规则导入（两阶段：预览 → 确认安装） ----------

MAX_IMPORT_BYTES = 200_000
_import_tokens: dict[str, dict] = {}  # token -> {"preview": {...}, "created_at": ts}


@router.post("/review/rules/import")
def preview_import(body: ImportIn):
    """第一阶段：校验并预览导入差异；返回安装 token（不安装）。"""
    import time
    if len(body.content.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise HTTPException(400, "导入文件超过 200KB 上限")
    try:
        data = json.loads(body.content)
    except Exception:
        raise HTTPException(400, "不是合法 JSON")
    if not isinstance(data, dict) or "rules" not in data:
        raise HTTPException(400, "导入格式：{\"rules\": [...]} 的 .wensu-rules.json")
    rules = data.get("rules", [])
    if not isinstance(rules, list) or not rules:
        raise HTTPException(400, "没有可导入的规则")
    if len(rules) > 100:
        raise HTTPException(400, "单次导入规则数超过 100")

    conn = db.connect()
    try:
        existing = repository.list_overrides(conn)
        custom_ids = {c["rule"]["id"] for c in repository.list_custom_rules(conn)}
    finally:
        conn.close()

    preview = {"added": [], "changed": [], "rejected": []}
    for r in rules:
        try:
            models.validate_rule(r)
        except models.ReviewRuleError as e:
            preview["rejected"].append({"id": r.get("id", "?"), "reason": str(e)})
            continue
        rid = r["id"]
        if rid in existing or rid in custom_ids:
            preview["changed"].append({"id": rid, "name": r.get("name", "")})
        else:
            preview["added"].append({"id": rid, "name": r.get("name", "")})

    token = secrets.token_urlsafe(24)
    _import_tokens[token] = {"rules": rules, "ts": time.time()}
    return {"preview": preview, "token": token,
            "message": f"将新增 {len(preview['added'])} 条、更新 {len(preview['changed'])} 条、拒绝 {len(preview['rejected'])} 条"}


@router.post("/review/rules/import/confirm")
def confirm_import(body: ImportIn):
    """第二阶段：凭 token 安装（用户确认后调用）。"""
    import time
    item = _import_tokens.pop(body.confirm_token, None) if body.confirm_token else None
    if item is None:
        raise HTTPException(400, "导入 token 无效或已过期，请重新预览")
    if time.time() - item["ts"] > 600:
        raise HTTPException(400, "导入预览已过期（10 分钟），请重新预览")
    conn = db.connect()
    try:
        for r in item["rules"]:
            try:
                models.validate_rule(r)
            except models.ReviewRuleError as e:
                raise HTTPException(400, f"安装时校验失败：{r.get('id')} → {e}")
            if r["id"].startswith("my."):
                repository.add_custom_rule(conn, r)
            else:
                # 内置规则 → 存为 override patch（不改内置 JSON，可恢复默认）
                patch = {k: r.get(k) for k in ("params", "severity", "enabled", "fix_mode") if k in r}
                repository.set_override(conn, r["id"], patch)
        return {"ok": True, "installed": len(item["rules"])}
    finally:
        conn.close()


@router.post("/reviews")
def create_review(body: ReviewCreateIn):
    conn = db.connect()
    try:
        out = service.create_review(conn, body.article_id, body.profile_selection)
    except db.NotFoundError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()
    return out


@router.get("/reviews/{review_id}")
def get_review(review_id: int):
    conn = db.connect()
    try:
        out = service.get_review(conn, review_id)
    except db.NotFoundError as e:
        raise HTTPException(404, str(e))
    finally:
        conn.close()
    return out


@router.get("/reviews/{review_id}/stream")
def stream_review(review_id: int):
    """NDJSON：stage → issue* → done。

    阶段顺序：prepare → format（确定性，已存库回放）→ content（AI 语义）
    → evidence（证据）→ 汇总。AI/证据失败不阻塞，产出 warning 事件；
    取消（客户端断连）→ 已写入的 issue 保留。
    """

    def gen():
        conn = db.connect()
        try:
            s = repository.get_session(conn, review_id)
            if s is None:
                yield json.dumps({"type": "warning", "message": "检查不存在"}, ensure_ascii=False) + "\n"
                return
            yield json.dumps({"type": "stage", "stage": "prepare", "status": "done"}, ensure_ascii=False) + "\n"
            yield json.dumps({"type": "stage", "stage": "format", "status": "done"}, ensure_ascii=False) + "\n"
            for i in repository.list_issues(conn, review_id):
                yield json.dumps({"type": "issue", "issue": i}, ensure_ascii=False) + "\n"

            # AI 语义 + 证据阶段（Phase 4）：失败不阻塞确定性结果
            from app.review import ai_checker, aggregator, evidence_checker
            ai_rules = [r for r in s["profile"].get("rules", []) if r.get("engine") == "ai"]
            if ai_rules:
                yield json.dumps({"type": "stage", "stage": "content", "status": "running"}, ensure_ascii=False) + "\n"
                ai_issues = ai_checker.run_ai_checks(conn, s, ai_rules)
                yield json.dumps({"type": "stage", "stage": "content", "status": "done",
                                  "count": len(ai_issues)}, ensure_ascii=False) + "\n"
                repository.add_issues(conn, review_id, ai_issues)
                for i in ai_issues:
                    yield json.dumps({"type": "issue", "issue": i}, ensure_ascii=False) + "\n"
                if ai_checker.last_diagnostics:
                    yield json.dumps({"type": "warning", "message": "部分 AI 检查项被丢弃（缺字段/锚点不符）",
                                      "detail": ai_checker.last_diagnostics[-1]}, ensure_ascii=False) + "\n"

            yield json.dumps({"type": "stage", "stage": "evidence", "status": "running"}, ensure_ascii=False) + "\n"
            ev_issues = evidence_checker.run_evidence_checks(conn, s)
            yield json.dumps({"type": "stage", "stage": "evidence", "status": "done",
                              "count": len(ev_issues)}, ensure_ascii=False) + "\n"
            repository.add_issues(conn, review_id, ev_issues)
            for i in ev_issues:
                yield json.dumps({"type": "issue", "issue": i}, ensure_ascii=False) + "\n"

            yield json.dumps({"type": "done", "status": s["status"]}, ensure_ascii=False) + "\n"
        except Exception as e:  # 流式阶段失败不吞静默
            yield json.dumps({"type": "warning", "message": f"检查阶段异常：{e}"}, ensure_ascii=False) + "\n"
        finally:
            conn.close()
    return StreamingResponse(gen(), media_type="application/x-ndjson")


@router.post("/reviews/{review_id}/issues/{issue_id}/accept")
def accept_issue(review_id: int, issue_id: int):
    conn = db.connect()
    try:
        out = service.accept_issue(conn, review_id, issue_id)
    except db.NotFoundError as e:
        raise HTTPException(404, str(e))
    except db.VersionConflict as e:
        raise HTTPException(409, f"主稿已变化（服务端 v{e.server_version}），请复检")
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()
    return out


@router.post("/reviews/{review_id}/issues/{issue_id}/ignore")
def ignore_issue(review_id: int, issue_id: int):
    conn = db.connect()
    try:
        ok = repository.set_issue_state(conn, issue_id, "ignored")
        if not ok:
            raise HTTPException(404, "问题不存在")
        return {"ok": True}
    finally:
        conn.close()


@router.post("/reviews/{review_id}/recheck")
def recheck_review(review_id: int):
    conn = db.connect()
    try:
        out = service.recheck(conn, review_id)
    except db.NotFoundError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()
    return out


@router.post("/reviews/{review_id}/exports")
def create_export(review_id: int, body: ExportIn):
    """生成通用/渠道 Markdown + 摘要 manifest（stale 补丁不静默应用，摘要记录）。"""
    conn = db.connect()
    try:
        out = service.export_review(conn, review_id, body.target)
    except db.NotFoundError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()
    return out


@router.get("/review-exports/{export_id}/{kind}")
def download_export(export_id: int, kind: str):
    """下载 general / channel Markdown，或 report（摘要 manifest JSON）。"""
    if kind not in ("general", "channel", "report"):
        raise HTTPException(400, "未知导出类型")
    conn = db.connect()
    try:
        row = conn.execute("SELECT manifest_json FROM review_exports WHERE id = ?", (export_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "导出不存在")
        manifest = json.loads(row["manifest_json"])
        files = manifest.get("files", {})
        if kind == "report":
            return JSONResponse(manifest)
        finfo = files.get(kind)
        if not finfo:
            raise HTTPException(404, f"该导出没有 {kind} 文件")
        path = os.path.join(service.EXPORT_DIR, finfo["name"])
        if not os.path.exists(path):
            raise HTTPException(404, "导出文件已丢失")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        from urllib.parse import quote
        return PlainTextResponse(content, media_type="text/markdown; charset=utf-8",
                                 headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(finfo['name'])}"})
    finally:
        conn.close()
