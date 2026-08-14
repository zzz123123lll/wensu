"""Phase 8 测试：session token 纵深防御 / 诊断包 / 每日备份。"""

import os

from fastapi.testclient import TestClient

from app import main


def _client():
    return TestClient(main.app, base_url="http://127.0.0.1:8766")


def test_session_cookie_issued():
    c = _client()
    r = c.get("/api/session")
    assert r.status_code == 200
    assert "wensu_session" in r.cookies


def test_wrong_token_rejected():
    """带了错误 token 的写请求必须拒绝（纵深防御；HttpOnly 防伪造）。"""
    c = _client()
    r = c.post("/api/projects", json={"name": "x"}, cookies={"wensu_session": "wrong-token"})
    assert r.status_code == 403


def test_correct_token_accepted():
    c = _client()
    tok = main.SESSION_TOKEN
    r = c.post("/api/projects", json={"name": "token-test"}, cookies={"wensu_session": tok})
    assert r.status_code == 200
    # 清理
    conn = main._conn()
    conn.execute("DELETE FROM projects WHERE name = 'token-test'")
    conn.commit()
    conn.close()


def test_wrong_header_token_rejected():
    c = _client()
    r = c.post("/api/projects", json={"name": "x"}, headers={"X-Wensu-Token": "bad"})
    assert r.status_code == 403


def test_no_token_local_request_allowed():
    """P1-6 收紧：无 session 的写请求必须 403（curl/脚本需带 X-Wensu-Token 或先取 cookie）。

    原断言 200 已随守卫强化（缺失 session 一律 403）更新。
    显式传空 token 覆盖 conftest 自动补头。
    """
    c = _client()
    r = c.post("/api/projects", json={"name": "no-token-ok"},
               headers={"X-Wensu-Token": ""})
    assert r.status_code == 403


def test_header_token_allowed_for_scripts():
    """本地脚本/curl：显式带 X-Wensu-Token 可写。"""
    c = _client()
    r = c.post("/api/projects", json={"name": "token-script-ok"},
               headers={"X-Wensu-Token": main.SESSION_TOKEN})
    assert r.status_code == 200
    conn = main._conn()
    conn.execute("DELETE FROM projects WHERE name = 'token-script-ok'")
    conn.commit()
    conn.close()


def test_diagnostics_no_secrets():
    c = _client()
    r = c.get("/api/diagnostics")
    assert r.status_code == 200
    body = r.json()
    assert body["db_ok"] is True
    assert "articles" in body["tables"]
    # 不泄漏 key/正文
    assert "sk-" not in r.text
    assert "prompt" not in r.text.lower()


def test_daily_backup_creates_and_skips_same_day(tmp_path, monkeypatch):
    from app import cli
    db_file = tmp_path / "workbench.db"
    db_file.write_bytes(b"DB-BYTES")
    dest = cli.backup_db(str(db_file))
    assert dest and os.path.exists(dest)
    with open(dest, "rb") as f:
        assert f.read() == b"DB-BYTES"
    # 同日再次启动 → 跳过（不重复备份）
    assert cli.backup_db(str(db_file)) is None
