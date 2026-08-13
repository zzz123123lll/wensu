"""本地敏感词扫描（回收自旧仓"文成" pipeline/sensitive_words.py 行为契约）。

原则：
- 只报告类别与命中数量，绝不回显命中词本身（防二次传播、防敏感词进入 Issue/日志/导出摘要）
- 词库为本地经验清单，非任何平台官方清单；发布前仍需人工复核
- 未知类别跳过，文件缺失/损坏时返回空（不崩溃、不阻断检查）
"""

import functools
import os

CRITICAL_CATEGORIES = ("政治合规", "平台合规")
ADVISORY_CATEGORIES = ("广告合规", "知识产权", "隐私保护")
ALLOWED_CATEGORIES = CRITICAL_CATEGORIES + ADVISORY_CATEGORIES

_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sensitive_words.txt")


@functools.lru_cache(maxsize=8)
def _load(path: str) -> tuple[tuple, ...]:
    """读取词库：`类别:词1|词2` 行格式，# 开头为注释。返回 ((category, words), ...)。"""
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return ()
    groups = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        category, raw_words = line.split(":", 1)
        words = tuple(w.strip() for w in raw_words.split("|") if w.strip())
        if category in ALLOWED_CATEGORIES and words:
            groups.append((category, words))
    return tuple(groups)


def load_sensitive_words(path: str | None = None) -> list[dict]:
    """加载词库分组；默认使用内置词库文件。失败返回 []。"""
    target = path or _DEFAULT_PATH
    if not target or not os.path.exists(target):
        return []
    return [{"category": c, "words": list(w)} for c, w in _load(target)]


def scan_hits(text: str | None, categories: tuple[str, ...] | list[str] | None = None) -> list[dict]:
    """返回命中统计 [{"category": ..., "hits": n}]；不回显命中词。"""
    body = str(text or "")
    if not body.strip():
        return []
    wanted = set(categories) if categories else set(ALLOWED_CATEGORIES)
    hits = []
    for group in load_sensitive_words():
        if group["category"] not in wanted:
            continue
        count = sum(body.count(word) for word in group["words"])
        if count:
            hits.append({"category": group["category"], "hits": count})
    return hits
