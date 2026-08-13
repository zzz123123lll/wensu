"""pytest 引导：
1. 剔除 bash 会话注入的 Hermes venv 路径（3.11 二进制与 3.12 pytest 不兼容）；
2. 确保 `from app import ...` 解析到本项目；
3. 安全守卫（P1-6）强化后，TestClient 写请求需带允许的 Origin 与有效 session。
   为保持既有测试集可读性，自动给请求补 Origin；仅当调用方未显式提供任何
   token（cookie 或 X-Wensu-Token 头）时补有效 header token。守卫自身的行为
   测试通过显式传空/错 token、空 Origin 覆盖（setdefault 不覆盖显式值）。
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 移除 Hermes venv / 其他项目注入的路径，避免解释器版本错配
sys.path = [p for p in sys.path if "hermes-agent" not in p]

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fastapi.testclient import TestClient  # noqa: E402

import app.main as main_mod  # noqa: E402

_LOCAL_ORIGIN = "http://127.0.0.1:8766"
_orig_request = TestClient.request


def _patched_request(self, method, url, **kw):
    headers = dict(kw.get("headers") or {})
    headers.setdefault("Origin", _LOCAL_ORIGIN)
    has_cookie = bool(kw.get("cookies"))
    has_token = any(k.lower() == "x-wensu-token" for k in headers)
    if not has_cookie and not has_token:
        headers["X-Wensu-Token"] = main_mod.SESSION_TOKEN
    kw["headers"] = headers
    return _orig_request(self, method, url, **kw)


TestClient.request = _patched_request
