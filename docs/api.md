# API 契约（同源 http://127.0.0.1:8766）

所有请求需带 `Origin` 为本地源（浏览器自动）；写请求纵深防御校验随机 session token
（启动时 `GET /api/session` 下发 HttpOnly cookie，前端自动携带）。

## 项目/草稿

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /api/projects | 项目列表（不含回收站） |
| POST | /api/projects | 建项目 `{name}` |
| DELETE | /api/projects/{pid} | 软删除（级联草稿入回收站） |
| GET | /api/projects/{pid}/articles | 草稿列表 |
| POST | /api/projects/{pid}/articles | 建草稿 `{title}` |
| GET | /api/articles/{aid} | 草稿全文（blocks + version） |
| PUT | /api/articles/{aid} | 保存 `{blocks, base_version, change_reason}`；版本冲突 409 |
| DELETE | /api/articles/{aid} | 软删除 |
| POST | /api/articles/{aid}/restore | 从回收站恢复 |
| GET | /api/articles/{aid}/revisions | 版本历史 |
| POST | /api/articles/{aid}/revisions/{v}/restore | 恢复历史版本（升新版本） |
| GET | /api/articles/{aid}/export | Markdown 导出 |
| GET | /api/articles/{aid}/asks | Ask 历史（按草稿隔离） |

## AI 链路（任务可绑定不同模型 profile）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /api/ai/ask | `{prompt, context, article_id}` → 注入偏好+历史；存 Ask 历史 |
| POST | /api/ai/rewrite | `{text, anchor...}` → 候选列表 + model |
| POST | /api/ai/insight | `{title, blocks}` → 洞察 + 建议 |
| POST | /api/ai/search | `{query, stream, anchor}`；stream=true 返回 NDJSON：`{"type":"stage"}` → `{"type":"result"}` |
| POST | /api/ai/check | `{claim, anchor}` → 三态核验 + evidence 列表 |

## 证据数据层

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | /api/projects/{pid}/sources | 来源列表/创建（同 URL 复用） |
| GET/POST | /api/projects/{pid}/materials | 素材 |
| GET/POST | /api/articles/{aid}/citations | 引用（含 orphaned 机械检查） |
| DELETE | /api/citations/{cid} | 删除引用 |
| POST | /api/projects/{pid}/fetch | 安全抓取 URL → evidence snapshot |

## 写作智能 / 记忆 / 模型

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /api/signals | 行为信号（tool_click/accept/reject/mark/draft_open） |
| POST | /api/copilot/suggest | 规则建议（无模型可用） |
| GET/POST | /api/prefs | 作者偏好（透明可删） |
| DELETE | /api/prefs/{key} | 删除偏好 |
| GET/POST | /api/profiles | 多模型配置 |
| DELETE | /api/profiles/{pid} | 删除（连带清绑定） |
| PUT | /api/bindings | `{task, profile_id}`（ask/rewrite/insight/search_synthesis/check） |
| POST | /api/profiles/{pid}/test | 连接测试（不存内容） |

## 成稿检查（review）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /api/review/packs | 规则包列表（内置 8 个：common/观点长文/公众号/知乎/头条/博客/学术/报告） |
| PUT | /api/review/rules/{id} | 规则 override（params/severity/enabled/fix_mode） |
| DELETE | /api/review/rules/{id} | 删除 override = 恢复默认 |
| POST | /api/review/custom-rules | 新增自定义规则（全 schema 校验） |
| POST | /api/review/rules/import | 两阶段导入·预览（返回 token，不安装） |
| POST | /api/review/rules/import/confirm | 两阶段导入·确认安装（10 分钟有效） |
| POST | /api/reviews | 创建检查 session（快照+确定性同步返回） |
| GET | /api/reviews/{id}/stream | NDJSON 流式：stage→issue*→done（AI 语义/证据阶段） |
| POST | /api/reviews/{id}/issues/{iid}/accept | 逐项采用（主稿走保存 revision/渠道建补丁） |
| POST | /api/reviews/{id}/issues/{iid}/ignore | 忽略（fingerprint 跳过后续） |
| POST | /api/reviews/{id}/recheck | 复检（新 session） |
| POST | /api/reviews/{id}/exports | 双版本导出（通用/渠道 + 摘要 manifest） |
| GET | /api/review-exports/{eid}/{kind} | 下载 general/channel Markdown 或 report 摘要 |

阶段顺序：prepare → format（确定性）→ content（AI 语义）→ evidence（证据）→ done；
AI/证据失败不阻塞，warning 事件告知；stream 重试幂等（同 fingerprint 不重复）。

## 系统

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /api/health | 健康检查 |
| GET | /api/session | 下发随机 session token（HttpOnly） |
| GET | /api/diagnostics | 诊断包（无 key/正文/prompt） |
| GET/PUT | /api/settings | 默认模型配置（DPAPI 加密；origin 变化清 Key） |
