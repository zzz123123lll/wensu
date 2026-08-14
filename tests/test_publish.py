"""发布三件套测试：目标 CRUD（凭据加密落盘）、webhook/本地发布、历史日志、API。"""

import json

import pytest

from app import db, main, publish


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.migrate(c)
    return c


def _mk_article(conn):
    pid = db.create_project(conn, "p")
    aid = db.create_article(conn, pid, "发布测试")
    db.save_article(conn, aid, blocks=[{"id": "b1", "type": "paragraph", "text": "正文内容", "attrs": {}}], base_version=1)
    return pid, aid


# ---------- 目标校验 ----------

def test_validate_webhook_ok():
    cfg = publish.validate_target("webhook", {"url": "https://example.com/hook", "headers": {"X-Token": "abc"}})
    assert cfg["url"] == "https://example.com/hook"
    assert cfg["headers"] == {"X-Token": "abc"}


def test_validate_webhook_rejects_bad_url():
    # 注：私有/本机地址是合法 webhook 目标（用户主动配置的接收端，非抓取场景），不拦
    for bad in ("javascript:alert(1)", "ftp://x", "not-a-url"):
        with pytest.raises(ValueError):
            publish.validate_target("webhook", {"url": bad})


def test_validate_local_rejects_empty_dir():
    with pytest.raises(ValueError):
        publish.validate_target("local", {"dir": ""})


def test_validate_unknown_kind_rejected():
    with pytest.raises(ValueError):
        publish.validate_target("wechat", {"appid": "x"})


# ---------- 目标 CRUD 与凭据加密 ----------

def test_create_target_config_encrypted_at_rest(conn):
    tid = db.create_publish_target(conn, "我的钩子", "webhook", publish._encrypt_config(
        {"url": "https://example.com/hook?token=SECRET123", "headers": {}}))
    row = conn.execute("SELECT config_enc FROM publish_targets WHERE id = ?", (tid,)).fetchone()
    raw = row["config_enc"]
    assert isinstance(raw, bytes)
    assert b"SECRET123" not in raw  # 明文不在库里


def test_list_targets_masks_secret(conn):
    db.create_publish_target(conn, "钩子", "webhook", publish._encrypt_config(
        {"url": "https://example.com/hook?token=SECRET123", "headers": {"X-K": "v"}}))
    out = publish.list_targets_public(conn)
    assert out[0]["kind"] == "webhook"
    assert "SECRET123" not in json.dumps(out, ensure_ascii=False)
    assert "example.com" in out[0]["summary"]


def test_duplicate_name_rejected(conn):
    db.create_publish_target(conn, "a", "local", publish._encrypt_config({"dir": "C:/x"}))
    with pytest.raises(ValueError):
        db.create_publish_target(conn, "a", "local", publish._encrypt_config({"dir": "C:/y"}))


def test_delete_target(conn):
    tid = db.create_publish_target(conn, "a", "local", publish._encrypt_config({"dir": "C:/x"}))
    db.delete_publish_target(conn, tid)
    assert db.get_publish_target(conn, tid) is None


# ---------- 发布执行 ----------

def test_publish_webhook_success_and_log(conn, monkeypatch):
    pid, aid = _mk_article(conn)
    tid = db.create_publish_target(conn, "钩子", "webhook", publish._encrypt_config(
        {"url": "https://example.com/hook", "headers": {}}))
    sent = {}

    def fake_post(url, headers, json_payload, timeout=10.0):
        sent.update({"url": url, "headers": headers, "payload": json_payload})
        return True, "ok"

    monkeypatch.setattr(publish, "_post_webhook", fake_post)
    out = publish.publish_article(conn, aid, tid, "markdown")
    assert out["status"] == "ok"
    assert sent["url"] == "https://example.com/hook"
    assert sent["payload"]["title"] == "发布测试"
    assert "正文内容" in sent["payload"]["content"]
    logs = db.list_publish_logs(conn, 5)
    assert len(logs) == 1 and logs[0]["status"] == "ok"


def test_publish_webhook_failure_honest(conn, monkeypatch):
    pid, aid = _mk_article(conn)
    tid = db.create_publish_target(conn, "钩子", "webhook", publish._encrypt_config(
        {"url": "https://example.com/hook", "headers": {}}))
    monkeypatch.setattr(publish, "_post_webhook", lambda url, headers, json_payload, timeout=10.0: (False, "HTTP 500"))
    out = publish.publish_article(conn, aid, tid, "markdown")
    assert out["status"] == "failed"
    assert "HTTP 500" in out["message"]
    assert db.list_publish_logs(conn, 5)[0]["status"] == "failed"


def test_publish_local_writes_file(conn, tmp_path):
    pid, aid = _mk_article(conn)
    tid = db.create_publish_target(conn, "本地", "local", publish._encrypt_config({"dir": str(tmp_path)}))
    out = publish.publish_article(conn, aid, tid, "markdown")
    assert out["status"] == "ok"
    files = list(tmp_path.glob("*.md"))
    assert len(files) == 1
    assert "正文内容" in files[0].read_text(encoding="utf-8")


def test_publish_unknown_target(conn):
    pid, aid = _mk_article(conn)
    with pytest.raises(publish.PublishError):
        publish.publish_article(conn, aid, 999, "markdown")


# ---------- API ----------

@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    conn = db.connect()
    db.migrate(conn)
    pid, aid = _mk_article(conn)
    conn.close()
    return TestClient(main.app, base_url="http://127.0.0.1:8766"), aid


def test_api_target_crud_and_masking(client):
    c, aid = client
    r = c.post("/api/publish-targets", json={"name": "钩子", "kind": "webhook",
                                             "config": {"url": "https://example.com/h?token=SECRET"}})
    assert r.status_code == 200
    tid = r.json()["id"]
    r = c.get("/api/publish-targets")
    assert r.status_code == 200
    assert "SECRET" not in r.text
    r = c.delete(f"/api/publish-targets/{tid}")
    assert r.status_code == 200


def test_api_publish_and_logs(client, monkeypatch):
    c, aid = client
    r = c.post("/api/publish-targets", json={"name": "钩子", "kind": "webhook",
                                             "config": {"url": "https://example.com/h"}})
    tid = r.json()["id"]
    monkeypatch.setattr(publish, "_post_webhook", lambda url, headers, json_payload, timeout=10.0: (True, "ok"))
    r = c.post(f"/api/articles/{aid}/publish", json={"target_id": tid, "fmt": "markdown"})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    r = c.get("/api/publish-logs")
    assert r.status_code == 200
    assert len(r.json()["logs"]) == 1


def test_api_publish_bad_fmt_400(client, monkeypatch):
    c, aid = client
    r = c.post("/api/publish-targets", json={"name": "钩子", "kind": "webhook",
                                             "config": {"url": "https://example.com/h"}})
    tid = r.json()["id"]
    r = c.post(f"/api/articles/{aid}/publish", json={"target_id": tid, "fmt": "docx"})
    assert r.status_code == 400
