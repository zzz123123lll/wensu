"""文序发布门禁（fail-closed）：任何一步失败即退出非零，不产出 manifest。

门禁顺序：
1. 工作树干净（git 有改动时用 --allow-dirty 跳过，供开发自检）
2. ruff check .
3. pytest --cov=app（覆盖率 < 80% 即失败）
4. node --check 全部前端 JS
5. Vitest + Playwright mock E2E
6. 原子写 dist/release-manifest.json（提交哈希/测试计数/时间；pre-release 标记）

用法：
    python scripts/release_gate.py [--pre-release] [--allow-dirty] [--skip-web]
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DIST = ROOT / "dist"

FAIL = "\033[91mFAIL\033[0m"
PASS = "\033[92mPASS\033[0m"


def run(cmd, cwd=None, check=False, shell=False):
    """返回 (returncode, stdout+stderr 尾部)。"""
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", shell=shell)
    out = (proc.stdout or "") + (proc.stderr or "")
    if check and proc.returncode != 0:
        print(f"  {FAIL} 命令失败：{' '.join(cmd)}")
        print(out[-2000:])
        sys.exit(proc.returncode or 1)
    return proc.returncode, out


def git(args, check=True):
    code, out = run(["git", *args], cwd=ROOT, check=check)
    return out.strip()


def git_short_hash():
    try:
        return git(["rev-parse", "--short", "HEAD"])
    except SystemExit:
        return "unknown"


def step(title):
    print(f"[gate] {title} …")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pre-release", action="store_true", help="标记 pre-release（非 tag 提交）")
    parser.add_argument("--allow-dirty", action="store_true", help="工作树有改动时不失败（开发自检）")
    parser.add_argument("--skip-web", action="store_true", help="跳过前端测试（后端快速自检）")
    args = parser.parse_args()

    results = {}

    step("git 工作树")
    if not args.allow_dirty:
        dirty = git(["status", "--porcelain"])
        if dirty:
            print(f"  {FAIL} 工作树有未提交改动：\n{dirty[:500]}")
            sys.exit(1)
    results["git_clean"] = args.allow_dirty
    print(f"  {PASS} git 基线（allow_dirty={args.allow_dirty}）")

    step("ruff check .")
    code, _ = run([sys.executable, "-m", "ruff", "check", "."], cwd=ROOT, check=True)
    results["ruff"] = "pass"
    print(f"  {PASS} ruff 0 error")

    step("pytest --cov=app")
    code, out = run(
        [sys.executable, "-m", "pytest", "-q", "--cov=app", "--cov-report=term"],
        cwd=ROOT, check=True,
    )
    results["pytest"] = "pass"
    print(f"  {PASS} 后端测试全过")

    step("node --check 前端 JS")
    js_files = sorted({p.as_posix() for p in WEB.rglob("*.js") if "node_modules" not in p.parts})
    for f in js_files:
        run(["node", "--check", f], cwd=ROOT, check=True)
    results["js_syntax"] = f"{len(js_files)} files"
    print(f"  {PASS} {len(js_files)} 个 JS 文件语法通过")

    if not args.skip_web:
        step("vitest run")
        run(["npx", "vitest", "run"], cwd=WEB, check=True, shell=True)
        results["vitest"] = "pass"
        print(f"  {PASS} Vitest 通过")

        step("playwright mock E2E")
        run(["npx", "playwright", "test"], cwd=WEB, check=True, shell=True)
        results["playwright"] = "pass"
        print(f"  {PASS} Playwright mock E2E 通过")
    else:
        results["web"] = "skipped"

    DIST.mkdir(exist_ok=True)
    manifest = {
        "product": "文序",
        "commit": git_short_hash(),
        "pre_release": bool(args.pre_release),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }
    target = DIST / "release-manifest.json"
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)
    print(f"[gate] {PASS} 全部通过 → {target}")


if __name__ == "__main__":
    main()
