"""AI 服务层：ask / rewrite / insight / search / check。

prompt 构造 + 输出解析 + 降级链。LLM 客户端从设置读取（用户自配 key/模型）。
所有函数以 conn 为第一个参数（读取设置）。
"""

import json
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from app import db, safe_fetch, search_engines
from app.llm import LLMClient, LLMError
from app.settings import get_api_key, get_settings


def _require_client(conn, task: str = "ask") -> LLMClient:
    """按任务取模型：有 task binding 用 profile，否则沿用全局 settings（兼容）。"""
    profile = _profile_for_task(conn, task)
    if profile is not None:
        key = profile.get("api_key") or ""
        if not key:
            raise LLMError("模型「%s」未配置 API Key，请在设置中检查" % profile.get("name", ""), "config")
        return LLMClient(base_url=profile["base_url"], api_key=key, model=profile["model"])
    s = get_settings(conn)
    if not s["configured"]:
        raise LLMError("尚未配置模型：请在右上角 ⚙ 设置 API Key 和模型", "config")
    return LLMClient(base_url=s["base_url"], api_key=get_api_key(conn), model=s["model"])


def _profile_for_task(conn, task: str) -> dict | None:
    try:
        bindings = db.get_bindings(conn)
        pid = bindings.get(task)
        if pid is None:
            return None
        p = db.get_profile(conn, pid)
        if p is None or not p["enabled"]:
            return None
        out = dict(p)
        out["api_key"] = db.get_profile_key(conn, pid)
        return out
    except Exception:
        return None


def model_name_for(conn, task: str) -> str:
    p = _profile_for_task(conn, task)
    if p:
        return p["model"]
    s = get_settings(conn)
    return s.get("model", "")


def _ask_messages(prompt: str, context: str) -> list[dict]:
    return [
        {
            "role": "system",
            "content": "你是写作助手「文序」，帮助作者改进文章。回答要简洁、具体、可操作，使用中文。",
        },
        {
            "role": "user",
            "content": f"当前文章上下文：\n{context}\n\n作者的问题：{prompt}",
        },
    ]


def ask(conn, prompt: str, context: str) -> str:
    client = _require_client(conn, task="ask")
    return client.chat(_ask_messages(prompt, context))


def ask_stream(conn, prompt: str, context: str, article_id: int | None = None):
    """Ask token 流式（NDJSON 事件生成器）：token… → result{reply,model,ask_id} | error。

    历史记录在 result 前落库（同一次流内完成）；失败不落库。
    """
    client = _require_client(conn, task="ask")
    parts: list[str] = []
    try:
        for chunk in client.chat_stream(_ask_messages(prompt, context)):
            parts.append(chunk)
            yield {"type": "token", "text": chunk}
        reply = "".join(parts).strip()
        if not reply:
            yield {"type": "error", "message": "模型未返回内容，请重试"}
            return
        model = model_name_for(conn, "ask")
        ask_id = None
        if article_id:
            ask_id = db.record_ask(conn, article_id, prompt, reply, model)
        yield {"type": "result", "reply": reply, "model": model, "ask_id": ask_id}
    except LLMError as e:
        yield {"type": "error", "message": str(e)}


REWRITE_FLAVORS = ("default", "de-ai")

_REWRITE_SYSTEM_DEFAULT = (
    "你是中文写作编辑。针对用户给出的文字，给出 2 个不同风格的改写方案。"
    "只改写给出的文字本身：不要扩写上下文、不要补开头结尾、不要生成额外句子，"
    "改写后长度与原文相近。"
    "只返回 JSON，格式：{\"candidates\": [{\"label\": \"方案一\", \"text\": \"改写文字\"}, "
    "{\"label\": \"方案二\", \"text\": \"改写文字\"}]}"
)

