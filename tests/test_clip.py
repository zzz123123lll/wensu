"""P2-⑧ 剪藏测试：抓取 → Source + Material 落库；失败路径诚实报错。"""

import pytest

from app import db, main, safe_fetch


SNAP = {
    "requested_url": "https://example.com/a",
    "final_url": "https://example.com/a",
    "mime": "text/html",
    "content_hash": "ab" * 16,
    "excerpt": "这是一篇被剪藏的网页正文，内容足够长。",
    "fetched_at": "2026-08-14T00:00:00+00:00",
    "fetch_status": "ok",
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    conn = db.connect()
    db.migrate(conn)
    pid = db.create_project(conn, "p")
    conn.close()
    return TestClient(main.app, base_url="http://127.0.0.1:8766"), pid


def test_clip_creates_material_and_source(client, monkeypatch, tmp_path):
    c, pid = client
    monkeypatch.setattr(safe_fetch, "fetch_url", lambda url: dict(SNAP))
    r = c.post(f"/api/projects/{pid}/clip", json={"url": "https://example.com/a"})
    assert r.status_code == 200
    body = r.json()
    assert body["material_id"] > 0 and body["source_id"] > 0
    # 落库校验：素材带剪藏标签，来源可溯源
    conn = db.connect()
    db.migrate(conn)
    mats = db.list_materials(conn, project_id=pid)
    assert len(mats) == 1
    assert mats[0]["tags"] == ["剪藏"]
    assert "被剪藏的网页正文" in mats[0]["content"]
    assert mats[0]["source_id"] == body["source_id"]
    conn.close()


def test_clip_fetch_failure_400(client, monkeypatch):
    c, pid = client

    def boom(url):
        raise safe_fetch.SafeFetchError("目标解析到内网/保留地址，已拦截")

    monkeypatch.setattr(safe_fetch, "fetch_url", boom)
    r = c.post(f"/api/projects/{pid}/clip", json={"url": "http://169.254.1.1/x"})
    assert r.status_code == 400
    assert "抓取失败" in r.json()["detail"]


def test_clip_empty_excerpt_400(client, monkeypatch):
    c, pid = client
    monkeypatch.setattr(safe_fetch, "fetch_url", lambda url: {**SNAP, "excerpt": ""})
    r = c.post(f"/api/projects/{pid}/clip", json={"url": "https://example.com/bin"})
    assert r.status_code == 400


def test_clip_missing_project_404(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(safe_fetch, "fetch_url", lambda url: dict(SNAP))
    r = c.post("/api/projects/999/clip", json={"url": "https://example.com/a"})
    assert r.status_code == 404


def test_clip_empty_url_400(client):
    c, pid = client
    r = c.post(f"/api/projects/{pid}/clip", json={"url": "  "})
    assert r.status_code == 400
