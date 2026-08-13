"""命令行入口：`wensu`（或 `python -m app.cli`）从任意工作目录启动本地服务。

负责清理被 bash 会话注入的 Hermes venv 路径（Python 3.11 二进制与
本机 3.12 解释器不兼容），每日自动备份数据库，再以 127.0.0.1:8766
启动并自动打开浏览器。
"""

import os
import shutil
import sys
import threading
import webbrowser
from datetime import datetime


def _daily_backup() -> str | None:
    """每日首次启动备份数据库到 data/backups/（同日已备份则跳过）。"""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_path = os.path.join(project_root, "data", "workbench.db")
    if not os.path.exists(db_path):
        return None
    backups_dir = os.path.join(project_root, "data", "backups")
    os.makedirs(backups_dir, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    dest = os.path.join(backups_dir, f"workbench-{today}.db")
    if os.path.exists(dest):
        return None
    shutil.copy2(db_path, dest)
    return dest


def main() -> None:
    sys.path = [p for p in sys.path if "hermes-agent" not in p]
    import uvicorn

    from app.main import app

    backup = _daily_backup()
    if backup:
        print(f"[wensu] 已备份数据库 → {backup}")
    print("[wensu] 文序已启动：http://127.0.0.1:8766")
    threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:8766")).start()
    uvicorn.run(app, host="127.0.0.1", port=8766)


if __name__ == "__main__":
    main()
