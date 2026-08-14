from fastapi.testclient import TestClient

from app import db, main


def _client(tmp_path):
    db.DB_PATH = str(tmp_path / "s.db")
    return TestClient(main.app, base_url="http://127.0.0.1:8766")


def test_settings_put_then_get(tmp_path):
    c = _client(tmp_path)
    r = c.put("/api/settings", json={
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key": "sk-test",
    })
    assert r.status_code == 200
    s = c.get("/api/settings").json()
    assert s["configured"] is True
    assert s["base_url"] == "https://api.deepseek.com/v1"
    assert s["model"] == "deepseek-chat"
    assert s["has_key"] is True


def test_settings_get_never_returns_key(tmp_path):
    c = _client(tmp_path)
    c.put("/api/settings", json={"base_url": "u", "model": "m", "api_key": "sk-secret"})
    body = c.get("/api/settings").json()
    assert "api_key" not in body
    assert "sk-secret" not in str(body)


def test_settings_default_unconfigured(tmp_path):
    c = _client(tmp_path)
    s = c.get("/api/settings").json()
    assert s["configured"] is False


def test_settings_update_without_key_keeps_key(tmp_path):
    c = _client(tmp_path)
    c.put("/api/settings", json={"base_url": "https://api.example.com/v1", "model": "m1", "api_key": "***"})
    c.put("/api/settings", json={"base_url": "https://api.example.com/v2", "model": "m2"})
    s = c.get("/api/settings").json()
    assert s["model"] == "m2"
    assert s["has_key"] is True
