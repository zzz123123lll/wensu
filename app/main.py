"""文序 · 后端 API（第一阶段：项目/草稿/正文块 CRUD）。"""

import json
import os
import secrets
import urllib.parse
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from app import ai_service, copilot, db, safe_fetch, settings
from app.domains import exports as export_service
from app.llm import LLMClient, LLMError
from app.review.routes import router as review_router
from app.schemas import Block

# 静态目录基于文件位置，而非当前工作目录（任意 CWD 可启动）
# 查找顺序：WENSU_WEB_DIR 环境变量 → 源码树 web/（开发）→ sys.prefix/web（wheel data-files 安装）
def _find_web_dir() -> str:
    env = os.environ.get("WENSU_WEB_DIR")
    if env:
        return env
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
    if os.path.isdir(src):
        return src
    import sys

    installed = os.path.join(sys.prefix, "web")
    if os.path.isdir(installed):
        return installed
    return src


WEB_DIR = _find_web_dir()

app = FastAPI(title="文序", version="0.2.0")

# 本机绑定服务：只允许本地源（防恶意网页跨源调用本机 API）
# WENSU_EXTRA_HOSTS/ORIGINS：测试/多端口部署扩展（默认安全行为不变）
ALLOWED_ORIGINS = {"http://localhost:8766", "http://127.0.0.1:8766"} | {
    o for o in os.environ.get("WENSU_EXTRA_ORIGINS", "").split(",") if o
}
ALLOWED_HOSTS = {"127.0.0.1:8766", "localhost:8766"} | {
    h for h in os.environ.get("WENSU_EXTRA_HOSTS", "").split(",") if h
}

# Phase 8：随机本地 session token（HttpOnly cookie，纵深防御；Origin 校验为主防线）
SESSION_TOKEN = secrets.token_urlsafe(32)
_err_count = 0


@app.middleware("http")
async def security_guard(request, call_next):
    global _err_count
    # Host 校验（防 DNS rebinding / 跨源直连）
    host = request.headers.get("host", "")
    if host not in ALLOWED_HOSTS:
        _err_count += 1
        return JSONResponse({"detail": "invalid host"}, status_code=403)
    # 所有状态改变方法（含 PATCH）+ AI API：Origin + session 双校验
    # （本地单用户应用；浏览器同源写请求必须带允许的 Origin 与有效 session；
    #  防恶意网页跨源调用本机服务 / 同机非授权进程写入）
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/"):
        origin = request.headers.get("origin")
        if not origin or origin not in ALLOWED_ORIGINS:
            _err_count += 1
            return JSONResponse({"detail": "cross-origin request rejected"}, status_code=403)
        # session token：HttpOnly cookie 无法被恶意网页伪造；curl/脚本必须带 X-Wensu-Token
        cookie_token = request.cookies.get("wensu_session")
        header_token = request.headers.get("x-wensu-token")
        if not (
            (cookie_token is not None and cookie_token == SESSION_TOKEN)
            or (header_token is not None and header_token == SESSION_TOKEN)
        ):
            _err_count += 1
            return JSONResponse({"detail": "invalid session"}, status_code=403)
    try:
        return await call_next(request)
    except Exception:
        _err_count += 1
        raise


# CORS：仅允许本地源（同源为主，跨端口调试为辅）；禁止 *
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(ALLOWED_ORIGINS),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/session")
def api_session():
    """下发随机本地 session token（HttpOnly cookie，防同机恶意网页）。"""
    resp = JSONResponse({"ok": True, "expires": "session"})
    resp.set_cookie("wensu_session", SESSION_TOKEN, httponly=True, samesite="strict", path="/")
    return resp


@app.get("/api/diagnostics")
def api_diagnostics():
    """诊断包（不含正文/Key/prompt）：版本、DB 大小、表计数、错误计数。"""
    conn = _conn()
    try:
        counts = {}
        for t in ("projects", "articles", "article_revisions", "sources", "citations", "materials", "article_asks", "author_prefs", "model_profiles"):
            try:
                counts[t] = conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
            except Exception:
                counts[t] = 0
        size = os.path.getsize(db.DB_PATH) if os.path.exists(db.DB_PATH) else 0
        return {
            "version": app.version,
            "db_size_bytes": size,
            "db_ok": True,
            "tables": counts,
            "security_errors": _err_count,
        }
    except Exception:
        return JSONResponse({"version": app.version, "db_ok": False, "security_errors": _err_count}, status_code=200)
    finally:
        conn.close()


