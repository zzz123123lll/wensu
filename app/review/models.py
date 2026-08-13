"""review 规则内核：Rule/Pack 的 Pydantic 校验（拒绝危险输入）。

规则包使用版本化 JSON，运行前由本模块校验；未知字段、重复 ID、
危险 URL、非法正则、超大参数或不支持的 engine 必须拒绝。
"""

import re
from urllib.parse import urlparse

from pydantic import BaseModel, Field, ValidationError, field_validator

ALLOWED_CATEGORIES = {"format", "language", "content", "evidence", "channel"}
ALLOWED_ENGINES = {"deterministic", "ai", "evidence"}
ALLOWED_SCOPES = {"master", "variant"}
ALLOWED_SEVERITIES = {"error", "warning", "suggestion"}
ALLOWED_SOURCE_TYPES = {"system", "official", "experience", "user", "ai"}
ALLOWED_FIX_MODES = {"exact_patch", "candidate", "advisory"}

MAX_PARAMS = 20
MAX_PARAM_VALUE_LEN = 500
MAX_PARAM_KEY_LEN = 50
MAX_RULES_PER_PACK = 200
SAFE_URL_SCHEMES = {"http", "https"}


class ReviewRuleError(Exception):
    """规则校验失败：给出精确原因（用于导入/安装失败提示）。"""


class RuleSource(BaseModel):
    model_config = {"extra": "forbid"}
    type: str
    title: str = ""
    url: str | None = None
    verified_at: str | None = None

    @field_validator("url")
    @classmethod
    def _safe_url(cls, v):
        if v is None or not v.strip():
            return v
        p = urlparse(v)
        if p.scheme not in SAFE_URL_SCHEMES or not p.netloc:
            raise ValueError("来源 URL 仅允许 http/https")
        return v

    @field_validator("type")
    @classmethod
    def _type(cls, v):
        if v not in ALLOWED_SOURCE_TYPES:
            raise ValueError(f"未知来源类型 {v}")
        return v


class Rule(BaseModel):
    model_config = {"extra": "forbid"}
    id: str = Field(min_length=3, max_length=100, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    pack_id: str = Field(min_length=1, max_length=50)
    pack_version: str = Field(min_length=1, max_length=30)
    category: str
    engine: str
    scope: str = "master"
    severity: str = "warning"
    enabled: bool = True
    params: dict = Field(default_factory=dict)
    source: RuleSource | None = None
    fix_mode: str = "advisory"

    @field_validator("category")
    @classmethod
    def _cat(cls, v):
        if v not in ALLOWED_CATEGORIES:
            raise ValueError(f"未知类别 {v}")
        return v

    @field_validator("engine")
    @classmethod
    def _eng(cls, v):
        if v not in ALLOWED_ENGINES:
            raise ValueError(f"未知引擎 {v}")
        return v

    @field_validator("scope")
    @classmethod
    def _scope(cls, v):
        if v not in ALLOWED_SCOPES:
            raise ValueError(f"未知作用域 {v}")
        return v

    @field_validator("severity")
    @classmethod
    def _sev(cls, v):
        if v not in ALLOWED_SEVERITIES:
            raise ValueError(f"未知严重度 {v}")
        return v

    @field_validator("fix_mode")
    @classmethod
    def _fix(cls, v):
        if v not in ALLOWED_FIX_MODES:
            raise ValueError(f"未知修复模式 {v}")
        return v

    @field_validator("params")
    @classmethod
    def _params(cls, v):
        if len(v) > MAX_PARAMS:
            raise ValueError(f"参数数量超过上限 {MAX_PARAMS}")
        for k, val in v.items():
            if len(k) > MAX_PARAM_KEY_LEN:
                raise ValueError(f"参数名过长 {k}")
            s = str(val)
            if len(s) > MAX_PARAM_VALUE_LEN:
                raise ValueError(f"参数值超过 {MAX_PARAM_VALUE_LEN} 字符")
            if k in ("pattern", "re"):
                try:
                    re.compile(s)
                except re.error as e:
                    raise ValueError(f"非法正则 {k}: {e}")
        return v


class RulePack(BaseModel):
    model_config = {"extra": "forbid"}
    pack_id: str = Field(min_length=1, max_length=50)
    pack_version: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    rules: list[Rule] = Field(min_length=1, max_length=MAX_RULES_PER_PACK)


def validate_rule(d: dict) -> Rule:
    """校验单条规则；失败抛 ReviewRuleError（含精确原因）。"""
    try:
        r = Rule.model_validate(d)
    except ValidationError as e:
        first = e.errors()[0]
        loc = ".".join(str(x) for x in first["loc"])
        raise ReviewRuleError(f"规则校验失败：{loc} → {first['msg']}") from e
    # 渠道规则必须携带来源（官方/用户配置），防伪硬规则
    if r.source is None and r.category == "channel":
        raise ReviewRuleError(f"规则 {r.id} 缺少来源（渠道规则必须有 source）")
    return r


def validate_pack(pack: dict) -> RulePack:
    """校验规则包：重复 ID、非法规则等；失败抛 ReviewRuleError。"""
    try:
        p = RulePack.model_validate(pack)
    except ValidationError as e:
        first = e.errors()[0]
        loc = ".".join(str(x) for x in first["loc"])
        raise ReviewRuleError(f"规则包校验失败：{loc} → {first['msg']}") from e
    seen = set()
    for r in p.rules:
        if r.id in seen:
            raise ReviewRuleError(f"规则 ID 重复：{r.id}")
        seen.add(r.id)
    return p
