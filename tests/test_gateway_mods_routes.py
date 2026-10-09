"""The Mods page's backend: routes registered, and the moves it makes are honest.

Two failures this guards, both already seen in this codebase:

1. **The join.** ``tests/test_gateway_admin_routes.py`` records the incident where a
   handler kept its body but lost its decorator — the client's URL and the server's
   function were both present, only the line between them was gone, and the page read
   "Error: Not Found".  So the frontend's ``modsAPI`` URLs are checked against the
   router's real route table.

2. **The claim.**  The Mods page exists to tell a user whether a mod can run here.
   Two statements must never collapse into one: *compatibility* (the static verdict)
   and *availability* (is there a host).  ``host.available`` is ``False`` today and
   the enable toggle must not imply otherwise; ``blocked_by`` must name the gap.

Behaviour is exercised by calling the handlers directly rather than through
``TestClient``: no existing test in this suite uses it, it drags a deprecation
warning in, and the interesting logic is in the handlers anyway.  Route *binding*
is covered by the route-table assertions.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import types

_BACKEND_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "src", "opensquad", "gateway", "backend")
)
if _BACKEND_ROOT not in os.sys.path:
    os.sys.path.insert(0, _BACKEND_ROOT)

import pytest  # noqa: E402
from app.ai_web.routes import _mods as mods_routes  # noqa: E402

from opensquad import mods_compat  # noqa: E402

API_TS = pathlib.Path(__file__).resolve().parents[1] / ("src/opensquad/gateway/nexuschat-pro/services/api.ts")


class _User:
    id = "test-user"
    name = "tester"
    email = "tester@example.com"


def _registered() -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for route in mods_routes.mods_router.routes:
        for method in getattr(route, "methods", ()) or ():
            if method in {"HEAD", "OPTIONS"}:
                continue
            out.add((method, route.path))
    return out


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Point the route module's workspace lookups at a tmp tree."""
    root = tmp_path / "mods"
    state = tmp_path / "state"
    monkeypatch.setattr(mods_compat, "mods_root", lambda: str(root))
    monkeypatch.setattr(mods_compat, "mods_state_root", lambda: str(state))
    return root, state