class ProjectIn(BaseModel):
    name: str


class ArticleIn(BaseModel):
    title: str


class ArticleUpdate(BaseModel):
    title: str | None = None
    blocks: list[Block] | None = None  # typed Block：非法输入 422
    base_version: int  # 乐观锁：客户端已知的服务端版本
    change_reason: Literal["autosave", "ai_rewrite", "ai_check", "conflict_recovery",
                           "material_insert", "ask_insert", "revision_restore"] = "autosave"
    # 统一 Revision 契约（P0-4）：来源对象 + 作用范围随保存写入
    source_object_type: str = ""  # material | ask | ai_rewrite | ...
    source_object_id: str = ""
    scope: str = "blocks"


class SettingsIn(BaseModel):
    base_url: str
    model: str
    api_key: str | None = None


class AskIn(BaseModel):
    prompt: str
    context: str = ""
    article_id: int | None = None


class AnchorMixin(BaseModel):
    """工具请求的可选锚点：结果应就近呈现，不靠"当前光标"猜位置。"""
    article_id: int | None = None
    target_block_id: str | None = None
    selection: dict | None = None  # {text, start_utf16, end_utf16}


class RewriteIn(AnchorMixin):
    text: str
    flavor: str = "default"  # default | de-ai（去 AI 味）


class TitleScoreIn(BaseModel):
    title: str
    context: str = ""


class InsightIn(BaseModel):
    title: str = ""
    blocks: list = []


class SearchIn(AnchorMixin):
    query: str
    stream: bool = False


class CheckIn(AnchorMixin):
    claim: str


class SourceIn(BaseModel):
    url: str
    title: str = ""
    snippet: str = ""
    provider: str = ""


# 标签上限：数量与单个长度（P0-2 统一边界校验）
MAX_MATERIAL_TAGS = 20
MAX_TAG_LEN = 30


class MaterialIn(BaseModel):
    title: str
    content: str = ""
    source_id: int | None = None
    tags: list[str] = []

    @field_validator("title")
    @classmethod
    def _check_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("素材标题不能为空")
        if len(v) > 200:
            raise ValueError("素材标题最长 200 字符")
        return v

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, v: list[str]) -> list[str]:
        """去空字符串、去首尾空格、去重复；上限校验。"""
        cleaned: list[str] = []
        for t in v or []:
            t = (t or "").strip()
            if not t or t in cleaned:
                continue
            if len(t) > MAX_TAG_LEN:
                raise ValueError(f"单个标签最长 {MAX_TAG_LEN} 字符")
            cleaned.append(t)
        if len(cleaned) > MAX_MATERIAL_TAGS:
            raise ValueError(f"标签最多 {MAX_MATERIAL_TAGS} 个")
        return cleaned


class AskUsageIn(BaseModel):
    usage: str  # saved_as_material | inserted_to_body


# 与 db.VERIF_* 常量一致的受控状态集合（P1-3：非法状态 422）
VERIF_STATUSES = ("pending", "supported", "insufficient", "conflicting", "source_dead", "needs_recheck")


class VerificationIn(BaseModel):
    status: Literal["pending", "supported", "insufficient", "conflicting", "source_dead", "needs_recheck"]
    note: str = ""


class CitationIn(BaseModel):
    block_id: str
    source_id: int
    quote: str = ""
    locator: str = ""
    display_label: str = ""


class FetchIn(BaseModel):
    url: str


class SignalIn(BaseModel):
    article_id: int
    type: str  # tool_click|accept|reject|dismiss|mark|draft_open
    tool: str | None = None
    issue: str | None = None
    focus: str | None = None
    block_id: str | None = None


class SuggestIn(BaseModel):
    article_id: int
    block_id: str | None = None
    issue: str | None = None


class PrefIn(BaseModel):
    key: str
    content: str


class ProfileIn(BaseModel):
    name: str
    base_url: str
    model: str
    api_key: str | None = None
    capabilities: str = "json_mode,stream"


class BindingIn(BaseModel):
    task: str
    profile_id: int


def _ai_error(e: LLMError) -> HTTPException:
    status = 400 if e.kind == "config" else 502
    return HTTPException(status, str(e))


def _anchor(body) -> dict:
    """响应原样回显稳定锚点：前端据此就近呈现，不靠"当前光标"猜位置。"""
    return {
        "article_id": body.article_id,
        "target_block_id": body.target_block_id,
        "selection": body.selection,
    }


