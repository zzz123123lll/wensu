"""review 规则包：内置包加载与安全校验（Phase 1 最小集）。"""

import json
import os

from app.review import models

_PACKS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "packs")

# 内置包清单（Phase 1 最小：通用基础 + 观点长文）
BUILTIN_PACK_IDS = ["common-markdown", "opinion-essay"]


def load_pack_file(pack_id: str) -> models.RulePack:
    """加载内置包并校验；失败抛 ReviewRuleError。"""
    path = os.path.join(_PACKS_DIR, f"{pack_id}.json")
    if not os.path.exists(path):
        raise models.ReviewRuleError(f"规则包不存在：{pack_id}")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return models.validate_pack(data)


def load_all_builtin() -> dict[str, models.RulePack]:
    """加载全部内置包，返回 {pack_id: RulePack}。"""
    return {pid: load_pack_file(pid) for pid in BUILTIN_PACK_IDS}


def load_rules_for_profile(pack_ids: list[str]) -> list[dict]:
    """按 Profile 选定的包加载规则（含 params），供确定性/其他检查器消费。"""
    out = []
    for pid in pack_ids:
        pack = load_pack_file(pid)
        for r in pack.rules:
            if r.enabled:
                out.append({"id": r.id, "params": r.params, "engine": r.engine,
                            "severity": r.severity, "scope": r.scope,
                            "pack_id": r.pack_id, "pack_version": r.pack_version,
                            "source": r.source.model_dump() if r.source else None})
    return out
