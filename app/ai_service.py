"""AI 服务层：ask / rewrite / insight / search / check。

prompt 构造 + 输出解析 + 降级链。LLM 客户端从设置读取（用户自配 key/模型）。
所有函数以 conn 为第一个参数（读取设置）。
"""

import json
import re
import urllib.parse
import urllib.request

from app.llm import LLMClient, LLMError
from app.settings import get_api_key, get_settings


def _require_client(conn) -> LLMClient:
    s = get_settings(conn)
    if not s["configured"]:
        raise LLMError("尚未配置模型：请先到设置里填写 API Key 与模型", "config")
    return LLMClient(s["base_url"], get_api_key(conn), s["model"])


def ask(conn, prompt: str, context: str) -> str:
    client = _require_client(conn)
    messages = [
        {
            "role": "system",
            "content": "你是写作助手「文序」，帮助作者改进文章。回答要简洁、具体、可操作，使用中文。",
        },
        {
            "role": "user",
            "content": f"当前文章上下文：\n{context}\n\n作者的问题：{prompt}",
        },
    ]
    return client.chat(messages)


def rewrite(conn, text: str) -> list[dict]:
    client = _require_client(conn)
    messages = [
        {
            "role": "system",
            "content": (
                "你是中文写作编辑。针对用户给出的文字，给出 2 个不同风格的改写方案。"
                "只返回 JSON，格式：{\"candidates\": [{\"label\": \"方案一\", \"text\": \"改写文字\"}, "
                "{\"label\": \"方案二\", \"text\": \"改写文字\"}]}"
            ),
        },
        {"role": "user", "content": text},
    ]
    raw = client.chat(messages, json_mode=True)
    return _parse_rewrite(raw, text)


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
    lines = [l.strip() for l in re.split(r"\n+", raw) if l.strip()]
    if lines:
        return [{"label": "方案一", "text": lines[0][:500]}]
    return [{"label": "方案一", "text": fallback_text[:500]}]


def insight(conn, title: str, blocks: list) -> dict:
    client = _require_client(conn)
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


def search(conn, query: str) -> list[dict]:
    """搜索：中文 Wikipedia → DuckDuckGo 真检索；源不可达时降级为 LLM 知识线索。

    每条结果带 source 字段：web=实时检索 / model=模型知识（建议核实）。
    """
    results = _wikipedia_search(query)
    if not results:
        results = _ddg_search(query)
    if results:
        return results[:5]
    try:
        return _model_search(conn, query)[:5]
    except LLMError:
        return []


def _model_search(conn, query: str) -> list[dict]:
    """LLM 知识降级：基于模型知识给出资料线索（不联网）。"""
    client = _require_client(conn)
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


def check(conn, claim: str) -> dict:
    """事实核验：LLM 三态判断（可信 / 存疑 / 建议修改）。"""
    client = _require_client(conn)
    messages = [
        {
            "role": "system",
            "content": (
                "你是事实核查员。判断用户给出的陈述是否可信。"
                "只返回 JSON，格式："
                '{"status": "ok|doubt|fix", "reason": "一句话理由", "suggestion": "若 status=fix 给出可替代的稳妥表述，否则空字符串"}。'
                "status 含义：ok=有把握可信；doubt=无法证实或信息不足；fix=明显不准确或夸大，需要修改。"
            ),
        },
        {"role": "user", "content": claim},
    ]
    raw = client.chat(messages, json_mode=True)
    return _parse_check(raw)


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
