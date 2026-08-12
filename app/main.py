"""文序 · 后端 API（第一阶段：项目/草稿/正文块 CRUD）。"""

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import ai_service, db, settings
from app.llm import LLMError
from app.schemas import Block

# 静态目录基于文件位置，而非当前工作目录（任意 CWD 可启动）
WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

app = FastAPI(title="文序", version="0.1.0")

# 本机绑定服务：只允许本地源（防恶意网页跨源调用本机 API）
ALLOWED_ORIGINS = {"http://localhost:8766", "http://127.0.0.1:8766"}
ALLOWED_HOSTS = {"127.0.0.1:8766", "localhost:8766"}


@app.middleware("http")
async def security_guard(request, call_next):
    # Host 校验（防 DNS rebinding / 伪造 Host）
    host = request.headers.get("host", "")
    if host not in ALLOWED_HOSTS:
        return JSONResponse({"detail": "invalid host"}, status_code=403)
    # 写请求与 AI API：Origin 校验（无 Origin 的同源/非浏览器请求放行）
    if request.method in ("POST", "PUT", "DELETE") and request.url.path.startswith("/api/"):
        origin = request.headers.get("origin")
        if origin and origin not in ALLOWED_ORIGINS:
            return JSONResponse({"detail": "cross-origin request rejected"}, status_code=403)
    return await call_next(request)


# 原型阶段允许跨源（prototype 独立端口调试用；上线前收紧）
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(ALLOWED_ORIGINS),
    allow_methods=["*"],
    allow_headers=["*"],
)


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
        return {"reply": ai_service.ask(conn, body.prompt, body.context)}
    except LLMError as e:
        raise _ai_error(e)
    finally:
        conn.close()


@app.post("/api/ai/rewrite")
def api_ai_rewrite(body: RewriteIn):
    conn = _conn()
    try:
        return {"candidates": ai_service.rewrite(conn, body.text), "anchor": _anchor(body)}
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


# 静态前端（必须最后挂载；基于文件位置，任意 CWD 可用）
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
