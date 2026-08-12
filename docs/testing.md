# 测试体系

## 后端（pytest，项目 venv Python 3.12）

```
cd D:\ai-writing-system
env -u PYTHONPATH ./.venv/Scripts/python.exe -m pytest -q
```

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

## 前端（Vitest + Playwright，web/ 下）

```
cd web
npx vitest run          # fake transport 单元测试（不碰真实网络）
npx playwright test     # E2E：系统 Chrome，route mock
```

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
