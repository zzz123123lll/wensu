"""Blocks data model — structured draft body as single source of truth.

阶段 4A：draft 从 `body`(markdown str) 升级为 `blocks`(`[{id, type, text, attrs}]`)。
blocks 是唯一事实源；`body` 通过 `serialize_blocks` 单向派生，供旧客户端/导出/预览
兼容读取。`blocks_version` + `body_rendered_from_version` + `body_hash` 用于守卫
派生一致性：版本不符或 hash 不符时以 blocks 重建 body。

严格边界：本模块只做数据模型 + 单向派生 + 版本/hash 守卫，不做材料入文/Citation/
AI 对话/工具条/评审联动。
"""
import hashlib
import re
import uuid

# 合法块类型（稳定顺序，供前端/文档使用）
BLOCK_TYPES = (
    "paragraph",
    "heading",
    "blockquote",
    "unordered_list",
    "ordered_list",
    "code",
    "image",
    "divider",
)


def make_block(type, text, attrs=None):
    """构造一个 block dict：`{id, type, text, attrs}`。

    - id: 稳定 UUID（str(uuid.uuid4())），插入/删除/移动不改无关 id。
    - type: 必须属于 BLOCK_TYPES，否则 ValueError。
    - attrs: 拷贝一份，避免外部引用共享。
    """
    if type not in BLOCK_TYPES:
        raise ValueError(f"未知 block 类型: {type!r}，合法类型: {BLOCK_TYPES}")
    return {
        "id": str(uuid.uuid4()),
        "type": type,
        "text": text if isinstance(text, str) else "",
        "attrs": dict(attrs) if attrs else {},
    }


def blocks_equal(a, b):
    """Blocks 列表语义相等：长度 + 顺序 + 每块 (id, type, text, attrs)。

    attrs 逐 key 比较（键集合一致且每个键的值相等）。用于 save_draft 的
    「无改动保存」判定：忽略 body/body_rendered_from_version/body_hash 等派生
    字段，避免 blocks 未变但派生字段漂移（如前端 autosave 每次递增 blocks_version）
    时误 bump draft_version。
    """
    if not isinstance(a, list) or not isinstance(b, list) or len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if not isinstance(x, dict) or not isinstance(y, dict):
            return False
        if (
            x.get("id") != y.get("id")
            or x.get("type") != y.get("type")
            or x.get("text") != y.get("text")
            or not _attrs_equal(x.get("attrs"), y.get("attrs"))
        ):
            return False
    return True


def _attrs_equal(a, b):
    a = a if isinstance(a, dict) else {}
    b = b if isinstance(b, dict) else {}
    return set(a) == set(b) and all(a[k] == b[k] for k in a)


def serialize_blocks(blocks):
    """blocks → markdown body（单向派生）。块间以空行分隔，文本内不转义。

    - paragraph: 原文
    - heading: `#`×level + 空格 + text（level 取 attrs["level"]，默认 1，钳制 1-6）
    - blockquote: 每行 `> ` 前缀
    - unordered_list: 每行 `- ` 前缀
    - ordered_list: 每行 `1. `/`2. ` 递增编号
    - code: ```\n<text>\n``` 围栏
    - image: `![alt](url)`（text=alt，attrs["url"]=url）
    - divider: `---`
    """
    parts = []
    for block in blocks or []:
        btype = block.get("type")
        text = block.get("text") or ""
        attrs = block.get("attrs") or {}
        if btype == "heading":
            try:
                level = int(attrs.get("level", 1))
            except (TypeError, ValueError):
                level = 1
            level = max(1, min(level, 6))
            parts.append("#" * level + " " + text)
        elif btype == "blockquote":
            parts.append("\n".join("> " + line for line in text.split("\n")))
        elif btype == "unordered_list":
            parts.append("\n".join("- " + item for item in text.split("\n")))
        elif btype == "ordered_list":
            items = text.split("\n")
            parts.append("\n".join(f"{i + 1}. {item}" for i, item in enumerate(items)))
        elif btype == "code":
            parts.append("```\n" + text + "\n```")
        elif btype == "image":
            parts.append(f"![{text}]({attrs.get('url', '')})")
        elif btype == "divider":
            parts.append("---")
        else:  # paragraph 及未知类型兜底：原文
            parts.append(text)
    return "\n\n".join(parts)


def body_hash(body):
    """body 的 sha256 hexdigest，用于派生一致性校验。"""
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()


def sync_body(draft):
    """守卫 blocks → body 单向派生。

    当 `body_rendered_from_version != blocks_version` 或 `body_hash` 与
    `serialize_blocks(blocks)` 的哈希不符时，以 blocks 重建 body，并同步
    `body_rendered_from_version` 与 `body_hash`。返回同一个 draft dict（原地更新）。
    4B 前置2 防御：`blocks=[]` 且 body 非空 → 视为未迁移，保留旧 body，不清空。
    """
    rendered = serialize_blocks(draft.get("blocks"))
    expected_hash = body_hash(rendered)
    if draft.get("blocks") == [] and draft.get("body"):
        return draft
    if (
        draft.get("body_rendered_from_version") != draft.get("blocks_version")
        or draft.get("body_hash") != expected_hash
    ):
        draft["body"] = rendered
        draft["body_rendered_from_version"] = draft.get("blocks_version")
        draft["body_hash"] = expected_hash
    return draft


