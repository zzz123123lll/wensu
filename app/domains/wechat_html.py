"""Markdown → 公众号编辑器兼容的行内样式 HTML（行为契约回收自旧仓"文成" pipeline/wechat_html.py）。

- 输出为可直接粘贴进微信公众号编辑器的 HTML 片段（行内样式，无外部依赖）
- 图片/链接 URL 必须 http/https 且有 host（安全白名单），非法者降级（图片丢弃、链接退化为纯文本）
- 代码块转义；mermaid/数学块输出占位 div（公众号编辑器内不渲染，保留原文）
- 4 套排版主题：default / elegant / simple / tech
"""

import html
import re
from urllib.parse import urlparse

_LIST_RE = re.compile(r"^([-*+]|\d+[.、)])\s+(.*)$")
_H2_RE = re.compile(r"^##\s+(.*)$")
_H3_RE = re.compile(r"^###\s+(.*)$")
_QUOTE_RE = re.compile(r"^>\s?(.*)$")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")

_ALLOWED_URL_SCHEMES = ("http", "https")

WECHAT_THEMES = {
    "default": {
        "body": (
            "margin:0;padding:16px;font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;"
            "color:#24292f;line-height:1.7;font-size:15px;background:#ffffff"
        ),
        "h2": "margin:0.6em 0;font-size:18px;font-weight:700;color:#1f2328",
        "h3": "margin:0.6em 0;font-size:16px;font-weight:700;color:#24292f",
        "p": "margin:0 0 1em;line-height:1.7;font-size:15px",
        "list": "margin:0 0 1em;padding-left:1.4em;line-height:1.7",
        "quote": "margin:0 0 1em;padding:0.5em 1em;border-left:3px solid #d0d7de;color:#59636e;line-height:1.7",
        "pre": "font-family:Consolas,Menlo,monospace;background:#f6f8fa;padding:0.5em;overflow-x:auto;line-height:1.5",
        "img": "max-width:100%;display:block;margin:1em auto",
    },
    "elegant": {
        "body": (
            "margin:0;padding:18px;font-family:'Songti SC','SimSun',Georgia,serif;"
            "color:#3f3124;line-height:1.85;font-size:16px;background:#fffdf8"
        ),
        "h2": "margin:0.8em 0;font-size:20px;font-weight:700;color:#8b4513",
        "h3": "margin:0.7em 0;font-size:17px;font-weight:700;color:#6b4423",
        "p": "margin:0 0 1em;line-height:1.85;font-size:16px",
        "list": "margin:0 0 1em;padding-left:1.4em;line-height:1.85",
        "quote": "margin:0 0 1em;padding:0.6em 1em;border-left:3px solid #c9a87c;color:#7a5c3a;line-height:1.85",
        "pre": "font-family:Consolas,Menlo,monospace;background:#f5efe4;padding:0.5em;overflow-x:auto;line-height:1.6",
        "img": "max-width:100%;display:block;margin:1em auto;border-radius:4px",
    },
    "simple": {
        "body": (
            "margin:0;padding:14px;font-family:'PingFang SC','Microsoft YaHei',sans-serif;"
            "color:#18181b;line-height:1.7;font-size:15px;background:#ffffff"
        ),
        "h2": "margin:0.6em 0;font-size:18px;font-weight:700;color:#09090b",
        "h3": "margin:0.6em 0;font-size:16px;font-weight:600;color:#18181b",
        "p": "margin:0 0 1em;line-height:1.7;font-size:15px",
        "list": "margin:0 0 1em;padding-left:1.2em;line-height:1.7",
        "quote": "margin:0 0 1em;padding:0.5em 1em;border-left:3px solid #e4e4e7;color:#52525b;line-height:1.7",
        "pre": "font-family:Consolas,Menlo,monospace;background:#f4f4f5;padding:0.5em;overflow-x:auto;line-height:1.5",
        "img": "max-width:100%;display:block;margin:1em auto",
    },
    "tech": {
        "body": (
            "margin:0;padding:16px;font-family:'DIN Alternate','Arial','Microsoft YaHei',sans-serif;"
            "color:#0b2545;line-height:1.7;font-size:15px;background:#f7fbff"
        ),
        "h2": "margin:0.6em 0;font-size:18px;font-weight:700;color:#0b5394",
        "h3": "margin:0.6em 0;font-size:16px;font-weight:700;color:#155eaa",
        "p": "margin:0 0 1em;line-height:1.7;font-size:15px",
        "list": "margin:0 0 1em;padding-left:1.4em;line-height:1.7",
        "quote": "margin:0 0 1em;padding:0.5em 1em;border-left:3px solid #6db3f2;color:#1c4e79;line-height:1.7",
        "pre": "font-family:Consolas,Menlo,monospace;background:#e8f1fb;padding:0.5em;overflow-x:auto;line-height:1.5",
        "img": "max-width:100%;display:block;margin:1em auto",
    },
}


def _theme(theme):
    return WECHAT_THEMES.get(theme) or WECHAT_THEMES["default"]


