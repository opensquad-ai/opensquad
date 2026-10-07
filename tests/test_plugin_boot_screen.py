"""Plugin-contributed boot screen (缝 B) — resolver + route contract.

``resolve_boot_screen`` is the whole decision: which enabled plugin, if any,
contributes a startup animation, which clips it may rotate through, and the URL
the shell should play. It is pure file reading with injected roots, so these
tests never touch the real workspace.

Two contracts are pinned separately because each fails silently:

* **the routes stay public** — the shell's boot loader runs *before login*, so an
  auth dependency would kill the feature on a logged-out first paint;
* **the asset route is confined** — its only inputs are ``kind`` (an enum) and a
  playlist ``index``, never a path, so it cannot be pointed at an arbitrary file.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys

import pytest

from opensquad import plugin_boot_screen


def _plugin(root, name, *, enabled=True, contributes=None, files=(), contents=None):
    """Write a minimal plugin dir; `contents` lets a test give each file its own bytes."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    meta = {"name": name, "enabled": enabled, "contributes": contributes or {}}
    (d / "plugin.json").write_text(json.dumps(meta), encoding="utf-8")
    for rel in files:
        target = d / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((contents or {}).get(rel, b"x"))
    return d


def _boot_video(**extra):
    return {"bootScreen": {"video": "assets/boot.mp4", **extra}}


def _boot_playlist(paths=("assets/1.mp4", "assets/2.mp4", "assets/3.mp4"), **extra):
    return {"bootScreen": {"videos": list(paths), **extra}}


# ── the resolver ───────────────────────────────────────────────────────────


def test_resolves_a_single_clip_plugin(tmp_path):
    _plugin(
        tmp_path,
        "theme",
        contributes=_boot_video(poster="assets/boot.jpg", holdMs=2500),
        files=["assets/boot.mp4", "assets/boot.jpg"],
    )
    out = plugin_boot_screen.resolve_boot_screen([str(tmp_path)])
    assert out["enabled"] is True
    assert out["plugin"] == "theme"
    assert len(out["videos"]) == 1
    assert out["videos"][0].startswith(plugin_boot_screen.ASSET_ROUTE + "?kind=video&index=0")
    assert out["poster"].startswith(plugin_boot_screen.ASSET_ROUTE + "?kind=poster")
    assert out["holdMs"] == 2500


def test_a_playlist_is_resolved_in_declaration_order(tmp_path):
    _plugin(tmp_path, "theme", contributes=_boot_playlist(), files=["assets/1.mp4", "assets/2.mp4", "assets/3.mp4"])
    urls = plugin_boot_screen.resolve_boot_screen([str(tmp_path)])["videos"]
    assert len(urls) == 3
    assert "?kind=video&index=0&v=" in urls[0]
    assert "?kind=video&index=1&v=" in urls[1]
    assert "?kind=video&index=2&v=" in urls[2]


def test_clips_that_do_not_exist_are_dropped_not_fatal(tmp_path):
    """One dead entry must not stop the rest of the playlist."""
    _plugin(tmp_path, "theme", contributes=_boot_playlist(("assets/a.mp4", "assets/ghost.mp4")), files=["assets/a.mp4"])
    out = plugin_boot_screen.resolve_boot_screen([str(tmp_path)])
    assert out["enabled"] is True
    assert len(out["videos"]) == 1


def test_a_playlist_of_nothing_usable_is_disabled(tmp_path):
    _plugin(tmp_path, "theme", contributes={"bootScreen": {"videos": ["assets/nope.mp4", "assets/more.mp4"]}})
    assert plugin_boot_screen.resolve_boot_screen([str(tmp_path)]) == {"enabled": False}


def test_a_disabled_plugin_is_ignored(tmp_path):
    _plugin(tmp_path, "theme", enabled=False, contributes=_boot_video(), files=["assets/boot.mp4"])
    assert plugin_boot_screen.resolve_boot_screen([str(tmp_path)]) == {"enabled": False}


def test_no_contribution_means_disabled(tmp_path):
    _plugin(tmp_path, "theme", contributes={})
    assert plugin_boot_screen.resolve_boot_screen([str(tmp_path)]) == {"enabled": False}


def test_a_declaration_with_no_file_is_not_advertised(tmp_path):
    """A dead URL is worse than no animation — the frontend would show nothing."""
    _plugin(tmp_path, "theme", contributes={"bootScreen": {"video": "assets/missing.mp4"}})
    assert plugin_boot_screen.resolve_boot_screen([str(tmp_path)]) == {"enabled": False}


