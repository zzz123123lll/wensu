"""AI 痕迹本地估计（行为契约回收自旧仓"文成" pipeline/humanizer.py）。

- 纯本地词汇规则，不调用模型：AI 高频词 / 连接词密度 / 平均句长 / 重复句比例 → 0-100 痕迹分
- 供成稿检查确定性规则（common.language.ai-trace）与"去 AI 味"改写 flavor 共用
"""

import re

AI_PHRASES = (
    "总而言之",
    "综上所述",
    "值得注意的是",
    "不难发现",
    "众所周知",
    "让我们来看看",
    "让我们一起来",
    "赋能",
    "抓手",
    "闭环",
    "底层逻辑",
    "颗粒度",
    "无独有偶",
    "无疑",
    "显然",
    "毋庸置疑",
    "在当今",
    "在当下",
    "在这个快节奏的时代",
)

CONNECTORS = (
    "因此",
    "然而",
    "此外",
    "换言之",
    "也就是说",
    "由此可见",
    "与此同时",
)

_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?\n]")


def estimate_ai_trace(text: str | None) -> dict:
    """0-100 AI 痕迹估计（仅本地词汇规则，确定性纯函数）。"""
    body = str(text or "")
    if not body.strip():
        return {
            "ai_phrase_hits": 0,
            "connector_hits": 0,
            "avg_sentence_length": 0,
            "repeat_sentence_ratio": 0,
            "score": 0,
        }
    phrase_hits = sum(body.count(phrase) for phrase in AI_PHRASES)
    connector_hits = sum(body.count(phrase) for phrase in CONNECTORS)
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(body) if s.strip()]
    avg_length = round(sum(len(s) for s in sentences) / len(sentences), 1) if sentences else 0
    seen = set()
    duplicates = 0
    for sentence in sentences:
        key = sentence[:40]
        if key in seen:
            duplicates += 1
        seen.add(key)
    repeat_ratio = round(duplicates / len(sentences), 2) if sentences else 0
    score = min(100, phrase_hits * 10 + connector_hits * 5)
    if avg_length > 45:
        score += 10
    if repeat_ratio > 0.05:
        score += 10
    return {
        "ai_phrase_hits": phrase_hits,
        "connector_hits": connector_hits,
        "avg_sentence_length": avg_length,
        "repeat_sentence_ratio": repeat_ratio,
        "score": min(100, score),
    }


def find_ai_phrases(text: str | None, limit: int = 3) -> list[str]:
    """返回正文中命中的 AI 高频词/连接词（按词表顺序，最多 limit 个）。"""
    body = str(text or "")
    if not body.strip():
        return []
    hits = [p for p in (*AI_PHRASES, *CONNECTORS) if p in body]
    return hits[:limit]
