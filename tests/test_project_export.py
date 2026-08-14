"""P2-⑩ 项目级导出测试：ZIP 包含文章/素材/来源/manifest，缺项目 404。"""

import io
import json
import zipfile

import pytest

from app import db
from app.domains import exports


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.migrate(c)
    return c


def _seed(conn):
    pid = db.create_project(conn, "我的项目")
    aid = db.create_article(conn, pid, "文章一")
    db.save_article(conn, aid, blocks=[{"id": "b1", "type": "paragraph", "text": "正文内容", "attrs": {}}], base_version=1)
    sid = db.create_source(conn, pid, "https://example.com/s", title="来源标题", snippet="摘要")
    db.create_citation(conn, aid, "b1", sid, quote="证据")
    db.create_material(conn, pid, "素材标题", "素材内容", sid, tags=["数据"])
    return pid, aid


def test_project_export_zip_contains_all(conn):
    pid, aid = _seed(conn)
    raw = exports.build_project_export(conn, pid)
    zf = zipfile.ZipFile(io.BytesIO(raw))
    names = zf.namelist()
    assert any(n.endswith("manifest.json") for n in names)
    assert any("文章一" in n and n.endswith(".md") for n in names)
    assert any("素材" in n for n in names)
    assert any("来源" in n for n in names)
    # manifest 内容
    manifest = json.loads(zf.read(next(n for n in names if n.endswith("manifest.json"))).decode("utf-8"))
    assert manifest["project"] == "我的项目"
    assert manifest["articles"][0]["title"] == "文章一"
    assert manifest["materials_count"] == 1
    assert manifest["sources_count"] == 1
    # 文章正文
    art_md = zf.read(next(n for n in names if "文章一" in n and n.endswith(".md"))).decode("utf-8")
    assert "正文内容" in art_md
    assert "引用清单" in art_md


def test_project_export_missing_project_raises(conn):
    with pytest.raises(exports.ExportError):
        exports.build_project_export(conn, 999)


def test_api_project_export(tmp_path, monkeypatch):
    from urllib.parse import unquote

    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    c = db.connect()
    db.migrate(c)
    pid, _ = _seed(c)
    c.close()
    client = TestClient(main.app, base_url="http://127.0.0.1:8766")
    r = client.get(f"/api/projects/{pid}/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/zip")
    assert "我的项目" in unquote(r.headers["content-disposition"])
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert zf.namelist()


def test_api_project_export_404(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    client = TestClient(main.app, base_url="http://127.0.0.1:8766")
    r = client.get("/api/projects/999/export")
    assert r.status_code == 404