def _conn():
    conn = db.connect()
    db.init(conn)
    return conn


@app.get("/api/projects")
def api_list_projects():
    conn = _conn()
    try:
        return [{"id": pid, "name": name} for pid, name in db.list_projects(conn)]
    finally:
        conn.close()


@app.post("/api/projects")
def api_create_project(body: ProjectIn):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "项目名不能为空")
    conn = _conn()
    try:
        return {"id": db.create_project(conn, name), "name": name}
    finally:
        conn.close()


@app.get("/api/projects/{pid}/articles")
def api_list_articles(pid: int):
    conn = _conn()
    try:
        return [
            {"id": i, "title": t, "updated_at": u}
            for i, t, u in db.list_articles(conn, pid)
        ]
    finally:
        conn.close()


@app.post("/api/projects/{pid}/articles")
def api_create_article(pid: int, body: ArticleIn):
    title = body.title.strip()
    if not title:
        raise HTTPException(400, "草稿标题不能为空")
    conn = _conn()
    try:
        try:
            return {"id": db.create_article(conn, pid, title), "title": title}
        except db.NotFoundError:
            raise HTTPException(404, "项目不存在")
    finally:
        conn.close()


@app.get("/api/articles/{aid}")
def api_get_article(aid: int):
    conn = _conn()
    try:
        a = db.get_article(conn, aid)
        if a is None:
            raise HTTPException(404, "草稿不存在")
        return a
    finally:
        conn.close()


@app.put("/api/articles/{aid}")
def api_save_article(aid: int, body: ArticleUpdate):
    conn = _conn()
    try:
        # P0-1：覆盖正文前先取旧 Block 快照（保存后再读就永远是"新正文"，diff 恒空）
        art = db.get_article(conn, aid)
        if art is None:
            raise HTTPException(404, "草稿不存在")
        old_blocks = art["blocks"]
        plain_blocks = [b.model_dump() for b in body.blocks] if body.blocks is not None else None
        # 素材插入：计算变化块并同事务记录显式使用关系（P0-6）
        # 目标块优先取"新增的块"（插入语义精确）；无新增块时取第一个实质变化块；
        # 无任何实质变化（如重复插入相同文本）→ 不记录，避免空关联
        material_usage = None
        if plain_blocks is not None and body.change_reason == "material_insert" \
                and body.source_object_type == "material" and body.source_object_id:
            try:
                mid_int = int(body.source_object_id)
            except ValueError:
                mid_int = 0
            if mid_int:
                changed_ids, _ = db.diff_block_texts(old_blocks, plain_blocks)
                old_ids = {b["id"] for b in old_blocks}
                added = {b["id"] for b in plain_blocks} - old_ids
                target = next(iter(added & changed_ids), None) or next(iter(changed_ids), None)
                if target is not None:
                    material_usage = (mid_int, target)
        try:
            # 保存、版本递增、Revision 创建、Citation 失效/孤立在 db.save_article 同一事务内
            version = db.save_article(
                conn, aid, body.title, plain_blocks,
                base_version=body.base_version, reason=body.change_reason,
                before_blocks=old_blocks if body.blocks is not None else None,
                source_object_type=body.source_object_type or "",
                source_object_id=body.source_object_id or "",
                material_usage=material_usage,
            )
        except db.NotFoundError:
            raise HTTPException(404, "草稿不存在")
        except db.VersionConflict as e:
            art = db.get_article(conn, aid)
            raise HTTPException(409, {
                "code": "version_conflict",
                "current_version": e.current_version,
                "blocks": art["blocks"] if art else [],
                "blocks_hash": art["blocks_hash"] if art else "",
            })
        return {
            "ok": True,
            "article_id": aid,
            "version": version,
            "blocks_hash": db.blocks_hash(plain_blocks or []),
        }
    finally:
        conn.close()


@app.get("/api/settings")
def api_get_settings():
    conn = _conn()
    try:
        return settings.get_settings(conn)
    finally:
        conn.close()


@app.put("/api/settings")
def api_save_settings(body: SettingsIn):
    conn = _conn()
    try:
        try:
            settings.save_settings(conn, body.base_url, body.model, body.api_key)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/ai/ask")
