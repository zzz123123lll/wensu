# 架构

## 层次

```
┌─ 作品层 ──────────────────────────────┐
│  web/（index.html + js/ 模块化前端）    │
│  写作区（Block 编辑）/ 左栏项目树 /    │
│  右栏写作助手（洞察/建议/工具坞）       │
└──────────────┬────────────────────────┘
               │ HTTP + NDJSON（同源 8766）
┌──────────────▼────────────────────────┐
│  能力层（FastAPI app/）                │
│  main.py 路由/守卫  ai_service.py AI   │
│  copilot.py 规则引擎  safe_fetch.py    │
│  llm.py 客户端  settings.py 凭据       │
│  blocks.py Block 模型                  │
└──────────────┬────────────────────────┘
               │ sqlite3（WAL + FK）
┌──────────────▼────────────────────────┐
│  数据层（app/db.py + data/workbench.db）│
│  projects/articles/blocks_json        │
│  article_revisions（AI 改动版本）       │
│  sources/evidence_snapshots/materials  │
│  citations  article_asks  author_prefs │
│  model_profiles/task_bindings          │
└───────────────────────────────────────┘
```

## 真相源

- **正文真相源 = 服务端 SQLite**（blocks_json + version 乐观锁）。前端编辑后自动保存，
  服务端是唯一权威；IndexedDB 只做本地恢复副本，不替代服务端。
- **引用真相源 = citations 表**。正文上标 [N] 由服务端数据渲染，不写入 blocks（保存无污染）。
- **版本 = version 乐观锁**。base_version 不匹配返回 409；冲突保留双份（服务端当前版 + 本地恢复副本）。

## 数据流（关键链路）

- 写作：输入 → 事件委托 → 每稿保存状态机（防抖/在途合并）→ PUT /api/articles/{id}（base_version）
- 改写/核验：选区捕获（UTF-16 偏移）→ POST /api/ai/*（带 anchor）→ 候选卡 → 接受 = 可撤销点 → 保存
- 搜索：NDJSON 流式（stage → result）→ web 探测与模型降级并发 → 24h 缓存 → 结果可引用/存素材
- 核验：搜索 → 安全抓取（SSRF 防护）→ 证据快照 → LLM 引用 [N] 判断 → 前端证据卡
- 建议：显式信号（标记/点击/接受）→ 规则引擎过滤"允许的动作" → 建议卡（无模型也可用）

## 安全边界

- 只绑定 127.0.0.1；Host 白名单 + Origin 白名单 + 随机 session token（HttpOnly）
- 抓取必须过 safe_fetch（内网/保留 IP 拦截、重定向逐跳校验、1MB 上限）
- 凭据 DPAPI 加密；origin 变化清 Key；data/ 不进 git
