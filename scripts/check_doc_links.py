#!/usr/bin/env python3
"""Check relative Markdown links in key documentation paths."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ["doc_en", "doc_cn"]
SCAN_FILES = [
    "README.md",
    "README_ZH.md",
    "CONTRIBUTING.md",
    "CONTRIBUTING_ZH.md",
    "docs/README.md",
    "doc_en/PLUGIN_ECOSYSTEM.md",
    "docs/GITHUB_SETTINGS.md",
    "RELEASING.md",
]
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")

SKIP_PREFIXES = ("http://", "https://", "mailto:", "#", "file://")


def is_external(url: str) -> bool:
    return any(url.startswith(p) for p in SKIP_PREFIXES)


def resolve_link(source: Path, target: str) -> Path:
    if target.startswith("/"):
        return ROOT / target.lstrip("/")
    return (source.parent / target).resolve()


def check_file(path: Path) -> list[str]:
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    for match in LINK_RE.finditer(text):
        raw = match.group(1).strip()
        if not raw or is_external(raw):
            continue
        # Strip optional title in angle brackets for md links
        url = raw.split()[0]
        if url.startswith("<") and url.endswith(">"):
            url = url[1:-1]
        # Strip in-page anchor before testing file existence
        # (e.g. README.md#section -> README.md). External and anchor-only
        # links ("#section") are already skipped via is_external / the
        # empty check below.
        url = url.split("#", 1)[0]
        if not url:
            continue
        resolved = resolve_link(path, url)
        if not resolved.exists():
            errors.append(f"{path.relative_to(ROOT)}: broken link -> {url}")
    return errors


def main() -> int:
    errors: list[str] = []
    for rel in SCAN_FILES:
        p = ROOT / rel
        if p.is_file():
            errors.extend(check_file(p))
    for rel_dir in SCAN_DIRS:
        base = ROOT / rel_dir
        if not base.is_dir():
            continue
        for md in base.rglob("*.md"):
            errors.extend(check_file(md))

    if errors:
        print("Documentation link check failed:")
        for err in errors:
            print(f"  - {err}")
        return 1
    print("Documentation link check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
