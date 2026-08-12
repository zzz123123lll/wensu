#!/usr/bin/env bash
HVENV="/c/Users/HZSM/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe"
echo "=== 卸载残留 fastapi/httpx ==="
"$HVENV" -m pip uninstall -y fastapi httpx 2>&1 | grep -iE "successfully uninstalled|not installed" | head -6
echo "=== 残留检查 ==="
ls /c/Users/HZSM/AppData/Local/hermes/hermes-agent/venv/Lib/site-packages/ | grep -iE "fastapi|^httpx|uvicorn" | head -5 || echo "（干净）"
echo "=== Hermes 自检 ==="
"$HVENV" -c "import pydantic, fastapi" 2>&1 | tail -1
