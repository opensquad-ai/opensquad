"""Run all frozen desktop smoke tests in sequence (fast release gate).

Usage: uv run python scripts/smoke_frozen_all.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Hard gate: path-resolution + writable-workspace smokes. These must pass on
# every build — they do not need a live agent runtime, only the module import +
# env-var-driven workspace resolution, so they run cleanly on a fresh CI runner.
HARD_SMOKES = [
    "check_frozen_writable_paths.py",
    "smoke_install_skill.py",  # B1: install_skill git-clone target
    "smoke_im_uploads_path.py",  # B2: IM /uploads/ path resolution
    "smoke_long_memory_config.py",  # B3/B4: long_memory config read path
    "smoke_plugin_registry_db.py",  # B6: plugin registry DB write path
    "smoke_frozen_gateway.py",  # gateway starts + /health ready
    "smoke_model_card_save.py",  # model card write path
    "smoke_role_card_save.py",  # role card write path
    "smoke_skill_upload.py",  # skill upload write path
]

# Soft gate: agent runtime smokes. These need the embed Python runtime which a
# fresh GitHub runner does not have, so they are run last and a single failure
# is reported but does not block the path-resolution hard gate. See the v0.4.8
# handover (CI #28571960660): agent smoke fails on fresh runners because the
# embed runtime is absent, not because of a code regression.
SOFT_SMOKES = [
    "smoke_frozen_agent.py",
]


def main() -> None:
    failed: list[str] = []
    soft_failed: list[str] = []

    for name in HARD_SMOKES:
        script = ROOT / "scripts" / name
        print(f"\n=== {name} (hard gate) ===")
        rc = subprocess.call([sys.executable, str(script)], cwd=ROOT)
        if rc != 0:
            failed.append(name)

    for name in SOFT_SMOKES:
        script = ROOT / "scripts" / name
        print(f"\n=== {name} (soft gate) ===")
        rc = subprocess.call([sys.executable, str(script)], cwd=ROOT)
        if rc != 0:
            soft_failed.append(name)

    print("\n" + "=" * 60)
    if failed:
        print(f"FAIL: {len(failed)} hard-gate smoke(s) failed: {', '.join(failed)}")
        sys.exit(1)
    if soft_failed:
        print(f"WARN: {len(soft_failed)} soft-gate smoke(s) failed: {', '.join(soft_failed)}")
        print("      (agent runtime smokes need embed Python; non-blocking on CI)")
    print(f"PASS: all {len(HARD_SMOKES)} hard-gate frozen smokes passed")


if __name__ == "__main__":
    main()
