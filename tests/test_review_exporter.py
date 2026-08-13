"""review exporter 测试：通用版无渠道污染、渠道补丁应用与 stale gate、引用渲染、文件名安全、摘要。"""

import pytest

from app.review import exporter


def _b(bid, text, btype="paragraph"):
    return {"id": bid, "type": btype, "text": text, "attrs": {}}


def _patch(block_id, original, replacement, status="active", target="wechat-mini"):
    return {
        "id": "p1", "review_id": 1, "target": target, "rule_id": "r1", "block_id": block_id,
        "selection": {"start_utf16": 0, "end_utf16": len(original), "original_text": original},
        "original_hash": exporter._patch_hash(original), "replacement": replacement,
        "status": status,
    }


# ---------- 通用版无渠道污染 ----------

def test_general_export_ignores_channel_patches():
    blocks = [_b("b1", "原文句子")]
    patches = [_patch("b1", "原文句子", "渠道版句子")]
    md = exporter.render_markdown(blocks)
    assert "渠道版句子" not in md
    assert "原文句子" in md
    applied, stale = exporter.apply_patches(blocks, patches)
    assert applied[0]["text"] == "渠道版句子"  # 渠道应用只在渠道流程


# ---------- 渠道版应用与 stale gate ----------

def test_channel_apply_active_patch():
    blocks = [_b("b1", "这段开头的话很重要")]
    p = _patch("b1", "开头的话", "起头的话")
    p["selection"] = {"start_utf16": 2, "end_utf16": 6, "original_text": "开头的话"}
    applied, stale = exporter.apply_patches(blocks, [p])
    assert applied[0]["text"] == "这段起头的话很重要"
    assert stale == []


def test_stale_patch_not_silently_applied():
    blocks = [_b("b1", "这段内容后来被改过了")]
    p = _patch("b1", "原来的句子", "渠道替换")  # 原文已不存在
    applied, stale = exporter.apply_patches(blocks, [p])
    assert applied[0]["text"] == "这段内容后来被改过了"  # 未静默应用
    assert len(stale) == 1 and stale[0]["id"] == "p1"


def test_stale_hash_mismatch_detected():
    blocks = [_b("b1", "原文")]
    p = _patch("b1", "原文", "改")
    p["original_hash"] = "wrong-hash"
    applied, stale = exporter.apply_patches(blocks, [p])
    assert applied[0]["text"] == "原文"
    assert len(stale) == 1


def test_multi_patch_reverse_order_no_offset_chain():
    blocks = [_b("b1", "ABCDEF")]
    # 两个补丁：先替换 BC，再替换 DE——逆序应用避免偏移连锁
    p1 = _patch("b1", "BC", "bc")
    p2 = _patch("b1", "DE", "de")
    p1["selection"] = {"start_utf16": 1, "end_utf16": 3, "original_text": "BC"}
    p2["selection"] = {"start_utf16": 3, "end_utf16": 5, "original_text": "DE"}
    applied, stale = exporter.apply_patches(blocks, [p1, p2])
    assert applied[0]["text"] == "AbcdeF"


def test_proposed_patch_not_applied():
    blocks = [_b("b1", "原文")]
    p = _patch("b1", "原文", "改", status="proposed")
    applied, stale = exporter.apply_patches(blocks, [p])
    assert applied[0]["text"] == "原文"


# ---------- 引用渲染 ----------

def test_citation_render_inline_and_source_list():
    blocks = [_b("b1", "正文引用处")]
    cites = [
        {"id": 1, "block_id": "b1", "quote": "q", "status": "active", "source_title": "来源一", "source_url": "https://a.com"},
        {"id": 2, "block_id": "b1", "quote": "q2", "status": "active", "source_title": "来源二", "source_url": ""},
    ]
    md = exporter.render_markdown(blocks, cites)
    assert "<sup>[1][2]</sup>" in md
    assert "[1] 来源一 · https://a.com" in md
    assert "[2] 来源二" in md


def test_citation_never_written_to_block_text():
    blocks = [_b("b1", "纯文本")]
    cites = [{"id": 1, "block_id": "b1", "quote": "q", "status": "active", "source_title": "t", "source_url": "u"}]
    md = exporter.render_markdown(blocks, cites)
    # 正文 block 文本不含 [1]
    assert "纯文本 <sup>" in md  # 上标追加在块外


# ---------- 文件名安全 ----------

def test_safe_filename_sanitizes():
    assert ":" not in exporter.safe_filename("标题:含冒号", "通用版")
    assert exporter.safe_filename("标题", "通用版") == "标题-通用版.md"


def test_safe_filename_rejects_traversal():
    with pytest.raises(ValueError):
        exporter.safe_filename("..\\..\\evil", "通用版")


def test_safe_filename_conflict_timestamp():
    f1 = exporter.safe_filename("文章", "通用版", existing=["文章-通用版.md"])
    assert f1 != "文章-通用版.md"
    assert f1.endswith(".md")


# ---------- 摘要 ----------

def test_manifest_counts_and_hashes():
    review = {"article_id": 1, "article_version": 3, "snapshot_hash": "h123"}
    profile = {"rules": [{"pack_id": "common-markdown", "pack_version": "1.0.0"}]}
    issues = [{"state": "accepted"}, {"state": "ignored"}, {"state": "open"}]
    patches = [{"status": "active"}, {"status": "stale"}]
    m = exporter.build_manifest(review, profile, issues, patches, [{"id": "x"}],
                                "通用内容", "渠道内容", "a-通用版.md", "a-渠道版.md")
    assert m["issues"] == {"accepted": 1, "ignored": 1, "open": 1}
    assert m["patches"]["active"] == 1 and m["patches"]["stale"] == 1
    assert m["files"]["general"]["sha1"]
    assert m["profile"]["packs"]["common-markdown"] == "1.0.0"
    assert m["article_version"] == 3
