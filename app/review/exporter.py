"""review exporter：主稿 + 渠道补丁 → 通用版/渠道版 Markdown + 摘要 manifest。

原则：
- 通用版：主稿快照 + 引用真相源，绝不应用任何 target=channel 补丁
- 渠道版：同一主稿快照，按 block 分组、selection 逆序应用 active 渠道补丁
  （防止偏移连锁）；每个补丁先核验 original_hash 与唯一精确原文匹配，
  任何 stale 补丁 → 导出需确认（不静默跳过、不模糊替换）
- 引用编号由 citations 真相源渲染，不写入 block 文本
- 文件名安全：拒绝路径穿越，冲突追加时间戳
"""

import hashlib
import os
import re
from datetime import datetime, timezone

from app import blocks as blocks_lib


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _block_text(b) -> str:
    return b.get("text", "")


# ---------- 引用渲染（citations 真相源 → [N] 上标 + 来源清单） ----------

def _citation_map(citations) -> dict[str, list[tuple[int, dict]]]:
    """{block_id: [(编号, citation)]}，编号按 citations 列表顺序（1 起）。"""
    out = {}
    for i, c in enumerate(citations, start=1):
        bid = c.get("block_id")
        if bid:
            out.setdefault(bid, []).append((i, c))
    return out


def _render_block_markdown(b) -> str:
    """复用 blocks.py 序列化（同一转换器，不引入第二套）。"""
    try:
        return blocks_lib.serialize_blocks([b]).rstrip("\n")
    except Exception:
        return _block_text(b)


def _source_list(citations) -> str:
    lines = []
    for i, c in enumerate(citations, start=1):
        title = c.get("source_title") or c.get("source_url") or c.get("quote", "")[:30]
        url = c.get("source_url") or ""
        if url:
            lines.append(f"[{i}] {title} · {url}")
        else:
            lines.append(f"[{i}] {title}")
    if not lines:
        return ""
    return "\n\n---\n\n**来源**\n\n" + "\n".join(lines)


def render_markdown(blocks: list, citations: list | None = None) -> str:
    """主稿快照 → Markdown（引用编号 + 文末来源清单）。"""
    citations = citations or []
    cmap = _citation_map(citations)
    parts = []
    for b in blocks:
        md = _render_block_markdown(b)
        nums = cmap.get(b.get("id"), [])
        if nums:
            md += " <sup>" + "".join(f"[{n}]" for n, _ in nums) + "</sup>"
        parts.append(md)
    body = "\n\n".join(p for p in parts if p)
    src = _source_list(citations)
    return body + ("\n" + src if src else "")


# ---------- 渠道补丁应用（stale gate） ----------

def _patch_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def apply_patches(blocks: list, patches: list) -> tuple[list, list]:
    """应用 active 渠道补丁；返回 (应用后的 blocks, stale 补丁列表)。

    按 block 分组，组内 selection 逆序（end 降序）应用，防止偏移连锁。
    每个补丁：original_hash 必须匹配 + 在该 block 文本中唯一精确匹配。
    """
    stale = []
    active = [p for p in patches if p.get("status") == "active"]
    if not active:
        return blocks, stale

    by_block: dict[str, list] = {}
    for p in active:
        by_block.setdefault(p.get("block_id", ""), []).append(p)

    out = [dict(b) for b in blocks]
    for b in out:
        ps = by_block.get(b.get("id"), [])
        if not ps:
            continue
        text = b.get("text", "")
        for p in sorted(ps, key=lambda x: x.get("selection", {}).get("end_utf16", 0), reverse=True):
            sel = p.get("selection", {})
            original = sel.get("original_text", "")
            start = sel.get("start_utf16", 0)
            end = sel.get("end_utf16", len(original))
            # stale gate：hash 校验 + 唯一精确匹配
            if _patch_hash(original) != p.get("original_hash", ""):
                stale.append({**p, "stale_reason": "原文 hash 不匹配"})
                continue
            if text[start:end] != original:
                stale.append({**p, "stale_reason": "原文精确匹配失败"})
                continue
            text = text[:start] + p.get("replacement", "") + text[end:]
        b["text"] = text
    return out, stale


# ---------- 文件名安全 ----------

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(title: str, suffix: str, existing: list[str] | None = None) -> str:
    """安全文件名：清理非法字符、拒绝路径穿越、冲突追加时间戳。"""
    cleaned = _UNSAFE.sub("_", (title or "文章").strip()) or "文章"
    cleaned = cleaned[:40]
    name = f"{cleaned}-{suffix}.md"
    if existing and name in existing:
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        name = f"{cleaned}-{suffix}-{ts}.md"
    if ".." in name or name.startswith("/") or name.startswith("\\"):
        raise ValueError("非法文件名")
    return name


# ---------- 摘要 manifest ----------

def build_manifest(review: dict, profile: dict, issues: list, patches: list,
                   stale: list, general_md: str, channel_md: str | None,
                   general_file: str, channel_file: str | None) -> dict:
    states = {"accepted": 0, "ignored": 0, "open": 0}
    for i in issues:
        st = i.get("state", "open")
        states[st if st in states else "open"] += 1
    pack_versions = {}
    for r in profile.get("rules", []):
        pack_versions.setdefault(r.get("pack_id", ""), r.get("pack_version", ""))
    return {
        "article_id": review.get("article_id"),
        "article_version": review.get("article_version"),
        "snapshot_hash": review.get("snapshot_hash"),
        "profile": {"packs": pack_versions},
        "issues": states,
        "patches": {
            "active": sum(1 for p in patches if p.get("status") == "active"),
            "stale": len(stale),
            "proposed": sum(1 for p in patches if p.get("status") == "proposed"),
        },
        "files": {
            "general": {"name": general_file, "sha1": hashlib.sha1(general_md.encode("utf-8")).hexdigest()[:16]},
            "channel": ({"name": channel_file, "sha1": hashlib.sha1(channel_md.encode("utf-8")).hexdigest()[:16]}
                        if channel_md is not None and channel_file else None),
        },
        "generated_at": _now(),
    }
