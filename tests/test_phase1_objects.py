"""下一阶段进化方案 阶段1 测试：素材库 / Ask 元数据 / 引用核验失效 / 删除影响范围。"""

import pytest

from app import db


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.migrate(c)
    return c


def _mk(conn, title="测试稿"):
    pid = db.create_project(conn, "测试项目")
    aid = db.create_article(conn, pid, title)
    return pid, aid


def _mk_source(conn, pid):
    return db.create_source(conn, pid, "https://example.com/a", "来源A")


def _mk_citation(conn, aid, bid, sid):
    return db.create_citation(conn, aid, bid, sid, "引文内容", "", "来源A")


# ---------- 工作包 A：素材库 ----------

def test_material_with_tags_and_search(conn):
    pid, _ = _mk(conn)
    sid = _mk_source(conn, pid)
    mid = db.create_material(conn, pid, "素材标题", "素材内容", sid, tags=["数据", "观点"])
    # 关键词搜索
    assert len(db.list_materials(conn, q="素材内容")) == 1
    assert len(db.list_materials(conn, q="不存在")) == 0
    # 标签筛选
    assert len(db.list_materials(conn, tag="数据")) == 1
    assert len(db.list_materials(conn, tag="其他")) == 0
    # 全部范围
    assert len(db.list_materials(conn)) == 1


def test_material_usage_and_protected_delete(conn):
    pid, aid = _mk(conn)
    sid = _mk_source(conn, pid)
    mid = db.create_material(conn, pid, "素材", "内容", sid)
    db.create_citation(conn, aid, "b1", sid, "引文", "", "来源")
    usage = db.material_usage(conn, mid)
    assert len(usage["citations"]) == 1  # 影响范围：1 处引用
    assert usage["articles"] == [aid]


# ---------- 工作包 B：Ask 历史 ----------

def test_ask_usage_metadata(conn):
    _, aid = _mk(conn)
    ask_id = db.record_ask(conn, aid, "问题", "回答", "deepseek-x")
    assert db.set_ask_usage(conn, ask_id, "saved_as_material") is True
    asks = db.list_asks(conn, aid)
    assert asks[0]["metadata"].get("usage") == "saved_as_material"
    assert db.set_ask_usage(conn, 9999, "inserted_to_body") is False


def test_ask_checkpoint_trims(conn):
    _, aid = _mk(conn)
    for i in range(60):
        db.record_ask(conn, aid, f"q{i}", "r", "m")
    asks = db.list_asks(conn, aid, 200)
    assert len(asks) <= db.ASK_KEEP  # 超限裁剪，保留最近


# ---------- 工作包 C：引用核验 ----------

def test_citation_verification_statuses(conn):
    pid, aid = _mk(conn)
    sid = _mk_source(conn, pid)
    cid = _mk_citation(conn, aid, "b1", sid)
    for st in (db.VERIF_PENDING, db.VERIF_SUPPORTED, db.VERIF_INSUFFICIENT,
               db.VERIF_CONFLICTING, db.VERIF_SOURCE_DEAD, db.VERIF_NEEDS_RECHECK):
        assert db.set_citation_verification(conn, cid, st, "注") is True
    cites = db.list_citations(conn, aid)
    assert cites[0]["verification_status"] == db.VERIF_NEEDS_RECHECK


def test_invalidate_citations_on_edit(conn):
    pid, aid = _mk(conn)
    sid = _mk_source(conn, pid)
    c1 = _mk_citation(conn, aid, "b1", sid)
    c2 = _mk_citation(conn, aid, "b2", sid)
    db.set_citation_verification(conn, c1, db.VERIF_SUPPORTED)
    db.set_citation_verification(conn, c2, db.VERIF_SUPPORTED)
    # b1 正文变化 → b1 的引用失效，b2 保留
    n = db.invalidate_citations_on_edit(conn, aid, {"b1"})
    assert n == 1
    cites = {c["block_id"]: c for c in db.list_citations(conn, aid)}
    assert cites["b1"]["verification_status"] == db.VERIF_NEEDS_RECHECK
    assert cites["b2"]["verification_status"] == db.VERIF_SUPPORTED
    # 幂等：再触发不重复计数
    assert db.invalidate_citations_on_edit(conn, aid, {"b1"}) == 0
