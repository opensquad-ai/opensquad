#!/usr/bin/env python3
"""Fail when repo-wide mypy errors exceed the recorded budget.

Why a budget instead of a clean run
-----------------------------------
``mypy src/opensquad/`` currently reports a large amount of pre-existing type
debt. Making the check unconditionally blocking would turn ``ci.yml`` red for
every PR and the job would simply be re-marked ``continue-on-error`` — which is
exactly how the gate was lost in the first place.

Instead we record the current error count in ``scripts/mypy-baseline.txt`` and
fail only when it *grows*. The debt can then be paid down in small PRs, and
each PR that removes errors is expected to lower the baseline.

Usage
-----
    mypy src/opensquad/ --ignore-missing-imports --warn-unused-ignores \\
        2>&1 | tee mypy-out.txt
    python scripts/check_mypy_budget.py mypy-out.txt

    # re-baseline after a mypy / Python version bump
    python scripts/check_mypy_budget.py mypy-out.txt --update

Exit codes: 0 = within budget, 1 = budget exceeded or output unreadable.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_FILE = Path(__file__).resolve().parent / "mypy-baseline.txt"

# mypy prints one of:
#   Found 1310 errors in 204 files (checked 745 source files)
#   Success: no issues found in 745 source files
COUNT_RE = re.compile(r"Found (\d+) error")
SUCCESS_RE = re.compile(r"^Success:", re.MULTILINE)


def parse_error_count(text: str) -> int | None:
    """Return the mypy error count, or None when the output is not mypy output."""
    if SUCCESS_RE.search(text):
        return 0
    match = COUNT_RE.search(text)
    if match:
        return int(match.group(1))
    return None


def read_baseline() -> int:
    if not BASELINE_FILE.is_file():
        raise SystemExit(f"error: baseline file missing: {BASELINE_FILE}")
    raw = BASELINE_FILE.read_text(encoding="utf-8").strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"error: {BASELINE_FILE} must contain a single integer, got {raw!r}") from exc


def write_baseline(count: int) -> None:
    BASELINE_FILE.write_text(f"{count}\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mypy_output", help="file containing captured mypy output")
    parser.add_argument(
        "--update",
        action="store_true",
        help="overwrite the baseline with the observed count instead of failing",
    )
    args = parser.parse_args()

    out_path = Path(args.mypy_output)
    if not out_path.is_file():
        print(f"error: mypy output not found: {out_path}", file=sys.stderr)
        return 1

    report = out_path.read_text(encoding="utf-8", errors="replace")
    current = parse_error_count(report)

    if current is None:
        # A crash / config error prints neither "Found N errors" nor "Success".
        print("error: could not parse a mypy error count from the output.", file=sys.stderr)
        print("--- last 20 lines ---", file=sys.stderr)
        print("\n".join(report.splitlines()[-20:]), file=sys.stderr)
        return 1

    if args.update:
        write_baseline(current)
        print(f"baseline updated to {current} errors -> {BASELINE_FILE.relative_to(ROOT)}")
        return 0

    baseline = read_baseline()
    delta = current - baseline

    if delta > 0:
        print(f"mypy error budget exceeded: {current} errors vs baseline {baseline} (+{delta}).")
        print(f"Fix {delta} error(s), or re-baseline with: python {Path(__file__).name} {out_path.name} --update")
        return 1

    print(f"mypy error budget OK: {current} errors vs baseline {baseline}.")
    if delta < 0:
        print(
            f"{abs(delta)} error(s) were fixed — lower the baseline to keep the ratchet tight:\n"
            f"  python {Path(__file__).name} {out_path.name} --update"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
