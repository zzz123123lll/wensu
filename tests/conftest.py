"""pytest 引导：
1. 剔除 bash 会话注入的 Hermes venv 路径（3.11 二进制与 3.12 pytest 不兼容）；
2. 确保 `from app import ...` 解析到本项目。
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 移除 Hermes venv / 其他项目注入的路径，避免解释器版本错配
sys.path = [p for p in sys.path if "hermes-agent" not in p]

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
