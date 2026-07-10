#!/usr/bin/env python3
"""
Sync project version from pyproject.toml (single source of truth).

Updates:
  - src/opensquad/__init__.py  (__version__, PEP 440 — same as pyproject.toml)
  - package.json               (npm semver — converted for pre-release markers)
  - src/opensquad/gateway/nexuschat-pro/package.json  (Electron app version)

Usage:
  python scripts/sync_version.py          # write synced files
  python scripts/sync_version.py --check  # exit 1 if anything would change
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
NEXUSCHAT_PACKAGE_JSON = ROOT / "src" / "opensquad" / "gateway" / "nexuschat-pro" / "package.json"

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


def compute_targets() -> tuple[str, str, str, str, str]:
    pep440 = read_pyproject_version()
    npm = pep440_to_npm(pep440)
    return (
        pep440,
        npm,
        render_init_py(pep440),
        render_package_json(PACKAGE_JSON, npm),
        render_package_json(NEXUSCHAT_PACKAGE_JSON, npm),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify version files match pyproject.toml; do not write.",
    )
    args = parser.parse_args()

    pep440, npm, init_content, pkg_content, nexus_content = compute_targets()
    init_current = INIT_PY.read_text(encoding="utf-8")
    pkg_current = PACKAGE_JSON.read_text(encoding="utf-8")
    nexus_current = NEXUSCHAT_PACKAGE_JSON.read_text(encoding="utf-8")

    drift: list[str] = []
    if init_current != init_content:
        drift.append(f"{INIT_PY.relative_to(ROOT)} (__version__ should be {pep440!r})")
    if pkg_current != pkg_content:
        drift.append(f"{PACKAGE_JSON.relative_to(ROOT)} (version should be {npm!r})")
    if nexus_current != nexus_content:
        drift.append(f"{NEXUSCHAT_PACKAGE_JSON.relative_to(ROOT)} (version should be {npm!r})")

    if args.check:
        if drift:
            print("Version drift detected (run: python scripts/sync_version.py):", file=sys.stderr)
            for item in drift:
                print(f"  - {item}", file=sys.stderr)
            return 1
        print(f"Version sync OK: pyproject.toml={pep440!r}, package.json={npm!r}, nexuschat-pro/package.json={npm!r}")
        return 0

    if not drift:
        print(f"Already in sync: {pep440!r} (npm {npm!r})")
        return 0

    INIT_PY.write_text(init_content, encoding="utf-8", newline="\n")
    PACKAGE_JSON.write_text(pkg_content, encoding="utf-8", newline="\n")
    NEXUSCHAT_PACKAGE_JSON.write_text(nexus_content, encoding="utf-8", newline="\n")
    print(f"Synced version {pep440!r} -> {INIT_PY.relative_to(ROOT)}")
    print(f"Synced npm version {npm!r} -> {PACKAGE_JSON.relative_to(ROOT)}")
    print(f"Synced npm version {npm!r} -> {NEXUSCHAT_PACKAGE_JSON.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
