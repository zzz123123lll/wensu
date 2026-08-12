#!/usr/bin/env bash
# 装精确匹配的 pydantic-core 2.46.4（pydantic 2.13.4 的要求）
HVENV="/c/Users/HZSM/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe"

"$HVENV" -m pip install -q --force-reinstall "pydantic-core==2.46.4" 2>&1 | grep -viE "notice|upgrade pip" | tail -2

echo "=== 验证 ==="
"$HVENV" -c "
import pydantic
from pydantic_core import SchemaValidator
print('pydantic', pydantic.VERSION, '| pydantic_core 完整可用')
import hermes_cli
print('hermes_cli 可加载')
" 2>&1 | tail -4
