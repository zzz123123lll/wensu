"""文序 · 后端 API（第一阶段：项目/草稿/正文块 CRUD）。"""

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import ai_service, db, settings
from app.llm import LLMError

# 静态目录基于文件位置，而非当前工作目录（任意 CWD 可启动）
WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

app = FastAPI(title="文序", version="0.1.0")

# 原型阶段允许跨源（prototype 独立端口调试用；上线前收紧）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ProjectIn(BaseModel):
    name: str


class ArticleIn(BaseModel):
    title: str


class ArticleUpdate(BaseModel):
    title: str | None = None
    blocks: list | None = None


class SettingsIn(BaseModel):
    base_url: str
    model: str
    api_key: str | None = None


class AskIn(BaseModel):
    prompt: str
    context: str = ""


class RewriteIn(BaseModel):
    text: str


class InsightIn(BaseModel):
    title: str = ""
    blocks: list = []


class SearchIn(BaseModel):
    query: str


class CheckIn(BaseModel):
    claim: str


def _ai_error(e: LLMError) -> HTTPException:
    status = 400 if e.kind == "config" else 502
    return HTTPException(status, str(e))


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
        return {"id": db.create_article(conn, pid, title), "title": title}
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
        if body.title is not None and body.title.strip():
            db.update_article_title(conn, aid, body.title.strip())
        if body.blocks is not None:
            db.save_article_blocks(conn, aid, body.blocks)
        return {"ok": True}
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
        settings.save_settings(conn, body.base_url, body.model, body.api_key)
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
        return {"candidates": ai_service.rewrite(conn, body.text)}
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
        return {"results": ai_service.search(conn, query)}
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
        return ai_service.check(conn, claim)
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
    return {"status": "ok", "version": "0.1.0", "db": db_ok}


# 静态前端（必须最后挂载；基于文件位置，任意 CWD 可用）
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
