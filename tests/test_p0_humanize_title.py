"""P0-②b/②c 测试：去 AI 味（本地痕迹估计 + 确定性规则 + 改写 flavor）与标题评分。"""

import json

import pytest

from app import ai_service, ai_trace
from app.review import deterministic


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, json_mode=False, **kwargs):
        self.calls.append((messages, json_mode))
        if isinstance(self.replies[0], Exception):
            raise self.replies.pop(0)
        return self.replies.pop(0)


# ---------- AI 痕迹本地估计（纯函数） ----------

def test_estimate_ai_trace_counts_phrases():
    out = ai_trace.estimate_ai_trace("总而言之，这个方案赋能了团队。综上所述，值得注意。")
    assert out["ai_phrase_hits"] >= 3
    assert out["score"] > 0


def test_estimate_ai_trace_empty_text_zero():
    out = ai_trace.estimate_ai_trace("")
    assert out["score"] == 0 and out["ai_phrase_hits"] == 0


def test_estimate_ai_trace_score_capped_at_100():
    text = "。".join(["综上所述赋能抓手闭环底层逻辑颗粒度"] * 20)
    assert ai_trace.estimate_ai_trace(text)["score"] <= 100


def test_find_ai_phrases_returns_matches():
    hits = ai_trace.find_ai_phrases("综上所述，赋能团队。")
    assert "综上所述" in hits and "赋能" in hits


def test_find_ai_phrases_clean_text_empty():
    assert ai_trace.find_ai_phrases("今天天气不错，我们去公园散步。") == []


# ---------- 确定性规则：common.language.ai-trace ----------

def _snap(text):
    return {"blocks": [{"id": "b1", "type": "paragraph", "text": text, "attrs": {}}], "citations": []}


def test_ai_trace_rule_flags_phrases():
    issues = deterministic.run_rule("common.language.ai-trace", _snap("综上所述，这个方案赋能了团队"))
    assert len(issues) == 1
    i = issues[0]
    assert i["severity"] == "suggestion"
    assert "综上所述" in i["reason"]


def test_ai_trace_rule_clean_text_no_issue():
    assert deterministic.run_rule("common.language.ai-trace", _snap("今天天气不错，我们去公园散步。")) == []


# ---------- 改写 flavor ----------

def test_rewrite_de_ai_flavor_prompt(monkeypatch):
    fake = FakeClient(['{"candidates": [{"label": "方案一", "text": "改后"}]}'])
    monkeypatch.setattr(ai_service, "_require_client", lambda conn, task="rewrite": fake)
    out = ai_service.rewrite(None, "综上所述，这个方案赋能了团队", flavor="de-ai")
    assert out[0]["text"] == "改后"
    system = fake.calls[0][0][0]["content"]
    assert "AI 痕迹" in system


def test_rewrite_default_flavor_unchanged(monkeypatch):
    fake = FakeClient(['{"candidates": [{"label": "方案一", "text": "改后"}]}'])
    monkeypatch.setattr(ai_service, "_require_client", lambda conn, task="rewrite": fake)
    ai_service.rewrite(None, "一段文字", flavor="default")
    system = fake.calls[0][0][0]["content"]
    assert "AI 痕迹" not in system


# ---------- 标题评分 ----------

TITLE_OK = '{"score": 78, "reason": "当前标题信息量一般", "candidates": [{"title": "候选A", "score": 90, "reason": "更具体"}]}'


def test_title_score_parses(monkeypatch):
    fake = FakeClient([TITLE_OK])
    monkeypatch.setattr(ai_service, "_require_client", lambda conn, task="rewrite": fake)
    out = ai_service.title_score(None, "原标题", "开头内容")
    assert out["score"] == 78
    assert len(out["candidates"]) == 1
    assert out["candidates"][0]["title"] == "候选A"


def test_title_score_caps_candidates(monkeypatch):
    cands = [{"title": f"标题{i}", "score": 80 + i, "reason": "r"} for i in range(8)]
    payload = json.dumps({"score": 70, "reason": "r", "candidates": cands}, ensure_ascii=False)
    monkeypatch.setattr(ai_service, "_require_client", lambda conn, task="rewrite": FakeClient([payload]))
    out = ai_service.title_score(None, "原标题", "ctx")
    assert len(out["candidates"]) == 6


def test_title_score_bad_json_falls_back(monkeypatch):
    monkeypatch.setattr(ai_service, "_require_client", lambda conn, task="rewrite": FakeClient(["不是 JSON"]))
    out = ai_service.title_score(None, "原标题", "ctx")
    assert out["score"] is None
    assert out["candidates"] == []


# ---------- API 层 ----------

@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    """API 测试不触碰真实模型。"""
    fake = FakeClient([TITLE_OK, '{"candidates": [{"label": "方案一", "text": "改后"}]}'])
    monkeypatch.setattr(ai_service, "_require_client", lambda conn, task="rewrite": fake)
    return fake


def test_api_rewrite_flavor_validation(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import db, main
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    client = TestClient(main.app, base_url="http://127.0.0.1:8766")
    r = client.post("/api/ai/rewrite", json={"text": "一段文字", "flavor": "de-ai"})
    assert r.status_code == 200
    r2 = client.post("/api/ai/rewrite", json={"text": "一段文字", "flavor": "evil"})
    assert r2.status_code == 400


def test_api_title_score_ok(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import db, main
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    client = TestClient(main.app, base_url="http://127.0.0.1:8766")
    r = client.post("/api/ai/title-score", json={"title": "原标题", "context": "开头内容"})
    assert r.status_code == 200
    body = r.json()
    assert body["score"] == 78
    assert body["candidates"][0]["title"] == "候选A"


def test_api_title_score_rejects_missing_title(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import db, main
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    client = TestClient(main.app, base_url="http://127.0.0.1:8766")
    r = client.post("/api/ai/title-score", json={"title": "  ", "context": ""})
    assert r.status_code == 400
