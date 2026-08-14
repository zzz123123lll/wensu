"""Phase 7 测试：Ask 历史隔离 / 作者记忆 / 多模型 profile。"""

import pytest

from app import ai_service, db


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.migrate(c)
    return c


# ---------- Ask 历史 ----------

def test_ask_history_isolated_by_article(conn):
    a1 = db.create_article(conn, db.create_project(conn, "p"), "t1")
    a2 = db.create_article(conn, db.create_project(conn, "p2"), "t2")
    db.record_ask(conn, a1, "问题1", "回答1", "m")
    db.record_ask(conn, a2, "问题2", "回答2", "m")
    assert [h["prompt"] for h in db.list_asks(conn, a1)] == ["问题1"]
    assert [h["prompt"] for h in db.list_asks(conn, a2)] == ["问题2"]
    assert "回答1" not in [h["prompt"] for h in db.list_asks(conn, a2)]


def test_ask_history_checkpoint_trims(conn):
    aid = db.create_article(conn, db.create_project(conn, "p"), "t")
    for i in range(60):
        db.record_ask(conn, aid, f"q{i}", f"a{i}", "m")
    asks = db.list_asks(conn, aid, 100)
    assert len(asks) == db.ASK_KEEP  # 裁剪保留最近 30
    assert asks[0]["prompt"] == "q59"  # 最新保留


def test_ask_context_injects_history_and_prefs(tmp_path, monkeypatch):
    """API 层：Ask 注入草稿历史 + 作者偏好（mock 模型，验证 ctx）。"""
    from fastapi.testclient import TestClient
    from app import main
    db.DB_PATH = str(tmp_path / "t.db")
    conn = db.connect()
    db.migrate(conn)
    pid = db.create_project(conn, "p")
    aid = db.create_article(conn, pid, "t")
    db.record_ask(conn, aid, "上一问", "上一答", "m")
    db.add_pref(conn, "风格", "简洁、少用成语")
    conn.close()

    captured = {}
    def fake_ask(conn2, prompt, ctx):
        captured["ctx"] = ctx
        return "回答"
    monkeypatch.setattr(ai_service, "ask", fake_ask)
    monkeypatch.setattr(ai_service, "model_name_for", lambda conn2, task: "m")

    c = TestClient(main.app, base_url="http://127.0.0.1:8766")
    r = c.post("/api/ai/ask", json={"prompt": "新问题", "article_id": aid})
    assert r.status_code == 200
    assert "风格" in captured["ctx"]          # 偏好注入
    assert "简洁" in captured["ctx"]
    assert "上一问" in captured["ctx"]        # 历史注入
    # 历史已记录（新问题入库）
    conn = db.connect()
    db.migrate(conn)
    assert db.list_asks(conn, aid, 10)[0]["prompt"] == "新问题"
    conn.close()


# ---------- 作者记忆（透明可删） ----------

def test_prefs_crud(conn):
    db.add_pref(conn, "k1", "v1")
    db.add_pref(conn, "k1", "v1-更新")  # upsert
    assert len(db.list_prefs(conn)) == 1
    assert db.list_prefs(conn)[0]["content"] == "v1-更新"
    assert db.delete_pref(conn, "k1") is True
    assert db.delete_pref(conn, "k1") is False


def test_prefs_never_auto_persist(conn):
    """反模式守卫：被拒候选/一次性内容不会自动进入记忆（记忆只能手动写入）。"""
    assert db.list_prefs(conn) == []  # 无任何自动沉淀


# ---------- 多模型 ----------

def test_profile_crud_and_binding(conn):
    pid = db.create_profile(conn, "写作模型", "https://api.a.com/v1", "m-a", b"ENC:key")
    assert pid > 0
    db.set_binding(conn, "rewrite", pid)
    assert db.get_bindings(conn) == {"rewrite": pid}
    assert db.list_profiles(conn)[0]["has_key"] is True
    assert db.delete_profile(conn, pid) is True
    assert db.get_bindings(conn) == {}  # 绑定随 profile 删除


def test_require_client_uses_profile_for_task(conn, monkeypatch):
    pid = db.create_profile(conn, "核验模型", "https://api.b.com/v1", "m-b", b"ENC:key2")
    db.set_binding(conn, "check", pid)
    monkeypatch.setattr(db, "get_profile_key", lambda c, p: "sk-b")
    client = ai_service._require_client(conn, task="check")
    assert client.model == "m-b"
    assert client.api_key == "sk-b"  # 各 profile 独立 Key，不共享


def test_require_client_falls_back_to_settings(conn, monkeypatch):
    """无 binding 时沿用全局 settings（兼容旧配置）。"""
    monkeypatch.setattr(ai_service, "get_settings", lambda c: {"configured": True, "base_url": "https://s/v1", "model": "m-s"})
    monkeypatch.setattr(ai_service, "get_api_key", lambda c: "sk-s")
    client = ai_service._require_client(conn, task="ask")
    assert client.model == "m-s"
