"""写作智能 v0.2：规则优先的建议引擎。

原则：
- 规则先过滤"此刻允许的动作"，可解释、可测试、无模型可用；
- 模型只用于在已允许动作内生成候选/排序（Phase 7 接入）；
- 低置信隐式信号（停顿/删改）不自动触发任何动作；
- 拒绝/关闭的建议限频（dismissed 集合，30 分钟不重复）。

建议 schema（复用旧 Copilot 结构）：
{id, type, priority, title, description, target_block_id, actions, source, reason, confidence}
"""

import time
import uuid

# 问题类型（手动标记/信号推导）
ISSUES = ("expression", "facts", "structure", "ideas", "tone", "none")

# 阶段（由信号推导）
STAGES = ("incubating", "writing", "revising", "checking", "unknown")

DISMISS_TTL = 30 * 60  # 秒：拒绝后同类建议限频


class CopilotEngine:
    """每请求实例：规则决策 + 限频过滤。"""

    def __init__(self, dismissed: dict[str, float] | None = None):
        self.dismissed = dismissed or {}  # rule_key -> dismiss_ts

    def suggest(self, ctx: dict) -> list[dict]:
        """ctx: {stage, issue, focus, block_id, block_text, article_id, model_configured}"""
        allowed = []
        issue = ctx.get("issue") or "none"
        focus = ctx.get("focus") or "article"
        block_id = ctx.get("block_id")
        model_ok = ctx.get("model_configured", False)

        # ---- 规则表：问题 × 焦点 → 允许的动作（可解释） ----
        if issue == "expression" and focus in ("block", "selection"):
            allowed.append(self._sug(
                type="rewrite", priority="high", title="改写这段",
                description="表达不顺时，让 AI 给出几种说法，选中部分只改选中。",
                reason="你标记了「表达不顺」", block_id=block_id,
                confidence="high", source="rule",
            ))

        if issue == "facts" and focus in ("block", "selection", "article"):
            allowed.append(self._sug(
                type="search", priority="high", title="查证资料",
                description="为这一段找可靠来源，可插入引用或存入素材。",
                reason="你标记了「需要资料」", block_id=block_id,
                confidence="high", source="rule",
            ))
            allowed.append(self._sug(
                type="check", priority="medium", title="核验这段陈述",
                description="对段落里的主张做证据型核验，结论附来源。",
                reason="你标记了「需要资料」，先核验已有内容更稳妥", block_id=block_id,
                confidence="medium", source="rule",
            ))

        if issue == "structure" and focus in ("article", "project"):
            allowed.append(self._sug(
                type="structure", priority="medium", title="梳理全文结构",
                description="让 AI 读全文，指出段落的组织问题与建议。",
                reason="你标记了「需要结构」", block_id=None,
                confidence="medium", source="rule",
            ))

        if issue == "ideas" and focus in ("article", "project", "block"):
            allowed.append(self._sug(
                type="ask", priority="medium", title="展开讨论想法",
                description="把当前内容作为上下文，和 AI 讨论下一步怎么写。",
                reason="你标记了「需要观点/思路」", block_id=block_id,
                confidence="medium", source="rule",
            ))

        if issue == "tone" and focus in ("block", "selection"):
            allowed.append(self._sug(
                type="rewrite", priority="medium", title="调整语气",
                description="保持原意，换成更合适这篇作品的口吻。",
                reason="你标记了「语气不对」", block_id=block_id,
                confidence="medium", source="rule",
            ))

        # 无标记但有可解释信号的默认建议（低打扰：只在高置信场景出现）
        if issue == "none" and model_ok and ctx.get("stage") == "revising" and focus == "block" and block_id:
            allowed.append(self._sug(
                type="check", priority="low", title="核验这段主张",
                description="刚改过这段，跑一次证据核验更稳妥。",
                reason="刚接受过修改（修改阶段）", block_id=block_id,
                confidence="low", source="rule",
            ))

        # 限频：被拒绝过的规则 key 在 TTL 内不重复
        out = []
        for s in allowed:
            key = s["type"] + ":" + str(s.get("target_block_id") or s.get("reason", ""))
            if key in self.dismissed and time.time() - self.dismissed[key] < DISMISS_TTL:
                continue
            out.append(s)
        return out

    def _sug(self, **kw) -> dict:
        block_id = kw.pop("block_id", None)
        d = {
            "id": uuid.uuid4().hex[:12],
            "priority": kw.pop("priority", "medium"),
            "actions": ["run", "dismiss"],
            "source": kw.pop("source", "rule"),
            **kw,
        }
        if block_id:
            d["target_block_id"] = block_id
        return d


def signals_to_state(signals: list[dict]) -> dict:
    """信号 → 状态（阶段/问题/焦点）。纯规则推导，可测试。"""
    stage = "unknown"
    issue = "none"
    focus = "article"
    for s in reversed(signals):  # 最近信号优先
        t = s.get("type")
        if t == "tool_click":
            tool = s.get("tool")
            if tool == "rewrite":
                stage = "revising"
                issue = "expression"
                focus = s.get("focus", "block")
            elif tool == "search":
                stage = "checking"
                issue = "facts"
                focus = s.get("focus", "block")
            elif tool == "check":
                stage = "checking"
                issue = "facts"
                focus = s.get("focus", "block")
            elif tool == "ask":
                stage = "incubating"
                issue = "ideas"
                focus = s.get("focus", "article")
        elif t == "accept":
            stage = "revising"
            issue = "expression"
            focus = s.get("focus", "block")
        elif t == "reject":
            stage = "revising"
        elif t == "mark":
            issue = s.get("issue", issue)
            focus = s.get("focus") or ("block" if s.get("block_id") else focus)
            stage = "revising" if issue in ("expression", "tone") else ("checking" if issue == "facts" else stage)
        elif t == "draft_open":
            # 只表达"打开了草稿"这个阶段事实；不覆盖更新信号（mark/tool_click）已定的 focus/issue
            stage = "incubating" if not s.get("blocks_count") else "writing"
    return {"stage": stage, "issue": issue, "focus": focus}


# ---------- 信号内存存储（Phase 6：按草稿隔离，最多 50 条/稿；Phase 7 落盘） ----------

_signals_store: dict[int, list[dict]] = {}
_dismissed_store: dict[int, dict[str, float]] = {}


def record_signal(article_id: int, signal: dict) -> None:
    lst = _signals_store.setdefault(article_id, [])
    lst.append(signal)
    if len(lst) > 50:
        del lst[0]


def get_signals(article_id: int) -> list[dict]:
    return _signals_store.get(article_id, [])


def mark_dismissed(article_id: int, key: str) -> None:
    _dismissed_store.setdefault(article_id, {})[key] = time.time()


def get_dismissed(article_id: int) -> dict[str, float]:
    return _dismissed_store.get(article_id, {})
