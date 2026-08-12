#!/usr/bin/env bash
echo "=== which pytest ==="
which pytest 2>/dev/null
echo "=== pytest 解释器 ==="
head -1 "$(which pytest)" 2>/dev/null
echo "=== 裸 pytest 的 sys.path（前 12） ==="
cd /d/ai-writing-system
pytest --version 2>&1 | head -2
python -c "import sys; [print(p) for p in sys.path[:12]]"
echo "=== 裸 pytest 收集时 app 从哪来 ==="
cd /d/ai-writing-system && pytest --collect-only -q 2>&1 | head -6
