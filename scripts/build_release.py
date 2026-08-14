"""文序构建脚本（构建诊断/门禁内部调用；构建成功不代表发布通过）。

正式发布唯一入口为 `python scripts/release_gate.py --build`。
本脚本可直接用于开发诊断：`python scripts/build_release.py [--skip-tests] [--skip-smoke]`
流程：pytest → PyInstaller 构建 → 产物凭据扫描 → 真实 smoke（临时库 + 临时端口 + health）。
"""

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
EXE = DIST / "Wensu" / "Wensu.exe"
FORBIDDEN_NAMES = {".env", "settings.json", "credentials.bin", "api_config.json"}


def _run(cmd, cwd=None):
    subprocess.run(cmd, cwd=cwd or ROOT, check=True)


def run_tests():
    _run([sys.executable, "-m", "pytest", "-q"])


def build_exe():
    _run([sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", "Wensu.spec"])


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_health(port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
                return r.status == 200
        except Exception:
            time.sleep(0.5)
    return False


def smoke(exe_path: Path, tmp_dir: Path) -> dict:
    """真实启动 exe（临时库 + 随机端口 + 不开浏览器）→ health 200 → 终止。"""
    port = _free_port()
    env = {**os.environ, "WENSU_DB": str(tmp_dir / "smoke.db"), "WENSU_UPLOADS_DIR": str(tmp_dir / "uploads")}
    proc = subprocess.Popen(
        [str(exe_path), "--port", str(port), "--no-browser"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
    )
    ok = False
    try:
        ok = _wait_health(port)
        if ok:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
                home = r.read(2000).decode("utf-8", "ignore")
            ok = "文序" in home
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    out = proc.stdout.read() if proc.stdout else ""
    return {"smoke_ok": bool(ok), "port": port, "log_tail": out[-500:]}


def scan_forbidden_files() -> list[str]:
    found = []
    if DIST.exists():
        for path in DIST.rglob("*"):
            if path.name in FORBIDDEN_NAMES or path.name.startswith("credential-") or path.suffix in (".db", ".sqlite"):
                found.append(str(path))
    return found


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_installer(version: str) -> Path | None:
    """Inno Setup 安装包（本机无 iscc 时跳过，便携目录仍可用）。"""
    iscc = os.environ.get("INNO_SETUP_ISCC") or (
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe")
    )
    if not os.path.exists(iscc):
        print("BUILD_WARN: 未找到 Inno Setup ISCC.exe，跳过安装包（dist/Wensu 便携目录可用）")
        return None
    out_dir = DIST / "installer"
    out_dir.mkdir(parents=True, exist_ok=True)
    _run([iscc, f"/DMyAppVersion={version}", str(ROOT / "installer" / "wensu.iss")])
    setup = out_dir / f"Wensu-Setup-v{version}.exe"
    return setup if setup.exists() else None


def main():
    parser = argparse.ArgumentParser(description="Build and smoke-test the Wensu release")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-smoke", action="store_true")
    parser.add_argument("--with-installer", action="store_true")
    args = parser.parse_args()

    if not args.skip_tests:
        print("[build] pytest …")
        run_tests()
    print("[build] PyInstaller …")
    build_exe()
    if not EXE.exists():
        print("RELEASE_BUILD_FAILED: 未找到 " + str(EXE))
        sys.exit(1)

    forbidden = scan_forbidden_files()
    if forbidden:
        print("RELEASE_BUILD_FAILED: dist 内发现禁止文件（凭据/数据库）")
        for p in forbidden:
            print("  " + p)
        sys.exit(1)

    info = {"exe_sha256": sha256(EXE)}
    if not args.skip_smoke:
        print("[build] smoke（临时库 + 随机端口）…")
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            smoke_result = smoke(EXE, Path(td))
        info.update(smoke_result)
        if not smoke_result["smoke_ok"]:
            print("RELEASE_BUILD_FAILED: smoke 未通过")
            print(smoke_result["log_tail"])
            sys.exit(1)
        print(f"[build] smoke OK（port {smoke_result['port']}）")

    if args.with_installer:
        import tomllib
        with open(ROOT / "pyproject.toml", "rb") as f:
            version = tomllib.load(f)["project"]["version"]
        setup = build_installer(version)
        if setup:
            info["installer"] = setup.name
            info["installer_sha256"] = sha256(setup)
            print(f"[build] 安装包 OK：{setup}")

    (DIST / "build-info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print("RELEASE_BUILD_OK")


if __name__ == "__main__":
    main()