def sync_blocks_from_body(draft):
    """业务写回路径（AI 改写/去 AI 味/报告修复）改 body 后调用。

    若 draft 已有非空 blocks → 以新 body 重建 blocks（新 uuid）+ blocks_version bump +
    sync_body，保证前端从新 blocks 渲染、保存时不再用旧 blocks 派生覆盖 AI 改写结果。
    若 body 未实际变化（blocks 已能渲染出该 body）→ 保持 UUID 稳定，只跑 sync_body 守卫。
    若无 blocks（body-only 稿 / 空 blocks）→ 原样返回，不动 blocks 派生。
    """
    blocks_ = draft.get("blocks")
    if not isinstance(blocks_, list) or not blocks_:
        return draft
    body = draft.get("body")
    if not isinstance(body, str):
        return draft
    if serialize_blocks(blocks_) == body:
        return sync_body(draft)
    draft["blocks"] = migrate_body_to_blocks(body)
    draft["blocks_version"] = int(draft.get("blocks_version") or 0) + 1
    return sync_body(draft)


# ---- 旧 body 懒迁移：markdown 字符串 → blocks（无损/幂等/原子）----
# 只认 canonical 块语法（与 serialize_blocks 输出严格互逆），非规范化行整体降级
# 为 paragraph 保留原文，保证 serialize_blocks(migrate_body_to_blocks(body)) 内容语义一致。
_HEADING_RE = re.compile(r"^(#{1,6}) (.*)$")
_IMAGE_RE = re.compile(r"^!\[(.*)\]\((.*)\)$")
_UNORDERED_RE = re.compile(r"^- (.*)$")
_ORDERED_RE = re.compile(r"^(\d+)\. (.*)$")
_QUOTE_RE = re.compile(r"^> (.*)$")


def _segment_body(body):
    """body 按「逻辑块」切成行组：空行分隔段落；代码围栏(```)保护内部空行。

    - 只有位于段落边界（前无内容）的裸 ``` 才算围栏，避免把正文中的 ``` 误吞；
    - 行尾 \r（CRLF 遗留）统一剥掉，避免破坏语法匹配；
    - 未闭合围栏整体落入普通段落（内容保留）。
    """
    segments = []
    cur = []
    lines = [line.rstrip("\r") for line in (body or "").split("\n")]
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if line == "```" and not cur:
            if cur:
                segments.append(cur)
                cur = []
            code_lines = [line]
            i += 1
            while i < n and lines[i] != "```":
                code_lines.append(lines[i])
                i += 1
            if i < n:  # 闭合围栏
                code_lines.append(lines[i])
                i += 1
            segments.append(code_lines)
        elif line == "":
            if cur:
                segments.append(cur)
                cur = []
            i += 1
        else:
            cur.append(line)
            i += 1
    if cur:
        segments.append(cur)
    return segments


def _all_matches(lines, regex):
    """所有行都匹配则返回 match 列表，否则 None。"""
    ms = [regex.match(line) for line in lines]
    return ms if all(ms) else None


def _classify(lines):
    """一组行 → (type, text, attrs)。无法无损归类时整体降级为 paragraph。"""
    if len(lines) >= 2 and lines[0] == "```" and lines[-1] == "```":
        return "code", "\n".join(lines[1:-1]), {}
    if len(lines) == 1:
        line = lines[0]
        m = _IMAGE_RE.match(line)
        if m:
            return "image", m.group(1), {"url": m.group(2)}
        if line == "---":
            return "divider", "", {}
        m = _HEADING_RE.match(line)
        if m:
            return "heading", m.group(2), {"level": len(m.group(1))}
        m = _ORDERED_RE.match(line)
        if m:
            return "ordered_list", m.group(2), {}
        m = _UNORDERED_RE.match(line)
        if m:
            return "unordered_list", m.group(2), {}
        m = _QUOTE_RE.match(line)
        if m:
            return "blockquote", m.group(1), {}
        return "paragraph", line, {}
    # 多行：全部同构才按对应类型，否则整体 paragraph（内容不丢）
    ms = _all_matches(lines, _UNORDERED_RE)
    if ms is not None:
        return "unordered_list", "\n".join(m.group(1) for m in ms), {}
    ms = _all_matches(lines, _ORDERED_RE)
    if ms is not None:
        return "ordered_list", "\n".join(m.group(2) for m in ms), {}
    ms = _all_matches(lines, _QUOTE_RE)
    if ms is not None:
        return "blockquote", "\n".join(m.group(1) for m in ms), {}
    return "paragraph", "\n".join(lines), {}


def migrate_body_to_blocks(body):
    """旧 markdown body → blocks（懒迁移）。

    按块级语法解析（空行分隔段落；#→heading；- / 1.→list；>→blockquote；
    ```→code；![..](..)→image；---→divider；其余 paragraph）。每个 block 生成 uuid4。
    文本原样保留（含中文/Emoji/旧材料标记 [MAT-XXX]）。非规范化的混排行整体保留为
    段落，保证与 serialize_blocks 无损往返。
    """
    out = []
    for lines in _segment_body(body):
        btype, text, attrs = _classify(lines)
        out.append(make_block(btype, text, attrs))
    return out


def ensure_blocks(draft):
    """保证 draft 有 blocks：有 body 无 blocks → 懒迁移 + sync_body；有 blocks → sync_body。

    - 幂等：已有 blocks 不重生成 id（只跑 sync_body 守卫派生一致性）。
    - 原子：迁移失败抛异常时 draft 不被改动，保留旧 body 供下次重试。
    """
    if draft.get("blocks") is not None:
        return sync_body(draft)
    body = draft.get("body")
    if body is None:
        return draft
    new_blocks = migrate_body_to_blocks(body)  # 失败则抛异常，draft 未动（原子）
    draft["blocks"] = new_blocks
    return sync_body(draft)
