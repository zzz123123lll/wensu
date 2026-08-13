"""Phase 6 测试：override 覆盖/恢复默认、自定义规则、两阶段导入（恶意包拒绝）。"""

import json

import pytest

from app import db
from app.review import repository


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.migrate(c)
    return c


def _rule(rid="my.rule.no-word", **over):
    d = {
        "id": rid, "name": "禁用词", "description": "d",
        "pack_id": "my-rules", "pack_version": "1.0.0", "category": "language",
        "engine": "deterministic", "scope": "master", "severity": "error",
        "enabled": True, "params": {"words": ["绝对"]},
        "source": {"type": "user", "title": "我的规则"}, "fix_mode": "advisory",
    }
    d.update(over)
    return d


# ---------- override 覆盖与恢复 ----------

def test_override_set_and_delete(conn):
    repository.set_override(conn, "common.heading.order", {"severity": "warning"})
    ov = repository.get_override(conn, "common.heading.order")
    assert ov["severity"] == "warning"
    assert repository.delete_override(conn, "common.heading.order") is True
    assert repository.get_override(conn, "common.heading.order") is None


def test_override_restores_default(conn):
    """删除 override 后 resolver 回到内置默认。"""
    from app.review import service
    repository.set_override(conn, "common.heading.order", {"severity": "suggestion"})
    sel = {"common": ["common-markdown"], "type": [], "channel": [], "personal": []}
    p = service._build_profile(sel, conn)  # 走 service：读取库中 override
    r = next(x for x in p["rules"] if x["id"] == "common.heading.order")
    assert r["severity"] == "suggestion"
    assert r.get("overridden") is True
    repository.delete_override(conn, "common.heading.order")
    p2 = service._build_profile(sel, conn)
    r2 = next(x for x in p2["rules"] if x["id"] == "common.heading.order")
    assert r2["severity"] == "error"  # 恢复内置默认
    assert not r2.get("overridden")


# ---------- 两阶段导入 ----------

def _client(tmp_path):
    from fastapi.testclient import TestClient
    from app import main
    db.DB_PATH = str(tmp_path / "t.db")
    conn = db.connect(); db.migrate(conn); conn.close()
    return TestClient(main.app, base_url="http://127.0.0.1:8766")


def test_import_preview_then_confirm(tmp_path):
    c = _client(tmp_path)
    r = c.post("/api/review/rules/import", json={"content": json.dumps({"rules": [_rule()]}, ensure_ascii=False)})
    assert r.status_code == 200
    body = r.json()
    assert len(body["preview"]["added"]) == 1
    token = body["token"]

    # 未确认前不安装
    conn = db.connect(); db.migrate(conn)
    assert repository.list_custom_rules(conn) == []
    conn.close()

    r2 = c.post("/api/review/rules/import/confirm", json={"confirm_token": token})
    assert r2.status_code == 200
    conn = db.connect(); db.migrate(conn)
    customs = repository.list_custom_rules(conn)
    assert len(customs) == 1 and customs[0]["rule"]["id"] == "my.rule.no-word"
    conn.close()


def test_import_without_confirm_does_not_install(tmp_path):
    c = _client(tmp_path)
    c.post("/api/review/rules/import", json={"content": json.dumps({"rules": [_rule()]})})
    conn = db.connect(); db.migrate(conn)
    assert repository.list_custom_rules(conn) == []
    conn.close()


def test_import_malicious_pack_rejected(tmp_path):
    """E2E 场景 8：危险 URL / 非法 JSON / 超限 → 预览阶段拒绝，不安装。"""
    c = _client(tmp_path)
    # 危险 URL
    bad_url = _rule(rid="my.evil", source={"type": "official", "title": "t", "url": "javascript:alert(1)"})
    r = c.post("/api/review/rules/import", json={"content": json.dumps({"rules": [bad_url]})})
    assert r.status_code == 200
    assert len(r.json()["preview"]["rejected"]) == 1  # 预览拒绝（不是安装）
    # 非法 JSON
    r = c.post("/api/review/rules/import", json={"content": "{{{not json"})
    assert r.status_code == 400
    # 超限（200KB）
    huge = json.dumps({"rules": [_rule() for _ in range(101)]})
    r = c.post("/api/review/rules/import", json={"content": huge})
    assert r.status_code == 400


def test_import_invalid_token_rejected(tmp_path):
    c = _client(tmp_path)
    r = c.post("/api/review/rules/import/confirm", json={"confirm_token": "nope"})
    assert r.status_code == 400


def test_custom_rule_api_validates(tmp_path):
    c = _client(tmp_path)
    r = c.post("/api/review/custom-rules", json=_rule())
    assert r.status_code == 200
    r2 = c.post("/api/review/custom-rules", json={"id": "bad", "name": "x"})  # 缺字段
    assert r2.status_code == 400
