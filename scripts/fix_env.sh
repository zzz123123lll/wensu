#!/usr/bin/env bash
# 清理误装的包：hermes venv 卸载 fastapi/uvicorn/httpx（pydantic 不动）
HVENV="/c/Users/HZSM/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe"
PY312="/c/Users/HZSM/AppData/Local/Programs/Python/Python312/python.exe"

echo "=== 1. hermes venv 卸载误装包 ==="
"$HVENV" -m pip uninstall -y fastapi uvicorn httpx 2>&1 | grep -E "Successfully uninstalled|not installed" | head -8

echo "=== 2. hermes venv pydantic 完好性 ==="
"$HVENV" -c "import pydantic; from pydantic_core import SchemaValidator; print('OK', pydantic.VERSION)"

echo "=== 3. Python312 修复 pydantic 系并装完整 fastapi ==="
"$PY312" -m pip install -q --force-reinstall pydantic pydantic-core 2>&1 | tail -1
"$PY312" -m pip install -q fastapi "uvicorn" httpx pytest 2>&1 | tail -1
"$PY312" -c "import fastapi, uvicorn, httpx, pytest, pydantic; print('Python312 完整可用: fastapi', fastapi.__version__, '| pydantic', pydantic.VERSION)"

echo "=== 4. 裸 pytest 模拟 hermes verify（PYTHONPATH 污染仍在）==="
cd /d/ai-writing-system
"$PY312" -m pytest -q 2>&1 | tail -3
