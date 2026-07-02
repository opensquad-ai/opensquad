"""Smoke test: install_skill git-clone target resolves to writable workspace.

Verifies the B1 fix (agent_setup.py _git_installs_dir): in frozen mode the
git-clone destination must be <workspace>/skills/_git/, never the read-only
install dir. This is a *path-resolution* smoke — it does not actually clone a
repo (that would require network + a live agent runtime), it just asserts the
resolved directory is under the workspace and is creatable/writable.

Usage: uv run python scripts/smoke_install_skill.py
"""

from __future__ import annotations

import os
import sys
import tempfile

# Simulate the Electron desktop env: OPENSQUAD_USER_DATA = writable userData dir.
APP_DATA = os.path.join(tempfile.gettempdir(), "opensquad_smoke_install_skill")
os.makedirs(APP_DATA, exist_ok=True)

os.environ["OPENSQUAD_APP_DATA"] = APP_DATA
os.environ["OPENSQUAD_USER_DATA"] = APP_DATA

# Import after env is set so _resolve_initial_workspace() picks it up.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from opensquad.system_config import syscfg  # noqa: E402
from opensquad.tools import agent_setup  # noqa: E402


def main() -> None:
    git_dir = agent_setup._git_installs_dir()
    workspace = os.path.abspath(syscfg.get_workspace())
    git_dir_abs = os.path.abspath(git_dir)

    # 1. Must be under the workspace, not the install dir.
    if not git_dir_abs.startswith(workspace + os.sep) and git_dir_abs != workspace:
        print(f"FAIL: _git_installs_dir() returned {git_dir}")
        print(f"      expected under workspace {workspace}")
        sys.exit(1)

    # 2. Must be creatable + writable (the whole point of the fix).
    os.makedirs(git_dir_abs, exist_ok=True)
    probe = os.path.join(git_dir_abs, ".write_probe")
    try:
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
    except OSError as e:
        print(f"FAIL: cannot write to {git_dir_abs}: {e}")
        sys.exit(1)

    # 3. Must NOT be under the install dir (builtin root).
    builtin = os.path.abspath(syscfg.get_builtin_root())
    if git_dir_abs.startswith(builtin + os.sep) or git_dir_abs == builtin:
        print(f"FAIL: _git_installs_dir() landed in read-only install dir: {git_dir_abs}")
        print(f"      install dir = {builtin}")
        sys.exit(1)

    print(f"PASS: install_skill git dir resolves to workspace: {git_dir_abs}")


if __name__ == "__main__":
    main()
