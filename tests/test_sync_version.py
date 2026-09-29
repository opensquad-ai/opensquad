"""Tests for scripts/sync_version.py version propagation helpers."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = ROOT / "scripts" / "sync_version.py"


def _load_sync_module():
    spec = importlib.util.spec_from_file_location("sync_version", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def sync_version():
    return _load_sync_module()


@pytest.mark.parametrize(
    ("pep440", "npm"),
    [
        ("0.4.1", "0.4.1"),
        ("0.4.2.dev0", "0.4.2-dev.0"),
        ("0.4.1a1", "0.4.1-alpha.1"),
        ("0.4.1b2", "0.4.1-beta.2"),
        ("0.4.1rc3", "0.4.1-rc.3"),
        ("0.4.0.post1", "0.4.0-post.1"),
    ],
)
def test_pep440_to_npm(sync_version, pep440, npm):
    assert sync_version.pep440_to_npm(pep440) == npm


def test_repo_version_files_match_pyproject(sync_version):
    pep440 = sync_version.read_pyproject_version()
    npm = sync_version.pep440_to_npm(pep440)
    assert sync_version.read_init_version() == pep440
    assert sync_version.read_package_json_version() == npm
    assert sync_version.read_package_json_version(sync_version.NEXUSCHAT_PACKAGE_JSON) == npm


@pytest.mark.parametrize(
    "lock_path_attr",
    ["PACKAGE_LOCK", "NEXUSCHAT_PACKAGE_LOCK"],
)
def test_package_lock_roots_match_pyproject(sync_version, lock_path_attr):
    """Both locks record the app version twice; a stale root is a silent drift."""
    import json

    npm = sync_version.pep440_to_npm(sync_version.read_pyproject_version())
    lock_path = getattr(sync_version, lock_path_attr)
    data = json.loads(lock_path.read_text(encoding="utf-8"))
    assert data["version"] == npm, lock_path
    assert data["packages"][""]["version"] == npm, lock_path


def test_sync_version_check_passes_from_repo_root():
    import subprocess

    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


# ── release tags ────────────────────────────────────────────────────────────
# release.yml publishes every `v*` tag, test builds (alpha/beta/rc) included, and
# validates the tag through `--check-tag`. The tag carries the npm spelling
# (`v0.8.49-alpha.1`) while pyproject.toml carries the PEP 440 one (`0.8.49a1`).
@pytest.mark.parametrize(
    ("pep440", "tag"),
    [
        ("0.8.49", "v0.8.49"),
        ("0.8.49a1", "v0.8.49-alpha.1"),
        ("0.8.49b2", "v0.8.49-beta.2"),
        ("0.8.49rc1", "v0.8.49-rc.1"),
    ],
)
def test_check_tag_accepts_the_matching_tag(sync_version, monkeypatch, pep440, tag):
    monkeypatch.setattr(sync_version, "read_pyproject_version", lambda: pep440)
    assert sync_version.check_tag(tag) == pep440


@pytest.mark.parametrize(
    "tag",
    [
        "v0.8.48",  # one release behind
        "v0.8.49-alpha.2",  # wrong counter
        "v0.8.49alpha1",  # PEP 440 spelling in the tag
    ],
)
def test_check_tag_rejects_a_mismatch(sync_version, monkeypatch, tag):
    monkeypatch.setattr(sync_version, "read_pyproject_version", lambda: "0.8.49a1")
    with pytest.raises(SystemExit):
        sync_version.check_tag(tag)


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("0.8.49", False),
        ("0.8.49a1", True),
        ("0.8.49-alpha.1", True),
        ("0.8.49b2", True),
        ("0.8.49rc1", True),
        ("0.8.49.dev0", False),  # the dev branch is never tagged
        ("0.8.49-dev.0", False),
    ],
)
def test_is_prerelease(sync_version, version, expected):
    assert sync_version.is_prerelease(version) is expected


def test_check_tag_cli_rejects_a_stale_tag():
    import subprocess

    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--check-tag", "v0.0.1"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "does not match" in result.stderr or "::error::Tag" in result.stderr
