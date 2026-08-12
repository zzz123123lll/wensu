"""命令行入口：`wensu`（或 `python -m app.cli`）从任意工作目录启动本地服务。

负责清理被 bash 会话注入的 Hermes venv 路径（Python 3.11 二进制与
本机 3.12 解释器不兼容），再以 127.0.0.1:8766 启动。
"""

import sys


def main() -> None:
    sys.path = [p for p in sys.path if "hermes-agent" not in p]
    import uvicorn

    from app.main import app

    uvicorn.run(app, host="127.0.0.1", port=8766)


if __name__ == "__main__":
    main()
