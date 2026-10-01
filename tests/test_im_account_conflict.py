"""im.register_account: an account that cannot be taken over must say why.

A 409 from the gateway means the @ai account is in use right now — the
two-machines-one-email case — and the agent-facing answer has to name the fix
(stop the other machine, or pick a unique email) instead of reporting a generic
"reset failed".
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "src"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from opensquad.tools import im  # noqa: E402


class _Response:
    def __init__(self, status_code: int, detail: str = ""):
        self.status_code = status_code
        self._detail = detail
        self.text = detail

    def json(self):
        return {"detail": self._detail} if self._detail else {}


@pytest.fixture()
def http(monkeypatch):
    """Route by URL so the test does not depend on the call order."""

    calls: list[str] = []
    reset_status = {"code": 409}

    def _post(url, **kwargs):
        calls.append(url)
        if url.endswith("/api/auth/register"):
            return _Response(400, "Email already registered")
        if url.endswith("/api/auth/login"):
            return _Response(401, "bad credentials")
        if url.endswith("/api/auth/reset-password"):
            return _Response(reset_status["code"], "This account is already in use by a live connection")
        return _Response(400, "unexpected")

    monkeypatch.setattr(im.requests, "post", _post)
    return {"calls": calls, "reset_status": reset_status}


def test_an_account_in_use_reports_the_fix(http):
    res = im.register_account(email="coder-001@ai", password="pw")

    assert res["status"] == "error"
    assert res["code"] == "email_in_use"
    assert "another machine" in res["message"]
    assert "unique email" in res["message"]
    # the gateway's own explanation is carried through for the log
    assert "live connection" in res["detail"]
    # it did not pretend to succeed
    assert not res.get("bridge_logged_in")


def test_a_plain_reset_failure_stays_generic(http):
    http["reset_status"]["code"] = 403

    res = im.register_account(email="coder-001@ai", password="pw")

    assert res["status"] == "error"
    assert res["code"] == "reset_failed"
    assert "password reset failed" in res["message"]
