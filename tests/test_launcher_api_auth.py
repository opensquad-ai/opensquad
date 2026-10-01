"""Launcher management API auth: no token means localhost-only, not wide open.

`_check_auth` used to `return True` whenever no launcher token was configured, on
a server that binds 0.0.0.0 — so anyone on the LAN could drive the launcher admin
API (start/stop services, install plugins, read config). Found while wiring
cross-machine agents, where "connect to a peer's IP" would have handed them that
surface too.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "src"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from opensquad.launcher.management_api._base import BaseHandlerMixin  # noqa: E402
from opensquad.system_config import syscfg  # noqa: E402


class _Handler(BaseHandlerMixin):
    """Minimal stand-in: the mixin only needs headers / client_address / _send_json."""

    def __init__(self, *, token: str, headers: dict | None = None, client=("127.0.0.1", 12345)):
        self._token = token
        self.headers = headers or {}
        self.client_address = client
        self.sent: list[tuple[int, dict]] = []

    def _get_launcher_token(self) -> str:  # bypass syscfg for the auth rules
        return self._token

    def _send_json(self, payload, code):
        self.sent.append((code, payload))


def test_a_configured_token_is_required():
    handler = _Handler(token="s3cret", headers={"Authorization": "Bearer s3cret"})
    assert handler._check_auth() is True

    missing = _Handler(token="s3cret")
    assert missing._check_auth() is False
    assert missing.sent[0][0] == 401

    wrong = _Handler(token="s3cret", headers={"Authorization": "Bearer nope"})
    assert wrong._check_auth() is False
    assert wrong.sent[0][0] == 403


def test_without_a_token_local_callers_still_work():
    """The gateway proxies launcher calls from localhost, so single-machine use
    must keep working with no token configured."""
    handler = _Handler(token="")
    assert handler._check_auth() is True

    v6 = _Handler(token="", client=("::1", 12345))
    assert v6._check_auth() is True


def test_without_a_token_remote_callers_are_refused():
    handler = _Handler(token="", client=("192.168.5.9", 51515))

    assert handler._check_auth() is False
    code, payload = handler.sent[0]
    assert code == 403
    assert "launcher_token" in payload["message"]


def test_token_lookup_prefers_the_documented_key(monkeypatch):
    config = {"auth": {"launcher_token": "from-auth"}, "launcher_token": "legacy"}
    monkeypatch.setattr(syscfg, "get", lambda *args, **kwargs: _lookup(config, args))
    assert BaseHandlerMixin._get_launcher_token() == "from-auth"


def test_token_lookup_falls_back_to_the_legacy_key(monkeypatch):
    config = {"launcher_token": "legacy"}
    monkeypatch.setattr(syscfg, "get", lambda *args, **kwargs: _lookup(config, args))
    assert BaseHandlerMixin._get_launcher_token() == "legacy"


@pytest.mark.parametrize("placeholder", ["", "   ", "CHANGE_ME", "your_launcher_token", "<token>"])
def test_placeholders_count_as_unset(monkeypatch, placeholder):
    config = {"launcher_token": placeholder}
    monkeypatch.setattr(syscfg, "get", lambda *args, **kwargs: _lookup(config, args))
    assert BaseHandlerMixin._get_launcher_token() == ""


def _lookup(config: dict, args: tuple):
    """Mimic syscfg.get(*path, default)."""
    if not args:
        return None
    *path, default = args
    node = config
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node
