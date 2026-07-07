"""Verify session cwd is visible from executor threads (run_in_executor simulation)."""
import concurrent.futures
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ["OPENSQUAD_WORKSPACE"] = r"C:\ai_work\pro0\opensquad_runtime_deploy"

from opensquad.tools.filesystem import set_session_cwd
from opensquad.utils.path_utils import get_session_cwd_override, get_workspace_root

EXPECTED = r"C:\Users\adminuser\Desktop\game2"


def _read_in_thread() -> str:
    return get_workspace_root()


def main() -> int:
    set_session_cwd(EXPECTED)
    assert get_session_cwd_override() == os.path.normcase(os.path.abspath(EXPECTED))

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        cwd = pool.submit(_read_in_thread).result()

    ok = os.path.normcase(cwd) == os.path.normcase(EXPECTED)
    print(f"thread get_workspace_root(): {cwd}")
    print(f"RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
