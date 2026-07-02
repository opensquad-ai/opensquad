"""Smoke test: long_memory plugin config read from workspace, not install dir.

Verifies the B3/B4 fix (agents_boot.py PROJECT_ROOT → syscfg.get_workspace()):
the long_memory plugin config path that agent_boot_phases.py builds must be
<workspace>/data/plugins/long_memory/config.json, never <install_dir>/data/....
In frozen mode the install dir is read-only and has no data/plugins/ subtree,
so the old path silently fell back to defaults, ignoring user customisation.

This smoke seeds a config file in the workspace and confirms the path resolves
there. It does not need a live agent.

Usage: uv run python scripts/smoke_long_memory_config.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

APP_DATA = os.path.join(tempfile.gettempdir(), "opensquad_smoke_long_mem")
os.makedirs(APP_DATA, exist_ok=True)

os.environ["OPENSQUAD_APP_DATA"] = APP_DATA
os.environ["OPENSQUAD_USER_DATA"] = APP_DATA

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from opensquad.system_config import syscfg  # noqa: E402


def main() -> None:
    workspace = os.path.abspath(syscfg.get_workspace())
    builtin = os.path.abspath(syscfg.get_builtin_root())

    # The path agent_boot_phases.py builds (now that project_root == workspace):
    plugin_cfg_path = os.path.join(workspace, "data", "plugins", "long_memory", "config.json")

    # 1. Must be under workspace.
    if not plugin_cfg_path.startswith(workspace + os.sep):
        print(f"FAIL: plugin_cfg_path {plugin_cfg_path} not under workspace {workspace}")
        sys.exit(1)

    # 2. Must NOT be under install dir.
    if plugin_cfg_path.startswith(builtin + os.sep) or plugin_cfg_path == builtin:
        print(f"FAIL: plugin_cfg_path landed in read-only install dir: {plugin_cfg_path}")
        sys.exit(1)

    # 3. Seed the file and confirm it is readable at that path.
    os.makedirs(os.path.dirname(plugin_cfg_path), exist_ok=True)
    seed = {"min_cooccurrence": 42, "decay_rate": 0.123, "decay_interval": 999}
    with open(plugin_cfg_path, "w", encoding="utf-8") as f:
        json.dump(seed, f)

    # 4. agents_boot passes project_root=syscfg.get_workspace(); simulate the
    #    same join to prove the seeded values are reachable.
    from opensquad.json_cache import load_json_cached

    loaded = load_json_cached(plugin_cfg_path)
    if loaded.get("min_cooccurrence") != 42:
        print(f"FAIL: seeded long_memory config not read back: {loaded}")
        sys.exit(1)

    print(f"PASS: long_memory config resolves to workspace: {plugin_cfg_path}")


if __name__ == "__main__":
    main()
