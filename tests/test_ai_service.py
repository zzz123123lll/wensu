"""ai_service 测试：mock LLM 客户端，验证 prompt 构造 / 输出解析 / 降级。"""

import json
import time

import pytest

from app import ai_service
from app.llm import LLMError


@pytest.fixture(autouse=True)
def _clear_search_cache():
    """search 结果缓存：每测试清空，避免同 query 串结果。"""
    ai_service._SEARCH_CACHE.clear()
    yield
    ai_service._SEARCH_CACHE.clear()


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, json_mode=False, **kwargs):
        self.calls.append((messages, json_mode))
        if isinstance(self.replies[0], Exception):
            raise self.replies.pop(0)
        return self.replies.pop(0)


def test_ask_requires_configuration(monkeypatch):
    monkeypatch.setattr(ai_service, "_require_client", lambda conn: (_ for _ in ()).throw(LLMError("未配置", "config")))
    with pytest.raises(LLMError):
        ai_service.ask(None, "怎么改开头", "正文...")


def test_ask_passes_context(monkeypatch):
    fake = FakeClient(["回答"])
    monkeypatch.setattr(ai_service, "_require_client", lambda conn: fake)
    out = ai_service.ask(None, "怎么改开头", "这是正文上下文")
    assert out == "回答"
    msgs = fake.calls[0][0]
    assert "这是正文上下文" in msgs[-1]["content"]
    assert "怎么改开头" in msgs[-1]["content"]


def test_rewrite_parses_two_candidates(monkeypatch):
    fake = FakeClient(['{"candidates": [{"label": "方案一", "text": "A"}, {"label": "方案二", "text": "B"}]}'])
    monkeypatch.setattr(ai_service, "_require_client", lambda conn: fake)
    out = ai_service.rewrite(None, "原文字")
    assert len(out) == 2
    assert out[0] == {"label": "方案一", "text": "A"}
    assert fake.calls[0][1] is True  # json_mode


def test_rewrite_fallback_on_bad_json(monkeypatch):
    fake = FakeClient(["这是第一行改写。\n这是第二行。"])
    monkeypatch.setattr(ai_service, "_require_client", lambda conn: fake)
    out = ai_service.rewrite(None, "原文字")
    assert len(out) == 1
    assert "第一行" in out[0]["text"]


def test_rewrite_empty_fallback(monkeypatch):
    fake = FakeClient(["   "])
    monkeypatch.setattr(ai_service, "_require_client", lambda conn: fake)
    out = ai_service.rewrite(None, "原文字")
    assert out[0]["text"] == "原文字"


def test_insight_parses(monkeypatch):
    payload = '{"insight": {"summary": "论点", "gap": "缺例子"}, "suggestions": [{"title": "补例子", "desc": "找案例", "action": "search"}, {"title": "改开头", "desc": "x", "action": "rewrite"}]}'
    fake = FakeClient([payload])
    monkeypatch.setattr(ai_service, "_require_client", lambda conn: fake)
    out = ai_service.insight(None, "标题", [{"text": "第一段"}, {"text": "第二段"}])
    assert out["insight"]["summary"] == "论点"
    assert out["suggestions"][0]["action"] == "search"
    # prompt 含正文
    joined = "".join(m["content"] for m in fake.calls[0][0])
    assert "第一段" in joined


def test_insight_fallback_on_bad_json(monkeypatch):
    fake = FakeClient(["不是 JSON"])
    monkeypatch.setattr(ai_service, "_require_client", lambda conn: fake)
    out = ai_service.insight(None, "t", [])
    assert out["insight"] == {"summary": "", "gap": ""}
    assert out["suggestions"] == []


def test_insight_bad_action_falls_back_to_rewrite(monkeypatch):
    payload = '{"insight": {"summary": "s", "gap": "g"}, "suggestions": [{"title": "x", "desc": "y", "action": "publish"}]}'
    fake = FakeClient([payload])
    monkeypatch.setattr(ai_service, "_require_client", lambda conn: fake)
    out = ai_service.insight(None, "t", [])
    assert out["suggestions"][0]["action"] == "rewrite"


# ---------- check（事实核验） ----------

def test_check_parses_ok(monkeypatch):
    payload = '{"status": "ok", "reason": "有据可查", "suggestion": ""}'
    fake = FakeClient([payload])
    monkeypatch.setattr(ai_service, "_require_client", lambda conn: fake)
    out = ai_service.check(None, "地球是圆的")
    assert out["status"] == "ok"
    assert out["reason"] == "有据可查"
    assert fake.calls[0][1] is True  # json_mode


