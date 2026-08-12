"""typed Block schema 测试：类型/ID/长度边界。"""

import pytest
from pydantic import ValidationError

from app.schemas import BLOCK_TYPES, Block, BlockList


def test_valid_block():
    b = Block(id="b1", type="paragraph", text="你好", attrs={"x": 1})
    assert b.text == "你好"


def test_old_ids_compatible():
    # 旧 ID：b1 / new-<ts> / b<ts>-<i> / UUID
    for old in ("b1", "new-1720000000000", "b1786453987650-0", "3f9c1a2e-0d1a-4b2c-9e3f-1a2b3c4d5e6f"):
        Block(id=old, type="paragraph", text="x")


def test_illegal_type_rejected():
    with pytest.raises(ValidationError):
        Block(id="b1", type="script", text="x")


def test_illegal_id_rejected():
    for bad in ("", "id with space", "带中文的id", "a" * 65, "a<b>c", "a\nb"):
        with pytest.raises(ValidationError):
            Block(id=bad, type="paragraph", text="x")


def test_oversize_text_rejected():
    with pytest.raises(ValidationError):
        Block(id="b1", type="paragraph", text="长" * 20001)


def test_attrs_cap():
    with pytest.raises(ValidationError):
        Block(id="b1", type="paragraph", text="x", attrs={f"k{i}": i for i in range(51)})


def test_block_types_enum():
    for t in BLOCK_TYPES:
        Block(id="b1", type=t, text="x")


def test_blocklist_cap():
    blocks = [Block(id=f"b{i}", type="paragraph", text="x") for i in range(2001)]
    with pytest.raises(ValidationError):
        BlockList(blocks=blocks)