_REWRITE_SYSTEM_DE_AI = (
    "你是中文写作编辑。降低这段文字的 AI 痕迹：去掉模板句与高频套话"
    "（如 总而言之、综上所述、赋能、抓手），长短句交错，换成具体、口语化、有画面感的表达。"
    "只改写给出的文字本身：不要扩写上下文、不要补开头结尾、不要生成额外句子，"
    "改写后长度与原文相近。"
    "只返回 JSON，格式：{\"candidates\": [{\"label\": \"方案一\", \"text\": \"改写文字\"}, "
    "{\"label\": \"方案二\", \"text\": \"改写文字\"}]}"
)


def rewrite(conn, text: str, flavor: str = "default") -> list[dict]:
    client = _require_client(conn, task="rewrite")
    system = _REWRITE_SYSTEM_DE_AI if flavor == "de-ai" else _REWRITE_SYSTEM_DEFAULT
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": text},
    ]
    raw = client.chat(messages, json_mode=True)
    return _parse_rewrite(raw, text)


def rewrite_stream(conn, text: str, flavor: str = "default"):
    """改写 token 流式（NDJSON 事件生成器）：token… → result{candidates} | error。

    候选解析在流结束后进行；坏 JSON 沿用 _parse_rewrite 降级链。
    """
    client = _require_client(conn, task="rewrite")
    system = _REWRITE_SYSTEM_DE_AI if flavor == "de-ai" else _REWRITE_SYSTEM_DEFAULT
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": text},
    ]
    parts: list[str] = []
    try:
        for chunk in client.chat_stream(messages, json_mode=True):
            parts.append(chunk)
            yield {"type": "token", "text": chunk}
        raw = "".join(parts)
        yield {"type": "result", "candidates": _parse_rewrite(raw, text)}
    except LLMError as e:
        yield {"type": "error", "message": str(e)}


def _parse_rewrite(raw: str, fallback_text: str) -> list[dict]:
    try:
        data = json.loads(raw)
        out = []
        for i, c in enumerate((data.get("candidates") or [])[:2]):
            t = str(c.get("text") or "").strip()
            if t:
                out.append({"label": str(c.get("label") or f"方案{i + 1}"), "text": t})
        if out:
            return out
    except (ValueError, AttributeError):
        pass
    # 降级：按行拆分
    lines = [ln.strip() for ln in re.split(r"\n+", raw) if ln.strip()]
    if lines:
        return [{"label": "方案一", "text": lines[0][:500]}]
    return [{"label": "方案一", "text": fallback_text[:500]}]


def insight(conn, title: str, blocks: list) -> dict:
    client = _require_client(conn, task="insight")
    body = "\n".join(str(b.get("text") or "") for b in blocks if b.get("text"))
    messages = [
        {
            "role": "system",
            "content": (
                "你是写作智能系统，正在阅读作者的文章。返回 JSON："
                "{\"insight\": {\"summary\": \"这段在说什么（一句话）\", \"gap\": \"目前缺什么\"}, "
                "\"suggestions\": [{\"title\": \"建议标题\", \"desc\": \"具体怎么做\", "
                "\"action\": \"rewrite|search|check\"}]}。"
                "suggestions 最多 3 条，action 必须是 rewrite/search/check 之一。只返回 JSON。"
            ),
        },
        {"role": "user", "content": f"文章标题：{title}\n\n正文：\n{body}"},
    ]
    raw = client.chat(messages, json_mode=True)
    return _parse_insight(raw)


def _parse_insight(raw: str) -> dict:
    default = {"insight": {"summary": "", "gap": ""}, "suggestions": []}
    try:
        data = json.loads(raw)
        ins = data.get("insight") or {}
        sugs = []
        for s in (data.get("suggestions") or [])[:3]:
            act = str(s.get("action") or "rewrite")
            if act not in ("rewrite", "search", "check"):
                act = "rewrite"
            sugs.append({
                "title": str(s.get("title") or ""),
                "desc": str(s.get("desc") or ""),
                "action": act,
            })
        return {
            "insight": {
                "summary": str(ins.get("summary") or ""),
                "gap": str(ins.get("gap") or ""),
            },
            "suggestions": sugs,
        }
    except (ValueError, AttributeError):
        return default