def test_check_parses_fix(monkeypatch):
    payload = '{"status": "fix", "reason": "数据过时", "suggestion": "改为更稳妥的表述"}'
    fake = FakeClient([payload])
    monkeypatch.setattr(ai_service, "_require_client", lambda conn: fake)
    out = ai_service.check(None, "56% 的创作者…")
    assert out["status"] == "fix"
    assert out["suggestion"] == "改为更稳妥的表述"


def test_check_bad_status_falls_back_doubt(monkeypatch):
    fake = FakeClient(['{"status": "maybe", "reason": "r"}'])
    monkeypatch.setattr(ai_service, "_require_client", lambda conn: fake)
    out = ai_service.check(None, "x")
    assert out["status"] == "doubt"


def test_check_bad_json_falls_back(monkeypatch):
    fake = FakeClient(["不是 JSON"])
    monkeypatch.setattr(ai_service, "_require_client", lambda conn: fake)
    out = ai_service.check(None, "x")
    assert out["status"] == "doubt"
    assert out["suggestion"] == ""


# ---------- search（真搜索，零 key 源） ----------

def test_search_wikipedia_first_then_ddg(monkeypatch):
    w = [{"title": "维基条目", "url": "u", "snippet": "s"}]
    monkeypatch.setattr(ai_service, "_wikipedia_search", lambda q: w)
    monkeypatch.setattr(ai_service, "_ddg_search", lambda q: [{"title": "X", "url": "", "snippet": ""}])
    out = ai_service.search(None, "q")
    assert len(out) == 1
    assert out[0]["title"] == "维基条目"


def test_search_falls_back_to_ddg(monkeypatch):
    monkeypatch.setattr(ai_service, "_wikipedia_search", lambda q: [])
    monkeypatch.setattr(ai_service, "_ddg_search", lambda q: [{"title": "D", "url": "u", "snippet": "s"}])
    out = ai_service.search(None, "q")
    assert len(out) == 1
    assert out[0]["title"] == "D"


def test_search_empty_when_both_fail(monkeypatch):
    monkeypatch.setattr(ai_service, "_wikipedia_search", lambda q: [])
    monkeypatch.setattr(ai_service, "_ddg_search", lambda q: [])
    # 未配置模型（LLMError）时降级为空，不抛
    def boom(conn):
        raise LLMError("未配置", "config")
    monkeypatch.setattr(ai_service, "_require_client", boom)
    assert ai_service.search(None, "q") == []


def test_search_model_fallback_when_web_unavailable(monkeypatch):
    monkeypatch.setattr(ai_service, "_wikipedia_search", lambda q: [])
    monkeypatch.setattr(ai_service, "_ddg_search", lambda q: [])
    fake = FakeClient(['[{"title": "著作A", "url": "", "snippet": "相关"}]'])
    monkeypatch.setattr(ai_service, "_require_client", lambda conn: fake)
    out = ai_service.search(None, "q")
    assert len(out) == 1
    assert out[0]["source"] == "model"
    assert out[0]["url"] == ""


def test_model_search_bad_json_gives_friendly_hint(monkeypatch):
    fake = FakeClient(["不是 JSON"])
    monkeypatch.setattr(ai_service, "_require_client", lambda conn: fake)
    out = ai_service._model_search(None, "q")
    assert len(out) == 1
    assert "不可达" in out[0]["title"]


def test_search_caps_at_five(monkeypatch):
    many = [{"title": "t%d" % i, "url": "u", "snippet": "s"} for i in range(8)]
    monkeypatch.setattr(ai_service, "_wikipedia_search", lambda q: many)
    out = ai_service.search(None, "q")
    assert len(out) == 5


# ---------- NDJSON 流式 ----------

def test_search_stream_events_order(monkeypatch):
    w = [{"title": "维基", "url": "u", "snippet": "s"}]
    monkeypatch.setattr(ai_service, "_wikipedia_search", lambda q: w)
    monkeypatch.setattr(ai_service, "_ddg_search", lambda q: [])
    monkeypatch.setattr(ai_service, "_require_client", lambda conn: FakeClient(["[]"]))
    events = [json.loads(line) for line in ai_service.search_stream(None, "q_stream")]
    types = [e["type"] for e in events]
    assert types == ["stage", "stage", "result"]
    assert events[-1]["results"][0]["title"] == "维基"


def test_search_stream_cached_hit(monkeypatch):
    ai_service._SEARCH_CACHE["q_cached"] = (time.time(), [{"title": "缓存", "url": "", "snippet": ""}])
    events = [json.loads(line) for line in ai_service.search_stream(None, "q_cached")]
    assert events[0]["stage"] == "cached"
    assert events[1]["results"][0]["title"] == "缓存"
