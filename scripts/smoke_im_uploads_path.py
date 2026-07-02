"""Smoke test: IM tool resolves /uploads/ to writable workspace path.

Verifies the B2 fix (tools/im.py uploads_abs): when an IM message contains a
relative /uploads/ path, the resolved absolute path must point at the workspace
uploads dir (data/uploads), not the install dir. This is a path-resolution
smoke that imports the module and checks the resolved path; it does not need a
live agent.

Usage: uv run python scripts/smoke_im_uploads_path.py
"""

from __future__ import annotations

import os
import sys
import tempfile

APP_DATA = os.path.join(tempfile.gettempdir(), "opensquad_smoke_im_uploads")
os.makedirs(APP_DATA, exist_ok=True)

os.environ["OPENSQUAD_APP_DATA"] = APP_DATA
os.environ["OPENSQUAD_USER_DATA"] = APP_DATA

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from opensquad.system_config import syscfg  # noqa: E402


def main() -> None:
    # The fix reads syscfg.workspace_uploads_dir(). Assert that resolves under
    # the workspace (not the install dir) and is creatable.
    uploads = syscfg.workspace_uploads_dir()
    uploads_abs = os.path.abspath(uploads)
    workspace = os.path.abspath(syscfg.get_workspace())

    if not uploads_abs.startswith(workspace + os.sep) and uploads_abs != workspace:
        print(f"FAIL: workspace_uploads_dir() = {uploads_abs}")
        print(f"      not under workspace {workspace}")
        sys.exit(1)

    builtin = os.path.abspath(syscfg.get_builtin_root())
    if uploads_abs.startswith(builtin + os.sep) or uploads_abs == builtin:
        print(f"FAIL: uploads path landed in read-only install dir: {uploads_abs}")
        sys.exit(1)

    # Must be creatable (the IM tool does os.makedirs on it indirectly).
    os.makedirs(uploads_abs, exist_ok=True)
    probe = os.path.join(uploads_abs, ".write_probe")
    try:
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
    except OSError as e:
        print(f"FAIL: cannot write to uploads dir {uploads_abs}: {e}")
        sys.exit(1)

    # The IM fix uses workspace_uploads_dir() directly; confirm the path shape
    # ends with data/uploads so /uploads/xxx replacement yields a real file.
    norm = uploads_abs.replace("\\", "/")
    if not norm.endswith("data/uploads"):
        print(f"FAIL: uploads path shape unexpected: {norm}")
        print("      expected to end with data/uploads")
        sys.exit(1)

    print(f"PASS: IM uploads path resolves to workspace: {uploads_abs}")


if __name__ == "__main__":
    main()