@pytest.mark.parametrize("bad", ["../secret.mp4", "/etc/passwd", "https://evil.example/x.mp4", "C:/x.mp4", ""])
def test_escaping_or_off_site_paths_are_rejected(tmp_path, bad):
    _plugin(tmp_path, "theme", contributes={"bootScreen": {"videos": [bad]}}, files=["assets/boot.mp4"])
    assert plugin_boot_screen.resolve_boot_screen([str(tmp_path)]) == {"enabled": False}


def test_hold_ms_is_clamped(tmp_path):
    _plugin(tmp_path, "theme", contributes=_boot_video(holdMs=10**9), files=["assets/boot.mp4"])
    out = plugin_boot_screen.resolve_boot_screen([str(tmp_path)])
    assert out["holdMs"] == plugin_boot_screen.MAX_HOLD_MS


def test_poster_is_only_emitted_when_it_exists(tmp_path):
    _plugin(tmp_path, "theme", contributes=_boot_video(poster="missing.jpg"), files=["assets/boot.mp4"])
    out = plugin_boot_screen.resolve_boot_screen([str(tmp_path)])
    assert out["enabled"] is True
    assert out["poster"] == ""


def test_the_operators_own_plugins_win_over_the_shipped_ones(tmp_path):
    """The whole point of the private/public split.

    A private animation lives in the *workspace* plugins dir (never packaged), so
    it must win locally — even when the shipped plugin sorts first alphabetically.
    A clean public install has no workspace plugin and falls back to the shipped
    one; with neither, the built-in loader in index.html stays.
    """
    shipped = tmp_path / "builtin"
    mine = tmp_path / "workspace"
    _plugin(shipped, "aa_shipped", contributes=_boot_video(), files=["assets/boot.mp4"])
    _plugin(mine, "zz_private", contributes=_boot_playlist(), files=["assets/1.mp4", "assets/2.mp4", "assets/3.mp4"])

    roots = [str(mine), str(shipped)]  # boot_screen_roots() order
    assert plugin_boot_screen.resolve_boot_screen(roots)["plugin"] == "zz_private"
    # Without the private one (a public install), the shipped animation is used.
    assert plugin_boot_screen.resolve_boot_screen([str(shipped)])["plugin"] == "aa_shipped"
    # And with nothing at all, the shell keeps its own loader.
    assert plugin_boot_screen.resolve_boot_screen([]) == {"enabled": False}


def test_missing_root_is_survivable(tmp_path):
    assert plugin_boot_screen.resolve_boot_screen([str(tmp_path / "nope")]) == {"enabled": False}


# ── resolve_asset ──────────────────────────────────────────────────────────


def test_resolve_asset_picks_the_playlist_member(tmp_path):
    _plugin(tmp_path, "theme", contributes=_boot_playlist(), files=["assets/1.mp4", "assets/2.mp4", "assets/3.mp4"])
    for index in (0, 1, 2):
        path = plugin_boot_screen.resolve_asset("video", index, [str(tmp_path)])
        assert path.endswith(os.path.join("assets", f"{index + 1}.mp4"))
        assert os.path.isfile(path)


@pytest.mark.parametrize("index", [-1, 3, 99])
def test_an_out_of_range_index_is_not_served(tmp_path, index):
    _plugin(tmp_path, "theme", contributes=_boot_playlist(), files=["assets/1.mp4", "assets/2.mp4", "assets/3.mp4"])
    assert plugin_boot_screen.resolve_asset("video", index, [str(tmp_path)]) == ""


@pytest.mark.parametrize("kind", ["", "nope", "../plugin.json", "assets/1.mp4"])
def test_resolve_asset_only_accepts_the_enum(tmp_path, kind):
    """`kind` is an enum, never a path — that is what keeps the route confined."""
    _plugin(tmp_path, "theme", contributes=_boot_video(), files=["assets/boot.mp4"])
    assert plugin_boot_screen.resolve_asset(kind, 0, [str(tmp_path)]) == ""


def test_resolve_asset_is_empty_without_a_declaration(tmp_path):
    assert plugin_boot_screen.resolve_asset("video", 0, [str(tmp_path)]) == ""


# ── the routes ─────────────────────────────────────────────────────────────

_BACKEND_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "src", "opensquad", "gateway", "backend")
)
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)


def _endpoint(path: str):
    from app.ai_web.routes import _main as routes_main

    for route in routes_main.router.routes:
        route_path = getattr(route, "path", None) or ""
        if route_path.endswith(path) and "GET" in (getattr(route, "methods", ()) or ()):
            return route.endpoint
    return None