def _write_mod(root, dir_name: str, *, hooks, module: str) -> str:
    mod_dir = root / dir_name
    (mod_dir / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (mod_dir / "hooks").mkdir(parents=True, exist_ok=True)
    (mod_dir / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": dir_name, "version": "1.0.0"}), encoding="utf-8"
    )
    (mod_dir / "hooks" / "hooks.json").write_text(json.dumps(hooks), encoding="utf-8")
    if module:
        (mod_dir / "hooks" / "mod.mjs").write_text(module, encoding="utf-8")
    return str(mod_dir)


# ── 1. the join ────────────────────────────────────────────────────────────


def test_mods_routes_are_registered():
    routes = _registered()
    assert ("GET", "/mods") in routes
    assert ("PUT", "/mods/{name}/enable") in routes
    assert ("PUT", "/mods/{name}/disable") in routes
    assert ("POST", "/mods/import") in routes


def test_frontend_urls_have_matching_routes():
    """Every `/ai-web/mods...` the client calls must exist on the router.

    The client's paths carry the /ai-web prefix; router paths are relative to it.
    """
    src = API_TS.read_text(encoding="utf-8")
    assert "/ai-web/mods" in src, "modsAPI disappeared from api.ts"

    routes = _registered()
    assert ("GET", "/mods") in routes  # modsAPI.list
    assert ("PUT", f"/mods/{{name}}/{'enable'}") in routes  # modsAPI.setEnabled(true)
    assert ("PUT", f"/mods/{{name}}/{'disable'}") in routes  # modsAPI.setEnabled(false)
    assert ("POST", "/mods/import") in routes  # modsAPI.importMod
    assert ("GET", "/mods/permissions") in routes  # modsAPI.permissions
    assert ("PUT", "/mods/{name}/permissions") in routes  # modsAPI.setPermissions


# ── gated capabilities (P5) ────────────────────────────────────────────────


def test_the_page_learns_what_each_capability_means(sandbox):
    # The blurbs come from here, so the page cannot drift from the gates.
    root, _state = sandbox
    _write_mod(root, "guard", hooks={"modules": ["./mod.mjs"]}, module="on('tool.call', () => {})")

    resp = asyncio.run(mods_routes.list_permissions(current_user=_User()))
    assert [c["id"] for c in resp["capabilities"]] == list(mods_compat.GATED_CAPABILITIES)
    assert all(c["blurb"] for c in resp["capabilities"])
    # Deny by default, and every installed mod is listed with what it holds.
    assert resp["mods"]["guard"] == {"granted": [], "domains": []}


def test_granting_persists_and_filters_junk(sandbox):
    root, state = sandbox
    _write_mod(root, "guard", hooks={"modules": ["./mod.mjs"]}, module="on('tool.call', () => {})")

    resp = asyncio.run(
        mods_routes.set_permissions(
            "guard",
            mods_routes.ModPermissionRequest(granted=["fs.write", "not-a-capability"], domains=["Example.COM"]),
            current_user=_User(),
        )
    )
    assert resp["permissions"]["granted"] == ["fs.write"], "unknown capabilities are dropped, not stored"
    assert resp["permissions"]["domains"] == ["example.com"], "domains are normalised"
    assert mods_compat.read_mod_permissions("guard", str(state))["granted"] == ["fs.write"]

    listed = asyncio.run(mods_routes.list_permissions(current_user=_User()))
    assert listed["mods"]["guard"]["granted"] == ["fs.write"]


def test_granting_for_an_unknown_mod_is_a_404(sandbox):
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        asyncio.run(mods_routes.set_permissions("nope", mods_routes.ModPermissionRequest(), current_user=_User()))


def test_the_list_payload_carries_each_mods_permissions(sandbox):
    root, state = sandbox
    _write_mod(root, "guard", hooks={"modules": ["./mod.mjs"]}, module="on('tool.call', () => {})")
    mods_compat.write_mod_permissions("guard", granted=["http.fetch"], domains=["example.com"], state_root=str(state))

    resp = asyncio.run(mods_routes.list_mods(current_user=_User()))
    assert resp["mods"][0]["permissions"]["granted"] == ["http.fetch"]


def test_lazy_mount_lands_before_the_static_mount():
    """The mods routes must be inserted *before* the catch-all StaticFiles Mount.

    ``_main.ensure_lazy_routers`` documents the trap: routes appended after the
    app's ``StaticFiles`` mount at ``/`` are shadowed and answer 404, which looks
    exactly like a missing handler.  Mods join the same lazy batch as admin and
    market, so this pins that the batch really lands ahead of the mount.
    """
    from app.ai_web.routes import _main as routes_main
    from fastapi import FastAPI
    from starlette.routing import Mount

    app = FastAPI()
    app.mount("/", Mount("/", app=lambda *a, **k: None))  # emulate the static mount
    routes_main._LAZY_ROUTERS_MOUNTED = False
    try:
        routes_main.ensure_lazy_routers(app)
    finally:
        routes_main._LAZY_ROUTERS_MOUNTED = False

    paths = [getattr(r, "path", "") for r in app.router.routes]
    mount_at = next(i for i, r in enumerate(app.router.routes) if isinstance(r, Mount))
    mods_at = paths.index("/api/ai-web/mods")
    assert mods_at < mount_at, "the mods route landed after the static mount and would be shadowed"
    assert "/api/ai-web/market/plugins" in paths or any(p.startswith("/api/ai-web/market") for p in paths)


# ── 2. the claim ───────────────────────────────────────────────────────────


def test_list_reports_compatibility_and_availability_separately(sandbox, monkeypatch):
    root, _state = sandbox
    _write_mod(root, "guard", hooks={"modules": ["./mod.mjs"]}, module="on('tool.call', ($, e, next) => next(e))")
    _write_mod(root, "nope", hooks={"modules": ["./mod.mjs"]}, module="on('prompt.edit', ($, e, next) => next(e))")

    resp = asyncio.run(mods_routes.list_mods(current_user=_User()))

    assert resp["ok"] is True
    by_name = {m["dir_name"]: m for m in resp["mods"]}
    assert by_name["guard"]["verdict"] == "runnable"
    assert by_name["nope"]["verdict"] == "blocked"
    assert by_name["nope"]["blocked_by"][0]["name"] == "prompt.edit"
    assert by_name["nope"]["blocked_by"][0]["why"]

    # Availability is a separate statement — "a host can start", never "this mod
    # runs" — and it must always carry the scope that is actually wired.
    assert resp["host"]["available"] == bool(resp["host"]["node_runtime"])
    assert "tool.call" in resp["host"]["scope"]
    monkeypatch.setattr(mods_compat, "_node_hint", lambda: "")
    missing_node = asyncio.run(mods_routes.list_mods(current_user=_User()))
    assert missing_node["host"]["available"] is False
    assert missing_node["host"]["reason"]

    # The matrix travels with every list response so the page can teach with zero mods.
    assert "tool.call" in resp["matrix"]["events"]["served"]
    assert any(d.startswith("$.store.") for d in resp["matrix"]["dollar"]["served"])


def test_list_on_an_empty_root_is_still_useful(sandbox):
    resp = asyncio.run(mods_routes.list_mods(current_user=_User()))
    assert resp["mods"] == []
    assert resp["counts"] == {}
    assert resp["matrix"]["events"]["served"]


def test_toggle_records_state_without_touching_the_vendor_manifest(sandbox):
    root, _state = sandbox
    _write_mod(root, "guard", hooks={"modules": ["./mod.mjs"]}, module="on('tool.call', ($, e, next) => next(e))")

    on = asyncio.run(mods_routes.enable_mod("guard", current_user=_User()))
    assert on["state"]["enabled"] is True
    # Enabling intent is not running the mod: the reply says only what is wired.
    assert "tool.call" in on["host"]["scope"]

    off = asyncio.run(mods_routes.disable_mod("guard", current_user=_User()))
    assert off["state"]["enabled"] is False

    manifest = json.loads((root / "guard" / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert "enabled" not in manifest


def test_import_copies_a_mod_and_rejects_a_non_mod(sandbox, tmp_path):
    _root, _state = sandbox
    src = pathlib.Path(
        _write_mod(tmp_path / "src", "imported", hooks={"modules": ["./mod.mjs"]}, module="on('tool.call', 1)")
    )

    resp = asyncio.run(mods_routes.import_mod(mods_routes.ModImportRequest(path=str(src)), current_user=_User()))
    assert resp["name"] == "imported"
    assert resp["mod"]["verdict"] == "runnable"

    listed = asyncio.run(mods_routes.list_mods(current_user=_User()))
    assert [m["dir_name"] for m in listed["mods"]] == ["imported"]

    # A directory that is not a mod must fail here, not appear as an empty card.
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(Exception) as exc:
        asyncio.run(mods_routes.import_mod(mods_routes.ModImportRequest(path=str(plain)), current_user=_User()))
    assert "not a mod" in str(exc.value)

    # Importing the same name twice is a conflict unless overwrite is asked for.
    with pytest.raises(Exception) as exc2:
        asyncio.run(mods_routes.import_mod(mods_routes.ModImportRequest(path=str(src)), current_user=_User()))
    assert "already exists" in str(exc2.value)


def test_unknown_mod_is_a_404_not_an_empty_ok(sandbox):
    with pytest.raises(Exception) as exc:
        asyncio.run(mods_routes.enable_mod("nope", current_user=_User()))
    assert "not found" in str(exc.value).lower()


def test_the_mods_routes_are_mounted_before_the_static_catch_all(monkeypatch):
    """The page's routes are added to a *live* app, where a mistake hides.

    `ensure_lazy_routers` splices the admin/market/mods routers into an already
    built app. Two ways it fails silently, and both look exactly like "the Mods
    page is broken": the `/api/ai-web` prefix is not baked into the copies (raw
    route objects carry none), or the routes land *after* the catch-all
    `StaticFiles` mount at `/`, which then answers 404 for everything.

    The real app was booted once to confirm it end to end — mods at route index
    294-299, the mount at 300. This pins the mechanism without paying for a full
    app boot (and an app boot reads the developer's own workspace).
    """
    from app.ai_web.routes import _main as routes_main
    from starlette.routing import Mount

    async def _noop(scope, receive, send):  # the mount needs an ASGI app
        return None

    mount = Mount("/", app=_noop, name="static")
    fake_app = types.SimpleNamespace(router=types.SimpleNamespace(routes=[mount]))
    monkeypatch.setattr(routes_main, "_LAZY_ROUTERS_MOUNTED", False)

    routes_main.ensure_lazy_routers(fake_app)

    paths = [getattr(r, "path", "") for r in fake_app.router.routes]
    mount_at = fake_app.router.routes.index(mount)
    for wanted in ("/api/ai-web/mods", "/api/ai-web/mods/permissions", "/api/ai-web/mods/import"):
        assert wanted in paths, f"{wanted} was never mounted — the page would 404"
        assert paths.index(wanted) < mount_at, f"{wanted} lands after the catch-all mount and is shadowed by it"
