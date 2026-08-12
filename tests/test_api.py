from fastapi.testclient import TestClient

from app import db, main


def test_full_flow(tmp_path):
    db.DB_PATH = str(tmp_path / "t.db")
    client = TestClient(main.app)
    r = client.post("/api/projects", json={"name": "随笔"})
    assert r.status_code == 200
    pid = r.json()["id"]
    r = client.post(f"/api/projects/{pid}/articles", json={"title": "第一篇"})
    assert r.status_code == 200
    aid = r.json()["id"]
    blocks = [{"id": "b1", "type": "paragraph", "text": "你好", "attrs": {}}]
    assert client.put(f"/api/articles/{aid}", json={"blocks": blocks}).status_code == 200
    got = client.get(f"/api/articles/{aid}").json()
    assert got["blocks"][0]["text"] == "你好"
    assert got["title"] == "第一篇"


def test_list_projects_and_articles(tmp_path):
    db.DB_PATH = str(tmp_path / "t2.db")
    client = TestClient(main.app)
    pid = client.post("/api/projects", json={"name": "p1"}).json()["id"]
    client.post(f"/api/projects/{pid}/articles", json={"title": "a1"})
    assert len(client.get("/api/projects").json()) == 1
    assert len(client.get(f"/api/projects/{pid}/articles").json()) == 1


def test_get_missing_article_404(tmp_path):
    db.DB_PATH = str(tmp_path / "t3.db")
    client = TestClient(main.app)
    assert client.get("/api/articles/999").status_code == 404


def test_update_title(tmp_path):
    db.DB_PATH = str(tmp_path / "t4.db")
    client = TestClient(main.app)
    pid = client.post("/api/projects", json={"name": "p"}).json()["id"]
    aid = client.post(f"/api/projects/{pid}/articles", json={"title": "旧"}).json()["id"]
    assert client.put(f"/api/articles/{aid}", json={"title": "新"}).status_code == 200
    assert client.get(f"/api/articles/{aid}").json()["title"] == "新"


def test_health(tmp_path):
    db.DB_PATH = str(tmp_path / "t5.db")
    client = TestClient(main.app)
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"


def test_health_db_error(tmp_path, monkeypatch):
    db.DB_PATH = str(tmp_path / "t6.db")
    client = TestClient(main.app)

    def boom():
        raise OSError("db 不可用")
    monkeypatch.setattr(db, "connect", boom)
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["db"] == "error"
