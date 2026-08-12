#!/usr/bin/env bash
# 彻底卸载 hermes venv 里误装的 fastapi/uvicorn/httpx（恢复 Hermes 环境纯净）
HVENV="/c/Users/HZSM/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe"

echo "=== 卸载前检查 ==="
ls /c/Users/HZSM/AppData/Local/hermes/hermes-agent/venv/Lib/site-packages/ | grep -iE "fastapi|uvicorn|^httpx" | head -10

echo "=== 卸载 ==="
"$HVENV" -m pip uninstall -y fastapi uvicorn httpx 2>&1 | grep -iE "successfully|not installed|WARNING" | head -10

echo "=== 卸载后检查 ==="
ls /c/Users/HZSM/AppData/Local/hermes/hermes-agent/venv/Lib/site-packages/ | grep -iE "fastapi|uvicorn|^httpx" | head -5 || echo "（已清空）"

echo "=== Hermes 环境完好性 ==="
"$HVENV" -c "import pydantic; from pydantic_core import SchemaValidator; print('pydantic OK', pydantic.VERSION); import hermes_cli; print('hermes_cli OK')" 2>&1 | tail -3
