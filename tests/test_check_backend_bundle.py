"""Unit tests for check_backend_bundle pollution detection."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_check_module():
    path = ROOT / "scripts" / "check_backend_bundle.py"
    spec = importlib.util.spec_from_file_location("check_backend_bundle", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_check_bundle_rejects_nested_opensquad_build(tmp_path: Path):
    check_bundle = _load_check_module().check_bundle
    run = tmp_path / "run"
    polluted = run / "_internal" / "opensquad" / "build" / "release-new" / "win-unpacked"
    polluted.mkdir(parents=True)
    (polluted / "OpenSquad.exe").write_bytes(b"x" * 10)
    errors = check_bundle(run, max_internal_mb=250.0)
    assert any("opensquad" in e and "build" in e for e in errors)


def test_check_bundle_ok_clean(tmp_path: Path):
    check_bundle = _load_check_module().check_bundle
    run = tmp_path / "run"
    internal = run / "_internal" / "opensquad"
    internal.mkdir(parents=True)
    (internal / "bridge.py").write_text("# ok\n", encoding="utf-8")
    errors = check_bundle(run, max_internal_mb=250.0)
    assert errors == []


def test_check_bundle_rejects_oversize_without_pollution(tmp_path: Path):
    check_bundle = _load_check_module().check_bundle
    run = tmp_path / "run"
    internal = run / "_internal"
    internal.mkdir(parents=True)
    (internal / "fat.bin").write_bytes(b"x" * (3 * 1024 * 1024))
    errors = check_bundle(run, max_internal_mb=1.0)
    assert any("_internal is" in e and "budget" in e for e in errors)
    assert not any("forbidden" in e for e in errors)


def test_default_budget_allows_playwright_ci_size():
    mod = _load_check_module()
    # Linux CI _internal is ~391MB with playwright; nested Electron is ~+400MB.
    assert mod.DEFAULT_MAX_INTERNAL_MB >= 450.0
    assert mod.DEFAULT_MAX_INTERNAL_MB < 800.0
