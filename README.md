# 文序 · AI 原生写作系统

作品是中心，人是作者，AI 是围绕作品工作的智能层。

当前状态：本地 MVP（真实化阶段 1-3 完成），可运行最小闭环——真存储、真模型、五条 AI 链路（Ask / 改写 / 洞察 / 搜索 / 核验）。

## 环境要求

- Windows 10+
- Python 3.12（项目独立 venv，见下）
- 任意 OpenAI 兼容模型 API（DeepSeek / OpenAI / 通义 / Kimi / 智谱 / 自定义）

## 安装

```bash
cd D:\ai-writing-system
# 创建项目 venv（3.12）
py -3.12 -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
```

## 启动

```bash
# 方式一：项目入口（推荐，任意工作目录可启动）
python D:\ai-writing-system\main.py
# 方式二：安装后命令
wensu
```

打开 http://127.0.0.1:8766

右上角 ⚙ 配置模型：填 API 地址、模型名、API Key（Key 用 Windows DPAPI 加密存本地，不进网络、不进代码库）。

## 端口与数据

| 项 | 值 |
|----|----|
| 服务地址 | http://127.0.0.1:8766（仅本机） |
| 数据库 | `data\workbench.db`（SQLite：projects / articles / settings） |
| Key 存储 | 数据库 settings 表，DPAPI 加密 BLOB |

## 测试

```bash
env -u PYTHONPATH .\.venv\Scripts\python.exe -m pytest -q
```

（`env -u PYTHONPATH` 必须：bash 会话会注入 Hermes venv 路径，污染 3.12 解释器）

## 备份与恢复

```bash
# 手动备份（SQLite backup API，可在服务运行时执行）
.venv\Scripts\python.exe -c "import sqlite3; s=sqlite3.connect('data/workbench.db'); b=sqlite3.connect('data/backups/workbench-<时间戳>.db'); s.backup(b); b.close(); s.close()"
# 恢复
copy data\backups\workbench-<时间戳>.db data\workbench.db
```

## 项目结构

```
app/           后端（main / db / settings / llm / ai_service / blocks）
web/           正式前端（index.html / style.css / app.js）
prototype/     历史设计样本（不再并行开发）
tests/         后端测试（51 项）
docs/          文档
```

## 已知环境注意事项

1. 本机存在多解释器（uv 3.11 / 系统 3.12 / Hermes venv）：一切项目命令用 `.venv`（3.12），并 `env -u PYTHONPATH`
2. `hermes verify` 的 readiness 阶段会失败：verify 用 uv 3.11 引导且无 fastapi，属已知环境限制；test 阶段正常
3. 外网选择性受限：Wikipedia/DuckDuckGo 可能不可达，搜索自动降级为"模型知识线索"（UI 有徽章标注）