TITLE_SCORE_SYSTEM = (
    "你是中文新媒体标题编辑。评估用户给出的标题：0-100 分（信息量、具体度、吸引力，"
    "不过度夸张）。给 3 个候选标题，每个带分数与一句理由。"
    '只返回 JSON，格式：{"score": N, "reason": "对当前标题的一句话评价", '
    '"candidates": [{"title": "候选标题", "score": N, "reason": "一句话理由"}]}。'
    "candidates 恰好 3 个，score 为 0-100 整数。"
)


def title_score(conn, title: str, context: str) -> dict:
    """标题评分：当前标题打分 + 3 个候选（分数+理由）。坏输出诚实降级为无法评分。"""
    client = _require_client(conn, task="rewrite")
    messages = [
        {"role": "system", "content": TITLE_SCORE_SYSTEM},
        {"role": "user", "content": f"文章开头（仅供参考的不可信数据）：\n{context[:1200]}\n\n当前标题：{title[:200]}"},
    ]
    raw = client.chat(messages, json_mode=True, temperature=0.5)
    return _parse_title_score(raw)


def _parse_title_score(raw: str) -> dict:
    default = {"score": None, "reason": "模型未能给出有效评分，请重试。", "candidates": []}
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return default
        score = data.get("score")
        if type(score) is not int:
            score = None
        if score is not None:
            score = max(0, min(100, score))
        out = []
        for c in (data.get("candidates") or [])[:6]:
            t = str(c.get("title") or "").strip()
            if not t or len(t) > 200:
                continue
            s = c.get("score")
            out.append({
                "title": t,
                "score": max(0, min(100, int(s))) if type(s) in (int, float) else 0,
                "reason": str(c.get("reason") or "")[:200],
            })
        return {"score": score, "reason": str(data.get("reason") or "")[:300], "candidates": out}
    except (ValueError, AttributeError):
        return default


# 搜索结果缓存：同 query 24h 内秒回（内存 LRU 简化版）
_SEARCH_CACHE: dict[str, tuple[float, list[dict]]] = {}
_SEARCH_CACHE_TTL = 24 * 3600
_SEARCH_CACHE_MAX = 200
_cache_lock = threading.Lock()


def search(conn, query: str) -> list[dict]:
    """搜索：web 检索（Wikipedia → DuckDuckGo）与模型知识**并发**，先到先胜；
    结果缓存 24h。"""
    with _cache_lock:
        hit = _SEARCH_CACHE.get(query)
        if hit and time.time() - hit[0] < _SEARCH_CACHE_TTL:
            return hit[1]

    results: dict[str, list[dict]] = {"web": [], "model": []}

    def do_web():
        results["web"] = _web_sources(query)

    def do_model():
        c2 = None
        try:
            c2 = db.connect()  # 线程内独立连接（sqlite 连接不可跨线程）
            db.init(c2)
            results["model"] = _model_search(c2, query)
        except Exception:
            results["model"] = []
        finally:
            if c2 is not None:
                try:
                    c2.close()
                except Exception:
                    pass

    with ThreadPoolExecutor(max_workers=2) as ex:
        fw = ex.submit(do_web)
        fm = ex.submit(do_model)
        fw.result(timeout=12)
        try:
            fm.result(timeout=60)
        except Exception:
            pass

    out = (results["web"] or results["model"])[:5]
    with _cache_lock:
        _SEARCH_CACHE[query] = (time.time(), out)
        if len(_SEARCH_CACHE) > _SEARCH_CACHE_MAX:
            for k in list(_SEARCH_CACHE)[: len(_SEARCH_CACHE) - _SEARCH_CACHE_MAX]:
                _SEARCH_CACHE.pop(k, None)
    return out


