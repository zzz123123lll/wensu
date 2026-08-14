"""Phase 4 测试：AI 语义检查 / 证据检查 / 聚合去重冲突。mock LLM，不碰真实网络。"""


from app.review import aggregator, ai_checker, evidence_checker


class FakeClient:
    """mock LLM 客户端：返回预设内容；记录调用。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, messages, **kw):
        self.calls.append(messages)
        return self.responses.pop(0)


def _snapshot(blocks, citations=None):
    return {"article_version": 2, "blocks": blocks, "citations": citations or []}


def _b(bid, text):
    return {"id": bid, "type": "paragraph", "text": text, "attrs": {}}


AI_RULE = {"id": "opinion.argument.thesis", "name": "中心论点", "description": "d",
           "pack_id": "opinion-essay", "pack_version": "1.0.0", "category": "content",
           "engine": "ai", "scope": "master", "severity": "warning",
           "params": {}, "source": {"type": "experience", "title": "t"}, "fix_mode": "advisory"}


# ---------- ai_checker ----------

def test_ai_check_parses_valid_issues(monkeypatch):
    fake = FakeClient([json_dumps([{
        "rule_id": "opinion.argument.thesis", "block_id": "b1",
        "start_utf16": 0, "end_utf16": 4, "quoted_text": "这段开头",
        "reason": "缺少中心论点", "confidence": "medium", "suggestion": "开头点明论点",
    }])])
    monkeypatch.setattr(ai_checker, "_require_client", lambda conn, task: fake)
    issues = ai_checker.run_ai_checks(None, _snapshot([_b("b1", "这段开头文字")]), [AI_RULE])
    assert len(issues) == 1
    i = issues[0]
    assert i["rule_id"] == "opinion.argument.thesis"
    assert i["confidence"] == "medium"
    assert i["anchor"]["block_id"] == "b1"
    assert i["reason"]
    assert i["source_type"] == "ai"


def test_ai_check_drops_issue_without_required_field():
    """缺 confidence（必填字段）→ 丢弃并记录诊断；完整字段保留。"""
    import json
    fake = FakeClient([json.dumps([{
        "rule_id": "opinion.argument.thesis", "block_id": "b1",
        "quoted_text": "这段开头", "reason": "r",
        # 缺 confidence → 必须丢弃
    }], ensure_ascii=False)])
    issues = ai_checker.run_ai_checks(None, _snapshot([_b("b1", "这段开头文字")]), [AI_RULE],
                                      client_factory=lambda conn, task: fake)
    assert issues == []
    assert ai_checker.last_diagnostics  # 诊断已记录


def test_ai_check_drops_mis_anchored_quote():
    """quoted_text 在 block 文本中不存在 → 丢弃（锚点核对）。"""
    fake = FakeClient([json_dumps([{
        "rule_id": "opinion.argument.thesis", "block_id": "b1",
        "start_utf16": 0, "end_utf16": 4, "quoted_text": "完全不存在的话",
        "reason": "r", "confidence": "high",
    }])])
    import app.review.ai_checker as ac
    issues = ac.run_ai_checks(None, _snapshot([_b("b1", "真实文本内容")]), [AI_RULE],
                              client_factory=lambda conn, task: fake)
    assert issues == []  # 锚点核对失败丢弃


def test_ai_check_bad_json_no_crash():
    fake = FakeClient(["不是JSON"])
    issues = ai_checker.run_ai_checks(None, _snapshot([_b("b1", "x")]), [AI_RULE],
                                      client_factory=lambda conn, task: fake)
    assert issues == []


def test_ai_check_empty_blocks_skipped():
    assert ai_checker.run_ai_checks(None, _snapshot([]), [AI_RULE]) == []


# ---------- evidence_checker ----------

def test_evidence_claim_without_citation_flagged_pending():
    fake = FakeClient([json_dumps([
        {"block_id": "b1", "quoted_text": "2025 年全球 AI 市场规模", "claim": "factual"},
    ])])
    issues = evidence_checker.run_evidence_checks(
        None, _snapshot([_b("b1", "2025 年全球 AI 市场规模达到 X")], citations=[]),
        client_factory=lambda conn, task: fake)
    assert len(issues) == 1
    assert issues[0]["severity"] == "suggestion"
    assert "待核实" in issues[0]["reason"]
    assert issues[0]["source_type"] == "evidence"


def test_evidence_rhetorical_analogy_not_flagged():
    """dogfood Bug#10：观点文类比/比喻句不应被标为「事实性主张待核实」。"""
    fake = FakeClient([json_dumps([
        {"block_id": "b1", "quoted_text": "摄影没有取代画家，计算器没有取代数学家。", "claim": "factual"},
        {"block_id": "b1", "quoted_text": "打字机没有取代作家，反而让更多人开始写作。", "claim": "factual"},
        {"block_id": "b1", "quoted_text": "观点就像种子，需要时间发芽。", "claim": "factual"},
        {"block_id": "b1", "quoted_text": "2025 年市场规模达 2000 亿美元。", "claim": "factual"},
    ])])
    issues = evidence_checker.run_evidence_checks(
        None, _snapshot([_b("b1", "摄影没有取代画家，计算器没有取代数学家。打字机没有取代作家，反而让更多人开始写作。观点就像种子，需要时间发芽。2025 年市场规模达 2000 亿美元。")], citations=[]),
        client_factory=lambda conn, task: fake)
    # 只有真实数据句保留
    assert len(issues) == 1
    assert "2000 亿" in issues[0]["reason"]


