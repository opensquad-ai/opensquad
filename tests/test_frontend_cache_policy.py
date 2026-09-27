"""Regression: the built UI must carry an explicit Cache-Control policy.

Field report: an upgraded desktop install kept showing the previous release's UI
*and* the previous release's version number, and restarting the app did not
help. The gateway served dist/ through a plain ``StaticFiles`` mount, which
sends only ``etag`` / ``last-modified`` — no ``Cache-Control`` — so Chromium fell
back to heuristic freshness (10% of the time since Last-Modified) for the
un-hashed ``index.html``. Electron keeps its HTTP cache in userData
(``Cache`` / ``Code Cache`` / ``GPUCache``), so the stale shell survived both
the reinstall and a restart, and it kept referencing the previous release's
hashed chunks — cached as well — reverting the whole UI.

The shell must revalidate on every launch; only the content-addressed bundles
under ``assets/`` may be cached immutably.
"""

from __future__ import annotations

import ast
import asyncio
import os
from pathlib import Path

import pytest
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.staticfiles import PathLike
from starlette.types import Scope

_MAIN_PY = Path(__file__).resolve().parents[1] / "src" / "opensquad" / "gateway" / "backend" / "app" / "main.py"

_WANTED_FUNCS = {"_frontend_cache_control"}
_WANTED_CLASSES = {"_FrontendStaticFiles"}
_WANTED_CONSTS = {"_SHELL_CACHE_CONTROL", "_ASSET_CACHE_CONTROL", "_HASHED_ASSET_DIR"}


def _load_serving_source() -> str:
    """Extract the cache-policy pieces out of ``app/main.py``.

    Importing ``app.main`` pulls in the whole gateway (workspace bootstrap,
    config, every mount). These pieces are self-contained, so exec them alone —
    the same isolation trick ``test_version_display.py`` uses.
    """
    tree = ast.parse(_MAIN_PY.read_text(encoding="utf-8"), filename=str(_MAIN_PY))
    chunks: list[str] = []
    for node in tree.body:
        is_func = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in _WANTED_FUNCS
        is_class = isinstance(node, ast.ClassDef) and node.name in _WANTED_CLASSES
        is_const = isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in _WANTED_CONSTS for target in node.targets
        )
        if is_func or is_class or is_const:
            chunks.append(ast.unparse(node))
    assert len(chunks) == len(_WANTED_FUNCS) + len(_WANTED_CLASSES) + len(_WANTED_CONSTS), (
        f"expected {len(_WANTED_FUNCS) + len(_WANTED_CLASSES) + len(_WANTED_CONSTS)} definitions in {_MAIN_PY}, "
        f"found {len(chunks)}"
    )
    return "\n\n".join(chunks)


@pytest.fixture(scope="module")
def serving() -> dict:
    namespace = {
        "os": os,
        "PathLike": PathLike,
        "Scope": Scope,
        "Response": Response,
        "StaticFiles": StaticFiles,
    }
    exec(_load_serving_source(), namespace)
    return namespace


# ── the policy itself ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "expected_immutable"),
    [
        (r"C:\app\_internal\nexuschat-pro\dist\assets\AIChatPage-HLJCjly4.js", True),
        (r"C:\app\_internal\nexuschat-pro\dist\assets\index-C3f9aQ.js", True),
        ("/srv/app/nexuschat-pro/dist/assets/vendor-react-Bx1y2z.css", True),
        (r"C:\app\_internal\nexuschat-pro\dist\index.html", False),
        (r"C:\app\_internal\nexuschat-pro\dist\favicon.svg", False),
        (r"C:\app\_internal\nexuschat-pro\dist\model_presets.json", False),
        (r"C:\app\_internal\nexuschat-pro\dist\logo.svg", False),
        # A file that merely *mentions* assets must not be treated as hashed.
        (r"C:\app\_internal\nexuschat-pro\dist\assets.json", False),
    ],
)
def test_cache_policy(serving, path, expected_immutable):
    policy = serving["_frontend_cache_control"](path)
    if expected_immutable:
        assert policy == serving["_ASSET_CACHE_CONTROL"]
        assert "immutable" in policy
    else:
        assert policy == serving["_SHELL_CACHE_CONTROL"]
        assert "immutable" not in policy


# ── the policy reaches the wire ─────────────────────────────────────────


def _cache_control(serving, directory: Path, path: str) -> tuple[int, str]:
    app = serving["_FrontendStaticFiles"](directory=str(directory), html=True)
    messages: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"127.0.0.1:9555")],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 9555),
    }
    asyncio.run(app(scope, receive, send))
    start = next(message for message in messages if message["type"] == "http.response.start")
    headers = {key.decode().lower(): value.decode() for key, value in start["headers"]}
    return start["status"], headers.get("cache-control", "")


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!DOCTYPE html><html></html>", encoding="utf-8")
    (tmp_path / "assets" / "AIChatPage-HLJCjly4.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    return tmp_path


def test_shell_revalidates_on_the_wire(serving, dist):
    assert _cache_control(serving, dist, "/index.html") == (200, serving["_SHELL_CACHE_CONTROL"])


def test_root_serves_the_shell_with_no_cache(serving, dist):
    """``/`` is literally what Electron loads (APP_URL = http://127.0.0.1:9555)."""
    assert _cache_control(serving, dist, "/") == (200, serving["_SHELL_CACHE_CONTROL"])


def test_hashed_bundle_is_immutable_on_the_wire(serving, dist):
    status, policy = _cache_control(serving, dist, "/assets/AIChatPage-HLJCjly4.js")
    assert status == 200
    assert "immutable" in policy


# ── wiring: the mount must actually use the subclass ────────────────────


def test_frontend_mount_uses_the_cache_aware_static_handler():
    source = _MAIN_PY.read_text(encoding="utf-8")
    assert 'app.mount("/", _FrontendStaticFiles(' in source, (
        "the / mount no longer uses _FrontendStaticFiles — the UI is back to "
        "heuristic caching and an upgrade can serve the previous release"
    )


def test_vite_fallback_path_sets_the_same_policy():
    """The dev-mode fallback (Vite down) serves dist/ too and must match."""
    source = _MAIN_PY.read_text(encoding="utf-8")
    assert 'headers={"Cache-Control": _frontend_cache_control(dist_file)}' in source, (
        "the Vite-unreachable fallback serves dist/ without a cache policy"
    )
