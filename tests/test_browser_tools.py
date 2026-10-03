"""browser_use: the agent's tools for the built-in browser.

The tools are thin on purpose — every call is an HTTP POST to the launcher, which owns the
Playwright session — so this file pins the contract that matters: the right endpoint, the right
payload, one browser per agent by default (so the panel and the agent look at the same page),
and that the tools are actually *reachable* by an agent (the mistake that once left
pair_with_node invisible: the module existed, the registration did not).
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import opensquad.tools.browser as bt  # noqa: E402

TINY_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode("ascii")


@pytest.fixture()
def calls(monkeypatch):
    """Capture the launcher calls instead of making them."""
    recorded: list[tuple[str, dict]] = []

    def _fake_post(path: str, payload: dict):
        recorded.append((path, payload))
        return {"ok": True, "session_id": payload.get("session_id"), "png": TINY_PNG}

    monkeypatch.setattr(bt, "_post", _fake_post)
    monkeypatch.setattr(bt, "_agent_id", lambda: "agent305")
    return recorded


def test_every_tool_posts_to_its_own_endpoint(calls):
    bt.browser_open()
    bt.browser_navigate("https://example.com/x")
    bt.browser_back()
    bt.browser_click(selector="#go")
    bt.browser_click(x=120, y=240)
    bt.browser_type("hello", selector="#box", submit=True)
    bt.browser_press("Enter")
    bt.browser_snapshot()
    bt.browser_screenshot(full_page=True)
    bt.browser_close()

    paths = [p for p, _ in calls]
    assert paths == [
        "/api/agents/agent305/browser/open",
        "/api/agents/agent305/browser/navigate",
        "/api/agents/agent305/browser/back",
        "/api/agents/agent305/browser/click",
        "/api/agents/agent305/browser/click",
        "/api/agents/agent305/browser/type",
        "/api/agents/agent305/browser/press",
        "/api/agents/agent305/browser/snapshot",
        "/api/agents/agent305/browser/screenshot",
        "/api/agents/agent305/browser/close",
    ]
    # the payloads carry what the launcher's handlers read
    assert calls[1][1] == {"session_id": "agent-agent305", "url": "https://example.com/x"}
    assert calls[3][1]["selector"] == "#go"
    assert calls[4][1]["x"] == 120 and calls[4][1]["y"] == 240
    assert calls[5][1]["text"] == "hello" and calls[5][1]["submit"] is True
    assert calls[6][1]["key"] == "Enter"
    assert calls[8][1]["full_page"] is True


def test_one_browser_per_agent_unless_told_otherwise(calls):
    bt.browser_open()

    assert calls[0][1]["session_id"] == "agent-agent305"

    bt.browser_open(session_id="shared-preview")

    assert calls[1][1]["session_id"] == "shared-preview"


def test_a_screenshot_is_saved_where_the_agent_can_use_it(calls, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "opensquad.input_hub.input_hub",
        SimpleNamespace(agent_dir=str(tmp_path)),
        raising=False,
    )

    result = bt.browser_screenshot()

    assert result["ok"] is True
    saved = Path(result["path"])
    assert saved.is_file()
    assert saved.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert saved.parent.name == "browser"


def test_a_launcher_failure_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(bt, "_post", lambda path, payload: {"ok": False, "error": "no launcher"})

    result = bt.browser_navigate("https://example.com")

    assert result["ok"] is False and result["error"] == "no launcher"


def test_without_a_launcher_address_the_tool_says_so(monkeypatch):
    monkeypatch.setattr(bt, "_launcher_base", lambda: "")

    result = bt.browser_open()

    assert result["ok"] is False
    assert "launcher address is unknown" in result["error"]


def test_the_tools_are_registered_for_every_agent():
    """Wiring lock (the `invite` lesson): a tools module that is not in the boot tables never
    reaches an agent, however good it is."""
    boot = (_SRC / "opensquad" / "agents_boot.py").read_text(encoding="utf-8")

    assert '"browser": "opensquad.tools.browser"' in boot
    # …and mandatory, so agents created before these tools existed still see them
    mandatory = boot.split("MANDATORY_TOOLS = {", 1)[1].split("}", 1)[0]
    assert '"browser"' in mandatory


def test_the_launcher_dispatches_every_browser_op():
    base = (_SRC / "opensquad" / "launcher" / "management_api" / "_base.py").read_text(encoding="utf-8")
    filesystem = (_SRC / "opensquad" / "launcher" / "management_api" / "_filesystem.py").read_text(encoding="utf-8")

    for op in ("open", "navigate", "back", "click", "type", "press", "snapshot", "screenshot", "frame", "close"):
        assert f"/browser/{op}" in base, op
        assert f"_handle_browser_{op}" in filesystem, op
