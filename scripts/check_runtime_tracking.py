#!/usr/bin/env python3
"""Fail if runtime or local-only files are tracked by git.

This guard prevents accidental commits of local configuration, runtime data,
and transient DB files.
"""

from __future__ import annotations

import fnmatch
import subprocess
import sys


DISALLOWED_PATTERNS = [
    "system_config.json",
    "system_config.gateway.json",
    "src/system_config.json",
    "src/system_config.gateway.json",
    "src/opensquad/gateway/nexuschat-pro/config.json",
    "data/plugins/token_analytics/*.db-shm",
    "data/plugins/token_analytics/*.db-wal",
    "data/logs/*",
    "data/sessions/*",
    "data/ai_his_talk/*",
    "src/data/logs/*",
    "src/data/sessions/*",
    "src/data/ai_his_talk/*",
]


def git_ls_files() -> list[str]:
    out = subprocess.check_output(["git", "ls-files"], text=True)
    return [line.strip() for line in out.splitlines() if line.strip()]


def main() -> int:
    tracked = git_ls_files()
    offenders: list[str] = []

    for path in tracked:
        for pattern in DISALLOWED_PATTERNS:
            if fnmatch.fnmatch(path, pattern):
                offenders.append(path)
                break

    if offenders:
        print("[guard] Disallowed tracked files detected:")
        for item in sorted(set(offenders)):
            print(f"  - {item}")
        print("\n[guard] Remove them from index (git rm --cached) and keep them ignored.")
        return 1

    print("[guard] OK: no disallowed runtime/local files are tracked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
