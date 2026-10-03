"""Non-ASCII input has to survive the trip to the shell.

Reported from the field: pasting `cd C:\\Users\\adminuser\\Desktop\\战略\\ai\\skill\\...` came back
as `cd C:\\Users\\adminuser\\Desktop\\������\\...` and the shell could not enter the folder. The
text was fine when it left the panel — `cmd` reads piped stdin in the *console code page* (GBK
on a Chinese Windows), so the bytes were reinterpreted on arrival. The session now switches the
code page to UTF-8 as part of its bootstrap.

Driving a Chinese folder is the case that matters (an agent workspace can live in one), so that
is what this checks: write a path with 中文 and read it back.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import opensquad.terminal_session as ts  # noqa: E402

CJK = "战略"  # the folder in the report

pytestmark = pytest.mark.skipif(
    "cmd" not in [s["id"] for s in ts.available_shells()] and sys.platform == "win32",
    reason="the code-page bug is Windows-specific; POSIX shells are UTF-8 already",
)


@pytest.fixture(autouse=True)
def clean_registry():
    yield
    ts.close_all()


def _wait_for(needle: str, timeout: float = 10.0) -> str:
    """Poll the session's own buffer (the panel poll path) for `needle`."""
    deadline = time.time() + timeout
    seen = ""
    since = 0
    while time.time() < deadline:
        res = ts.read_terminal("cjk", since)
        chunk = str(res.get("chunk") or "")
        if chunk:
            seen += chunk
            since = int(res.get("offset") or since)
        if needle in seen:
            return seen
        time.sleep(0.05)
    return seen


def test_chinese_text_reaches_the_shell_intact():
    opened = ts.open_terminal(terminal_id="cjk")
    assert opened["ok"] is True, opened
    # the bootstrap needs a moment before the first write is interpreted the same way
    time.sleep(0.4)

    assert ts.write_terminal("cjk", f"echo {CJK}\n")["ok"] is True

    seen = _wait_for(CJK)

    assert CJK in seen, f"the Chinese text did not survive the shell: {seen!r}"


def test_a_chinese_directory_can_be_entered(tmp_path):
    """The reported failure, end to end: cd into a folder whose name is 中文."""
    folder = tmp_path / CJK
    folder.mkdir()
    opened = ts.open_terminal(terminal_id="cjk", cwd=str(tmp_path), trusted=True)
    assert opened["ok"] is True, opened
    time.sleep(0.4)

    ts.write_terminal("cjk", f"cd {CJK}\n")
    ts.write_terminal("cjk", "cd\n")

    seen = _wait_for(str(folder).lower().replace("\\", "/"))

    assert CJK in seen and "cannot find the path" not in seen.lower()