def _safe_url(value):
    parsed = urlparse(value)
    if parsed.scheme.lower() not in _ALLOWED_URL_SCHEMES:
        return ""
    if not parsed.netloc:
        return ""
    return value


def _render_inline(text, theme):
    """渲染行内图片/链接，其余文本一律转义。"""
    escaped = html.escape(text, quote=True)
    image_style = _theme(theme)["img"]

    def image_replacer(match):
        alt = html.escape(match.group(1), quote=True)
        url = _safe_url(match.group(2))
        if not url:
            return ""
        return f'<img src="{url}" alt="{alt}" style="{image_style}">'

    escaped = _IMAGE_RE.sub(image_replacer, escaped)

    def link_replacer(match):
        label = html.escape(match.group(1), quote=True)
        url = _safe_url(match.group(2))
        if not url:
            return label
        return f'<a href="{url}" target="_blank" rel="noopener">{label}</a>'

    return _LINK_RE.sub(link_replacer, escaped)


def _render_list(items, theme):
    ordered = bool(re.match(r"^\d+[.、)]", items[0]))
    tag = "ol" if ordered else "ul"
    list_style = _theme(theme)["list"]
    body = "".join(
        f"<li>{_render_inline(re.sub(r'^[-*+]\s+', '', re.sub(r'^\d+[.、)]\s+', '', line)), theme)}</li>"
        for line in items
    )
    return f'<{tag} style="{list_style}">{body}</{tag}>'


def _render_quote(lines, theme):
    inner = " ".join(_render_inline(re.sub(r"^>\s?", "", line), theme) for line in lines)
    quote_style = _theme(theme)["quote"]
    return f'<blockquote style="{quote_style}">{inner}</blockquote>'


def _render_paragraph(lines, theme):
    content = "<br>".join(_render_inline(line, theme) for line in lines)
    paragraph_style = _theme(theme)["p"]
    return f'<p style="{paragraph_style}">{content}</p>'


def _render_code(lines, theme, language=""):
    code = "\n".join(lines)
    content = html.escape(code, quote=True)
    if language == "mermaid":
        return f'<div class="mermaid">{content}</div>'
    if language in {"math", "latex", "tex"} or code.strip().startswith("$$"):
        return f'<div class="katex-block">{content}</div>'
    if language:
        try:
            from pygments import highlight
            from pygments.formatters import HtmlFormatter
            from pygments.lexers import get_lexer_by_name

            lexer = get_lexer_by_name(language)
            formatter = HtmlFormatter(nowrap=True, noclasses=True)
            highlighted = highlight(code, lexer, formatter)
            return f'<pre style="{_theme(theme)["pre"]}">{highlighted}</pre>'
        except Exception:
            pass
    pre_style = _theme(theme)["pre"]
    return f'<pre style="{pre_style}">{content}</pre>'


def _render_block(block, theme):
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    if not lines:
        return ""
    if lines[0].startswith("```"):
        language = lines[0][3:].strip()
        return _render_code(
            lines[1:-1] if lines[-1].startswith("```") else lines[1:],
            theme,
            language=language,
        )

    parts = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if _LIST_RE.match(line):
            ordered = bool(re.match(r"^\d+[.、)]", line))
            list_items = []
            while index < len(lines) and _LIST_RE.match(lines[index]):
                current = lines[index]
                current_ordered = bool(re.match(r"^\d+[.、)]", current))
                if current_ordered != ordered:
                    break
                list_items.append(current)
                index += 1
            parts.append(_render_list(list_items, theme))
            continue

        h3 = _H3_RE.match(line)
        h2 = _H2_RE.match(line)
        if h3:
            parts.append(f'<h3 style="{_theme(theme)["h3"]}">{_render_inline(h3.group(1), theme)}</h3>')
            index += 1
            continue
        if h2:
            parts.append(f'<h2 style="{_theme(theme)["h2"]}">{_render_inline(h2.group(1), theme)}</h2>')
            index += 1
            continue

        if _QUOTE_RE.match(line):
            quote_lines = []
            while index < len(lines) and _QUOTE_RE.match(lines[index]):
                quote_lines.append(lines[index])
                index += 1
            parts.append(_render_quote(quote_lines, theme))
            continue

        paragraph_lines = []
        while index < len(lines) and not (
            _LIST_RE.match(lines[index])
            or _H2_RE.match(lines[index])
            or _H3_RE.match(lines[index])
            or _QUOTE_RE.match(lines[index])
        ):
            paragraph_lines.append(lines[index])
            index += 1
        parts.append(_render_paragraph(paragraph_lines, theme))

    return "\n".join(part for part in parts if part)


def markdown_to_wechat_html(markdown: str, theme: str = "default") -> str:
    """把 Markdown 正文转换为公众号编辑器兼容的行内样式 HTML 片段。"""
    normalized = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n[ \t]*\n", normalized)
    return "\n".join(part for part in (_render_block(block, theme) for block in blocks) if part)
