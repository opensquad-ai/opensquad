#!/usr/bin/env python3
"""
Recover work lost by pre-commit's stash-rollback failure.

When ``git commit`` runs with unstaged changes, pre-commit stashes them to
``.cache/pre-commit/patch*`` and restores them with ``git apply`` after the
hooks run. If a hook auto-modified files (ruff --fix / format), the restore
can conflict and pre-commit silently drops the unstaged changes — the work
still exists in the patch file.

Usage (from repo root):
    python scripts/recover_precommit_stash.py            # audit only
    python scripts/recover_precommit_stash.py --apply    # restore missing
    python scripts/recover_precommit_stash.py --apply --all-patches

What it does per patch file:
  - tries ``git apply --check`` against the current tree:
      * applies cleanly  -> those changes are NOT in git yet (lost)
      * conflicts       -> changes already present (committed or restored)
  - ``--apply`` restores the missing files (whole patch, or per-file include)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def git(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["git"] + args, capture_output=True, text=True, cwd=REPO_ROOT)


def cache_dir() -> str:
    return os.environ.get("PRE_COMMIT_HOME", os.path.expanduser("~/.cache/pre-commit"))


def find_patches() -> list[str]:
    d = cache_dir()
    if not os.path.isdir(d):
        return []
    return sorted(
        os.path.join(d, f)
        for f in os.listdir(d)
        if f.startswith("patch") and not f.endswith(".json")
    )


def patch_files(path: str) -> list[str]:
    files = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("diff --git"):
                parts = line.strip().split(" b/", 1)
                if len(parts) == 2:
                    files.append(parts[1])
    return files


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="restore changes that are missing from git")
    ap.add_argument("--all-patches", action="store_true", help="scan old patches too (default: last 20)")
    args = ap.parse_args()

    patches = find_patches()
    if not patches:
        print("[recover] no pre-commit patch files found in", cache_dir())
        return 0
    if not args.all_patches:
        patches = patches[-20:]

    lost_total = 0
    for patch in patches:
        # A patch whose hunks still apply cleanly contains changes NOT in git.
        check = git(["apply", "--check", patch])
        if check.returncode == 0:
            files = patch_files(patch)
            print(f"\n⚠  {os.path.basename(patch)} — {len(files)} file(s) NOT in git:")
            for f in files:
                print(f"    {f}")
            lost_total += len(files)
            if args.apply:
                result = git(["apply", patch])
                if result.returncode == 0:
                    print(f"    ✅ restored from {os.path.basename(patch)}")
                else:
                    print(f"    ❌ apply failed: {result.stderr.splitlines()[:3]}")
                    print("      Try: git apply --3way", patch)
        # else: conflicts -> changes already present, skip

    if lost_total == 0:
        print("\n[recover] OK — no lost changes detected (all patch content is already in git).")
    else:
        print(f"\n[recover] {lost_total} file(s) were missing; "
              + ("restored." if args.apply else "run with --apply to restore."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
