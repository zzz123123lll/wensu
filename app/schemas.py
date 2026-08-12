"""typed Block schema：API 边界校验。

前端输入与模型输出都是不可信输入；blocks 进入存储前必须通过本校验。
兼容旧 ID（b1 / new-* / b<ts>-<i>），新建 Block 才要求 UUID（由前端生成）。
"""

import re

from pydantic import BaseModel, Field, field_validator

BLOCK_TYPES = (
    "paragraph",
    "heading",
    "blockquote",
    "unordered_list",
    "ordered_list",
    "code",
    "image",
    "divider",
)

# 稳定不透明 ID：非空、1-64 位、字母数字下划线连字符（兼容旧 ID 与 UUID）
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

MAX_BLOCK_TEXT = 20000      # 单 Block 文本长度上限
MAX_BLOCK_ATTRS = 50        # attrs 键数量上限
MAX_BLOCKS = 2000           # 单稿 Block 数量上限


class Block(BaseModel):
    id: str
    type: str
    text: str = ""
    attrs: dict = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError(f"非法 block id: {v!r}")
        return v

    @field_validator("type")
    @classmethod
    def _check_type(cls, v: str) -> str:
        if v not in BLOCK_TYPES:
            raise ValueError(f"未知 block 类型: {v!r}")
        return v

    @field_validator("text")
    @classmethod
    def _check_text(cls, v: str) -> str:
        if len(v) > MAX_BLOCK_TEXT:
            raise ValueError(f"block 文本超长（>{MAX_BLOCK_TEXT}）")
        return v

    @field_validator("attrs")
    @classmethod
    def _check_attrs(cls, v: dict) -> dict:
        if len(v) > MAX_BLOCK_ATTRS:
            raise ValueError(f"attrs 键过多（>{MAX_BLOCK_ATTRS}）")
        return v


class BlockList(BaseModel):
    blocks: list[Block] = Field(default_factory=list, max_length=MAX_BLOCKS)
