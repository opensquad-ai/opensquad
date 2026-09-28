#!/usr/bin/env python3
"""
Sync project version from pyproject.toml (single source of truth).

Updates:
  - src/opensquad/__init__.py  (__version__, PEP 440 — same as pyproject.toml)
  - package.json               (npm semver — converted for pre-release markers)
  - package-lock.json          (same npm version, so the lock cannot drift)
  - src/opensquad/gateway/nexuschat-pro/package.json       (Electron app version)
  - src/opensquad/gateway/nexuschat-pro/package-lock.json  (lock root version)

Usage:
  python scripts/sync_version.py          # write synced files
  python scripts/sync_version.py --check  # exit 1 if anything would change
  python scripts/sync_version.py --check-tag v0.8.49-alpha.1   # release tag ↔ pyproject.toml
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
INIT_PY = ROOT / "src" / "opensquad" / "__init__.py"
PACKAGE_JSON = ROOT / "package.json"
PACKAGE_LOCK = ROOT / "package-lock.json"
NEXUSCHAT_PACKAGE_JSON = ROOT / "src" / "opensquad" / "gateway" / "nexuschat-pro" / "package.json"
NEXUSCHAT_PACKAGE_LOCK = ROOT / "src" / "opensquad" / "gateway" / "nexuschat-pro" / "package-lock.json"

_VERSION_LINE = re.compile(r'^(__version__\s*=\s*)["\'][^"\']+["\']', re.MULTILINE)


def read_pyproject_version() -> str:
    with PYPROJECT.open("rb") as f:
        data = tomllib.load(f)
    version = data.get("project", {}).get("version")
    if not version or not isinstance(version, str):
        raise SystemExit(f"::error::Missing [project].version in {PYPROJECT}")
    return version


def pep440_to_npm(pep440: str) -> str:
    """Map PEP 440 version strings to npm-compatible semver for package.json."""
    patterns = (
        (r"^(\d+\.\d+\.\d+)\.dev(\d+)$", r"\1-dev.\2"),
        (r"^(\d+\.\d+\.\d+)a(\d+)$", r"\1-alpha.\2"),
        (r"^(\d+\.\d+\.\d+)b(\d+)$", r"\1-beta.\2"),
        (r"^(\d+\.\d+\.\d+)rc(\d+)$", r"\1-rc.\2"),
        (r"^(\d+\.\d+\.\d+)\.post(\d+)$", r"\1-post.\2"),
    )
    for pattern, repl in patterns:
        if re.fullmatch(pattern, pep440):
            return re.sub(pattern, repl, pep440)
    return pep440


def check_tag(tag: str) -> str:
    """Validate a release tag against pyproject.toml; return the PEP 440 version.

    Tags carry the npm/semver spelling (``v0.8.49-alpha.1``) while
    pyproject.toml carries the PEP 440 one (``0.8.49a1``) — ``pep440_to_npm``
    above is the only mapping, so both sides are checked through it instead of
    by string surgery. Stable tags match themselves (``v0.8.49``).
    """
    pep440 = read_pyproject_version()
    expected_npm = pep440_to_npm(pep440)
    tag_version = tag.strip().lstrip("v")
    if tag_version != expected_npm:
        raise SystemExit(
            f"::error::Tag {tag!r} is version {tag_version!r}, but pyproject.toml has "
            f"{pep440!r} (npm spelling {expected_npm!r}). Bump [project].version and run "
            f"`python scripts/sync_version.py` before tagging."
        )
    print(
        f"Tag check passed: {tag} == pyproject.toml {pep440} (npm {expected_npm}, prerelease={is_prerelease(pep440)})"
    )
    return pep440


def is_prerelease(version: str) -> bool:
    """True for the alpha/beta/rc test-build spellings, npm or PEP 440.

    Covers both ``0.8.49a1`` and ``0.8.49-alpha.1``. ``.devN`` is deliberately
    not counted: the dev branch is never tagged, and a test tag must not be
    mistaken for one.
    """
    return bool(re.search(r"[-_.]?(?:alpha|beta|rc|a|b)[-_.]?\d*$", version.strip().lstrip("v").lower()))


def read_init_version() -> str | None:
    text = INIT_PY.read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    return match.group(1) if match else None


def read_package_json_version(path: Path = PACKAGE_JSON) -> str | None:
    data = json.loads(path.read_text(encoding="utf-8"))
    version = data.get("version")
    return version if isinstance(version, str) else None


def render_init_py(pep440: str) -> str:
    text = INIT_PY.read_text(encoding="utf-8")
    if not _VERSION_LINE.search(text):
        raise SystemExit(f"::error::Could not find __version__ assignment in {INIT_PY}")
    return _VERSION_LINE.sub(f'__version__ = "{pep440}"', text, count=1)


def render_package_json(path: Path, npm_version: str) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = npm_version
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def render_package_lock(path: Path, npm_version: str) -> str:
    """The lock records the root version twice: top-level and under packages[""]."""
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = npm_version
    root = data.get("packages", {}).get("")
    if root is None:
        raise SystemExit(f"::error::No root entry in {path}")
    root["version"] = npm_version
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def compute_targets() -> tuple[str, str, str, str, str, str, str]:
    pep440 = read_pyproject_version()
    npm = pep440_to_npm(pep440)
    return (
        pep440,
        npm,
        render_init_py(pep440),
        render_package_json(PACKAGE_JSON, npm),
        render_package_json(NEXUSCHAT_PACKAGE_JSON, npm),
        render_package_lock(PACKAGE_LOCK, npm),
        render_package_lock(NEXUSCHAT_PACKAGE_LOCK, npm),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify version files match pyproject.toml; do not write.",
    )
    parser.add_argument(
        "--check-tag",
        metavar="TAG",
        help="Verify a release tag (npm spelling, e.g. v0.8.49-alpha.1) matches pyproject.toml.",
    )
    args = parser.parse_args()

    if args.check_tag:
        check_tag(args.check_tag)
        return 0

    pep440, npm, init_content, pkg_content, nexus_content, lock_content, nexus_lock_content = compute_targets()
    init_current = INIT_PY.read_text(encoding="utf-8")
    pkg_current = PACKAGE_JSON.read_text(encoding="utf-8")
    nexus_current = NEXUSCHAT_PACKAGE_JSON.read_text(encoding="utf-8")
    lock_current = PACKAGE_LOCK.read_text(encoding="utf-8")
    nexus_lock_current = NEXUSCHAT_PACKAGE_LOCK.read_text(encoding="utf-8")

    drift: list[str] = []
    if init_current != init_content:
        drift.append(f"{INIT_PY.relative_to(ROOT)} (__version__ should be {pep440!r})")
    if pkg_current != pkg_content:
        drift.append(f"{PACKAGE_JSON.relative_to(ROOT)} (version should be {npm!r})")
    if nexus_current != nexus_content:
        drift.append(f"{NEXUSCHAT_PACKAGE_JSON.relative_to(ROOT)} (version should be {npm!r})")
    if lock_current != lock_content:
        drift.append(f"{PACKAGE_LOCK.relative_to(ROOT)} (version should be {npm!r})")
    if nexus_lock_current != nexus_lock_content:
        drift.append(f"{NEXUSCHAT_PACKAGE_LOCK.relative_to(ROOT)} (version should be {npm!r})")

    if args.check:
        if drift:
            print("Version drift detected (run: python scripts/sync_version.py):", file=sys.stderr)
            for item in drift:
                print(f"  - {item}", file=sys.stderr)
            return 1
        print(
            f"Version sync OK: pyproject.toml={pep440!r}, package.json={npm!r}, "
            f"package-lock.json={npm!r}, nexuschat-pro/package.json={npm!r}, "
            f"nexuschat-pro/package-lock.json={npm!r}"
        )
        return 0

    if not drift:
        print(f"Already in sync: {pep440!r} (npm {npm!r})")
        return 0

    INIT_PY.write_text(init_content, encoding="utf-8", newline="\n")
    PACKAGE_JSON.write_text(pkg_content, encoding="utf-8", newline="\n")
    NEXUSCHAT_PACKAGE_JSON.write_text(nexus_content, encoding="utf-8", newline="\n")
    PACKAGE_LOCK.write_text(lock_content, encoding="utf-8", newline="\n")
    NEXUSCHAT_PACKAGE_LOCK.write_text(nexus_lock_content, encoding="utf-8", newline="\n")
    print(f"Synced version {pep440!r} -> {INIT_PY.relative_to(ROOT)}")
    print(f"Synced npm version {npm!r} -> {PACKAGE_JSON.relative_to(ROOT)}")
    print(f"Synced npm version {npm!r} -> {PACKAGE_LOCK.relative_to(ROOT)}")
    print(f"Synced npm version {npm!r} -> {NEXUSCHAT_PACKAGE_JSON.relative_to(ROOT)}")
    print(f"Synced npm version {npm!r} -> {NEXUSCHAT_PACKAGE_LOCK.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
