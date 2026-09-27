"""Regression: a failed update check must not masquerade as "already latest".

Field report: a downloaded 0.8.46 desktop build showed an older version in the
UI and the update panel claimed that old build was the current stable release.
The ``/version`` route swallowed every failure mode the same way — non-200,
transport exception, and a 200 with no ``tag_name`` all returned
``update_available=False`` and nothing else, which the About tab rendered as the
green "already the latest version" panel. On a network where
``api.github.com`` is unreachable ("check failed") was therefore
indistinguishable from "genuinely no newer release", so users were told they
were current while stuck on an old build.

The route now sets ``check_failed`` / ``check_error`` on every failure path;
the frontend keys off those fields to render an explicit "could not reach the
update service" state instead of the green panel.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[1] / "src" / "opensquad" / "gateway" / "backend"
_ROUTE_FILE = _BACKEND_DIR / "app" / "ai_web" / "routes" / "_main.py"


class _FakeResponse:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    """Minimal stand-in for the TLS httpx client the route awaits."""

    def __init__(self, response=None, exc: Exception | None = None):
        self._response = response
        self._exc = exc
        self.called = False

    async def get(self, *args, **kwargs):
        self.called = True
        if self._exc is not None:
            raise self._exc
        return self._response


def _load_route() -> dict:
    """Exec ``check_version`` alone — the routes module cascades into heavy
    ``app.*`` imports that are irrelevant here (same isolation trick as
    ``test_version_display.py``). The ``@router.get`` decorator is dropped so
    no FastAPI app is needed."""
    if not _ROUTE_FILE.is_file():
        pytest.skip(f"{_ROUTE_FILE} is not present")
    tree = ast.parse(_ROUTE_FILE.read_text(encoding="utf-8"), filename=str(_ROUTE_FILE))
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "check_version":
            node.decorator_list = []
            namespace = {"logger": logging.getLogger("test.version_route")}
            exec(ast.unparse(node), namespace)
            return namespace
    raise LookupError("check_version not found in routes/_main.py")


@pytest.fixture(scope="module")
def route_ns() -> dict:
    return _load_route()


def _run(route_ns: dict, client: _FakeClient, current: str = "0.8.46") -> dict:
    route_ns["_get_current_version"] = lambda: current
    fake_module = types.ModuleType("app.http_clients")
    fake_module.get_tls_http_client = lambda: client
    with patch.dict(sys.modules, {"app.http_clients": fake_module}):
        return asyncio.run(route_ns["check_version"]())


# ── failure paths must be flagged, never silently "up to date" ─────────


def test_http_error_marks_check_failed(route_ns):
    result = _run(route_ns, _FakeClient(response=_FakeResponse(403)))
    assert result["check_failed"] is True
    assert "403" in (result["check_error"] or "")
    assert result["update_available"] is False
    assert result["check_skipped"] is False


def test_transport_exception_marks_check_failed(route_ns):
    result = _run(route_ns, _FakeClient(exc=RuntimeError("connection reset")))
    assert result["check_failed"] is True
    assert "connection reset" in (result["check_error"] or "")


def test_200_without_tag_name_marks_check_failed(route_ns):
    """A 200 whose body has no tag_name cannot be interpreted as "no update"."""
    result = _run(route_ns, _FakeClient(response=_FakeResponse(200, {"tag_name": ""})))
    assert result["check_failed"] is True
    assert result["latest"] == ""


# ── success paths must NOT be flagged ──────────────────────────────────


def test_same_version_is_a_genuine_no_update(route_ns):
    client = _FakeClient(response=_FakeResponse(200, {"tag_name": "v0.8.46", "html_url": "https://example.invalid/r"}))
    result = _run(route_ns, client)
    assert result["check_failed"] is False
    assert result["check_error"] is None
    assert result["update_available"] is False


def test_newer_release_reports_update(route_ns):
    client = _FakeClient(response=_FakeResponse(200, {"tag_name": "v0.8.47", "html_url": "https://example.invalid/r"}))
    result = _run(route_ns, client)
    assert result["update_available"] is True
    assert result["check_failed"] is False


def test_non_stable_channel_skips_without_failure(route_ns):
    client = _FakeClient(response=_FakeResponse(500))
    result = _run(route_ns, client, current="0.8.48.dev0")
    assert result["check_skipped"] is True
    assert result["check_failed"] is False
    assert client.called is False, "a skipped channel must not touch the network"
