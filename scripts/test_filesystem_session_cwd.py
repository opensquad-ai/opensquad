"""Verify filesystem relative paths resolve against session_cwd."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ["OPENSQUAD_WORKSPACE"] = r"C:\ai_work\pro0\opensquad_runtime_deploy"

from opensquad.tools import filesystem

SESSION = os.path.normcase(os.path.abspath(os.path.join(os.path.dirname(__file__))))
PERMANENT = os.path.normcase(os.path.abspath(r"C:\ai_work\pro0\opensquad_runtime_deploy"))
TEST_NAME = ".hello_fs_session_test.txt"


def main() -> int:
    filesystem.set_session_cwd(SESSION)

    rel = TEST_NAME
    resolved = filesystem._resolve_path(rel)
    if os.path.normcase(resolved) != os.path.join(SESSION, TEST_NAME.lstrip("./\\")):
        print(f"FAIL resolve: {resolved}")
        return 1

    # cleanup stale files
    for base in (SESSION, PERMANENT):
        p = os.path.join(base, TEST_NAME)
        if os.path.isfile(p):
            os.remove(p)

    r = filesystem.write_file(rel, "session cwd test")
    if r.get("status") != "success":
        print(f"FAIL write: {r}")
        return 1

    expected = os.path.join(SESSION, TEST_NAME)
    wrong = os.path.join(PERMANENT, TEST_NAME)
    if not os.path.isfile(expected):
        print(f"FAIL: file not at session path {expected}")
        return 1
    if os.path.isfile(wrong):
        print(f"FAIL: file incorrectly at permanent root {wrong}")
        return 1

    print(f"PASS: wrote to {expected}")
    os.remove(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
