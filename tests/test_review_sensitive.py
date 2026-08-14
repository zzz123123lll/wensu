"""敏感词扫描回收测试（旧仓"文成" pipeline/sensitive_words.py 行为契约）。

原则：只报告类别与命中数量，绝不回显命中词本身（reason/suggestion 不得出现命中词）。
"""

import pytest

from app import db
from app.review import deterministic, sensitive_words
from app.review import service


# ---------- 词库加载 ----------

def test_load_bundled_word_list():
    groups = sensitive_words.load_sensitive_words()
    cats = {g["category"] for g in groups}
    assert cats >= {"政治合规", "平台合规", "广告合规", "知识产权", "隐私保护"}
    for g in groups:
        assert g["words"], f"类别 {g['category']} 词表为空"


def test_load_missing_file_returns_empty():
    assert sensitive_words.load_sensitive_words(path="Z:/nonexistent/sensitive_words.txt") == []


def test_load_skips_comments_and_unknown_categories(tmp_path):
    p = tmp_path / "words.txt"
    p.write_text(
        "# 注释行\n\n"
        "政治合规:词A|词B\n"
        "未知类别:词C\n"
        "广告合规:词D\n"
        "无冒号行\n",
        encoding="utf-8",
    )
    groups = sensitive_words.load_sensitive_words(path=str(p))
    cats = {g["category"] for g in groups}
    assert cats == {"政治合规", "广告合规"}
    assert "未知类别" not in cats


# ---------- 命中统计（不回显命中词） ----------

def test_scan_hits_counts_per_category():
    hits = sensitive_words.scan_hits("本文提到赌博一次，另提到赌博又一次", ("平台合规",))
    assert len(hits) == 1
    assert hits[0]["category"] == "平台合规"
    assert hits[0]["hits"] == 2


def test_scan_hits_filters_by_category():
    assert sensitive_words.scan_hits("赌博", ("广告合规",)) == []


def test_scan_empty_text_no_hits():
    assert sensitive_words.scan_hits("") == []
    assert sensitive_words.scan_hits(None) == []


# ---------- 确定性规则 ----------

def _snap(text, bid="b1"):
    return {"blocks": [{"id": bid, "type": "paragraph", "text": text, "attrs": {}}], "citations": []}


def test_critical_rule_flags_block_without_echoing_word():
    issues = deterministic.run_rule("wechat.compliance.sensitive-critical", _snap("本文涉及赌博相关内容"))
    assert len(issues) == 1
    i = issues[0]
    assert i["severity"] == "error"
    assert i["anchor"]["block_id"] == "b1"
    assert "平台合规" in i["reason"]
    assert "赌博" not in i["reason"]
    assert "赌博" not in (i.get("suggestion") or "")


def test_advisory_rule_flags_warning():
    issues = deterministic.run_rule("wechat.compliance.sensitive-advisory", _snap("这个产品包治百病"))
    assert len(issues) == 1
    assert issues[0]["severity"] == "warning"
    assert "广告合规" in issues[0]["reason"]


def test_clean_text_no_sensitive_issues():
    assert deterministic.run_rule("wechat.compliance.sensitive-critical", _snap("普通的写作内容")) == []
    assert deterministic.run_rule("wechat.compliance.sensitive-advisory", _snap("普通的写作内容")) == []


def test_sensitive_rules_deterministic():
    snap = _snap("赌博与刷单都不可取")
    a = deterministic.run_rule("wechat.compliance.sensitive-critical", snap)
    b = deterministic.run_rule("wechat.compliance.sensitive-critical", snap)
    assert a == b


# ---------- 服务层：advisory 不可直接采用（防正文被通用建议文案覆盖） ----------

@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.migrate(c)
    return c


def test_accept_advisory_issue_rejected(conn):
    pid = db.create_project(conn, "p")
    aid = db.create_article(conn, pid, "t")
    db.save_article(conn, aid, blocks=[{"id": "b1", "type": "paragraph", "text": "本文涉及赌博", "attrs": {}}], base_version=1)
    out = service.create_review(conn, aid, {"common": ["common-markdown"], "type": [], "channel": ["wechat-mini"], "personal": []})
    issue = next(i for i in out["issues"] if i["rule_id"] == "wechat.compliance.sensitive-critical")
    with pytest.raises(ValueError):
        service.accept_issue(conn, out["review_id"], issue["id"])
    # 正文未被改动
    art = db.get_article(conn, aid)
    assert art["blocks"][0]["text"] == "本文涉及赌博"
