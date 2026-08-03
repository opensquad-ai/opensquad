#!/usr/bin/env python3
"""
Install a local pre-commit wrapper that rejects commits with unstaged changes
BEFORE the pre-commit framework runs.

Why: the pre-commit framework stashes unstaged files before running any hook
(staged_files_only), so an in-config guard hook can never see them. If a hook
then auto-modifies files, the stash restore can conflict and pre-commit
silently drops the unstaged work (recoverable via
scripts/recover_precommit_stash.py). This wrapper runs FIRST — before
pre-commit — and fails fast on unstaged changes.

Installs to .git/hooks/pre-commit and preserves the original as
.git/hooks/pre-commit.precommit-backup (restore with --uninstall).

Usage:
    python scripts/install_commit_guard.py            # install
    python scripts/install_commit_guard.py --uninstall  # restore original
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO_ROOT, ".git", "hooks", "pre-commit")
BACKUP = HOOK + ".precommit-backup"

WRAPPER = """#!/usr/bin/env bash
# Installed by scripts/install_commit_guard.py — rejects commits while
# unstaged changes exist, then delegates to the pre-commit framework.
set -euo pipefail

UNSTAGED=$(git diff --name-only)
if [ -n "$UNSTAGED" ]; then
  echo
  echo "[commit-guard] Blocked: you have unstaged changes."
  echo "  pre-commit stashes unstaged files during commits; a failed restore"
  echo "  silently loses them. Stage everything first:"
  echo "      git add -A"
  echo "  ($(echo "$UNSTAGED" | wc -l | tr -d ' ') file(s) unstaged, e.g. $(echo "$UNSTAGED" | head -1))"
  echo "  Already lost work? python scripts/recover_precommit_stash.py --apply"
  echo
  exit 1
fi

# Run the pre-commit framework hooks (the original installed hook).
if [ -x "$(dirname "$0")/pre-commit.precommit-backup" ]; then
  exec "$(dirname "$0")/pre-commit.precommit-backup" "$@"
fi
exec pre-commit run --hook-stage pre-commit "$@"
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--uninstall", action="store_true", help="restore the original hook")
    args = ap.parse_args()

    if args.uninstall:
        if os.path.exists(BACKUP):
            shutil.move(BACKUP, HOOK)
            print("[commit-guard] restored original pre-commit hook")
        elif os.path.exists(HOOK):
            os.remove(HOOK)
            print("[commit-guard] removed wrapper hook (no backup found)")
        else:
            print("[commit-guard] no hook installed")
        return 0

    if not os.path.isdir(os.path.join(REPO_ROOT, ".git", "hooks")):
        print("[commit-guard] not a git repo with .git/hooks", file=sys.stderr)
        return 1

    if os.path.exists(HOOK):
        if "commit-guard" in open(HOOK, encoding="utf-8", errors="replace").read():
            print("[commit-guard] already installed")
            return 0
        shutil.copy2(HOOK, BACKUP)
        print(f"[commit-guard] backed up existing hook to {BACKUP}")

    with open(HOOK, "w", encoding="utf-8", newline="\n") as f:
        f.write(WRAPPER)
    try:
        os.chmod(HOOK, 0o755)
    except OSError:
        pass
    print("[commit-guard] installed wrapper (checks unstaged, then runs pre-commit)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
