"""Static gate: flag likely read-only path writes in desktop-critical modules.

Usage: uv run python scripts/check_frozen_writable_paths.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SCAN_ROOTS = [
    ROOT / "src" / "opensquad" / "launcher_main.py",
    ROOT / "src" / "opensquad" / "gateway" / "backend" / "app",
]

EXCLUDE_FILES = {
    ROOT / "src" / "opensquad" / "gateway" / "backend" / "app" / "ai_web" / "routes.py",
}

WRITE_PATTERNS = (
    re.compile(r"""open\s*\([^)]*,\s*['"][wa]['"]"""),
    re.compile(r"""\.write_text\s*\("""),
    re.compile(r"""os\.makedirs\s*\("""),
    re.compile(r"""shutil\.(copy|copytree|move|rmtree)\s*\("""),
)

BAD_PATH_HINTS = (
    "builtin_resources_dir",
    "get_builtin_root",
    "_BUILTIN_ROOT",
    "_internal",
    "dirname(__file__)",
    "project_root()",
)

GOOD_PATH_HINTS = (
    "workspace_",
    "get_workspace()",
    "OPENSQUAD_APP_DATA",
    "OPENSQUAD_USER_DATA",
)


def _scan_file(path: Path) -> list[str]:
    issues: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{path}: cannot read ({exc})"]
    lines = text.splitlines()
    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        if not any(p.search(line) for p in WRITE_PATTERNS):
            continue
        if not any(h in line for h in BAD_PATH_HINTS):
            continue
        if any(g in line for g in GOOD_PATH_HINTS):
            continue
        issues.append(f"{path.relative_to(ROOT)}:{idx}: {line.strip()}")
    return issues


def main() -> None:
    issues: list[str] = []
    for scan_root in SCAN_ROOTS:
        paths = [scan_root] if scan_root.is_file() else scan_root.rglob("*.py")
        for path in paths:
            if path in EXCLUDE_FILES:
                continue
            issues.extend(_scan_file(path))

    if issues:
        print("FAIL: possible frozen read-only path writes detected:")
        for item in issues:
            print(f"  {item}")
        sys.exit(1)

    print("PASS: no obvious frozen writable-path anti-patterns found")


if __name__ == "__main__":
    main()
