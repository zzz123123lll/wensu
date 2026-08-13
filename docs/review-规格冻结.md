# 成稿检查与渠道化 · 设计规格冻结（v1.0）

> 来源：`文序-个人自用完善与渠道化成稿检查-完整设计方案-v1.0.docx`（D-01~D-08 用户已确认，D-09~D-11 设计冻结）
> 冻结日期：2026-08-13。实施以本文为准；改动需走决策记录。

## 实施状态（2026-08-13 全部完成）

| Phase | 内容 | 状态 | 验证 |
|---|---|---|---|
| 0 | 设计基线冻结 | ✅ | docs/review-规格冻结.md |
| 1 | 规则内核（Rule schema 校验/危险输入拒绝/pack_loader/resolver 四层） | ✅ | test_review_models 14 + resolver 5 |
| 2 | Session/Issue/面板（迁移 v6、repository/service/routes、NDJSON、定位） | ✅ | test_review_api 11 + E2E review 1 |
| 3 | 渠道变体+双导出（stale gate/引用渲染/文件名安全/摘要） | ✅ | test_review_exporter 12 + API 2 + E2E 导出 |
| 4 | AI 语义+证据（结构化 JSON 校验/锚点核对/待核实/聚合冲突/流式） | ✅ | test_review_ai 11 |
| 5 | 内置规则包 8 个（渠道经验建议诚实标注，来源门禁） | ✅ | test_review_packs 11 |
| 6 | 规则编辑/两阶段导入/恢复默认 | ✅ | test_review_import 7 |
| 7 | 真实文章验收（1020 字观点长文全流程）+ heading2-4 层级修复 | ✅ | scripts/acceptance_*.py |

**验收记录**：真实观点长文（16 blocks / 1020 字，含空标题与事实主张）→ 检查 session
确定性 2 条（空标题/重复字）+ 证据 4 条（市场规模等主张标待核实）→ 通用版 Markdown
导出（1068 字符，标题层级正确）+ 摘要 manifest（version/hash/状态）。
AI 语义 0 条为模型输出不合规被 schema 丢弃（诚实降级，不伪造结果）。

**已知边界（如实）**：渠道包为经验建议级（official 规则需用户核验官方文档后导入，
来源门禁强制 url+verified_at）；AI 语义检查受模型输出质量影响（缺字段丢弃有 warning）。

1. **主稿/变体分离**：通用与文章类型修复可进入主稿（走既有保存/撤销/版本）；渠道专属修复只进入渠道变体补丁层（不写 blocks_json）。通用版导出不得含渠道补丁。
2. **逐项确认**：任何正文或变体修改必须逐项采用；没有"一键接受全部"。AI 未经确认写入正文 = 0。
3. **快照运行**：一次检查针对不可变快照（article_version + blocks + citations + hash + profile），运行中草稿变化不干扰结果。
4. **失效即停止**：锚点/原文不匹配 → stale，禁止采用/导出，要求复检；不做模糊替换。
5. **来源可见**：规则、Issue、导出摘要都显示 pack_version 与 source_type。无官方来源的渠道建议不得写成"平台要求"；来源不可达显示"未完成核验"。
6. **规则优先**：能用确定性规则判断的不调用模型；AI 只做语义补充。确定性与 AI 冲突时确定性技术事实优先。
7. **本地安全**：正文/覆盖/检查记录/导出清单全本地；诊断不含正文/Prompt/Key；规则抓取走 safe_fetch。

## Rule schema（Pydantic 校验，拒绝：未知字段/重复 ID/危险 URL/非法正则/超限参数/未知 engine）

```json
{
  "id": "common.heading.order",       // 全局稳定，命名空间.名
  "name": "标题层级顺序",
  "description": "标题不得跳级",
  "pack_id": "common-markdown",
  "pack_version": "1.0.0",
  "category": "format|language|content|evidence|channel",
  "engine": "deterministic|ai|evidence",
  "scope": "master|variant",
  "severity": "error|warning|suggestion",
  "enabled": true,
  "params": {},
  "source": {"type": "system|official|experience|user|ai", "title": "", "url": "https...", "verified_at": "ISO-8601"},
  "fix_mode": "exact_patch|candidate|advisory"
}
```