def api_ai_ask(body: AskIn):
    conn = _conn()
    try:
        # 作者记忆注入（透明：设置中可查看/删除）；Ask 历史按草稿隔离注入
        prefs = db.get_prefs_map(conn)
        ctx = body.context
        if prefs:
            pref_block = "作者写作偏好（来自用户明确保存的记忆）：" + "；".join(f"{k}：{v}" for k, v in prefs.items())
            ctx = pref_block + ("\n\n" + ctx if ctx else "")
        history = db.list_asks(conn, body.article_id, 6) if body.article_id else []
        if history:
            hist_block = "本草稿最近的问答（作为上下文，保持对话连贯）：\n" + "\n".join(
                f"问：{h['prompt'][:300]}\n答：{h['response'][:300]}" for h in reversed(history)
            )
            ctx = (ctx + "\n\n" if ctx else "") + hist_block
        answer = ai_service.ask(conn, body.prompt, ctx)
        model = ai_service.model_name_for(conn, "ask")
        ask_id = None
        if body.article_id:
            ask_id = db.record_ask(conn, body.article_id, body.prompt, answer, model)
        return {"reply": answer, "model": model, "ask_id": ask_id}
    except LLMError as e:
        raise _ai_error(e)
    finally:
        conn.close()


@app.post("/api/ai/rewrite")
def api_ai_rewrite(body: RewriteIn):
    if body.flavor not in ai_service.REWRITE_FLAVORS:
        raise HTTPException(400, f"未知改写模式：{body.flavor}")
    conn = _conn()
    try:
        return {
            "candidates": ai_service.rewrite(conn, body.text, flavor=body.flavor),
            "anchor": _anchor(body),
            "model": ai_service.model_name_for(conn, "rewrite"),
        }
    except LLMError as e:
        raise _ai_error(e)
    finally:
        conn.close()


@app.post("/api/ai/title-score")
def api_ai_title_score(body: TitleScoreIn):
    """标题评分：当前标题打分 + 3 个候选（分数+理由）。AI 只递候选，不自动写入标题。"""
    title = body.title.strip()
    if not title:
        raise HTTPException(400, "标题不能为空")
    if len(title) > 200:
        raise HTTPException(400, "标题过长（最多 200 字）")
    conn = _conn()
    try:
        out = ai_service.title_score(conn, title, body.context or "")
        out["model"] = ai_service.model_name_for(conn, "rewrite")
        return out
    except LLMError as e:
        raise _ai_error(e)
    finally:
        conn.close()


@app.post("/api/ai/insight")
def api_ai_insight(body: InsightIn):
    conn = _conn()
    try:
        return ai_service.insight(conn, body.title, body.blocks)
    except LLMError as e:
        raise _ai_error(e)
    finally:
        conn.close()


@app.post("/api/ai/search")
def api_ai_search(body: SearchIn):
    query = body.query.strip()
    if not query:
        raise HTTPException(400, "查询内容不能为空")
    if body.stream:
        # NDJSON 流式：stage → result 渐进渲染
        conn = _conn()
        return StreamingResponse(
            ai_service.search_stream(conn, query),
            media_type="application/x-ndjson",
        )
    conn = _conn()
    try:
        return {"results": ai_service.search(conn, query), "anchor": _anchor(body)}
    except LLMError as e:
        raise _ai_error(e)
    finally:
        conn.close()


@app.post("/api/ai/check")
def api_ai_check(body: CheckIn):
    claim = body.claim.strip()
    if not claim:
        raise HTTPException(400, "核验内容不能为空")
    conn = _conn()
    try:
        return {**ai_service.check(conn, claim), "anchor": _anchor(body)}
    except LLMError as e:
        raise _ai_error(e)
    finally:
        conn.close()


@app.get("/api/health")
def api_health():
    try:
        conn = db.connect()
        db.init(conn)
        conn.close()
        db_ok = "ok"
    except Exception:
        db_ok = "error"
    return {"status": "ok", "version": "0.2.0", "db": db_ok}


# ---------- 证据数据层（v3）API ----------

@app.get("/api/projects/{pid}/sources")
def api_list_sources(pid: int):
    conn = _conn()
    try:
        return {"sources": db.list_sources(conn, pid)}
    finally:
        conn.close()


@app.post("/api/projects/{pid}/sources")
def api_create_source(pid: int, body: SourceIn):
    url = body.url.strip()
    if not url:
        raise HTTPException(400, "来源地址不能为空")
    conn = _conn()
    try:
        sid = db.create_source(conn, pid, url, body.title, body.snippet, body.provider)
        return {"id": sid}
    finally:
        conn.close()