def test_evidence_claim_covered_by_citation_ok():
    fake = FakeClient([json_dumps([
        {"block_id": "b1", "quoted_text": "2025 年全球 AI 市场规模", "claim": "factual"},
    ])])
    cites = [{"id": 1, "block_id": "b1", "status": "active", "source_title": "t", "source_url": "u"}]
    issues = evidence_checker.run_evidence_checks(
        None, _snapshot([_b("b1", "2025 年全球 AI 市场规模达到 X")], cites),
        client_factory=lambda conn, task: fake)
    assert issues == []  # 有引用覆盖 → 不标待核实


def test_evidence_bad_json_no_crash():
    fake = FakeClient(["垃圾"])
    issues = evidence_checker.run_evidence_checks(
        None, _snapshot([_b("b1", "x")]), client_factory=lambda conn, task: fake)
    assert issues == []


# ---------- aggregator ----------

def test_aggregate_dedup_same_fingerprint():
    det = [{"fingerprint": "f1", "rule_id": "r1", "severity": "error", "anchor": {"block_id": "b1"}, "reason": "a", "source_type": "system"}]
    ai = [{"fingerprint": "f1", "rule_id": "r1", "severity": "warning", "anchor": {"block_id": "b1"}, "reason": "a", "source_type": "ai", "confidence": "high"}]
    out, conflicts = aggregator.aggregate(det, ai, [])
    assert len(out) == 1
    assert out[0]["severity"] == "error"  # 确定性保留


def test_aggregate_ai_conflicts_with_deterministic_downgraded():
    det = [{"fingerprint": "f1", "rule_id": "r1", "severity": "error", "anchor": {"block_id": "b1", "start_utf16": 0, "end_utf16": 2}, "reason": "技术事实", "source_type": "system"}]
    ai = [{"fingerprint": "f2", "rule_id": "r2", "severity": "warning", "anchor": {"block_id": "b1", "start_utf16": 0, "end_utf16": 2}, "reason": "语义", "source_type": "ai", "confidence": "high"}]
    out, conflicts = aggregator.aggregate(det, ai, [])
    ai_issue = next(i for i in out if i["rule_id"] == "r2")
    assert ai_issue["severity"] == "suggestion"  # 与确定性同锚点 → 降为建议
    assert conflicts  # 冲突说明


def test_aggregate_keeps_distinct_issues():
    det = [{"fingerprint": "f1", "rule_id": "r1", "severity": "error", "anchor": {"block_id": "b1"}, "reason": "a", "source_type": "system"}]
    ai = [{"fingerprint": "f3", "rule_id": "r3", "severity": "suggestion", "anchor": {"block_id": "b2"}, "reason": "b", "source_type": "ai", "confidence": "low"}]
    out, _ = aggregator.aggregate(det, ai, [])
    assert len(out) == 2


def json_dumps(x):
    import json
    return json.dumps(x, ensure_ascii=False)