def test_both_routes_are_registered_on_the_eager_router():
    assert _endpoint("/boot-screen") is not None, "the shell's loader would 404"
    assert _endpoint("/boot-screen/asset") is not None, "the animation would 404"


@pytest.mark.parametrize("path", ["/boot-screen", "/boot-screen/asset"])
def test_the_routes_need_no_authentication(path):
    """The boot loader runs before login — an auth dependency would break it."""
    from fastapi.params import Depends as DependsParam

    deps = [p for p in inspect.signature(_endpoint(path)).parameters.values() if isinstance(p.default, DependsParam)]
    assert deps == [], f"{path} must stay public"


def test_the_config_route_passes_through_the_resolver(monkeypatch):
    from app.ai_web.routes import _main as routes_main

    monkeypatch.setattr(plugin_boot_screen, "resolve_boot_screen", lambda: {"enabled": True, "videos": ["/x"]})
    assert asyncio.run(routes_main.get_boot_screen())["enabled"] is True


def test_the_config_route_fails_open(monkeypatch):
    from app.ai_web.routes import _main as routes_main

    def boom(roots=None):
        raise RuntimeError("resolution blew up")

    monkeypatch.setattr(plugin_boot_screen, "resolve_boot_screen", boom)
    assert asyncio.run(routes_main.get_boot_screen()) == {"enabled": False}


def test_the_asset_route_404s_instead_of_guessing(monkeypatch):
    from app.ai_web.routes import _main as routes_main
    from fastapi import HTTPException

    monkeypatch.setattr(plugin_boot_screen, "resolve_asset", lambda kind, index=0, roots=None: "")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes_main.get_boot_screen_asset(kind="video", index=7))
    assert exc.value.status_code == 404


# ── the whole chain, over a real ASGI app ─────────────────────────────────


def test_the_declared_assets_are_actually_served(tmp_path, monkeypatch):
    """End to end: endpoint → URLs → the same bytes, over real ASGI.

    The URL the resolver emits is only useful if the route really serves that
    file, with Range (what a ``<video>`` streams with). A mismatch would be a
    boot loader pointing at a 404 — a splash that silently never appears — and no
    unit test of either half would see it.
    """
    import httpx
    from app.ai_web.routes import _main as routes_main
    from fastapi import FastAPI

    root = tmp_path / "plugins"
    _plugin(
        root,
        "theme",
        contributes={"bootScreen": {"videos": ["assets/a.mp4", "assets/b.mp4"]}},
        files=["assets/a.mp4", "assets/b.mp4"],
        contents={"assets/a.mp4": b"one", "assets/b.mp4": b"two"},
    )
    monkeypatch.setattr(plugin_boot_screen, "boot_screen_roots", lambda: [str(root)])

    app = FastAPI()
    app.get("/api/ai-web/boot-screen")(routes_main.get_boot_screen)
    app.get("/api/ai-web/boot-screen/asset")(routes_main.get_boot_screen_asset)

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            config = (await client.get("/api/ai-web/boot-screen")).json()
            assert config["enabled"] is True

            for i, expected in enumerate((b"one", b"two")):
                url = config["videos"][i]
                asset = await client.get(url)
                assert asset.status_code == 200, f"{url} is not served — the splash would be blank"
                assert asset.content == expected, "the index must reach the right clip, not just any clip"

                ranged = await client.get(url, headers={"Range": "bytes=0-0"})
                assert ranged.status_code == 206, "a <video> element streams with Range; it must be supported"
                assert ranged.headers["content-range"] == f"bytes 0-0/{len(expected)}"

            # An index past the playlist is a clean 404, never a wrong clip.
            past = config["videos"][0].replace("index=0", "index=9")
            assert (await client.get(past)).status_code == 404

    asyncio.run(_run())


def test_the_route_is_included_before_the_static_catch_all():
    """`/api/ai-web/*` must be registered before the frontend ``StaticFiles`` mount.

    ``app.include_router(ai_web_router)`` copies routes at include time; anything
    mounted after the catch-all ``/`` mount is shadowed and answers 404 — which
    looks exactly like a missing handler. Pinned against ``main.py`` itself, since
    the ordering lives there and cannot be seen from the router object.
    """
    import pathlib

    main_py = pathlib.Path(_BACKEND_ROOT) / "app" / "main.py"
    source = main_py.read_text(encoding="utf-8")
    include_at = source.index("app.include_router(ai_web_router)")
    mount_at = source.index('app.mount("/", _FrontendStaticFiles')
    assert include_at < mount_at, "the ai-web routes land after the static mount and would be shadowed"