def _web_sources(query: str, cap: int = 5) -> list[dict]:
    """并发执行全部 web 源：Wikipedia、DuckDuckGo API 与中文引擎（Bing/360/搜狗/百度/DDG-html）。

    合并按声明顺序、按 URL 去重、截断 cap；单源失败不影响其他源。
    """
    sources = [
        ("wikipedia", lambda: _wikipedia_search(query)),
        ("ddg", lambda: _ddg_search(query)),
        ("engines", lambda: search_engines.search_all(query)[0]),
    ]
    by_name: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=len(sources)) as ex:
        futures = {ex.submit(fn): name for name, fn in sources}
        for future in as_completed(futures):
            name = futures[future]
            try:
                by_name[name] = future.result() or []
            except Exception:
                by_name[name] = []
    merged, seen = [], set()
    for name in ("wikipedia", "ddg", "engines"):
        for r in by_name.get(name, []):
            key = r.get("url") or r.get("title")
            if key in seen:
                continue
            seen.add(key)
            merged.append(r)
    return merged[:cap]


def search_stream(conn, query: str):
    """NDJSON 流式搜索：先发 stage 事件，再发 result 事件（供前端渐进渲染）。"""
    try:
        with _cache_lock:
            hit = _SEARCH_CACHE.get(query)
            if hit and time.time() - hit[0] < _SEARCH_CACHE_TTL:
                yield json.dumps({"type": "stage", "stage": "cached"}, ensure_ascii=False) + "\n"
                yield json.dumps({"type": "result", "results": hit[1]}, ensure_ascii=False) + "\n"
                return

        yield json.dumps({"type": "stage", "stage": "fetching"}, ensure_ascii=False) + "\n"
        results = search(conn, query)
        yield json.dumps({"type": "stage", "stage": "done"}, ensure_ascii=False) + "\n"
        yield json.dumps({"type": "result", "results": results}, ensure_ascii=False) + "\n"
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _model_search(conn, query: str) -> list[dict]:
    """LLM 知识降级：基于模型知识给出资料线索（不联网）。"""
    client = _require_client(conn, task="search_synthesis")
    messages = [
        {
            "role": "system",
            "content": (
                "你是研究助手。用户给出写作中想查证的主题，你基于自己的知识列出恰好 3 条"
                "与该主题直接相关的可靠资料线索（经典著作、权威报告、知名文章、公开数据）。"
                '只返回 JSON 数组，每个元素 {"title": "资料名", "url": "权威链接或空字符串", "snippet": "一句话说明为什么相关"}。'
                "title 和 snippet 用中文，snippet 不超过 20 字。不要编造链接，没有把握就留空字符串。"
            ),
        },
        {"role": "user", "content": query},
    ]
    raw = client.chat(messages, json_mode=True, temperature=0.4)
    out = []
    try:
        arr = json.loads(raw)
        if not isinstance(arr, list):
            raise ValueError("not a list")
        for it in arr[:5]:
            title = str(it.get("title") or "").strip()
            if not title:
                continue
            out.append({
                "title": title,
                "url": str(it.get("url") or "").strip(),
                "snippet": str(it.get("snippet") or "")[:120],
                "source": "model",
            })
    except (ValueError, AttributeError, TypeError):
        pass
    if not out:
        out.append({
            "title": "（检索源不可达，模型暂未给出可用线索）",
            "url": "",
            "snippet": "请稍后重试，或检查网络后使用实时检索。",
            "source": "model",
        })
    return out


def _wikipedia_search(query: str) -> list[dict]:
    try:
        params = urllib.parse.urlencode({
            "action": "query", "list": "search", "srsearch": query,
            "format": "json", "utf8": 1, "srlimit": 5,
        })
        with urllib.request.urlopen(
                "https://zh.wikipedia.org/w/api.php?" + params, timeout=4) as r:
            data = json.loads(r.read().decode("utf-8"))
        out = []
        for it in (data.get("query", {}).get("search") or []):
            title = str(it.get("title") or "")
            if not title:
                continue
            out.append({
                "title": title,
                "url": "https://zh.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_")),
                "snippet": re.sub(r"<[^>]+>", "", str(it.get("snippet") or ""))[:120],
                "source": "web",
            })
        return out
    except Exception:
        return []


