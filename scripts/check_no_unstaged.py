#!/usr/bin/env python3
"""
Pre-commit guard: reject commits while unstaged changes exist.

Why: pre-commit's ``staged_files_only`` mechanism stashes unstaged changes
before running hooks and restores them afterwards. If a hook auto-modifies
files (ruff --fix / ruff-format), the restore can conflict and pre-commit
SILENTLY drops the unstaged work (it only leaves a patch file in
~/.cache/pre-commit/). We lost four files this way once.

This hook runs FIRST and fails fast when ``git diff`` (unstaged, tracked
files only) is non-empty, so the stash path never triggers. Commit again
after ``git add -A``.

Recovery if work was already lost: python scripts/recover_precommit_stash.py
"""

from __future__ import annotations

import subprocess
import sys


def main() -> int:
    r = subprocess.run(
        ["git", "diff", "--name-only"],
        capture_output=True,
        text=True,
    )
    unstaged = [line for line in (r.stdout or "").splitlines() if line.strip()]
    if not unstaged:
        return 0
    print(
        "\n[pre-commit] Blocked: you have unstaged changes."
        "\n  pre-commit stashes unstaged files during commits; a failed restore"
        "\n  silently loses them. Stage everything first:"
        "\n      git add -A"
        f"\n  ({len(unstaged)} file(s) unstaged, e.g. {unstaged[0]})"
        "\n  If you already lost work: python scripts/recover_precommit_stash.py --apply\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
