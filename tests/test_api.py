from fastapi.testclient import TestClient

from app import db, main


def test_full_flow(tmp_path):
    db.DB_PATH = str(tmp_path / "t.db")
    client = TestClient(main.app, base_url="http://127.0.0.1:8766")
    r = client.post("/api/projects", json={"name": "随笔"})
    assert r.status_code == 200
    pid = r.json()["id"]
    r = client.post(f"/api/projects/{pid}/articles", json={"title": "第一篇"})
    assert r.status_code == 200
    aid = r.json()["id"]
    blocks = [{"id": "b1", "type": "paragraph", "text": "你好", "attrs": {}}]
    assert client.put(f"/api/articles/{aid}", json={"blocks": blocks, "base_version": 1}).status_code == 200
    got = client.get(f"/api/articles/{aid}").json()
    assert got["blocks"][0]["text"] == "你好"
    assert got["title"] == "第一篇"
    assert got["version"] == 2  # 初始 1 + 一次保存


def test_list_projects_and_articles(tmp_path):
    db.DB_PATH = str(tmp_path / "t2.db")
    client = TestClient(main.app, base_url="http://127.0.0.1:8766")
    pid = client.post("/api/projects", json={"name": "p1"}).json()["id"]
    client.post(f"/api/projects/{pid}/articles", json={"title": "a1"})
    assert len(client.get("/api/projects").json()) == 1
    assert len(client.get(f"/api/projects/{pid}/articles").json()) == 1


def test_get_missing_article_404(tmp_path):
    db.DB_PATH = str(tmp_path / "t3.db")
    client = TestClient(main.app, base_url="http://127.0.0.1:8766")
    assert client.get("/api/articles/999").status_code == 404


def test_update_title(tmp_path):
    db.DB_PATH = str(tmp_path / "t4.db")
    client = TestClient(main.app, base_url="http://127.0.0.1:8766")
    pid = client.post("/api/projects", json={"name": "p"}).json()["id"]
    aid = client.post(f"/api/projects/{pid}/articles", json={"title": "旧"}).json()["id"]
    assert client.put(f"/api/articles/{aid}", json={"title": "新", "base_version": 1}).status_code == 200
    assert client.get(f"/api/articles/{aid}").json()["title"] == "新"


def test_put_missing_base_version_422(tmp_path):
    db.DB_PATH = str(tmp_path / "t7.db")
    client = TestClient(main.app, base_url="http://127.0.0.1:8766")
    pid = client.post("/api/projects", json={"name": "p"}).json()["id"]
    aid = client.post(f"/api/projects/{pid}/articles", json={"title": "t"}).json()["id"]
    r = client.put(f"/api/articles/{aid}", json={"blocks": []})
    assert r.status_code == 422  # 缺 base_version 不得猜版本


def test_put_version_conflict_409(tmp_path):
    db.DB_PATH = str(tmp_path / "t8.db")
    client = TestClient(main.app, base_url="http://127.0.0.1:8766")
    pid = client.post("/api/projects", json={"name": "p"}).json()["id"]
    aid = client.post(f"/api/projects/{pid}/articles", json={"title": "t"}).json()["id"]
    # 第一次保存 → version 2
    blocks = [{"id": "b1", "type": "paragraph", "text": "v1", "attrs": {}}]
    assert client.put(f"/api/articles/{aid}", json={"blocks": blocks, "base_version": 1}).status_code == 200
    # 用旧 base_version 再存 → 409 + 服务端当前内容
    r = client.put(f"/api/articles/{aid}", json={"blocks": [{"id": "b2", "type": "paragraph", "text": "stale", "attrs": {}}], "base_version": 1})
    assert r.status_code == 409
    body = r.json()["detail"]
    assert body["code"] == "version_conflict"
    assert body["current_version"] == 2
    assert body["blocks"][0]["text"] == "v1"


def test_put_missing_article_404(tmp_path):
    db.DB_PATH = str(tmp_path / "t9.db")
    client = TestClient(main.app, base_url="http://127.0.0.1:8766")
    r = client.put("/api/articles/999", json={"blocks": [], "base_version": 1})
    assert r.status_code == 404


def test_create_article_orphan_rejected(tmp_path):
    db.DB_PATH = str(tmp_path / "t10.db")
    client = TestClient(main.app, base_url="http://127.0.0.1:8766")
    r = client.post("/api/projects/999/articles", json={"title": "孤儿"})
    assert r.status_code == 404


def test_ai_rewrite_creates_revision(tmp_path):
    db.DB_PATH = str(tmp_path / "t11.db")
    client = TestClient(main.app, base_url="http://127.0.0.1:8766")
    pid = client.post("/api/projects", json={"name": "p"}).json()["id"]
    aid = client.post(f"/api/projects/{pid}/articles", json={"title": "t"}).json()["id"]
    blocks = [{"id": "b1", "type": "paragraph", "text": "原文", "attrs": {}}]
    r = client.put(f"/api/articles/{aid}", json={"blocks": blocks, "base_version": 1, "change_reason": "ai_rewrite"})
    assert r.status_code == 200
    conn = db.connect(str(tmp_path / "t11.db"))
    rows = conn.execute("SELECT reason FROM article_revisions").fetchall()
    assert len(rows) == 1
    assert rows[0]["reason"] == "ai_rewrite"
    conn.close()


