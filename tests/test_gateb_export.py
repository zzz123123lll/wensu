"""P0-5 失败测试：统一导出（Markdown/纯文本/Word + 引用清单 + 来源附录）。

临时数据库，不触碰 data/workbench.db。
"""

import io
import urllib.parse
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import db, main

ORIGIN = "http://127.0.0.1:8766"


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "gateb_export.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    c = TestClient(main.app, base_url="http://127.0.0.1:8766")
    conn = db.connect()
    db.migrate(conn)
    conn.close()
    c.get("/api/session")
    return c


def auth(client, method, url, **kw):
    headers = dict(kw.pop("headers", {}))
    headers.setdefault("Origin", ORIGIN)
    return client.request(method, url, headers=headers, **kw)


def _mk_article_with_citations(client, title="测试文章", blocks=None, quotes=None, statuses=None):
    """建项目+草稿+来源+正文+若干 citation（可指定核验/引用状态）。"""
    pid = auth(client, "POST", "/api/projects", json={"name": "p"}).json()["id"]
    aid = auth(client, "POST", f"/api/projects/{pid}/articles", json={"title": title}).json()["id"]
    blocks = blocks or [{"id": "b1", "type": "paragraph", "text": "正文主张一", "attrs": {}},
                        {"id": "b2", "type": "heading2", "text": "小节", "attrs": {}},
                        {"id": "b3", "type": "paragraph", "text": "正文主张二", "attrs": {}}]
    quotes = quotes or ["证据片段A", "证据片段B"]
    statuses = statuses or ["supported", "pending"]
    conn = main._conn()
    db.save_article(conn, aid, blocks=blocks, base_version=1)
    sid1 = db.create_source(conn, pid, "https://example.com/来源1", "来源标题一", "证据原文一", "web")
    sid2 = db.create_source(conn, pid, "https://example.com/来源2", "来源标题二", "证据原文二", "web")
    c1 = db.create_citation(conn, aid, "b1", sid1, quote=quotes[0])
    c2 = db.create_citation(conn, aid, "b3", sid2, quote=quotes[1])
    db.set_citation_verification(conn, c1, statuses[0])
    db.set_citation_verification(conn, c2, statuses[1])
    conn.close()
    return pid, aid


def test_p05_markdown_export_includes_citations(client):
    _, aid = _mk_article_with_citations(client)
    r = auth(client, "GET", f"/api/articles/{aid}/export?format=md")
    assert r.status_code == 200
    assert "text/markdown" in r.headers["content-type"]
    body = r.text
    assert "# 测试文章" in body
    assert "正文主张一" in body
    assert "引用清单" in body
    assert "[1]" in body and "[2]" in body
    assert "来源标题一" in body and "https://example.com/来源1" in body
    assert "证据片段A" in body
    assert "已核验" in body and "待核验" in body  # 核验状态诚实显示


def test_p05_plain_export_includes_citations(client):
    _, aid = _mk_article_with_citations(client)
    r = auth(client, "GET", f"/api/articles/{aid}/export?format=txt")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    body = r.text
    assert "测试文章" in body
    assert "引用清单" in body
    assert "来源标题一" in body
    assert "已核验" in body


def test_p05_docx_export_valid(client):
    _, aid = _mk_article_with_citations(client)
    r = auth(client, "GET", f"/api/articles/{aid}/export?format=docx")
    assert r.status_code == 200
    assert "wordprocessingml" in r.headers["content-type"]
    data = r.content
    # 合法 DOCX = 合法 zip + word/document.xml
    assert data[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert "word/document.xml" in z.namelist()
        xml = z.read("word/document.xml").decode("utf-8")
        assert "测试文章" in xml
        assert "引用清单" in xml
        assert "来源标题一" in xml


def test_p05_citation_numbers_match_appendix(client):
    _, aid = _mk_article_with_citations(client)
    r = auth(client, "GET", f"/api/articles/{aid}/export?format=md")
    body = r.text
    # 正文引用编号与附录顺序一致：b1 → [1]，b3 → [2]
    assert "正文主张一 <sup>[1]</sup>" in body
    assert "正文主张二 <sup>[2]</sup>" in body
    # 清单顺序 [1] 来源标题一 → [2] 来源标题二
    idx1 = body.index("[1] 来源标题一")
    idx2 = body.index("[2] 来源标题二")
    assert idx1 < idx2


def test_p05_export_no_citations(client):
    pid = auth(client, "POST", "/api/projects", json={"name": "p"}).json()["id"]
    aid = auth(client, "POST", f"/api/projects/{pid}/articles", json={"title": "无引用"}).json()["id"]
    conn = main._conn()
    db.save_article(conn, aid, blocks=[{"id": "b1", "type": "paragraph", "text": "正文", "attrs": {}}], base_version=1)
    conn.close()
    r = auth(client, "GET", f"/api/articles/{aid}/export?format=md")
    assert r.status_code == 200
    assert "正文" in r.text
    assert "引用清单" not in r.text


def test_p05_export_orphaned_honest(client):
    """orphaned 引用必须诚实显示，不得伪装为正常。"""
    pid, aid = _mk_article_with_citations(client)
    conn = main._conn()
    conn.execute("UPDATE citations SET status = 'orphaned' WHERE id IN (SELECT id FROM citations LIMIT 1)")
    conn.commit()
    conn.close()
    r = auth(client, "GET", f"/api/articles/{aid}/export?format=md")
    assert r.status_code == 200
    assert "孤立" in r.text


def test_p05_export_source_dead_honest(client):
    pid, aid = _mk_article_with_citations(client, statuses=["source_dead", "pending"])
    r = auth(client, "GET", f"/api/articles/{aid}/export?format=md")
    assert r.status_code == 200
    assert "来源失效" in r.text


def test_p05_export_does_not_modify_db(client):
    pid, aid = _mk_article_with_citations(client)
    conn = main._conn()
    before = dict(db.get_article(conn, aid))
    before_blocks = list(before["blocks"])
    before_version = before["version"]
    conn.close()
    r = auth(client, "GET", f"/api/articles/{aid}/export?format=md")
    assert r.status_code == 200
    r = auth(client, "GET", f"/api/articles/{aid}/export?format=docx")
    assert r.status_code == 200
    conn = main._conn()
    after = db.get_article(conn, aid)
    conn.close()
    assert after["version"] == before_version
    assert after["blocks"] == before_blocks


def test_p05_export_chinese_filename_safe(client):
    pid, aid = _mk_article_with_citations(client, title="中文 长标题：带:冒号/斜杠\\反斜杠？问号")
    r = auth(client, "GET", f"/api/articles/{aid}/export?format=md")
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    fname = urllib.parse.unquote(cd.split("filename*=UTF-8''")[1])
    assert ".." not in fname
    assert "/" not in fname and "\\" not in fname
    assert fname.endswith(".md")
    # 英文非法字符（: ? \ /）被替换为 _；全角字符合法保留
    assert ":" not in fname and "?" not in fname
    assert "中文" in fname


def test_p05_export_unknown_format_400(client):
    pid, aid = _mk_article_with_citations(client)
    r = auth(client, "GET", f"/api/articles/{aid}/export?format=exe")
    assert r.status_code == 400


def test_p05_export_missing_article_404(client):
    r = auth(client, "GET", "/api/articles/99999/export?format=md")
    assert r.status_code == 404
