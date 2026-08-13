"""review Session/Issue/API 测试：快照创建、确定性运行、逐项采用（主稿/变体）、复检、状态机。"""

import json

import pytest

from app import db
from app.review import repository, service


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.migrate(c)
    return c


def _profile_sel():
    return {"common": ["common-markdown"], "type": ["opinion-essay"], "channel": [], "personal": []}


def _mk_article(conn, blocks):
    pid = db.create_project(conn, "p")
    aid = db.create_article(conn, pid, "t")
    db.save_article(conn, aid, blocks=blocks, base_version=1)
    return aid


def _art_with_issues(conn):
    blocks = [
        {"id": "b1", "type": "heading", "text": "标题", "attrs": {}},
        {"id": "b2", "type": "heading2", "text": "", "attrs": {}},          # 空标题 warning
        {"id": "b3", "type": "paragraph", "text": "见 [危险](javascript:alert(1))", "attrs": {}},  # 不安全链接 error
    ]
    aid = _mk_article(conn, blocks)
    return aid


# ---------- Session 创建与快照 ----------

def test_create_review_snapshot_and_deterministic(conn):
    aid = _art_with_issues(conn)
    out = service.create_review(conn, aid, _profile_sel())
    assert out["review_id"] > 0
    assert out["issues"], "应有确定性问题"
    s = repository.get_session(conn, out["review_id"])
    assert s["status"] == "completed"
    assert s["article_version"] == 2  # 快照记录创建时版本
    assert s["snapshot_hash"]
    rids = {i["rule_id"] for i in out["issues"]}
    assert "common.markdown.unsafe-url" in rids
    assert "common.heading.empty" in rids


def test_snapshot_isolated_from_later_edits(conn):
    aid = _art_with_issues(conn)
    out = service.create_review(conn, aid, _profile_sel())
    # 之后修改正文，快照不变
    db.save_article(conn, aid, blocks=[{"id": "b1", "type": "paragraph", "text": "新内容", "attrs": {}}], base_version=2)
    s = repository.get_session(conn, out["review_id"])
    assert s["blocks"][0]["text"] == "标题"  # 快照仍是旧内容


def test_identical_snapshot_same_issues(conn):
    aid = _art_with_issues(conn)
    a = service.create_review(conn, aid, _profile_sel())
    b = service.create_review(conn, aid, _profile_sel())
    fa = {(i["rule_id"], i["anchor"].get("block_id")) for i in a["issues"]}
    fb = {(i["rule_id"], i["anchor"].get("block_id")) for i in b["issues"]}
    assert fa == fb


# ---------- 逐项采用：主稿 ----------

def test_accept_master_issue_applies_and_versions(conn):
    aid = _art_with_issues(conn)
    out = service.create_review(conn, aid, _profile_sel())
    issue = next(i for i in out["issues"] if i["rule_id"] == "common.markdown.unsafe-url")
    r = service.accept_issue(conn, out["review_id"], issue["id"])
    assert r["action"] == "master"
    assert r["new_version"] > 2
    art = db.get_article(conn, aid)
    assert "javascript" not in art["blocks"][2]["text"]
    # issue 状态已更新
    assert repository.get_issue(conn, out["review_id"], issue["id"])["state"] == "accepted"


def test_accept_stale_issue_rejected(conn):
    aid = _art_with_issues(conn)
    out = service.create_review(conn, aid, _profile_sel())
    issue = next(i for i in out["issues"] if i["rule_id"] == "common.heading.empty")
    # 主稿被外部修改（版本前进），快照的 base_version 失配 → VersionConflict
    db.save_article(conn, aid, blocks=[{"id": "b1", "type": "heading", "text": "改", "attrs": {}},
                                       {"id": "b2", "type": "heading2", "text": "补上", "attrs": {}},
                                       {"id": "b3", "type": "paragraph", "text": "x", "attrs": {}}], base_version=2)
    with pytest.raises(db.VersionConflict):
        service.accept_issue(conn, out["review_id"], issue["id"])


def test_accept_twice_rejected(conn):
    aid = _art_with_issues(conn)
    out = service.create_review(conn, aid, _profile_sel())
    issue = next(i for i in out["issues"] if i["rule_id"] == "common.heading.empty")
    service.accept_issue(conn, out["review_id"], issue["id"])
    with pytest.raises(ValueError):
        service.accept_issue(conn, out["review_id"], issue["id"])