@app.get("/api/projects/{pid}/materials")
def api_list_materials(pid: int):
    conn = _conn()
    try:
        return {"materials": db.list_materials(conn, pid)}
    finally:
        conn.close()


@app.get("/api/materials")
def api_search_materials(q: str = "", tag: str = "", source_type: str = "", project_id: int | None = None):
    """素材库：全部或按项目，支持关键词/标签/来源类型筛选（方案 A）。"""
    conn = _conn()
    try:
        return {"materials": db.list_materials(conn, project_id, q, tag, source_type)}
    finally:
        conn.close()


@app.post("/api/projects/{pid}/materials")
def api_create_material(pid: int, body: MaterialIn):
    title = body.title.strip()
    if not title:
        raise HTTPException(400, "素材标题不能为空")
    conn = _conn()
    try:
        try:
            mid = db.create_material(conn, pid, title, body.content, body.source_id, body.tags)
        except db.NotFoundError as e:
            raise HTTPException(404, str(e))
        return {"id": mid}
    finally:
        conn.close()


@app.get("/api/materials/{mid}")
def api_get_material(mid: int):
    conn = _conn()
    try:
        m = db.get_material(conn, mid)
        if m is None:
            raise HTTPException(404, "素材不存在")
        return {"material": m}
    finally:
        conn.close()


