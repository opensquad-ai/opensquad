"""Smoke test: plugin registry DB writes to writable workspace.

Verifies the B6 fix (gateway/plugin_registry/main.py DB_PATH): the
plugins_db.json that save_plugins() writes must land in the workspace
(data/plugin_registry/), not next to the source file (read-only in frozen mode).

This smoke imports the registry module with the desktop env vars set and
checks DB_PATH resolves under the workspace; it then writes+reads the DB to
prove writability.

Usage: uv run python scripts/smoke_plugin_registry_db.py
"""

from __future__ import annotations

import os
import sys
import tempfile

APP_DATA = os.path.join(tempfile.gettempdir(), "opensquad_smoke_registry_db")
os.makedirs(APP_DATA, exist_ok=True)

os.environ["OPENSQUAD_APP_DATA"] = APP_DATA
os.environ["OPENSQUAD_USER_DATA"] = APP_DATA

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from opensquad.gateway.plugin_registry import main as registry  # noqa: E402
from opensquad.system_config import syscfg  # noqa: E402


def main() -> None:
    db_path = os.path.abspath(registry.DB_PATH)
    workspace = os.path.abspath(syscfg.get_workspace())
    builtin = os.path.abspath(syscfg.get_builtin_root())

    # 1. Must be under workspace.
    if not db_path.startswith(workspace + os.sep) and db_path != workspace:
        print(f"FAIL: DB_PATH = {db_path}")
        print(f"      not under workspace {workspace}")
        sys.exit(1)

    # 2. Must NOT be under the install dir / source dir.
    if db_path.startswith(builtin + os.sep) or db_path == builtin:
        print(f"FAIL: DB_PATH landed in read-only install dir: {db_path}")
        sys.exit(1)

    # 3. Must be writable (the whole point — save_plugins() writes here).
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    probe_data = [{"id": "smoke_probe", "name": "Smoke Probe"}]
    try:
        registry.save_plugins(probe_data)
        loaded = registry.load_plugins()
        if not loaded or loaded[0].get("id") != "smoke_probe":
            print(f"FAIL: DB write/read roundtrip failed, got {loaded}")
            sys.exit(1)
    except OSError as e:
        print(f"FAIL: cannot write DB at {db_path}: {e}")
        sys.exit(1)
    finally:
        try:
            os.remove(db_path)
        except OSError:
            pass

    print(f"PASS: plugin registry DB resolves to workspace: {db_path}")


if __name__ == "__main__":
    main()
