"""Phase 5 规则包来源门禁测试：全部内置包可加载且来源合规。"""

import pytest

from app.review import pack_loader


@pytest.mark.parametrize("pid", pack_loader.BUILTIN_PACK_IDS)
def test_all_builtin_packs_load(pid):
    pack = pack_loader.load_pack_file(pid)
    assert pack.pack_id == pid
    assert pack.rules


def test_channel_packs_have_no_fake_official_rules():
    """防伪硬规则：渠道包不得有未核验的 official 规则（当前全部为 experience/user）。"""
    for pid in ("wechat-mini", "zhihu", "toutiao"):
        pack = pack_loader.load_pack_file(pid)
        for r in pack.rules:
            assert r.source.type != "official", f"{pid}:{r.id} 不得自称官方规则（未核验）"
            assert r.scope == "variant", f"{pid}:{r.id} 渠道规则必须 variant 作用域"


def test_template_packs_disabled_by_default():
    """模板类包（博客/学术/报告）默认禁用，等待用户配置。"""
    for pid in ("blog", "academic", "work-report"):
        pack = pack_loader.load_pack_file(pid)
        assert all(not r.enabled for r in pack.rules), f"{pid} 模板规则应默认禁用"


def test_pack_ids_match_rule_prefixes():
    """规则 id 前缀与包 id 一致（命名空间约束）。"""
    for pid in pack_loader.BUILTIN_PACK_IDS:
        pack = pack_loader.load_pack_file(pid)
        for r in pack.rules:
            assert r.id.startswith(pid.split("-")[0]), f"{r.id} 不属于包 {pid}"
