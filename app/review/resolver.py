"""resolver：四层 Profile 规则合并（通用 + 文章类型 + 发布目标 + 个人覆盖）。

冲突解析：个人 > 发布目标 > 文章类型 > 通用。
同优先级同规则冲突 → 不静默选择，标记 conflict 交由用户决定。
"""

from app.review import models

LAYERS = ("common", "type", "channel", "personal")


class ResolveConflict(Exception):
    """规则冲突：系统不静默选择，返回给用户决定。"""


def resolve_profile(pack_selection: dict[str, list[str]],
                    overrides: dict[str, dict] | None = None,
                    custom_rules: list[dict] | None = None) -> dict:
    """合并规则。

    pack_selection: {"common": ["common-markdown"], "type": ["opinion-essay"],
                     "channel": ["wechat"], "personal": []}
    overrides: {rule_id: patch_dict} 用户覆盖（只影响该规则参数/严重度/启停）
    custom_rules: [{rule dict}...] 用户自定义规则（layer=personal）

    返回 {"rules": [已解析规则含 layer], "conflicts": [冲突说明]}
    """
    from app.review import pack_loader

    layer_order = {"common": 0, "type": 1, "channel": 2, "personal": 3}
    merged: dict[str, dict] = {}
    conflicts: list[dict] = []

    for layer, pack_ids in pack_selection.items():
        for pid in pack_ids:
            pack = pack_loader.load_pack_file(pid)
            for r in pack.rules:
                if not r.enabled:
                    continue
                key = r.id
                entry = {
                    "id": r.id, "name": r.name, "description": r.description,
                    "pack_id": r.pack_id, "pack_version": r.pack_version,
                    "category": r.category, "engine": r.engine, "scope": r.scope,
                    "severity": r.severity, "params": dict(r.params),
                    "fix_mode": r.fix_mode,
                    "source": r.source.model_dump() if r.source else None,
                    "layer": layer,
                }
                if key in merged:
                    prev = merged[key]
                    # 同层冲突：不静默选择
                    if prev["layer"] == layer:
                        conflicts.append({
                            "rule_id": key, "layers": [layer, layer],
                            "message": f"规则 {key} 在同一层出现两处不同定义（pack {prev['pack_id']} vs {pack.pack_id}）",
                        })
                        continue
                    # 高层覆盖低层
                    if layer_order[layer] > layer_order[prev["layer"]]:
                        merged[key] = entry
                else:
                    merged[key] = entry

    # 个人覆盖 patch：只改参数/严重度/启停，不改 ID/引擎
    for rid, patch in (overrides or {}).items():
        if rid in merged:
            for k in ("params", "severity", "enabled", "fix_mode"):
                if k in patch:
                    merged[rid][k] = patch[k]
            merged[rid]["layer"] = "personal"
            merged[rid]["overridden"] = True

    # 用户自定义规则
    for c in (custom_rules or []):
        try:
            r = models.validate_rule(c)
        except models.ReviewRuleError as e:
            conflicts.append({"rule_id": c.get("id", "?"), "layers": ["personal"],
                              "message": f"自定义规则无效：{e}"})
            continue
        merged[r.id] = {
            "id": r.id, "name": r.name, "description": r.description,
            "pack_id": r.pack_id, "pack_version": r.pack_version,
            "category": r.category, "engine": r.engine, "scope": r.scope,
            "severity": r.severity, "params": dict(r.params), "fix_mode": r.fix_mode,
            "source": r.source.model_dump() if r.source else None,
            "layer": "personal", "custom": True,
        }

    rules = sorted(merged.values(), key=lambda x: (layer_order[x["layer"]], x["id"]))
    return {"rules": rules, "conflicts": conflicts}


def rules_for_engine(profile: dict, engine: str) -> list[dict]:
    return [r for r in profile["rules"] if r["engine"] == engine and r.get("enabled", True)]


def rules_for_scope(profile: dict, scope: str) -> list[dict]:
    return [r for r in profile["rules"] if r["scope"] == scope and r.get("enabled", True)]
