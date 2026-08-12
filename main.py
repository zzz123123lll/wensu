"""uvicorn 入口（`uvicorn main:app`），供 hermes verify 与本地启动使用。

注意：顶部清理被 bash 会话注入的 Hermes venv 路径（Python 3.11 二进制与
本机 3.12 解释器不兼容），确保 fastapi 等从干净的 3.12 环境加载。
"""

import sys

sys.path = [p for p in sys.path if "hermes-agent" not in p]

from app.main import app  # noqa: E402

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8766, reload=True)
