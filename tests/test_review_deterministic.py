"""review 确定性检查器测试：同一快照同规则 → 相同 Issue（纯函数）。"""


from app.review import deterministic


def _snapshot(blocks, citations=None):
    return {
        "article_version": 3,
        "blocks": blocks,
        "citations": citations or [],
    }


def _block(bid, text, btype="paragraph"):
    return {"id": bid, "type": btype, "text": text, "attrs": {}}


# ---------- 标题规则 ----------

def test_heading_skip_level_detected():
    blocks = [_block("b1", "主标题", "heading"), _block("b2", "正文", "paragraph"), _block("b3", "小标题", "heading4")]
    issues = deterministic.run_rule("common.heading.order", _snapshot(blocks))
    assert any(i["rule_id"] == "common.heading.order" and i["anchor"]["block_id"] == "b3" for i in issues)
    assert issues[0]["severity"] == "error"


def test_heading_order_ok_when_sequential():
    blocks = [_block("b1", "主标题", "heading"), _block("b2", "小标题", "heading2")]
    assert deterministic.run_rule("common.heading.order", _snapshot(blocks)) == []


def test_empty_heading_detected():
    blocks = [_block("b1", "", "heading")]
    issues = deterministic.run_rule("common.heading.empty", _snapshot(blocks))
    assert len(issues) == 1 and issues[0]["severity"] == "warning"


def test_duplicate_h1_detected():
    blocks = [_block("b1", "同一个标题", "heading"), _block("b2", "正文", "paragraph"), _block("b3", "同一个标题", "heading")]
    issues = deterministic.run_rule("common.heading.duplicate-title", _snapshot(blocks))
    assert len(issues) == 1


# ---------- Markdown/链接 ----------

def test_unsafe_url_detected():
    blocks = [_block("b1", "见 [危险](javascript:alert(1)) 链接")]
    issues = deterministic.run_rule("common.markdown.unsafe-url", _snapshot(blocks))
    assert len(issues) == 1 and issues[0]["severity"] == "error"


def test_missing_image_alt_detected():
    blocks = [_block("b1", "图片 ![](http://x.com/a.png) 之后文字")]
    issues = deterministic.run_rule("common.markdown.image-alt", _snapshot(blocks))
    assert len(issues) == 1


def test_image_with_alt_ok():
    blocks = [_block("b1", "图片 ![图表说明](http://x.com/a.png) 之后文字")]
    assert deterministic.run_rule("common.markdown.image-alt", _snapshot(blocks)) == []


# ---------- 语言机械项 ----------

def test_double_punctuation_detected():
    blocks = [_block("b1", "这句话结尾有重复标点。。下一句")]
    issues = deterministic.run_rule("common.language.double-punctuation", _snapshot(blocks))
    assert len(issues) == 1


def test_repeated_word_detected():
    blocks = [_block("b1", "这里出现了了重复字")]
    issues = deterministic.run_rule("common.language.repeated-word", _snapshot(blocks))
    assert len(issues) == 1


def test_long_sentence_detected():
    blocks = [_block("b1", "这是一个特别长的句子它没有标点符号一直延续下去完全没有停顿读者会读得很辛苦" * 3)]
    issues = deterministic.run_rule("common.language.long-sentence", _snapshot(blocks), {"max_len": 60})
    assert len(issues) == 1


# ---------- 证据机械项 ----------

def test_orphan_citation_detected():
    blocks = [_block("b1", "正文")]
    cites = [{"id": 1, "block_id": "ghost-block", "source_id": 1, "status": "active"}]
    issues = deterministic.run_rule("common.evidence.orphan-citation", _snapshot(blocks, cites))
    assert len(issues) == 1 and issues[0]["severity"] == "error"


def test_missing_source_title_detected():
    blocks = [_block("b1", "正文")]
    cites = [{"id": 1, "block_id": "b1", "source_id": 1, "status": "active", "source_title": "", "source_url": ""}]
    issues = deterministic.run_rule("common.evidence.missing-source", _snapshot(blocks, cites))
    assert len(issues) == 1


# ---------- 纯函数确定性 ----------

def test_same_input_same_output():
    snap = _snapshot([_block("b1", "标题", "heading"), _block("b2", "", "heading2")])
    a = deterministic.run_rule("common.heading.empty", snap)
    b = deterministic.run_rule("common.heading.empty", snap)
    assert a == b


def test_run_all_returns_rule_ids():
    blocks = [_block("b1", "见 [危险](javascript:alert(1))", "heading"), _block("b2", "", "heading2")]
    issues = deterministic.run_all(_snapshot(blocks), [{"id": "common.markdown.unsafe-url", "params": {}}, {"id": "common.heading.empty", "params": {}}])
    assert {i["rule_id"] for i in issues} == {"common.markdown.unsafe-url", "common.heading.empty"}
