"""ensure_services must reuse only the desired backend (frozen vs source)."""

from __future__ import annotations

import os
import time

from opensquad.cli.runtime_boot import ident_is_source_script, ident_matches_frozen


def test_frozen_match_same_exe_and_fresh_process(tmp_path):
    frozen = tmp_path / "run.exe"
    frozen.write_bytes(b"x")
    ident = {
        "exe": str(frozen),
        "cmdline": [str(frozen), "--service", "gateway"],
        "create_time": time.time(),
    }
    assert ident_matches_frozen(ident, str(frozen)) is True


def test_frozen_reject_stale_process_after_rebuild(tmp_path):
    frozen = tmp_path / "run.exe"
    frozen.write_bytes(b"old")
    old_start = os.path.getmtime(frozen) - 60
    frozen.write_bytes(b"new")
    ident = {
        "exe": str(frozen),
        "cmdline": [str(frozen), "--service", "gateway"],
        "create_time": old_start,
    }
    assert ident_matches_frozen(ident, str(frozen)) is False


def test_frozen_reject_different_exe(tmp_path):
    frozen = tmp_path / "run.exe"
    other = tmp_path / "other.exe"
    frozen.write_bytes(b"a")
    other.write_bytes(b"b")
    ident = {
        "exe": str(other),
        "cmdline": [str(other), "--service", "gateway"],
        "create_time": time.time(),
    }
    assert ident_matches_frozen(ident, str(frozen)) is False


def test_source_gateway_python_run_py():
    ident = {
        "exe": r"C:\Users\adminuser\anaconda3\python.exe",
        "cmdline": [
            r"C:\Users\adminuser\anaconda3\python.exe",
            r"C:\ai_work\pro0\opensquad_deploy_test\src\opensquad\gateway\backend\run.py",
        ],
        "create_time": time.time(),
    }
    assert ident_is_source_script(ident, "run.py") is True
    assert ident_is_source_script(ident, "launcher_main.py") is False


def test_source_reject_frozen_run_exe(tmp_path):
    frozen = tmp_path / "run.exe"
    frozen.write_bytes(b"x")
    ident = {
        "exe": str(frozen),
        "cmdline": [str(frozen), "--service", "gateway"],
        "create_time": time.time(),
    }
    assert ident_is_source_script(ident, "run.py") is False
