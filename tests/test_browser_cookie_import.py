"""Importing a user's cookie export into the agent's browser.

Two halves: the reader (Netscape ``cookies.txt`` / JSON exports) and the launcher handler
that turns a posted path-or-text into cookies in the session's persistent profile. The
handler is exercised against a stub rather than a real launcher: what matters here is that a
missing file, an empty body and an unusable export each come back as a reason, and that an
empty ``session_id`` lands on ``agent-<name>`` (the id the tools and the panel both use)
instead of minting a random session nobody drives.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from opensquad.launcher.management_api._filesystem import FilesystemMixin  # noqa: E402
from opensquad.utils.cookie_import import parse_cookies  # noqa: E402

NETSCAPE = (
    "# Netscape HTTP Cookie File\n"
    "# a comment that is not a cookie\n"
    ".example.com\tTRUE\t/\tTRUE\t1893456000\tsid\tabc123\n"
    "#HttpOnly_example.com\tFALSE\t/app\tFALSE\t0\ttoken\thttp-only-value\n"
    "broken.example.com\tFALSE\t/\tFALSE\n"
)


# ── the reader ──────────────────────────────────────────────────────────────


class TestNetscape:
    def test_reads_both_lines_and_skips_the_rest(self):
        parsed = parse_cookies(NETSCAPE)
        assert parsed.source == "netscape"
        assert parsed.error == ""
        assert [c["name"] for c in parsed.cookies] == ["sid", "token"]
        assert parsed.skipped_total == 1
        assert "expected 7 tab-separated fields" in parsed.skipped[0]

    def test_maps_the_fields_a_cookie_needs(self):
        sid = parse_cookies(NETSCAPE).cookies[0]
        assert sid == {
            "name": "sid",
            "value": "abc123",
            "domain": ".example.com",
            "path": "/",
            "expires": 1893456000,
            "secure": True,
        }

    def test_http_only_prefix_is_a_flag_not_a_domain(self):
        token = parse_cookies(NETSCAPE).cookies[1]
        assert token["domain"] == "example.com"
        assert token["httpOnly"] is True
        # expiry 0 is the format's "session cookie" — Playwright wants no `expires` at all
        assert "expires" not in token

    def test_include_subdomains_flag_keeps_its_dot(self):
        text = "example.com\tTRUE\t/\tFALSE\t0\tn\tv\n"
        assert parse_cookies(text).cookies[0]["domain"] == ".example.com"


class TestJson:
    def test_reads_an_extension_export(self):
        payload = json.dumps(
            [
                {
                    "name": "SID",
                    "value": "xyz",
                    "domain": ".google.com",
                    "path": "/",
                    "secure": True,
                    "httpOnly": True,
                    "sameSite": "no_restriction",
                    "expirationDate": 1893456000.5,
                },
                {"name": "", "value": "no name", "domain": "x.com"},
            ]
        )
        parsed = parse_cookies(payload)
        assert parsed.source == "json"
        assert parsed.cookies == [
            {
                "name": "SID",
                "value": "xyz",
                "domain": ".google.com",
                "path": "/",
                "expires": 1893456000,
                "secure": True,
                "httpOnly": True,
                "sameSite": "None",
            }
        ]
        assert parsed.skipped_total == 1

    def test_reads_a_playwright_storage_state(self):
        payload = json.dumps({"cookies": [{"name": "a", "value": "b", "url": "https://x.test/", "sameSite": "Lax"}]})
        cookie = parse_cookies(payload).cookies[0]
        assert cookie == {"name": "a", "value": "b", "url": "https://x.test/", "sameSite": "Lax"}

    def test_millisecond_expiry_is_normalised(self):
        payload = json.dumps([{"name": "a", "value": "b", "domain": "x.test", "expires": 1893456000000}])
        assert parse_cookies(payload).cookies[0]["expires"] == 1893456000


class TestRejections:
    def test_empty_file(self):
        assert parse_cookies("   \n").error == "the file is empty"

    def test_broken_json(self):
        parsed = parse_cookies("{not json")
        assert "invalid JSON" in parsed.error
        assert parsed.cookies == []

    def test_json_that_is_not_a_cookie_list(self):
        assert "list of cookies" in parse_cookies('{"foo": 1}').error

    def test_netscape_without_a_single_valid_line(self):
        parsed = parse_cookies("nope\tnope\n")
        assert parsed.cookies == []
        assert parsed.skipped_total == 1


# ── the launcher handler ────────────────────────────────────────────────────


class _Handler(FilesystemMixin):
    """Just enough of the management-API handler to call one of its methods."""

    def __init__(self):
        self.replies: list[dict] = []

    def _browser_reply(self, result: dict):  # noqa: D102 - mirrors the real helper
        self.replies.append(result)
        return result

    def _send_json(self, payload, status=200):  # pragma: no cover - not used here
        raise AssertionError("cookies import must answer through _browser_reply")


@pytest.fixture
def imported(monkeypatch):
    """Capture what the handler hands to the browser session instead of starting one."""
    calls: list[tuple[str, list]] = []

    def _add_cookies(session_id, cookies):
        calls.append((session_id, cookies))
        return {"ok": True, "session_id": session_id, "added": len(cookies), "failed": [], "total": len(cookies)}

    from opensquad import browser_session

    monkeypatch.setattr(browser_session, "add_cookies", _add_cookies)
    return calls


def test_text_body_goes_to_the_agents_session(imported):
    handler = _Handler()
    result = handler._handle_browser_cookies_import("agent305", {"text": "a.test\tFALSE\t/\tFALSE\t0\tn\tv\n"})
    assert result["ok"] is True
    assert imported[0][0] == "agent-agent305", "an empty session_id must not mint a random session"
    assert result["parsed"] == 1
    assert result["cookies"][0]["name"] == "n"


def test_an_explicit_session_id_is_honoured(imported):
    handler = _Handler()
    handler._handle_browser_cookies_import(
        "agent305", {"text": "a.test\tFALSE\t/\tFALSE\t0\tn\tv\n", "session_id": "agent-other"}
    )
    assert imported[0][0] == "agent-other"


def test_reads_the_file_the_panel_picked(imported, tmp_path):
    export = tmp_path / "cookies.txt"
    export.write_text(".example.com\tTRUE\t/\tTRUE\t1893456000\tsid\tabc\n", encoding="utf-8")
    result = _Handler()._handle_browser_cookies_import("agent305", {"path": str(export)})
    assert result["ok"] is True and result["parsed"] == 1
    assert imported[0][1][0]["name"] == "sid"


def test_a_missing_file_is_an_error_not_an_exception(imported, tmp_path):
    result = _Handler()._handle_browser_cookies_import("agent305", {"path": str(tmp_path / "nope.txt")})
    assert result["ok"] is False
    assert "cannot read" in result["error"]
    assert imported == []


def test_an_empty_body_explains_itself(imported):
    result = _Handler()._handle_browser_cookies_import("agent305", {})
    assert result["ok"] is False
    assert "path" in result["error"] and "text" in result["error"]


def test_an_unusable_export_reports_why(imported):
    result = _Handler()._handle_browser_cookies_import("agent305", {"text": "{not json"})
    assert result["ok"] is False
    assert "invalid JSON" in result["error"]
    assert imported == []