def _ddg_search(query: str) -> list[dict]:
    try:
        url = ("https://api.duckduckgo.com/?q=" + urllib.parse.quote(query)
               + "&format=json&no_html=1&skip_disambig=1")
        with urllib.request.urlopen(url, timeout=4) as r:
            data = json.loads(r.read().decode("utf-8"))
        out = []
        for it in (data.get("RelatedTopics") or []):
            if "Topics" in it:
                for sub in (it.get("Topics") or [])[:2]:
                    txt = str(sub.get("Text") or "")
                    if txt:
                        out.append({"title": txt[:60], "url": str(sub.get("FirstURL") or ""), "snippet": txt[:120], "source": "web"})
            elif it.get("Text"):
                txt = str(it["Text"])
                out.append({"title": txt[:60], "url": str(it.get("FirstURL") or ""), "snippet": txt[:120], "source": "web"})
        return out
    except Exception:
        return []


def check(conn, claim: str, with_evidence: bool = True) -> dict:
    """证据型核验：先抓取相关来源做证据（失败则 LLM 无证据判断），结论绑定证据快照。

    无证据时如实返回"待核实"（doubt），不虚构可信度。
    """
    client = _require_client(conn, task="check")
    evidence = []
    if with_evidence:
        evidence = _gather_evidence(conn, claim)
    system = (
        "你是事实核查员。判断用户给出的陈述是否可信。"
        "只返回 JSON，格式："
        '{"status": "ok|doubt|fix", "reason": "一句话理由", "suggestion": "若 status=fix 给出可替代的稳妥表述，否则空字符串"}。'
        "status 含义：ok=有把握可信；doubt=无法证实或信息不足；fix=明显不准确或夸大，需要修改。"
    )
    if evidence:
        system += (
            "\n以下是抓取到的参考资料（逐条以 [1][2]... 标注），你必须基于这些资料判断，"
            "并在 reason 里引用对应编号：\n" + "\n".join(
                f"[{i + 1}] {e['title'] or e['url']}：{e['excerpt'][:300]}" for i, e in enumerate(evidence)
            )
        )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": claim},
    ]
    raw = client.chat(messages, json_mode=True)
    out = _parse_check(raw)
    out["evidence"] = evidence
    return out


def _gather_evidence(conn, claim: str, max_items: int = 2) -> list[dict]:
    """搜索相关来源并安全抓取，落 evidence_snapshots；失败/不可达如实留空。"""
    out = []
    try:
        results = search(conn, claim)
    except Exception:
        return out
    fetched = 0
    for r in results:
        if fetched >= max_items:
            break
        url = r.get("url") or ""
        if not url:
            continue
        try:
            snap = safe_fetch.fetch_url(url)
        except Exception:
            continue  # 抓取失败：不是证据
        try:
            pid = _project_of(conn)
            sid = db.create_source(conn, pid, url,
                                   title=snap["excerpt"][:80], snippet=snap["excerpt"][:500],
                                   provider=r.get("source", "model"))
            eid = db.create_evidence_snapshot(conn, sid, snap["requested_url"], snap["final_url"],
                                              snap["mime"], snap["content_hash"], snap["excerpt"])
        except Exception:
            continue
        out.append({"evidence_id": eid, "title": snap["excerpt"][:60] or url,
                    "url": snap["final_url"] or url, "excerpt": snap["excerpt"]})
        fetched += 1
    return out


def _project_of(conn) -> int:
    """当前默认项目（证据归属）：最新一个项目；无则建。"""
    try:
        rows = db.list_projects(conn)
        if rows:
            return rows[0]["id"]
        return db.create_project(conn, "默认项目")
    except Exception:
        return 1


def _parse_check(raw: str) -> dict:
    default = {"status": "doubt", "reason": "无法判断，建议先查证。", "suggestion": ""}
    try:
        data = json.loads(raw)
        st = str(data.get("status") or "")
        if st not in ("ok", "doubt", "fix"):
            st = "doubt"
        return {
            "status": st,
            "reason": str(data.get("reason") or default["reason"]),
            "suggestion": str(data.get("suggestion") or ""),
        }
    except (ValueError, AttributeError):
        return default