## 四层 Profile（冲突解析：个人 > 发布目标 > 文章类型 > 通用）

最终 Profile = 通用基础 + 文章类型 + 发布目标 + 个人覆盖。同优先级互斥 → 显示冲突要求用户决定，不静默选。

## 数据表（迁移 v6，只追加不改 v1-v5）

| 表 | 用途 | 关键字段 |
|---|---|---|
| review_rule_overrides | 用户对内置规则的覆盖 | rule_id, patch_json, updated_at |
| review_custom_rules | 用户自定义规则 | id, rule_json, enabled, created_at |
| review_sessions | 不可变检查快照与运行状态 | article_id, article_version, blocks_json, citations_json, snapshot_hash, profile_json, status |
| review_issues | 标准化检查结果及处理状态 | review_id, fingerprint, rule_id, severity, anchor_json, suggestion_json, state |
| review_variant_patches | 渠道专属已确认非破坏补丁 | review_id, target, block_id, selection_json, original_hash, replacement, status |
| review_exports | 导出清单 | review_id, article_version, target, manifest_json, created_at |

## 状态机

```
Session: draft → queued → running → completed / cancelled / failed（failed 保留已完成确定性结果）
Issue:   open → accepted | ignored | suppressed | stale
Patch:   proposed → active → stale | removed
```

## API（Host/Origin/Session Token 边界沿用）

| 方法 | 路径 | 职责 |
|---|---|---|
| GET | /api/review/packs | 列出规则包、版本、来源、更新状态 |
| GET/PUT | /api/review/rules/{rule_id} | 读取/更新用户 override（不改内置包） |
| POST | /api/review/rules/import | 校验并预览导入差异（二阶段 token） |
| POST | /api/reviews | 保存草稿并创建快照 → review_id |
| GET | /api/reviews/{id}/stream | NDJSON：stage / issue / warning / done |
| GET | /api/reviews/{id} | Session、Profile、Issue 汇总、状态 |
| POST | /api/reviews/{id}/issues/{iid}/accept | 确认主稿候选或激活渠道补丁 |
| POST | /api/reviews/{id}/issues/{iid}/ignore | 忽略/本次抑制 |
| POST | /api/reviews/{id}/recheck | 基于当前文章+同一 Profile 建新 Session |
| POST | /api/reviews/{id}/exports | 生成通用/渠道 Markdown + 摘要 manifest |
| GET | /api/review-exports/{id}/{kind} | 下载 general / channel / report / bundle |

## VariantPatch

```
{ id, review_id, target: wechat|zhihu|toutiao|blog|..., rule_id, block_id,
  selection: { start_utf16, end_utf16, original_text }, original_hash, replacement,
  status: active|stale|dismissed, confirmed_at }
```

## 文件结构（不膨胀 main.py / db.py / app.js）

```
app/review/
  models.py pack_loader.py resolver.py deterministic.py ai_checker.py
  evidence_checker.py aggregator.py repository.py service.py exporter.py
  routes.py packs/*.json
web/js/review/
  api.js launcher.js panel.js anchors.js variants.js rules.js export.js
tests/test_review_*.py
web/tests/e2e/review*.spec.js
```

## 关键 E2E 场景（Phase 验收基线）

1. 观点长文 + 真实引用 → 运行"观点长文 + 微信公众号 + 我的规则"
2. 采用通用语言修复 → 主稿 version+1 且 Ctrl+Z 可恢复
3. 采用公众号专属建议 → 主稿不变、渠道预览变
4. 编辑被渠道补丁引用的原句 → 补丁 stale 且导出要求确认
5. 复检后重新采用 → 双导出：通用版无渠道改动，渠道版含
6. 摘要含规则版本/来源/未处理项/双文件 hash
7. 模型超时 → 确定性结果仍可用、失败可重试、无假结果
8. 导入恶意规则包（脚本/危险 URL/路径穿越）→ 拒绝且安全显示

## 执行纪律

每 Phase：先写失败测试 → 实现 → 全量回归（143 后端 + 24 前端基线不得下降）→ 独立 commit → 代码审查。
真实环境：模型真实调用；规则来源人工核验；mock 不得冒充真实网络/模型。
