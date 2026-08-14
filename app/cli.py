"""命令行入口：`wensu`（或 `python -m app.cli`）从任意工作目录启动本地服务。

- 清理被 bash 会话注入的 Hermes venv 路径（Python 3.11 二进制与本机 3.12 不兼容）
- 每日自动备份数据库（备份目录与 DB 同目录 backups/）
- 端口冲突自动回退（8766 → 8767 → …），并把实际端口加入安全守卫白名单
- 启动后自动打开浏览器（--no-browser 关闭；--port 指定端口，供 smoke/无头使用）
"""

import argparse
import os
import shutil
import socket
import sys
import threading
import webbrowser
from datetime import datetime

from app import db


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="文序 · AI 原生写作系统（本地服务）")
    parser.add_argument("--port", type=int, default=None, help="指定端口（默认 8766，被占用则自动 +1 回退）")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    return parser.parse_args(argv)


def pick_port(start: int = 8766, tries: int = 10) -> int:
    """选一个空闲端口：start 起逐个尝试，全部占用抛 OSError。"""
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise OSError(f"没有可用端口（{start}~{start + tries - 1} 均被占用）")


def backup_db(db_path: str) -> str | None:
    """每日首次启动备份数据库到同目录 backups/；同日已备份或源不存在返回 None。"""
    if not db_path or not os.path.exists(db_path):
        return None
    backups_dir = os.path.join(os.path.dirname(os.path.abspath(db_path)), "backups")
    os.makedirs(backups_dir, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    dest = os.path.join(backups_dir, f"workbench-{today}.db")
    if os.path.exists(dest):
        return None
    shutil.copy2(db_path, dest)
    return dest


def _extra_hosts_origins(port: int) -> None:
    """把实际监听端口加入安全守卫白名单（ALLOWED_HOSTS/ORIGINS 在 app.main 导入时读取）。"""
    for key, values in (
        ("WENSU_EXTRA_ORIGINS", (f"http://127.0.0.1:{port}", f"http://localhost:{port}")),
        ("WENSU_EXTRA_HOSTS", (f"127.0.0.1:{port}", f"localhost:{port}")),
    ):
        existing = [v for v in os.environ.get(key, "").split(",") if v]
        os.environ[key] = ",".join(existing + [v for v in values if v not in existing])


def main(argv=None) -> None:
    args = _parse_args(argv)
    sys.path = [p for p in sys.path if "hermes-agent" not in p]

    port = args.port or pick_port()
    _extra_hosts_origins(port)

    import uvicorn

    from app.main import app

    backup = backup_db(db.DB_PATH)
    if backup:
        print(f"[wensu] 已备份数据库 → {backup}")
    url = f"http://127.0.0.1:{port}"
    print("[wensu] 数据目录：" + os.path.dirname(os.path.abspath(db.DB_PATH)))
    print(f"[wensu] 文序已启动：{url}")
    print("[wensu] 关闭此窗口即退出服务。")
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