@app.patch("/api/materials/{mid}")
def api_update_material(mid: int, body: MaterialIn):
    """编辑标签/标题/内容（方案 A：素材详情可编辑标签）。

    P0-2：先校验存在（404），tags 已在 pydantic 层清洗去重并限长（422），
    非法输入一律 4xx，不返回 500。
    """
    conn = _conn()
    try:
        m = db.get_material(conn, mid)
        if m is None:
            raise HTTPException(404, "素材不存在")
        conn.execute(
            "UPDATE materials SET title = ?, content = ?, tags = ? WHERE id = ?",
            (body.title, body.content, json.dumps(body.tags, ensure_ascii=False), mid),
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.get("/api/materials/{mid}/usage")
def api_material_usage(mid: int):
    """删除影响范围：该素材被哪些草稿引用（方案 A：删除前必须展示）。"""
    conn = _conn()
    try:
        return db.material_usage(conn, mid)
    finally:
        conn.close()


@app.delete("/api/materials/{mid}")
def api_delete_material(mid: int, unlink_only: int = 0, force: int = 0):
    """删除素材（P0-6 明确语义）。

    - unlink_only=1：只解除全部使用关系（Material/Citation/正文保留）→ unlinked=true
    - 被使用且未 force：409 + 真实影响（usages），不静默删除
    - force=1：删除 Material + 其使用关系（正文与 Citation 保留）
    - 未使用：直接删除
    """
    conn = _conn()
    try:
        usage = db.material_usage(conn, mid)
        if usage["material"] is None:
            raise HTTPException(404, "素材不存在")
        n_usage = len(usage["usages"])
        if unlink_only:
            removed = db.unlink_material(conn, mid)
            return {"ok": True, "unlinked": True, "removed_usages": removed,
                    "kept_material": True, "kept_citations": True, "kept_blocks": True}
        if n_usage and not force:
            raise HTTPException(409, {
                "code": "material_in_use",
                "message": f"该素材被 {n_usage} 处使用，需确认影响范围",
                "usages": usage["usages"],
                "articles": usage["articles"],
            })
        # force 或未使用：删除素材（material_usages 由 FK 级联删除；正文/Citation 保留）
        conn.execute("DELETE FROM materials WHERE id = ?", (mid,))
        conn.commit()
        return {"ok": True, "deleted": True, "removed_usages": n_usage,
                "kept_citations": True, "kept_blocks": True}
    finally:
        conn.close()


@app.get("/api/articles/{aid}/citations")
def api_list_citations(aid: int):
    conn = _conn()
    try:
        return {"citations": db.list_citations(conn, aid)}
    finally:
        conn.close()


@app.post("/api/articles/{aid}/citations")
def api_create_citation(aid: int, body: CitationIn):
    conn = _conn()
    try:
        cid = db.create_citation(conn, aid, body.block_id, body.source_id,
                                 body.quote, body.locator, body.display_label)
        return {"id": cid}
    finally:
        conn.close()


@app.delete("/api/citations/{cid}")
def api_delete_citation(cid: int):
    conn = _conn()
    try:
        if not db.delete_citation(conn, cid):
            raise HTTPException(404, "引用不存在")
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/projects/{pid}/fetch")
def api_fetch(pid: int, body: FetchIn):
    """安全抓取 URL → 存 evidence snapshot（source 自动创建/复用）。"""
    url = body.url.strip()
    if not url:
        raise HTTPException(400, "地址不能为空")
    try:
        snap = safe_fetch.fetch_url(url)
    except safe_fetch.SafeFetchError as e:
        raise HTTPException(400, str(e))
    conn = _conn()
    try:
        sid = db.create_source(conn, pid, url, title=snap["excerpt"][:80], snippet=snap["excerpt"][:500])
        eid = db.create_evidence_snapshot(conn, sid, snap["requested_url"], snap["final_url"],
                                          snap["mime"], snap["content_hash"], snap["excerpt"])
        return {"evidence_id": eid, "source_id": sid, **snap}
    finally:
        conn.close()


# ---------- 回收站 / 历史 / 导出（v4） ----------

@app.delete("/api/articles/{aid}")
def api_delete_article(aid: int):
    conn = _conn()
    try:
        if not db.soft_delete_article(conn, aid):
            raise HTTPException(404, "草稿不存在或已在回收站")
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/articles/{aid}/restore")
def api_restore_article(aid: int):
    conn = _conn()
    try:
        if not db.restore_article(conn, aid):
            raise HTTPException(404, "草稿不存在")
        return {"ok": True}
    finally:
        conn.close()


@app.get("/api/projects/{pid}/trash")
def api_list_trash(pid: int):
    conn = _conn()
    try:
        return {"trash": db.list_trash(conn, pid)}
    finally:
        conn.close()


@app.get("/api/trash")
def api_list_all_trash():
    """回收站：全部项目的已删草稿（回收站 UI 入口用）。"""
    conn = _conn()
    try:
        return {"trash": db.list_trash(conn, None)}
    finally:
        conn.close()


@app.delete("/api/projects/{pid}")
def api_delete_project(pid: int):
    conn = _conn()
    try:
        if not db.soft_delete_project(conn, pid):
            raise HTTPException(404, "项目不存在或已在回收站")
        return {"ok": True}
    finally:
        conn.close()


@app.get("/api/articles/{aid}/revisions")
def api_list_revisions(aid: int):
    conn = _conn()
    try:
        return {"revisions": db.list_revisions(conn, aid)}
    finally:
        conn.close()


@app.post("/api/articles/{aid}/revisions/{version}/restore")
def api_restore_revision(aid: int, version: int, point: str = "after"):
    conn = _conn()
    try:
        try:
            new_version = db.restore_revision(conn, aid, version, point)
        except db.NotFoundError as e:
            raise HTTPException(404, str(e))
        except db.RevisionNoBefore as e:
            raise HTTPException(400, str(e))
        except db.VersionConflict as e:
            raise HTTPException(409, {
                "code": "version_conflict",
                "current_version": e.current_version,
                "message": "恢复时正文已被修改，请刷新后重试",
            })
        return {"ok": True, "version": new_version}
    finally:
        conn.close()


@app.post("/api/signals")
def api_signals(body: SignalIn):
    """写作行为信号上报（显式：工具点击/接受/拒绝/标记）。不落正文，只存最小元数据。"""
    copilot.record_signal(body.article_id, body.model_dump())
    return {"ok": True}


@app.post("/api/copilot/suggest")
def api_copilot_suggest(body: SuggestIn):
    """规则优先建议：无模型配置时规则建议仍工作；每条建议可解释（reason/target/source）。"""
    conn = _conn()
    try:
        s = settings.get_settings(conn)
        state = copilot.signals_to_state(copilot.get_signals(body.article_id))
        ctx = {
            "stage": state["stage"],
            "issue": body.issue or state["issue"],
            "focus": body.block_id or state["focus"],
            "block_id": body.block_id,
            "article_id": body.article_id,
            "model_configured": s["configured"],
        }
        engine = copilot.CopilotEngine(dismissed=copilot.get_dismissed(body.article_id))
        sugs = engine.suggest(ctx)
        if body.issue and sugs:
            # 手动标记后刷新建议：把标记也记入信号（保持状态一致）
            copilot.record_signal(body.article_id, {"type": "mark", "issue": body.issue, "focus": body.block_id or "article"})
        return {"suggestions": sugs, "state": state}
    finally:
        conn.close()


@app.get("/api/articles/{aid}/continue")
def api_continue_writing(aid: int):
    """方案 E：继续写入口——上次编辑时间、位置、最近素材、待办、待复查引用。"""
    conn = _conn()
    try:
        art = db.get_article(conn, aid)
        if art is None:
            raise HTTPException(404, "草稿不存在")
        mats = db.list_materials(conn, art["project_id"])
        sess = conn.execute(
            "SELECT id FROM review_sessions WHERE article_id = ? AND status != 'error' ORDER BY id DESC LIMIT 1",
            (aid,),
        ).fetchone()
        pending = 0
        if sess:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM review_issues WHERE review_id = ? AND state = 'open'", (sess["id"],)
            ).fetchone()
            pending = row["n"] if row else 0
        needs_recheck = conn.execute(
            "SELECT COUNT(*) AS n FROM citations WHERE article_id = ? AND status = 'active'"
            " AND verification_status = 'needs_recheck'", (aid,),
        ).fetchone()["n"]
        state = db.get_editor_state(conn, aid)
        block_ids = {b["id"] for b in art["blocks"]}
        # 位置失效安全回退：保存的 block 已不存在 → 不返回位置（前端回退最近块）
        pos = state.get("position") or {}
        if pos.get("block_id") and pos["block_id"] not in block_ids:
            pos = {}
        # 一句可解释的"下一步"
        if not art["blocks"]:
            next_step = "新草稿：从第一段开始写，或先搜索/保存素材"
        elif needs_recheck:
            next_step = f"有 {needs_recheck} 处引用因正文变化需复查"
        elif pending:
            next_step = f"有 {pending} 项待处理检查"
        else:
            next_step = "继续上次写作位置"
        return {
            "last_edited": art["updated_at"],
            "recent_materials": mats[:3],
            "pending_review": pending,
            "needs_recheck": needs_recheck,
            "position": pos,
            "next_step": next_step,
        }
    finally:
        conn.close()


