"""公众号兼容 HTML 导出测试（行为契约回收自旧仓"文成" pipeline/wechat_html.py）。"""

import pytest

from app import db
from app.domains import exports, wechat_html


# ---------- markdown → 公众号 HTML 转换 ----------

def test_heading_paragraph_list_quote():
    md = "## 小节标题\n\n正文段落\n\n- 项目一\n- 项目二\n\n1. 第一\n2. 第二\n\n> 引用内容"
    out = wechat_html.markdown_to_wechat_html(md)
    assert "<h2" in out and "小节标题" in out
    assert "<p" in out and "正文段落" in out
    assert "<ul" in out and "<li>项目一</li>" in out
    assert "<ol" in out and "<li>第一</li>" in out
    assert "<blockquote" in out and "引用内容" in out


def test_unsafe_image_url_dropped():
    out = wechat_html.markdown_to_wechat_html("![x](javascript:alert(1))")
    assert "<img" not in out


def test_safe_image_kept_with_alt():
    out = wechat_html.markdown_to_wechat_html("![说明文字](https://example.com/a.png)")
    assert '<img src="https://example.com/a.png" alt="说明文字"' in out


def test_unsafe_link_rendered_as_plain_text():
    out = wechat_html.markdown_to_wechat_html("[点击](javascript:alert(1))")
    assert "<a " not in out
    assert "点击" in out


def test_html_escaped():
    out = wechat_html.markdown_to_wechat_html("<script>alert(1)</script>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_code_block_escaped():
    out = wechat_html.markdown_to_wechat_html("```\n<tag>&\n```")
    assert "<pre" in out
    assert "&lt;tag&gt;&amp;" in out


def test_invalid_theme_falls_back_default():
    assert wechat_html.markdown_to_wechat_html("文本", theme="不存在") == \
        wechat_html.markdown_to_wechat_html("文本", theme="default")


def test_four_themes_available():
    for t in ("default", "elegant", "simple", "tech"):
        assert t in wechat_html.WECHAT_THEMES


# ---------- exports 装配：wechat 格式 ----------

@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.migrate(c)
    return c


def _make_article(conn):
    pid = db.create_project(conn, "p")
    aid = db.create_article(conn, pid, "测试标题")
    blocks = [
        {"id": "b1", "type": "heading2", "text": "小节", "attrs": {}},
        {"id": "b2", "type": "paragraph", "text": "正文有主张", "attrs": {}},
    ]
    db.save_article(conn, aid, blocks=blocks, base_version=1)
    sid = db.create_source(conn, pid, "https://example.com/s", title="来源标题", snippet="摘要")
    db.create_citation(conn, aid, "b2", sid, quote="证据原文")
    return aid


def test_render_wechat_contains_body_and_citation_markers(conn):
    aid = _make_article(conn)
    data = exports.build_export_data(conn, aid)
    out = exports.render_wechat(data)
    assert "<h2" in out and "小节" in out
    assert "正文有主张 [1]" in out  # 纯文本引用标记（微信编辑器对 sup 支持差）
    assert "引用清单" in out
    assert 'href="https://example.com/s"' in out


def test_render_wechat_no_sup_tag(conn):
    aid = _make_article(conn)
    data = exports.build_export_data(conn, aid)
    assert "<sup" not in exports.render_wechat(data)


def test_render_wechat_without_appendix(conn):
    aid = _make_article(conn)
    data = exports.build_export_data(conn, aid)
    out = exports.render_wechat(data, include_appendix=False)
    assert "引用清单" in out
    assert "来源附录" not in out


def test_render_dispatch_wechat(conn):
    aid = _make_article(conn)
    data = exports.build_export_data(conn, aid)
    raw = exports.render(data, "wechat")
    assert raw.startswith(b"<")
    assert "正文有主张".encode("utf-8") in raw


def test_safe_filename_wechat_ext():
    name = exports.safe_filename("标题", "wechat")
    assert name.endswith(".html")


def test_api_export_wechat(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    c = db.connect()
    db.migrate(c)
    aid = _make_article(c)
    c.close()
    client = TestClient(main.app, base_url="http://127.0.0.1:8766")
    r = client.get(f"/api/articles/{aid}/export?format=wechat&theme=elegant")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert ".html" in r.headers["content-disposition"]
    assert "正文有主张".encode("utf-8") in r.content
