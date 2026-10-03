"""The built-in browser session: a real Chromium that the agent's tools and the panel share.

The point of this module is that there is exactly ONE page — the one the agent clicks and the
one the user sees — so the test drives a real browser: navigate, click, type, screenshot, and a
local file so nothing here needs the network.
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

pytest.importorskip("playwright.sync_api", reason="the built-in browser needs Playwright")

import opensquad.browser_session as bs  # noqa: E402

# Every session in this suite is headless: a window per test would put eight browser windows on
# screen mid-run. The window path has a test of its own (test_a_window_is_opened_when_asked).
os.environ.setdefault("OPENSQUAD_BROWSER_HEADLESS", "1")


@pytest.fixture(autouse=True)
def clean_sessions():
    """Every browser is closed, whatever the test did."""
    yield
    bs.close_all()


def _page(tmp_path: Path, marker: str = "opensquad-browser-ok") -> Path:
    # One file per marker: two "pages" that share a filename are the same page.
    path = tmp_path / f"{marker}.html"
    path.write_text(
        "<html><head><title>" + marker + "</title></head><body>"
        f"<h1>{marker}</h1>"
        "<a href='https://example.com/x'>link</a>"
        "<input id='box'>"
        "<button id='go' onclick=\"document.getElementById('out').textContent="
        "document.getElementById('box').value\">go</button>"
        "<div id='out'></div>"
        "</body></html>",
        encoding="utf-8",
    )
    return path


def test_a_session_renders_navigates_and_captures(tmp_path):
    opened = bs.open_session(session_id="b1")

    assert opened["ok"] is True, opened
    assert opened["running"] is True

    nav = bs.navigate("b1", _page(tmp_path).as_uri())

    assert nav["ok"] is True, nav
    assert nav["title"] == "opensquad-browser-ok"
    assert nav["url"].startswith("file:")

    shot = bs.screenshot("b1")
    assert shot["ok"] is True, shot
    # a real PNG, not an empty placeholder
    assert base64.b64decode(shot["png"])[:8] == b"\x89PNG\r\n\x1a\n"
    assert shot["bytes"] > 1000

    snap = bs.snapshot("b1")
    assert "opensquad-browser-ok" in snap["text"]
    assert any("example.com" in link for link in snap["links"])

    # the panel's poll hands back the frame already captured — no new render per tick
    frame = bs.frame("b1")
    assert frame["ok"] is True and frame["png"] == shot["png"]


def test_typing_and_clicking_change_the_shared_page(tmp_path):
    bs.open_session(session_id="b2")
    bs.navigate("b2", _page(tmp_path).as_uri())

    typed = bs.type_text("b2", "#box", "hello-from-the-agent")
    assert typed["ok"] is True, typed
    clicked = bs.click("b2", "#go")
    assert clicked["ok"] is True, clicked

    after = bs.snapshot("b2")

    assert "hello-from-the-agent" in after["text"], after["text"][:400]
    # …and the frame the user would see was refreshed by the action
    assert bs.frame("b2")["ok"] is True


def test_back_returns_to_the_previous_page(tmp_path):
    first = _page(tmp_path, "first-page")
    second = _page(tmp_path, "second-page")
    bs.open_session(session_id="b3")

    bs.navigate("b3", first.as_uri())
    bs.navigate("b3", second.as_uri())
    assert bs.snapshot("b3")["title"] == "second-page"

    went_back = bs.back("b3")

    assert went_back["ok"] is True
    assert bs.snapshot("b3")["title"] == "first-page"


def test_the_same_id_is_one_browser(tmp_path):
    first = bs.open_session(session_id="b4")
    second = bs.open_session(session_id="b4")

    assert first["ok"] and second["ok"]
    assert second.get("reused") is True
    assert len(bs.list_sessions()) == 1


def test_unknown_and_closed_sessions_are_reported_not_raised(tmp_path):
    unknown = bs.navigate("nope", "https://example.com")

    assert unknown["ok"] is False and "unknown browser session" in unknown["error"]

    bs.open_session(session_id="b5")
    bs.close_session("b5")
    after_close = bs.navigate("b5", "https://example.com")

    assert after_close["ok"] is False
    assert after_close["error"]


def test_a_bad_url_is_refused_without_killing_the_session(tmp_path):
    bs.open_session(session_id="b6")

    empty = bs.navigate("b6", "   ")

    assert empty["ok"] is False and "url is required" in empty["error"]
    # the session still works
    assert bs.navigate("b6", _page(tmp_path).as_uri())["ok"] is True


def test_close_all_stops_every_browser():
    bs.open_session(session_id="b7")
    bs.open_session(session_id="b8")

    assert bs.close_all() == 2
    assert bs.list_sessions() == []


def test_a_window_is_opened_when_asked(tmp_path):
    """Option A: the user gets a browser they can really use, not only a picture of one.

    `headless=False` overrides the suite-wide env var, so this is a real window on a machine
    that has a desktop; where none exists the session says so instead of failing.
    """
    opened = bs.open_session(session_id="win1", headless=False)
    try:
        if not opened.get("headed"):
            pytest.skip(f"no desktop here: {opened.get('window_note') or 'no window'}")
        assert opened["ok"] is True and opened["headed"] is True
        assert opened["headless"] is False
        assert bs.navigate("win1", _page(tmp_path).as_uri())["ok"] is True
    finally:
        bs.close_session("win1")


def test_a_machine_without_a_desktop_falls_back_to_headless(monkeypatch):
    """A server or a CI box must still give the agent a browser — and a reason to report."""
    # The suite-wide env var would force headless before the window is even attempted; this test
    # is about the *attempt* failing.
    monkeypatch.delenv("OPENSQUAD_BROWSER_HEADLESS", raising=False)

    class _FakePage:
        def set_default_timeout(self, *_args, **_kwargs):
            return None

    def _no_window(self, *, headless):
        if not headless:
            raise RuntimeError("no display available")
        self._page = _FakePage()
        self._context = None
        self.headless = headless

    monkeypatch.setattr(bs.BrowserSession, "_start_context", _no_window)

    opened = bs.open_session(session_id="nodisp", headless=False)

    assert opened["ok"] is True, opened
    assert opened["headed"] is False
    assert "cannot show a window" in opened["window_note"]


def test_the_profile_is_persistent(tmp_path):
    """Logins survive a restart: that is why the session does not use a throwaway profile."""
    opened = bs.open_session(session_id="prof")

    assert opened["ok"] is True
    profile = Path(opened["profile_dir"])
    bs.close_session("prof")

    assert profile.is_dir()
    assert any(profile.iterdir()), "the profile was never written"


def test_the_env_var_keeps_a_suite_headless(monkeypatch):
    monkeypatch.setenv("OPENSQUAD_BROWSER_HEADLESS", "1")

    opened = bs.open_session(session_id="envless")

    assert opened["ok"] is True
    assert opened["headed"] is False