def test_autosave_does_not_create_revision(tmp_path):
    db.DB_PATH = str(tmp_path / "t12.db")
    client = TestClient(main.app, base_url="http://127.0.0.1:8766")
    pid = client.post("/api/projects", json={"name": "p"}).json()["id"]
    aid = client.post(f"/api/projects/{pid}/articles", json={"title": "t"}).json()["id"]
    r = client.put(f"/api/articles/{aid}", json={"blocks": [], "base_version": 1, "change_reason": "autosave"})
    assert r.status_code == 200
    conn = db.connect(str(tmp_path / "t12.db"))
    n = conn.execute("SELECT COUNT(*) AS n FROM article_revisions").fetchone()["n"]
    assert n == 0
    conn.close()


def test_put_illegal_block_type_422(tmp_path):
    db.DB_PATH = str(tmp_path / "t13.db")
    client = TestClient(main.app, base_url="http://127.0.0.1:8766")
    pid = client.post("/api/projects", json={"name": "p"}).json()["id"]
    aid = client.post(f"/api/projects/{pid}/articles", json={"title": "t"}).json()["id"]
    r = client.put(f"/api/articles/{aid}", json={
        "blocks": [{"id": "b1", "type": "<script>", "text": "x", "attrs": {}}],
        "base_version": 1,
    })
    assert r.status_code == 422


def test_put_illegal_block_id_422(tmp_path):
    db.DB_PATH = str(tmp_path / "t14.db")
    client = TestClient(main.app, base_url="http://127.0.0.1:8766")
    pid = client.post("/api/projects", json={"name": "p"}).json()["id"]
    aid = client.post(f"/api/projects/{pid}/articles", json={"title": "t"}).json()["id"]
    r = client.put(f"/api/articles/{aid}", json={
        "blocks": [{"id": "x y z", "type": "paragraph", "text": "x", "attrs": {}}],
        "base_version": 1,
    })
    assert r.status_code == 422


def test_put_old_style_ids_ok(tmp_path):
    """旧 ID（b1 / b<ts>-<i>）不受破坏。"""
    db.DB_PATH = str(tmp_path / "t15.db")
    client = TestClient(main.app, base_url="http://127.0.0.1:8766")
    pid = client.post("/api/projects", json={"name": "p"}).json()["id"]
    aid = client.post(f"/api/projects/{pid}/articles", json={"title": "t"}).json()["id"]
    blocks = [
        {"id": "b1", "type": "paragraph", "text": "一", "attrs": {}},
        {"id": "b1786453987650-1", "type": "heading", "text": "标题", "attrs": {"level": 2}},
    ]
    r = client.put(f"/api/articles/{aid}", json={"blocks": blocks, "base_version": 1})
    assert r.status_code == 200
    got = client.get(f"/api/articles/{aid}").json()
    assert [b["id"] for b in got["blocks"]] == ["b1", "b1786453987650-1"]


def test_health(tmp_path):
    db.DB_PATH = str(tmp_path / "t5.db")
    client = TestClient(main.app, base_url="http://127.0.0.1:8766")
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"


def test_health_db_error(tmp_path, monkeypatch):
    db.DB_PATH = str(tmp_path / "t6.db")
    client = TestClient(main.app, base_url="http://127.0.0.1:8766")

    def boom():
        raise OSError("db 不可用")
    monkeypatch.setattr(db, "connect", boom)
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["db"] == "error"


# ---------- 安全守卫 ----------

def test_cross_origin_write_rejected(tmp_path):
    db.DB_PATH = str(tmp_path / "t16.db")
    client = TestClient(main.app, base_url="http://127.0.0.1:8766")
    r = client.post("/api/projects", json={"name": "x"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    assert "cross-origin" in r.json()["detail"]


def test_cross_origin_ai_rejected(tmp_path):
    db.DB_PATH = str(tmp_path / "t17.db")
    client = TestClient(main.app, base_url="http://127.0.0.1:8766")
    r = client.post("/api/ai/ask", json={"prompt": "x"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_local_origin_allowed(tmp_path):
    db.DB_PATH = str(tmp_path / "t18.db")
    client = TestClient(main.app, base_url="http://127.0.0.1:8766")
    r = client.post("/api/projects", json={"name": "本地源"}, headers={"origin": "http://127.0.0.1:8766"})
    assert r.status_code == 200


def test_invalid_host_rejected(tmp_path):
    db.DB_PATH = str(tmp_path / "t19.db")
    client = TestClient(main.app, base_url="http://attacker.example")
    r = client.get("/api/health")
    assert r.status_code == 403
    assert r.json()["detail"] == "invalid host"


def test_read_requests_no_origin_ok(tmp_path):
    db.DB_PATH = str(tmp_path / "t20.db")
    client = TestClient(main.app, base_url="http://127.0.0.1:8766")
    assert client.get("/api/projects").status_code == 200  # GET 不受 Origin 限制
