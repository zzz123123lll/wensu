"""打包与分发测试：冻结模式数据目录、端口选择、每日备份、启动参数。"""

import os
import socket
import sys
from unittest import mock

import pytest

from app import cli, db, main


# ---------- 冻结模式数据目录 ----------

def test_db_path_dev_mode_is_project_data_dir(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.delenv("WENSU_DB", raising=False)
    path = db._default_db_dir()
    assert path.endswith(os.path.join("ai-writing-system", "data"))


def test_db_path_frozen_mode_uses_appdata(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(os, "environ", {**os.environ, "APPDATA": r"C:\Users\Test\AppData\Roaming"})
    path = db._default_db_dir()
    assert path == os.path.join(r"C:\Users\Test\AppData\Roaming", "Wensu")


def test_db_path_env_override_wins(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(os, "environ", {**os.environ, "APPDATA": r"C:\Users\Test\AppData\Roaming"})
    assert db.resolve_db_path({"WENSU_DB": r"D:\x\y.db"}) == r"D:\x\y.db"
    assert db.resolve_db_path({}) == os.path.join(r"C:\Users\Test\AppData\Roaming", "Wensu", "workbench.db")


def test_uploads_dir_frozen_uses_appdata(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(os, "environ", {**os.environ, "APPDATA": r"C:\Users\Test\AppData\Roaming"})
    assert main._default_uploads_dir() == os.path.join(r"C:\Users\Test\AppData\Roaming", "Wensu", "uploads")


# ---------- 端口选择 ----------

def test_pick_port_free():
    port = cli.pick_port(8800, tries=3)
    assert 8800 <= port < 8803


def test_pick_port_skips_occupied():
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 8805))
    blocker.listen(1)
    try:
        port = cli.pick_port(8805, tries=2)
        assert port == 8806
    finally:
        blocker.close()


def test_pick_port_all_busy_raises():
    blockers = []
    for p in (8810, 8811):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", p))
        s.listen(1)
        blockers.append(s)
    try:
        with pytest.raises(OSError):
            cli.pick_port(8810, tries=2)
    finally:
        for s in blockers:
            s.close()


# ---------- 每日备份 ----------

def test_backup_db_creates_dated_copy(tmp_path):
    src = tmp_path / "workbench.db"
    src.write_bytes(b"data")
    dest = cli.backup_db(str(src))
    assert dest and os.path.exists(dest)
    assert os.path.basename(dest).startswith("workbench-")
    assert os.path.dirname(dest) == str(tmp_path / "backups")
    assert open(dest, "rb").read() == b"data"


def test_backup_db_same_day_skips(tmp_path):
    src = tmp_path / "workbench.db"
    src.write_bytes(b"data")
    first = cli.backup_db(str(src))
    second = cli.backup_db(str(src))
    assert first is not None
    assert second is None


def test_backup_db_missing_source_returns_none(tmp_path):
    assert cli.backup_db(str(tmp_path / "nope.db")) is None


# ---------- 启动参数 ----------

def test_parse_args_no_browser_and_port():
    args = cli._parse_args(["--port", "8899", "--no-browser"])
    assert args.port == 8899
    assert args.no_browser is True


def test_parse_args_defaults():
    args = cli._parse_args([])
    assert args.port is None
    assert args.no_browser is False
