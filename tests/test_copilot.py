"""写作智能 v0.2 规则引擎测试：可解释、可重复、无模型可用、限频。"""

import time

from app import copilot


def _ctx(**kw):
    base = {"stage": "unknown", "issue": "none", "focus": "article",
            "block_id": None, "article_id": 1, "model_configured": False}
    base.update(kw)
    return base


def test_expression_mark_yields_rewrite():
    sugs = copilot.CopilotEngine().suggest(_ctx(issue="expression", focus="block", block_id="b1"))
    assert len(sugs) == 1
    s = sugs[0]
    assert s["type"] == "rewrite"
    assert s["target_block_id"] == "b1"
    assert s["reason"]  # 可解释
    assert s["source"] == "rule"
    assert "run" in s["actions"] and "dismiss" in s["actions"]


def test_facts_mark_yields_search_and_check():
    sugs = copilot.CopilotEngine().suggest(_ctx(issue="facts", focus="block", block_id="b1"))
    types = {s["type"] for s in sugs}
    assert types == {"search", "check"}


def test_same_input_same_output_repeatable():
    e = copilot.CopilotEngine()
    a = e.suggest(_ctx(issue="expression", focus="block", block_id="b1"))
    b = e.suggest(_ctx(issue="expression", focus="block", block_id="b1"))
    assert [s["type"] for s in a] == [s["type"] for s in b]


def test_no_signal_no_suggestion_low_disturbance():
    # 无标记、无模型 → 无建议（不打扰）
    sugs = copilot.CopilotEngine().suggest(_ctx())
    assert sugs == []


def test_works_without_model_configured():
    # 完成门：无模型配置时规则建议仍工作
    sugs = copilot.CopilotEngine().suggest(_ctx(issue="facts", focus="block", block_id="b1", model_configured=False))
    assert len(sugs) == 2


def test_dismissed_suggestion_suppressed():
    e = copilot.CopilotEngine()
    ctx = _ctx(issue="expression", focus="block", block_id="b1")
    s = e.suggest(ctx)[0]
    key = s["type"] + ":" + (s.get("target_block_id") or s.get("reason", ""))
    e.dismissed[key] = time.time()
    assert e.suggest(ctx) == []  # 限频：拒绝后不立即反复


def test_signals_to_state_derivation():
    state = copilot.signals_to_state([{"type": "tool_click", "tool": "rewrite", "focus": "block"}])
    assert state["issue"] == "expression"
    assert state["stage"] == "revising"

    state2 = copilot.signals_to_state([{"type": "mark", "issue": "facts", "focus": "block"}])
    assert state2["issue"] == "facts"

    state3 = copilot.signals_to_state([])
    assert state3["issue"] == "none"  # 无信号 → 无打扰


def test_mark_recorded_in_signals():
    copilot.record_signal(1, {"type": "mark", "issue": "structure", "focus": "article"})
    state = copilot.signals_to_state(copilot.get_signals(1))
    assert state["issue"] == "structure"
    copilot._signals_store.pop(1, None)  # 清场
