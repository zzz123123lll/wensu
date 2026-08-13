"""review 规则内核测试：Rule schema 校验（拒绝危险输入）+ 规则包加载。"""

import pytest

from app.review import models


def _good_rule(**over):
    d = {
        "id": "common.heading.order",
        "name": "标题层级顺序",
        "description": "标题不得跳级",
        "pack_id": "common-markdown",
        "pack_version": "1.0.0",
        "category": "format",
        "engine": "deterministic",
        "scope": "master",
        "severity": "error",
        "enabled": True,
        "params": {"max_skip": 1},
        "source": {"type": "system", "title": "引擎技术约束", "url": "https://example.com/guide", "verified_at": "2026-08-13"},
        "fix_mode": "exact_patch",
    }
    d.update(over)
    return d


# ---------- 合法规则 ----------

def test_valid_rule_passes():
    r = models.validate_rule(_good_rule())
    assert r.id == "common.heading.order"
    assert r.engine == "deterministic"
    assert r.source.type == "system"


# ---------- 非法规则拒绝 ----------

def test_reject_unknown_field():
    with pytest.raises(models.ReviewRuleError):
        models.validate_rule(_good_rule(evil_field="x"))


@pytest.mark.parametrize("field,bad", [
    ("category", "unknown-cat"),
    ("engine", "unknown-engine"),
    ("scope", "both"),
    ("severity", "fatal"),
    ("fix_mode", "auto_replace"),
])
def test_reject_bad_enum(field, bad):
    with pytest.raises(models.ReviewRuleError):
        models.validate_rule(_good_rule(**{field: bad}))


def test_reject_dangerous_url():
    with pytest.raises(models.ReviewRuleError):
        models.validate_rule(_good_rule(source={"type": "official", "title": "t", "url": "javascript:alert(1)"}))


def test_reject_invalid_regex_param():
    with pytest.raises(models.ReviewRuleError):
        models.validate_rule(_good_rule(params={"pattern": "([unclosed"}))


def test_reject_huge_param():
    with pytest.raises(models.ReviewRuleError):
        models.validate_rule(_good_rule(params={"big": "x" * 10000}))


def test_reject_missing_id():
    d = _good_rule()
    del d["id"]
    with pytest.raises(models.ReviewRuleError):
        models.validate_rule(d)


def test_reject_missing_source_for_official():
    """官方规则必须有来源。"""
    d = _good_rule(category="channel", scope="variant")
    del d["source"]
    with pytest.raises(models.ReviewRuleError):
        models.validate_rule(d)


def test_official_rule_requires_url_and_verified_at():
    """来源门禁：official 规则必须 url + verified_at（防伪硬规则）。"""
    with pytest.raises(models.ReviewRuleError):
        models.validate_rule(_good_rule(source={"type": "official", "title": "t"}))
    with pytest.raises(models.ReviewRuleError):
        models.validate_rule(_good_rule(source={"type": "official", "title": "t", "url": "https://x.com"}))
    # 完整官方来源通过
    r = models.validate_rule(_good_rule(source={"type": "official", "title": "t", "url": "https://x.com", "verified_at": "2026-08-13"}))
    assert r.source.type == "official"


def test_experience_rule_without_url_ok():
    """经验规则不需要 URL（诚实标注为经验建议）。"""
    r = models.validate_rule(_good_rule(source={"type": "experience", "title": "编辑经验"}))
    assert r.source.type == "experience"


# ---------- 规则包加载 ----------

def test_pack_load_rejects_duplicate_ids():
    rules = [_good_rule(), _good_rule()]
    pack = {"pack_id": "p1", "pack_version": "1.0.0", "name": "p", "rules": rules}
    with pytest.raises(models.ReviewRuleError):
        models.validate_pack(pack)


def test_pack_load_rejects_unknown_engine_inside():
    pack = {"pack_id": "p1", "pack_version": "1.0.0", "name": "p",
            "rules": [_good_rule(engine="nope")]}
    with pytest.raises(models.ReviewRuleError):
        models.validate_pack(pack)


def test_pack_load_ok():
    pack = {"pack_id": "p1", "pack_version": "1.0.0", "name": "p", "rules": [_good_rule()]}
    p = models.validate_pack(pack)
    assert p.pack_id == "p1"
    assert len(p.rules) == 1
