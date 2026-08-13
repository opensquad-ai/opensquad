"""Static gate: flag likely read-only path writes in desktop-critical modules.

Catches the "frozen read-only vs writable workspace" bug class that plagues the
PyInstaller desktop build: when the app is installed under Program Files, the
``_internal/`` bundle dir is read-only, so any code that derives a write path
from ``__file__`` / ``_DEFAULT_ROOT`` / ``PROJECT_ROOT`` crashes with
PermissionError. In dev mode (``opensquad start``) the same code works because
the source tree is writable, which is exactly the dev/build inconsistency this
gate exists to prevent.

Usage: uv run python scripts/check_frozen_writable_paths.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Scan the whole source tree that ships in the bundle. The previous version
# only scanned launcher_main.py + gateway/backend/app, which let bugs in
# tools/, utils/, _syscfg/, gateway/plugin_registry/ and
# src/plugins/ slip through (B1/B2/B5/B6/B7 in the v0.4.8 audit).
SCAN_ROOTS = [
    ROOT / "src" / "opensquad",
    ROOT / "src" / "plugins",
]

# Files that are known dead code (shadowed by a routes/ subpackage) and would
# produce noisy false positives. Kept minimal — do not add files here unless
# they are genuinely unreferenced at runtime.
EXCLUDE_FILES = {
    ROOT / "src" / "opensquad" / "gateway" / "backend" / "app" / "ai_web" / "routes.py",
}

# --- Write operations (any of these on a line triggers inspection) ---
# Matches: open(..., "w"/"a"/"wb"/"ab"), .write_text(, os.makedirs(,
# os.mkdir(, Path(...).mkdir(, shutil.copy/copytree/move/rmtree(,
# sqlite3.connect(..., "w"-ish), zf.writestr(, tarfile.add(, etc.
WRITE_PATTERNS = (
    re.compile(r"""open\s*\([^)]*,\s*['"][wa]"""),
    re.compile(r"""open\s*\([^)]*,\s*['"][wa]b"""),
    re.compile(r"""\.write_text\s*\("""),
    re.compile(r"""\.write_bytes\s*\("""),
    re.compile(r"""\.mkdir\s*\("""),
    re.compile(r"""os\.makedirs\s*\("""),
    re.compile(r"""os\.mkdir\s*\("""),
    re.compile(r"""shutil\.(copy|copy2|copyfile|copytree|move|rmtree)\s*\("""),
    re.compile(r"""sqlite3\.connect\s*\("""),
    re.compile(r"""\.writestr\s*\("""),
)

# --- Hints that the write target was derived from the read-only install dir ---
# These are the __file__ / package-root派生 patterns that point at the bundle
# dir in frozen mode. If a write line contains any of these (and no GOOD hint),
# it's almost certainly a frozen-mode crash.
BAD_PATH_HINTS = (
    # Literal __file__ derivation
    "dirname(__file__)",
    "dirname(os.path.abspath(__file__))",
    "dirname(os.path.dirname(os.path.abspath(__file__)))",
    "Path(__file__)",
    "Path(__file__).parent",
    # Module-level constants that resolve to the install dir
    "_DEFAULT_ROOT",
    "_PACKAGE_ROOT",
    "_MODULE_ROOT",
    "_BUILTIN_ROOT",
    # NOTE: "PROJECT_ROOT" and "_project_root" / "_current_dir" are flagged
    # below via a dedicated check, because they are sometimes legitimately
    # workspace-aware (e.g. tools/filesystem.py). We handle them by looking at
    # whether the same line / nearby assignment mentions a GOOD hint.
)

# Constants that are *often* install-dir-derived but sometimes workspace-aware.
# Flagged only when the line does NOT contain a GOOD hint.
MAYBE_BAD_CONSTANTS = (
    "PROJECT_ROOT",
    "_project_root",
    "_current_dir",
    "_PKG_GATEWAY_DIR",
    "BACKEND_DIR",
)

# --- Hints that the write target is a writable, workspace-aware path ---
# If a write line mentions any of these, it's safe regardless of BAD hints.
GOOD_PATH_HINTS = (
    "workspace_",
    "workspace_data_dir",
    "workspace_logs_dir",
    "workspace_uploads_dir",
    "workspace_plugins_dir",
    "workspace_skills_dir",
    "workspace_agents_dir",
    "workspace_sessions_dir",
    "workspace_metadata_dir",
    "get_workspace()",
    "project_root()",  # syscfg.project_root() returns the workspace, NOT install dir
    "OPENSQUAD_APP_DATA",
    "OPENSQUAD_USER_DATA",
    "OPENSQUAD_WORKSPACE",
    # OS temp is always writable
    "tempfile.gettempdir",
    "tempfile.mkdtemp",
    "tempfile.NamedTemporaryFile",
    "tempfile.TemporaryDirectory",
    "gettempdir()",
)


def _line_is_comment_or_docstring(stripped: str) -> bool:
    return stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''")


def _scan_file(path: Path) -> list[str]:
    issues: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{path}: cannot read ({exc})"]
    lines = text.splitlines()
    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if _line_is_comment_or_docstring(stripped):
            continue
        # Must contain a write operation to be interesting
        if not any(p.search(line) for p in WRITE_PATTERNS):
            continue
        # Must contain a BAD hint (literal __file__ derivation or install-root
        # constant) OR a MAYBE_BAD constant without a GOOD hint.
        has_bad = any(h in line for h in BAD_PATH_HINTS)
        has_maybe_bad = any(h in line for h in MAYBE_BAD_CONSTANTS)
        if not has_bad and not has_maybe_bad:
            continue
        has_good = any(h in line for h in GOOD_PATH_HINTS)
        if has_good:
            continue
        issues.append(f"{path.relative_to(ROOT)}:{idx}: {line.strip()}")
    return issues


def main() -> None:
    issues: list[str] = []
    for scan_root in SCAN_ROOTS:
        if not scan_root.exists():
            continue
        paths = scan_root.rglob("*.py")
        for path in paths:
            if path in EXCLUDE_FILES:
                continue
            issues.extend(_scan_file(path))

    if issues:
        print("FAIL: possible frozen read-only path writes detected:")
        print()
        print("These lines derive a write target from __file__ / install-dir")
        print("constants, which crashes with PermissionError under Program Files.")
        print("Use syscfg.workspace_*() or tempfile instead.")
        print()
        for item in issues:
            print(f"  {item}")
        sys.exit(1)

    print("PASS: no obvious frozen writable-path anti-patterns found")


if __name__ == "__main__":
    main()
