"""P1-⑦ 图片上传测试：类型白名单 / 大小上限 / uuid 文件名 / 静态服务。"""

import os

import pytest

from app import main


def _png() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(main, "UPLOADS_DIR", str(tmp_path / "uploads"))
    os.makedirs(main.UPLOADS_DIR, exist_ok=True)
    from app import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    return TestClient(main.app, base_url="http://127.0.0.1:8766")


def test_upload_png_returns_uuid_url(client, tmp_path):
    r = client.post("/api/uploads/image", files={"file": ("photo.png", _png(), "image/png")})
    assert r.status_code == 200
    url = r.json()["url"]
    assert url.startswith("/uploads/")
    assert url.endswith(".png")
    name = url.rsplit("/", 1)[-1]
    assert len(name) == 32 + 4  # uuid.hex + .png
    assert os.path.exists(os.path.join(tmp_path, "uploads", name))


def test_upload_rejects_non_image(client):
    r = client.post("/api/uploads/image", files={"file": ("evil.txt", b"hello", "text/plain")})
    assert r.status_code == 400


def test_upload_rejects_oversize(client, monkeypatch):
    monkeypatch.setattr(main, "UPLOAD_MAX_BYTES", 10)
    r = client.post("/api/uploads/image", files={"file": ("big.png", b"1" * 11, "image/png")})
    assert r.status_code == 400


def test_upload_ignores_original_filename(client, tmp_path):
    """文件名不来自客户端（uuid 生成），路径穿越无攻击面。"""
    r = client.post("/api/uploads/image", files={"file": ("../../evil.png", _png(), "image/png")})
    assert r.status_code == 200
    name = r.json()["url"].rsplit("/", 1)[-1]
    assert ".." not in name and "evil" not in name


def _mounted_uploads_dir():
    """StaticFiles 挂载捕获的是 import 时的目录（测试 monkeypatch 不影响）。"""
    for route in main.app.routes:
        if getattr(route, "path", None) == "/uploads":
            return route.app.directory
    raise AssertionError("uploads mount not found")


def test_get_uploaded_image(client):
    """静态挂载服务已上传图片（写入挂载目录后清理）。"""
    name = "a" * 32 + ".png"
    path = os.path.join(_mounted_uploads_dir(), name)
    with open(path, "wb") as f:
        f.write(_png())
    try:
        r = client.get(f"/uploads/{name}")
        assert r.status_code == 200
        assert r.content.startswith(b"\x89PNG")
    finally:
        os.remove(path)


def test_get_missing_image_404(client):
    r = client.get("/uploads/does-not-exist.png")
    assert r.status_code == 404
