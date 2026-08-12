#!/usr/bin/env bash
# 修复 Hermes venv 的 pydantic/pydantic_core（损坏于误装 fastapi 时）
HVENV="/c/Users/HZSM/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe"

echo "=== 1. ensurepip ==="
"$HVENV" -m ensurepip --upgrade 2>&1 | tail -2

echo "=== 2. 重装 pydantic 2.13.4 + 配套 pydantic-core ==="
"$HVENV" -m pip install -q --force-reinstall "pydantic==2.13.4" 2>&1 | tail -2
"$HVENV" -m pip install -q --force-reinstall pydantic-core 2>&1 | tail -2

echo "=== 3. 验证 ==="
"$HVENV" -c "
import pydantic
from pydantic_core import SchemaValidator
print('pydantic', pydantic.VERSION, '| pydantic_core 可用')
import importlib.metadata as md
print('pydantic-core', md.version('pydantic-core'))
" 2>&1 | tail -4
