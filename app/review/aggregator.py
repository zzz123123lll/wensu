"""结果聚合：fingerprint 去重、严重度冲突、AI 与确定性冲突处理。

- 同 fingerprint → 保留最高置信度与完整来源
- AI 与确定性同锚点冲突 → 确定性技术事实优先，AI 降为 suggestion 并说明冲突
- 不同规则指向同一机械问题 → 合并一张卡（列全部触发规则）
"""


def aggregate(deterministic_issues: list, ai_issues: list, evidence_issues: list) -> tuple[list, list]:
    """合并三类 issue；返回 (合并列表, 冲突说明)。"""
    conflicts = []
    merged: dict[str, dict] = {}

    for src in ("det", "ai", "ev"):
        for i in (deterministic_issues if src == "det" else ai_issues if src == "ai" else evidence_issues):
            fp = i.get("fingerprint") or f"{i.get('rule_id')}|{i.get('anchor', {}).get('block_id')}"
            if fp in merged:
                prev = merged[fp]
                # AI 与确定性冲突：确定性优先
                if prev.get("source_type") == "system" and i.get("source_type") != "system":
                    i["severity"] = "suggestion"
                    conflicts.append({"fingerprint": fp, "message": "AI 与确定性结果冲突，确定性优先",
                                      "deterministic": prev["rule_id"], "ai": i["rule_id"]})
                    # 保留确定性，AI 降级后仍并入（不同 rule_id）
                    if i["rule_id"] != prev["rule_id"]:
                        merged[fp + "|ai"] = i
                    continue
                if i.get("source_type") == "system" and prev.get("source_type") != "system":
                    conflicts.append({"fingerprint": fp, "message": "AI 与确定性结果冲突，确定性优先",
                                      "deterministic": i["rule_id"], "ai": prev["rule_id"]})
                    merged[fp] = i
                else:
                    # 同类型同指纹：保留高严重度
                    order = {"error": 3, "warning": 2, "suggestion": 1}
                    if order.get(i.get("severity", "suggestion"), 1) > order.get(prev.get("severity", "suggestion"), 1):
                        merged[fp] = i
            else:
                merged[fp] = i

    # 第二遍：AI 与确定性同锚点（同 block + 同起点）冲突 → 确定性优先
    keys = list(merged.keys())
    for a in keys:
        for b in keys:
            if a == b:
                continue
            ia, ib = merged[a], merged[b]
            if ia.get("source_type") == "ai" and ib.get("source_type") == "system":
                if _same_anchor(ia, ib):
                    if ia.get("severity") != "suggestion":
                        ia["severity"] = "suggestion"
                        conflicts.append({"fingerprint": a, "message": "AI 与确定性结果冲突，确定性优先",
                                          "deterministic": ib["rule_id"], "ai": ia["rule_id"]})
            elif ib.get("source_type") == "ai" and ia.get("source_type") == "system":
                if _same_anchor(ia, ib):
                    if ib.get("severity") != "suggestion":
                        ib["severity"] = "suggestion"
                        conflicts.append({"fingerprint": b, "message": "AI 与确定性结果冲突，确定性优先",
                                          "deterministic": ia["rule_id"], "ai": ib["rule_id"]})

    return list(merged.values()), conflicts


def _same_anchor(a: dict, b: dict) -> bool:
    aa, ab = a.get("anchor", {}), b.get("anchor", {})
    if not aa.get("block_id") or aa.get("block_id") != ab.get("block_id"):
        return False
    if aa.get("start_utf16") is None or ab.get("start_utf16") is None:
        return False
    return abs(aa.get("start_utf16", 0) - ab.get("start_utf16", 0)) <= 2
