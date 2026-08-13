# 文序 · AI 原生写作系统

作品是中心，人是作者，AI 是围绕作品工作的智能层。

当前状态：实施计划 v0.2 全量完成 + Gate B（可发布闭环版）修复——真存储、真模型、
五条 AI 链路（Ask / 改写 / 洞察 / 搜索 / 核验）、证据引用、规则智能建议、作者记忆、
多模型绑定、回收站/历史/导出、自动备份与安全收口。

## 主要能力

- **写作**：Block 式编辑（H1-H4/列表/代码/图片/分隔线完整往返，不降级 paragraph）、
  自动保存状态机、中文 IME、粘贴白名单、撤销栈、版本历史、回收站、
  统一导出（Markdown / 纯文本 / Word，含引用清单与来源附录）
- **证据链**：来源 → 素材（显式使用关系，不靠共享来源推断）→ 正文主张 → 引用 →
  核验（六态受控）→ 正文变化自动失效复查 → 安全删除/解除关系
- **Revision 管道**：素材插入 / Ask 插入 / AI 改写 / 核验修订 / 冲突恢复 / 版本恢复
  统一记录 before/after 快照，可从版本历史恢复或撤销单次修改
- **继续写**：上次编辑位置（块 + 光标 + 滚动）、最近素材、待处理检查、待复查引用、
  一句可解释的下一步；位置属本地写作状态，不进模型上下文
- **AI 五链路**：Ask（偏好+历史上下文）/ 改写（段下候选，选中即改）/ 洞察（手动触发）/
  搜索（NDJSON 流式+缓存）/ 核验（证据型）
- **成稿检查**：规则包四层 Profile → 确定性检查 → AI 语义 → 证据 → 逐项采用 →
  双版本 Markdown 导出+摘要
- **安全**：本机绑定；Host 校验 + Origin 强制 + session token 三重守卫
  （POST/PUT/PATCH/DELETE 一律要求允许的 Origin 与有效 session，缺失即 403；
  静态页面与健康检查不受限）；DPAPI 加密 Key；每日自动备份；诊断包不含正文/Key

## 环境要求

- Windows 10+
- Python 3.12（项目独立 venv，见下）
- 任意 OpenAI 兼容模型 API（DeepSeek / OpenAI / 通义 / Kimi / 智谱 / 自定义；可多套按任务绑定）

## 安装

```bash
cd D:\文序项目\ai-writing-system
py -3.12 -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
```

## 启动

```bash
wensu            # 或 python -m app.cli；任意工作目录可启动
```

启动时自动：每日备份数据库 → 打开 http://127.0.0.1:8766（仅本机）→ 服务就绪。

右上角 ⚙ 配置模型：API 地址 / 模型名 / API Key（DPAPI 加密存本地，不进网络、不进代码库）。
高级：可添加多套模型并绑定任务（Ask / 改写 / 洞察 / 搜索 / 核验各自用不同模型）。

## 端口与数据

| 项 | 值 |
|----|----|
| 服务地址 | http://127.0.0.1:8766（仅本机；Host/Origin/session 三重防护，写请求缺任一即 403） |
| 数据库 | `data\workbench.db`（SQLite，WAL + FK，版本乐观锁；v8 迁移：Material 显式使用关系 + Revision 契约扩展 + 继续写位置） |
| 备份 | `data\backups\workbench-YYYYMMDD.db`（每日首次启动自动） |
| Key 存储 | DPAPI 加密 BLOB；改 base_url origin 自动清 Key |

## 测试

```bash
# 后端单元 + 集成（313 项，2026-08-13 Gate B 实测全绿）
env -u PYTHONPATH .\.venv\Scripts\python.exe -m pytest -q
# 前端单元（7 项，fake transport 不碰网络）
cd web && npx vitest run
# 浏览器 E2E（23 项，系统 Chrome + route mock 快速回归）
cd web && npx playwright test
# 真实后端 E2E（12 项，Gate B E01~E12：真实 FastAPI + 临时 SQLite + 本地假 LLM）
cd web && npx playwright test --config playwright.config.real.js
```

（`env -u PYTHONPATH` 必须：bash 会话会注入 Hermes venv 路径，污染 3.12 解释器）

## 备份与恢复

```bash
# 每日自动（wensu 启动时）+ 手动：
.venv\Scripts\python.exe -c "import sqlite3; s=sqlite3.connect('data/workbench.db'); b=sqlite3.connect('data/backups/workbench-manual.db'); s.backup(b); b.close(); s.close()"
# 恢复：停服务 → 覆盖 data\workbench.db → 启动（自动补迁移）
```

## 文档

- `docs/architecture.md` 层次与真相源
- `docs/api.md` API 契约
- `docs/data-migrations.md` 迁移/备份/恢复/回滚
- `docs/security.md` 凭据与网络边界
- `docs/testing.md` 测试体系

## 项目结构

```
app/           后端（main / db / settings / llm / ai_service / copilot / safe_fetch / blocks / cli）
app/domains/   领域服务（exports：统一导出装配与渲染）
app/review/    成稿检查（路由/服务/确定性/证据/AI/导出/规则包 packs/*.json）
web/           正式前端（index.html / style.css / js/ 模块化）
tests/         后端测试（313 项）
web/tests/     前端单元（7）+ mock E2E（23）+ 真实后端 E2E（12，Gate B）
docs/          文档
data/          数据库与备份（gitignore）
scripts/       E2E 服务启动器（fake_llm / e2e_app / launcher）
```

## 已知环境注意事项

1. 本机多解释器（uv 3.11 / 系统 3.12 / Hermes venv）：项目命令一律用 `.venv`（3.12）+ `env -u PYTHONPATH`
2. `hermes verify` readiness 阶段 FAIL：verify 用 uv 3.11 引导且无 fastapi，属工具环境限制；test 阶段 PASS
3. 外网选择性受限：Wikipedia/DuckDuckGo 可能不可达，搜索降级为"模型知识线索"（UI 徽章诚实标注）
4. 云端多用户版明确不支持（本地桌面版为唯一支持形态）