# ---------- 逐项采用：变体 ----------

def test_accept_variant_creates_patch_not_master(conn):
    # 渠道规则在 Phase 5 内置；这里用自定义渠道规则验证机制
    repository.add_custom_rule(conn, {
        "id": "my.wechat.short", "name": "公众号分段", "description": "d",
        "pack_id": "my-rules", "pack_version": "1.0.0", "category": "channel",
        "engine": "deterministic", "scope": "variant", "severity": "warning",
        "enabled": True, "params": {},
        "source": {"type": "user", "title": "我的渠道规则"}, "fix_mode": "candidate",
    })
    # 手动注入一条渠道 issue（模拟 future engine 产出）
    blocks = [{"id": "b1", "type": "paragraph", "text": "这一句需要渠道化调整", "attrs": {}}]
    aid = _mk_article(conn, blocks)
    out = service.create_review(conn, aid, _profile_sel())
    repository.add_issues(conn, out["review_id"], [{
        "fingerprint": "f1", "rule_id": "my.wechat.short", "severity": "warning",
        "anchor": {"block_id": "b1", "start_utf16": 0, "end_utf16": 6, "original_text": "这一句需要"},
        "suggestion": "渠道版写法", "reason": "渠道规则", "source_type": "user",
    }])
    issue = repository.list_issues(conn, out["review_id"])[-1]
    r = service.accept_issue(conn, out["review_id"], issue["id"])
    assert r["action"] == "variant"
    patches = repository.list_patches(conn, out["review_id"])
    assert len(patches) == 1 and patches[0]["status"] == "proposed"
    # 主稿未变
    art = db.get_article(conn, aid)
    assert art["blocks"][0]["text"] == "这一句需要渠道化调整"
    assert art["version"] == 2  # 无 review_accept 版本


# ---------- 忽略与复检 ----------

def test_ignore_and_recheck(conn):
    aid = _art_with_issues(conn)
    out = service.create_review(conn, aid, _profile_sel())
    issue = out["issues"][0]
    repository.set_issue_state(conn, issue["id"], "ignored")
    assert repository.get_issue(conn, out["review_id"], issue["id"])["state"] == "ignored"
    # 复检：新 session，旧 issue 不干扰
    out2 = service.recheck(conn, out["review_id"])
    assert out2["review_id"] != out["review_id"]
    s2 = repository.get_session(conn, out2["review_id"])
    assert s2["article_id"] == aid


# ---------- API 层 ----------

def test_api_review_flow(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    db.DB_PATH = str(tmp_path / "t.db")
    conn = db.connect(); db.migrate(conn)
    aid = _art_with_issues(conn)
    conn.close()
    c = TestClient(main.app, base_url="http://127.0.0.1:8766")

    r = c.get("/api/review/packs")
    assert r.status_code == 200
    ids = {p["pack_id"] for p in r.json()["packs"]}
    assert "common-markdown" in ids and "opinion-essay" in ids

    r = c.post("/api/reviews", json={"article_id": aid, "profile_selection": _profile_sel()})
    assert r.status_code == 200
    review_id = r.json()["review_id"]
    assert r.json()["issues"]

    r = c.get(f"/api/reviews/{review_id}")
    assert r.status_code == 200
    assert r.json()["review"]["status"] == "completed"

    # stream 回放
    r = c.get(f"/api/reviews/{review_id}/stream")
    lines = r.text.strip().split("\n")
    assert lines[0] == json.dumps({"type": "stage", "stage": "prepare", "status": "done"}, ensure_ascii=False)
    assert lines[-1].startswith('{"type": "done"')

    # ignore 一条
    iid = r.json() if False else json.loads(c.get(f"/api/reviews/{review_id}").text)["issues"][0]["id"]
    r = c.post(f"/api/reviews/{review_id}/issues/{iid}/ignore")
    assert r.status_code == 200

    # accept 主稿修复（不安全链接）
    issues = json.loads(c.get(f"/api/reviews/{review_id}").text)["issues"]
    link = next(i for i in issues if i["rule_id"] == "common.markdown.unsafe-url")
    r = c.post(f"/api/reviews/{review_id}/issues/{link['id']}/accept")
    assert r.status_code == 200
    assert r.json()["action"] == "master"

    # recheck
    r = c.post(f"/api/reviews/{review_id}/recheck")
    assert r.status_code == 200
    assert r.json()["review_id"] != review_id