class PositionIn(BaseModel):
    block_id: str = ""
    offset: int = 0
    scroll_top: int = 0


@app.put("/api/articles/{aid}/position")
def api_save_position(aid: int, body: PositionIn):
    """保存本地写作位置（继续写）。位置不改变正文、不进模型上下文。"""
    conn = _conn()
    try:
        if db.get_article(conn, aid) is None:
            raise HTTPException(404, "草稿不存在")
        state = db.get_editor_state(conn, aid)
        state["position"] = {
            "block_id": body.block_id,
            "offset": max(0, body.offset),
            "scroll_top": max(0, body.scroll_top),
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        db.save_editor_state(conn, aid, state)
        return {"ok": True}
    finally:
        conn.close()


@app.get("/api/articles/{aid}/asks")
def api_list_asks(aid: int):
    conn = _conn()
    try:
        return {"asks": db.list_asks(conn, aid, 50)}
    finally:
        conn.close()


@app.delete("/api/asks/{ask_id}")
def api_delete_ask(ask_id: int):
    """删除一条 Ask 历史（P1-4：Ask 历史可管理）。"""
    conn = _conn()
    try:
        if not db.delete_ask(conn, ask_id):
            raise HTTPException(404, "问答记录不存在")
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/asks/{ask_id}/usage")
def api_set_ask_usage(ask_id: int, body: AskUsageIn):
    """Ask 结果使用状态（方案 B 固定动作名：保存为素材 / 插入正文）。"""
    conn = _conn()
    try:
        if not db.set_ask_usage(conn, ask_id, body.usage):
            raise HTTPException(404, "问答记录不存在")
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/citations/{cid}/verification")
def api_set_citation_verification(cid: int, body: VerificationIn):
    """引用核验状态（方案 C 六态）。"""
    conn = _conn()
    try:
        if not db.set_citation_verification(conn, cid, body.status, body.note):
            raise HTTPException(404, "引用不存在")
        return {"ok": True}
    finally:
        conn.close()


# ---------- 作者记忆（透明可删） ----------

@app.get("/api/prefs")
def api_list_prefs():
    conn = _conn()
    try:
        return {"prefs": db.list_prefs(conn)}
    finally:
        conn.close()


@app.post("/api/prefs")
def api_add_pref(body: PrefIn):
    key = body.key.strip()
    content = body.content.strip()
    if not key or not content:
        raise HTTPException(400, "偏好键与内容不能为空")
    conn = _conn()
    try:
        db.add_pref(conn, key[:50], content)
        return {"ok": True}
    finally:
        conn.close()


@app.delete("/api/prefs/{key}")
def api_delete_pref(key: str):
    conn = _conn()
    try:
        if not db.delete_pref(conn, key):
            raise HTTPException(404, "偏好不存在")
        return {"ok": True}
    finally:
        conn.close()


# ---------- 多模型配置（profile + task binding） ----------

@app.get("/api/profiles")
def api_list_profiles():
    conn = _conn()
    try:
        return {"profiles": db.list_profiles(conn), "bindings": db.get_bindings(conn)}
    finally:
        conn.close()


@app.post("/api/profiles")
def api_create_profile(body: ProfileIn):
    name = body.name.strip()
    if not name or not body.base_url.strip() or not body.model.strip():
        raise HTTPException(400, "名称/地址/模型不能为空")
    try:
        settings._validate_base_url(body.base_url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    enc = settings._encrypt(body.api_key.strip().encode("utf-8")) if body.api_key and body.api_key.strip() else None
    conn = _conn()
    try:
        pid = db.create_profile(conn, name, body.base_url.strip(), body.model.strip(), enc, body.capabilities)
        return {"id": pid}
    finally:
        conn.close()


@app.delete("/api/profiles/{pid}")
def api_delete_profile(pid: int):
    conn = _conn()
    try:
        if not db.delete_profile(conn, pid):
            raise HTTPException(404, "模型不存在")
        return {"ok": True}
    finally:
        conn.close()


@app.put("/api/bindings")
def api_set_binding(body: BindingIn):
    if body.task not in ("ask", "rewrite", "insight", "search_synthesis", "check"):
        raise HTTPException(400, "未知任务类型")
    conn = _conn()
    try:
        db.set_binding(conn, body.task, body.profile_id)
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/profiles/{pid}/test")
def api_test_profile(pid: int):
    """连接测试：发最小请求探测可达性，不保存任何用户内容。

    P0-3：错误按 LLMError.kind 映射为明确状态码；任何响应不含 API Key。
    """
    conn = _conn()
    try:
        p = db.get_profile(conn, pid)
        if p is None:
            raise HTTPException(404, "模型不存在")
        key = db.get_profile_key(conn, pid)
        if not key:
            raise HTTPException(400, "该模型未配置 API Key")
        client = LLMClient(base_url=p["base_url"], api_key=key, model=p["model"])
        client.chat([{"role": "user", "content": "ping"}], json_mode=False, max_tokens=5)
        return {"ok": True, "model": p["model"]}
    except LLMError as e:
        # kind: timeout → 504；其余上游失败（auth/network/http/empty）→ 502。
        # 消息只含可读原因，绝不回显密钥。
        status = 504 if e.kind == "timeout" else 502
        raise HTTPException(status, f"模型连接失败：{e}")
    finally:
        conn.close()


@app.get("/api/articles/{aid}/export")
def api_export_article(aid: int, format: str = "md", appendix: int = 1, theme: str = "default"):
    """统一导出（P0-5）：Markdown / 纯文本 / Word / 公众号 HTML，含引用清单与来源附录。

    - format: md | txt | docx | wechat（公众号编辑器可粘贴的行内样式 HTML 片段）
    - theme 仅对 wechat 生效：default | elegant | simple | tech（非法值回落 default）
    - appendix=0 时省略来源附录（引用清单始终包含）
    - 导出只读，不修改原稿
    """
    conn = _conn()
    try:
        try:
            data = export_service.build_export_data(conn, aid)
        except export_service.ExportError as e:
            raise HTTPException(404, str(e))
        try:
            content = export_service.render(data, format, include_appendix=bool(appendix), theme=theme)
        except export_service.ExportError as e:
            raise HTTPException(400, str(e))
        media = {
            "md": "text/markdown; charset=utf-8",
            "markdown": "text/markdown; charset=utf-8",
            "txt": "text/plain; charset=utf-8",
            "text": "text/plain; charset=utf-8",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "wechat": "text/html; charset=utf-8",
        }[format.lower()]
        fname = export_service.safe_filename(data["article"]["title"], format)
        # RFC 5987：中文/特殊字符文件名在响应头中用百分号编码
        quoted = urllib.parse.quote(fname)
        return Response(content=content, media_type=media,
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"})
    except export_service.ExportError as e:
        raise HTTPException(400, str(e))
    except ValueError as e:
        raise HTTPException(400, f"无法生成安全的文件名：{e}")
    finally:
        conn.close()


app.include_router(review_router)

# 静态前端（必须最后挂载；基于文件位置，任意 CWD 可用）
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
