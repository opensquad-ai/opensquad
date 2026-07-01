#!/usr/bin/env python3
"""Desktop build Python guards — interpreter must be 3.11; bundle must embed 3.11."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REQUIRED_MAJOR = 3
REQUIRED_MINOR = 11


def check_interpreter() -> int:
    v = sys.version_info
    if v.major != REQUIRED_MAJOR or v.minor != REQUIRED_MINOR:
        print(
            f"ERROR: Desktop builds require Python {REQUIRED_MAJOR}.{REQUIRED_MINOR}.x, "
            f"got {v.major}.{v.minor}.{v.micro}\n"
            f"  executable: {sys.executable}\n"
            f"  Use: uv sync --python 3.11 && uv run --python 3.11 pyinstaller ...",
            file=sys.stderr,
        )
        return 1
    print(f"OK: interpreter Python {v.major}.{v.minor}.{v.micro} ({sys.executable})")
    return 0


def _find_311_markers(dist: Path) -> list[Path]:
    found: list[Path] = []
    for path in dist.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.lower()
        if name == "python311.dll" or name.startswith("libpython3.11"):
            found.append(path)
    return found


def _find_wrong_markers(dist: Path) -> list[Path]:
    wrong: list[Path] = []
    for path in dist.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.lower()
        # python3.dll is a version-agnostic Windows shim — not a version mismatch.
        if name == "python3.dll":
            continue
        if (name.startswith("python3") and name.endswith(".dll") and name != "python311.dll") or (
            name.startswith("libpython3.") and not name.startswith("libpython3.11")
        ):
            wrong.append(path)
    return wrong


def check_bundle(dist_dir: Path) -> int:
    if not dist_dir.is_dir():
        print(f"ERROR: bundle directory not found: {dist_dir}", file=sys.stderr)
        return 1

    wrong = _find_wrong_markers(dist_dir)
    if wrong:
        sample = ", ".join(p.name for p in wrong[:5])
        print(
            f"ERROR: PyInstaller bundle under {dist_dir} embeds wrong Python runtime: {sample}",
            file=sys.stderr,
        )
        return 1

    markers = _find_311_markers(dist_dir)
    if not markers:
        print(
            f"ERROR: No Python 3.11 runtime marker (python311.dll / libpython3.11*) found under {dist_dir}",
            file=sys.stderr,
        )
        return 1

    print(f"OK: bundle uses Python 3.11 ({markers[0].relative_to(dist_dir)})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Python 3.11 for desktop backend builds")
    parser.add_argument(
        "--bundle",
        type=Path,
        help="PyInstaller COLLECT output directory (e.g. build/backend-win/run)",
    )
    args = parser.parse_args()
    if args.bundle:
        return check_bundle(args.bundle.resolve())
    return check_interpreter()


if __name__ == "__main__":
    sys.exit(main())
