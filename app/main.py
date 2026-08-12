"""文序 · 后端 API（第一阶段：项目/草稿/正文块 CRUD）。"""

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import ai_service, blocks, copilot, db, safe_fetch, settings
from app.llm import LLMError
from app.schemas import Block

# 静态目录基于文件位置，而非当前工作目录（任意 CWD 可启动）
WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

import secrets
from datetime import datetime, timezone

app = FastAPI(title="文序", version="0.2.0")

# 本机绑定服务：只允许本地源（防恶意网页跨源调用本机 API）
ALLOWED_ORIGINS = {"http://localhost:8766", "http://127.0.0.1:8766"}
ALLOWED_HOSTS = {"127.0.0.1:8766", "localhost:8766"}

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
    # 写请求 + AI API：Origin 校验（防恶意网页跨源调用本机服务）
    if request.method in ("POST", "PUT", "DELETE") and request.url.path.startswith("/api/"):
        origin = request.headers.get("origin")
        if origin and origin not in ALLOWED_ORIGINS:
            _err_count += 1
            return JSONResponse({"detail": "cross-origin request rejected"}, status_code=403)
        # session token 纵深防御：带了 token 就必须匹配（HttpOnly cookie 无法被恶意网页伪造）
        cookie_token = request.cookies.get("wensu_session")
        header_token = request.headers.get("x-wensu-token")
        if cookie_token is not None and cookie_token != SESSION_TOKEN:
            _err_count += 1
            return JSONResponse({"detail": "invalid session"}, status_code=403)
        if header_token is not None and header_token != SESSION_TOKEN:
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
    change_reason: str = "autosave"


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


class MaterialIn(BaseModel):
    title: str
    content: str = ""
    source_id: int | None = None


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
        plain_blocks = [b.model_dump() for b in body.blocks] if body.blocks is not None else None
        try:
            version = db.save_article(
                conn, aid, body.title, plain_blocks,
                base_version=body.base_version, reason=body.change_reason,
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
        if body.article_id:
            db.record_ask(conn, body.article_id, body.prompt, answer, model)
        return {"reply": answer, "model": model}
    except LLMError as e:
        raise _ai_error(e)
    finally:
        conn.close()


@app.post("/api/ai/rewrite")
def api_ai_rewrite(body: RewriteIn):
    conn = _conn()
    try:
        return {
            "candidates": ai_service.rewrite(conn, body.text),
            "anchor": _anchor(body),
            "model": ai_service.model_name_for(conn, "rewrite"),
        }
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


@app.post("/api/projects/{pid}/materials")
def api_create_material(pid: int, body: MaterialIn):
    title = body.title.strip()
    if not title:
        raise HTTPException(400, "素材标题不能为空")
    conn = _conn()
    try:
        mid = db.create_material(conn, pid, title, body.content, body.source_id)
        return {"id": mid}
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
def api_restore_revision(aid: int, version: int):
    conn = _conn()
    try:
        try:
            new_version = db.restore_revision(conn, aid, version)
        except db.NotFoundError as e:
            raise HTTPException(404, str(e))
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


@app.get("/api/articles/{aid}/asks")
def api_list_asks(aid: int):
    conn = _conn()
    try:
        return {"asks": db.list_asks(conn, aid, 20)}
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
    """连接测试：发最小请求探测可达性，不保存任何用户内容。"""
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
        raise HTTPException(502, str(e))
    finally:
        conn.close()


@app.get("/api/articles/{aid}/export")
def api_export_article(aid: int):
    conn = _conn()
    try:
        art = db.get_article(conn, aid)
        if art is None:
            raise HTTPException(404, "草稿不存在")
        md = f"# {art['title']}\n\n" + blocks.serialize_blocks(art["blocks"])
        return PlainTextResponse(md, media_type="text/markdown; charset=utf-8",
                                 headers={"Content-Disposition": f'attachment; filename="article-{aid}.md"'})
    finally:
        conn.close()


# 静态前端（必须最后挂载；基于文件位置，任意 CWD 可用）
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
