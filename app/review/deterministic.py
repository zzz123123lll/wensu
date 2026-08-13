"""确定性检查器：纯函数，输入快照 + 已解析规则 → 标准 Issue 列表。

相同输入必须产生相同结果；不调用模型；可机械验证的问题都在这里。
"""

import hashlib
import re
from urllib.parse import urlparse

# 重复标点（同一标点连续 2+）：。，、；：！？…
DUP_PUNCT = re.compile(r"([。，、；：！？…])\1+")
# 重复词（同一汉字连续 2+）
DUP_WORD = re.compile(r"([\u4e00-\u9fff])\1+")
# Markdown 链接
MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
# 图片
MD_IMG = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

HEADING_LEVEL = {"heading": 1, "heading2": 2, "heading3": 3, "heading4": 4}

_SAFE_SCHEMES = {"http", "https"}


def _issue(rule_id, severity, block_id, start, end, text, reason, suggestion=None, source_type="system"):
    anchor = {"block_id": block_id, "start_utf16": start, "end_utf16": end, "original_text": text}
    d = {
        "fingerprint": hashlib.sha1(f"{rule_id}|{block_id}|{start}|{end}|{text}".encode("utf-8")).hexdigest()[:16],
        "rule_id": rule_id,
        "severity": severity,
        "anchor": anchor,
        "reason": reason,
        "source_type": source_type,
    }
    if suggestion:
        d["suggestion"] = suggestion
    return d


def _run(rule_id, snap, params, f):
    """包装：规则实现接收 (snap, params) 返回 Issue 列表；任何异常都不吞静默。"""
    return f(snap, params)


# ---------- 规则实现 ----------

def _heading_order(snap, params):
    out = []
    max_skip = int(params.get("max_skip", 1))
    prev_level = 0
    for b in snap["blocks"]:
        level = HEADING_LEVEL.get(b.get("type"))
        if level is None:
            continue
        if prev_level and level > prev_level + 1 + max_skip:
            out.append(_issue(
                "common.heading.order", "error", b["id"], 0, len(b["text"]), b["text"][:80],
                f"标题跳级：从 H{prev_level} 直接到 H{level}",
                suggestion=f"将标题层级调整为 H{prev_level + 1} 或拆分段落"))
        prev_level = level
    return out


def _heading_empty(snap, params):
    out = []
    for b in snap["blocks"]:
        if b.get("type") in HEADING_LEVEL and not (b.get("text") or "").strip():
            out.append(_issue("common.heading.empty", "warning", b["id"], 0, 0, "",
                              "空标题：标题下没有文字内容", suggestion="填写标题或删除该标题"))
    return out


def _heading_duplicate(snap, params):
    out = []
    seen = {}
    for b in snap["blocks"]:
        if b.get("type") == "heading":
            t = (b.get("text") or "").strip()
            if not t:
                continue
            if t in seen:
                out.append(_issue("common.heading.duplicate-title", "warning", b["id"], 0, len(t), t[:80],
                                  f"主标题重复：与上文「{t[:30]}」相同", suggestion="区分章节标题或改写"))
            else:
                seen[t] = b["id"]
    return out


def _unsafe_url(snap, params):
    out = []
    for b in snap["blocks"]:
        text = b.get("text") or ""
        for m in MD_LINK.finditer(text):
            url = m.group(2).strip()
            p = urlparse(url)
            if p.scheme and p.scheme not in _SAFE_SCHEMES:
                out.append(_issue("common.markdown.unsafe-url", "error", b["id"], m.start(), m.end(), text[m.start():m.end()],
                                  f"不安全链接协议：{p.scheme}://", suggestion="改用 https 链接或删除"))
    return out


def _image_alt(snap, params):
    out = []
    for b in snap["blocks"]:
        text = b.get("text") or ""
        for m in MD_IMG.finditer(text):
            if not m.group(1).strip():
                out.append(_issue("common.markdown.image-alt", "warning", b["id"], m.start(), m.end(), text[m.start():m.end()],
                                  "图片缺少替代文本（alt）", suggestion="补充图片说明文字"))
    return out


def _double_punct(snap, params):
    out = []
    for b in snap["blocks"]:
        text = b.get("text") or ""
        for m in DUP_PUNCT.finditer(text):
            out.append(_issue("common.language.double-punctuation", "warning", b["id"], m.start(), m.end(), m.group(0),
                              "连续重复标点", suggestion="保留一个标点"))
    return out


def _repeated_word(snap, params):
    out = []
    for b in snap["blocks"]:
        text = b.get("text") or ""
        for m in DUP_WORD.finditer(text):
            out.append(_issue("common.language.repeated-word", "suggestion", b["id"], m.start(), m.end(), m.group(0),
                              "疑似重复字", suggestion=f"检查「{m.group(0)}」是否笔误"))
    return out


def _long_sentence(snap, params):
    out = []
    max_len = int(params.get("max_len", 100))
    for b in snap["blocks"]:
        text = b.get("text") or ""
        # 按标点切句，找超长片段
        parts = re.split(r"[。！？；\n]", text)
        pos = 0
        for part in parts:
            if len(part) > max_len:
                out.append(_issue("common.language.long-sentence", "suggestion", b["id"], pos, pos + len(part), part[:80],
                                  f"句子过长（{len(part)} 字 > 阈值 {max_len}）", suggestion="拆成短句"))
            pos += len(part) + 1
    return out


def _orphan_citation(snap, params):
    out = []
    block_ids = {b.get("id") for b in snap["blocks"]}
    for c in snap.get("citations", []):
        if c.get("status") == "active" and c.get("block_id") and c["block_id"] not in block_ids:
            out.append(_issue("common.evidence.orphan-citation", "error", c["block_id"], 0, 0, "",
                              "孤立引用：引用的段落已不存在", suggestion="删除该引用或恢复段落"))
    return out


def _missing_source(snap, params):
    out = []
    for c in snap.get("citations", []):
        if c.get("status") == "active" and not (c.get("source_title") or "").strip() and not (c.get("source_url") or "").strip():
            out.append(_issue("common.evidence.missing-source", "warning", c.get("block_id") or "", 0, 0, "",
                              "引用缺少来源标题与链接", suggestion="补充来源信息"))
    return out


_IMPL = {
    "common.heading.order": _heading_order,
    "common.heading.empty": _heading_empty,
    "common.heading.duplicate-title": _heading_duplicate,
    "common.markdown.unsafe-url": _unsafe_url,
    "common.markdown.image-alt": _image_alt,
    "common.language.double-punctuation": _double_punct,
    "common.language.repeated-word": _repeated_word,
    "common.language.long-sentence": _long_sentence,
    "common.evidence.orphan-citation": _orphan_citation,
    "common.evidence.missing-source": _missing_source,
}


def run_rule(rule_id: str, snapshot: dict, params: dict | None = None) -> list[dict]:
    """运行单条确定性规则；未知规则返回空（由 pack 校验保证不出现）。"""
    impl = _IMPL.get(rule_id)
    if impl is None:
        return []
    return _run(rule_id, snapshot, params or {}, impl)


def run_all(snapshot: dict, rules: list[dict]) -> list[dict]:
    """运行规则列表（每条含 id + params），返回合并的 Issue 列表。"""
    out = []
    for r in rules:
        if not r.get("enabled", True):
            continue
        out.extend(run_rule(r["id"], snapshot, r.get("params", {})))
    return out
