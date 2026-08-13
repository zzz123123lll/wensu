# 数据迁移记录

迁移框架：`app/db.py` 的 `MIGRATIONS` 列表，幂等应用，`schema_migrations` 表记录已应用版本。
失败版本不记录（可重试），已应用版本保留（回滚安全）。

| 版本 | 内容 |
|---|---|
| v1 | 基础表：projects / articles（blocks_json）/ settings（api_key_enc DPAPI） |
| v2 | articles.version 乐观锁 + 索引 + article_revisions（AI 改动版本日志） |
| v3 | 证据数据层：sources / evidence_snapshots / materials / citations |
| v4 | 回收站：articles.deleted_at / projects.deleted_at（软删除） |
| v5 | Phase 7：article_asks（草稿隔离历史）/ author_prefs（作者记忆）/ model_profiles + task_bindings（多模型） |
| v6 | Phase 8：软删除级联恢复（防孤儿） |
| v7 | 素材标签/元数据（materials.tags / metadata_json）、Ask 元数据、引用核验元数据（6 态） |
| v8 | Gate B：material_usages（素材↔草稿/引用显式关系，不再靠共享 source_id 推断）；article_revisions 契约扩展（before_blocks_json / scope / source_object_type / source_object_id / status）；articles.editor_state_json（继续写位置，本地写作状态） |

## 迁移原则

- 只追加版本，不修改已应用的旧迁移；幂等；失败版本不记录、整体回滚、不破坏已有正文
- 旧数据兼容：无 material_usages 的旧素材显示"未使用"（不伪造关系）；
  旧 Revision 读取得默认空 before 快照；旧非法核验状态原样保留（由 API 校验层暴露，不静默吞）

## 备份

- 每日首次启动自动备份：`data/backups/workbench-YYYYMMDD.db`（`wensu` 启动时，同日跳过）
- 手动备份（sqlite backup API）：
  ```
  env -u PYTHONPATH ./.venv/Scripts/python.exe -c "import sqlite3; s=sqlite3.connect('data/workbench.db'); d=sqlite3.connect('data/backups/workbench-manual.db'); s.backup(d); d.close(); s.close()"
  ```

## 恢复

1. 停止服务
2. 用备份文件覆盖 `data/workbench.db`（或改 DB_PATH 指向备份）
3. 启动 `wensu`（自动执行缺失迁移）

## 回滚

- 代码回滚：git revert / checkout 历史 commit（每 WEN/Phase 独立 commit）
- 数据回滚：升级前自动每日备份保证可回退
