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
    assert sync_version.read_init_version() == pep440
    assert sync_version.read_package_json_version() == sync_version.pep440_to_npm(pep440)


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
