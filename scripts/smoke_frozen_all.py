"""Run all frozen desktop smoke tests in sequence (fast release gate).

Usage: uv run python scripts/smoke_frozen_all.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SMOKES = [
    "check_frozen_writable_paths.py",
    "smoke_frozen_gateway.py",
    "smoke_model_card_save.py",
    "smoke_role_card_save.py",
    "smoke_skill_upload.py",
    "smoke_frozen_agent.py",
]


def main() -> None:
    failed: list[str] = []
    for name in SMOKES:
        script = ROOT / "scripts" / name
        print(f"\n=== {name} ===")
        rc = subprocess.call([sys.executable, str(script)], cwd=ROOT)
        if rc != 0:
            failed.append(name)
    if failed:
        print(f"\nFAIL: {len(failed)} smoke(s) failed: {', '.join(failed)}")
        sys.exit(1)
    print(f"\nPASS: all {len(SMOKES)} frozen smokes passed")


if __name__ == "__main__":
    main()
