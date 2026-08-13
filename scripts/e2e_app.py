"""真实后端 E2E 启动器：独立临时 SQLite + 允许 E2E 端口 + 启动 uvicorn。

- WENSU_DB：临时库路径（由 playwright webServer env 注入）
- WENSU_EXTRA_HOSTS/ORIGINS：允许 127.0.0.1:8770
- 不触碰 data/workbench.db；退出后临时目录由 playwright 清理
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("WENSU_EXTRA_HOSTS", "127.0.0.1:8770")
os.environ.setdefault("WENSU_EXTRA_ORIGINS", "http://127.0.0.1:8770")

# 确保临时库存在并完成迁移（首次启动时）
from app import db  # noqa: E402

if os.environ.get("WENSU_DB"):
    os.makedirs(os.path.dirname(os.environ["WENSU_DB"]), exist_ok=True)
    conn = db.connect()
    db.migrate(conn)
    conn.close()

from app.main import app  # noqa: E402

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8770, log_level="warning")
