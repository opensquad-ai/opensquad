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
        # Windows: python311.dll
        if name == "python311.dll":
            found.append(path)
            continue
        # Linux / macOS dylib: libpython3.11.so.1.0, libpython3.11.dylib
        if name.startswith("libpython3.11"):
            found.append(path)
            continue
        # macOS framework: the shared library is just "Python" inside
        # Versions/3.11/ or Python.framework/Versions/3.11/
        if name == "python" and "3.11" in str(path).replace("\\", "/").lower():
            found.append(path)
            continue
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
        # Fallback: some macOS/Linux PyInstaller builds statically link Python
        # into the run binary, so no separate .dylib/.so exists. In that case
        # the interpreter check (run as a separate CI step) already verified
        # 3.11; the presence of base_library.zip confirms PyInstaller ran.
        base_lib = dist_dir / "base_library.zip"
        run_bin = dist_dir / "run"
        if base_lib.exists() and run_bin.exists():
            print(
                "OK: no standalone runtime marker found, but base_library.zip + run binary present "
                "(Python likely statically linked). Interpreter check verifies 3.11 separately."
            )
            return 0
        print(
            f"ERROR: No Python 3.11 runtime marker (python311.dll / libpython3.11* / framework Python) "
            f"found under {dist_dir}",
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
