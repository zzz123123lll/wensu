# 测试体系

## 后端（pytest，项目 venv Python 3.12）

```
cd D:\文序项目\ai-writing-system
env -u PYTHONPATH ./.venv/Scripts/python.exe -m pytest -q
```

**当前规模：402 项全部通过（2026-08-14 差距修复批次实测），覆盖率 81%（门禁 ≥80%）**

| 文件 | 覆盖 |
|---|---|
| test_db.py | 迁移幂等/回滚、乐观锁 409、原子保存、FK/WAL |
| test_schemas.py | typed Block、旧 ID 兼容、422 |
| test_api.py | 项目/草稿 CRUD、冲突双份、回收站、版本恢复、导出、安全守卫 |
| test_ai_service.py | 五链路解析、降级链、流式事件、缓存、task→profile |
| test_ai_api.py | AI 端点契约、anchor 回显、空输入 |
| test_settings.py / test_settings_api.py | DPAPI、origin 变化清 Key、scheme 校验 |
| test_citations.py | 证据数据层、越权、orphaned |
| test_safe_fetch.py | SSRF/重定向/大小/剥离 |
| test_copilot.py | 规则引擎可解释/可重复/限频/无模型可用 |
| test_trash_history.py | 回收站级联、历史恢复、导出 |
| test_phase7.py | Ask 隔离/checkpoint、prefs、profiles/bindings |
| test_phase8.py | session token、诊断包、每日备份 |
| test_gateb_p0.py | P0-1~P0-3：正文变化引用失效（API 级）、素材 PATCH、模型连接测试 |
| test_gateb_p1.py | P1-1/P1-3/P1-6：Block 往返、核验状态 422、守卫（PATCH/Origin/session） |
| test_gateb_p0_revision.py | P0-4 统一 Revision 管道 + 事务完整性（冲突/失败回滚） |
| test_gateb_p0_usage.py | P0-6 素材显式使用关系/删除语义/旧数据兼容 |
| test_gateb_export.py | P0-5 统一导出（md/txt/docx/引用附录/文件名安全/不改稿） |
| test_gateb_position.py | P1-5 继续写位置保存/恢复/失效回退 |
| test_gateb_migration.py | v1/v5/v7 升级链、重复迁移、失败回滚、旧数据兼容 |
| test_gateb_backup.py | SQLite backup API 完整备份→恢复→数量与关系校验 |
| test_review_*.py | 成稿检查：规则内核/解析器/确定性/AI/证据/导出/导入/API/敏感词/ai-trace |
| test_search_engines.py | 中文引擎适配器（fixture 解析 + 并发合并/去重/失败可观测，不碰公网） |
| test_wechat_html.py | 公众号 HTML 转换（主题/URL 白名单/转义）与 wechat 导出装配 |
| test_streaming.py | llm.chat_stream（SSE/回退）+ ask/rewrite 流式事件 + API NDJSON |
| test_uploads.py | 图片上传（MIME 白名单/大小/uuid 文件名/静态服务） |
| test_project_export.py | 项目级 ZIP（manifest/文章/素材/来源） |
| test_clip.py | 剪藏（抓取→Source+Material，失败诚实报错） |
| test_p0_humanize_title.py | 去 AI 味（本地痕迹/规则/flavor）与标题评分 |

## 前端（Vitest + Playwright，web/ 下）

```
cd web
npx vitest run          # 单元测试（7 项，fake transport 不碰真实网络）
npx playwright test     # mock E2E（27 项）：系统 Chrome，route mock 快速回归
npx playwright test --config playwright.config.real.js  # 真实后端 E2E（12 项，Gate B E01~E12）
```

**真实后端 E2E（Gate B 验收，全部通过）：**
- 启动真实 FastAPI（127.0.0.1:8770）+ 独立临时 SQLite（WENSU_DB）+ 本地假 LLM（OpenAI 兼容 8899）
- 浏览器调用真实 API；不调用真实模型、不访问公网、不触碰 data/workbench.db
- 覆盖 E01~E12：素材/Ask 保存与插入、Revision、引用建立与自动失效、帮我查不覆盖正文、
  删除影响与解除关系、AI 改写接受/拒绝、网络失败本地可用、重启数据完整、三格式导出、长文位置恢复

| 文件 | 覆盖 |
|---|---|
| tests/unit/fake-api.test.js | fake transport 行为 |
| tests/e2e/smoke.spec.js | 打开→编辑→自动保存、ID 稳定 |
| tests/e2e/autosave.spec.js | 防串稿、Enter 新块、in-flight 合并 |
| tests/e2e/ime-paste.spec.js | IME 不拆块、危险粘贴纯文本化 |
| tests/e2e/responsive.spec.js | 375/768/1280 抽屉 |
| tests/e2e/full-flow.spec.js | 建项目→写→切稿→AI→刷新全链路 |
| tests/e2e/citation.spec.js | 引用落库 + badge 渲染 |
| tests/e2e/undo.spec.js | Ctrl+Z 撤销、选区精确替换 |
| tests/e2e/copilot.spec.js | 规则建议、限频关闭 |
| tests/e2e/phase7.spec.js | 偏好/模型配置设置 |
| tests/e2e-real/gateb.spec.js | Gate B E01~E12 真实后端验收 |

## 真实环境验证（不可用 mock 冒充）

- 浏览器 → 8766 → DeepSeek：改写/洞察/Ask 真调用（用户已配 key，[REDACTED]）
- 流式搜索：NDJSON 首次 8~23s（本机外网受限，web 源不可达时降级 model 知识，source 诚实标注）
- 证据核验：抓取真实来源（如 sqlite.org）→ 证据快照 → LLM 编号引用判断
- 外网可达性随部署环境变化；验证时如实记录"拿到数据/未拿到"

## 官方验证

```
hermes verify --json   # bootstrap PASS / test PASS；readiness FAIL 是 Hermes 引导环境
                       # （uv 3.11 无 fastapi）限制，非项目问题
```
