"""Assert the PyInstaller backend bundle has no nested desktop build pollution.

Catches the regression where ``collect_data_files("opensquad")`` pulled
``src/opensquad/build/release-*`` (Electron win-unpacked trees, hundreds of MB)
into ``run/_internal/opensquad/build/``.

Usage:
  uv run python scripts/check_backend_bundle.py
  uv run python scripts/check_backend_bundle.py --bundle build/backend-win/run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Soft size budget for _internal after pollution removal (MB). Tuned from a
# clean local build (~70–150MB depending on playwright/plugins); keep headroom.
DEFAULT_MAX_INTERNAL_MB = 250.0

FORBIDDEN_RELATIVE = (Path("opensquad") / "build",)

FORBIDDEN_NAME_MARKERS = (
    "win-unpacked",
    "mac-unpacked",
    "linux-unpacked",
    ".pyinstaller-work",
)


def _dir_size_mb(path: Path) -> float:
    total = 0
    for f in path.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total / (1024 * 1024)


def check_bundle(bundle_dir: Path, max_internal_mb: float) -> list[str]:
    errors: list[str] = []
    if not bundle_dir.is_dir():
        return [f"bundle directory not found: {bundle_dir}"]

    internal = bundle_dir / "_internal"
    if not internal.is_dir():
        # onedir layout should always have _internal
        return [f"missing _internal under {bundle_dir}"]

    for rel in FORBIDDEN_RELATIVE:
        bad = internal / rel
        if bad.exists():
            errors.append(f"forbidden nested build path present: {bad.relative_to(bundle_dir)}")

    for marker in FORBIDDEN_NAME_MARKERS:
        hits = list(internal.rglob(marker))
        # Cap noise
        for hit in hits[:8]:
            errors.append(f"forbidden marker {marker!r} at {hit.relative_to(bundle_dir)}")
        if len(hits) > 8:
            errors.append(f"... and {len(hits) - 8} more paths matching {marker!r}")

    size_mb = _dir_size_mb(internal)
    print(f"[check_backend_bundle] _internal size: {size_mb:.1f} MB (max {max_internal_mb:.0f} MB)")
    if size_mb > max_internal_mb:
        errors.append(
            f"_internal is {size_mb:.1f} MB > budget {max_internal_mb:.0f} MB "
            "(likely nested Electron/release artifacts)"
        )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bundle",
        type=Path,
        default=ROOT / "build" / "backend-win" / "run",
        help="Path to PyInstaller onedir output (contains run.exe and _internal/)",
    )
    parser.add_argument(
        "--max-internal-mb",
        type=float,
        default=DEFAULT_MAX_INTERNAL_MB,
        help="Fail if _internal exceeds this size in MB",
    )
    args = parser.parse_args()
    bundle = args.bundle if args.bundle.is_absolute() else ROOT / args.bundle
    errors = check_bundle(bundle, args.max_internal_mb)
    if errors:
        print("[check_backend_bundle] FAILED:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"[check_backend_bundle] OK: {bundle}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
