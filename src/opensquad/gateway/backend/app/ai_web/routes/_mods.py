"""Mods routes — third-party Claude Code mods, as seen by this host.

A *mod* is a plugin whose behaviour lives in JavaScript hooks
(``.claude-plugin/plugin.json`` + ``hooks/hooks.json`` + a module exporting
``register(on, options)``).  The Mods page in System Settings is a management
surface for them, and this router is its whole backend.

Why this lives in the gateway rather than the launcher: the scan is pure file IO
over the workspace, which the gateway already does for the market, skills and
roles (see ``_market.py``).  The launcher's management API is a separately pinned
surface (``tests/test_management_api_surface.py`` counts its routes and mixin
methods), and nothing here needs the launcher's process.

Honesty contract — three things this API refuses to blur:

1. **Compatibility is not availability.**  ``scan_mod`` only says whether a mod's
   references are inside the served subset.  ``host.available`` means only "a host
   can start here" (a node runtime exists) and always carries the ``scope`` of
   what is actually wired; each mod's ``plan.effect`` says what a user would
   actually notice.  Enabling a mod records intent — the host picks it up.
2. **A refusal names its gap.**  Every ``blocked_by`` entry carries a ``why``
   string from the compatibility table, so the user reads *what is missing*
   instead of "unsupported".
3. **The toggle never touches the vendor manifest.**  A mod's
   ``.claude-plugin/plugin.json`` belongs to its author; our state goes to
   ``<workspace>/data/mods/<name>/state.json``.
4. **Permissions are a contract, not a sandbox.**  The four capabilities that
   leave the mod's own world (``fs.write`` / ``process.run`` / ``http.fetch`` /
   ``env.set``) are default-deny and granted per mod here.  A mod is ordinary
   Node code and can reach the filesystem without asking, so the honest boundary
   is consent at this point, not enforcement at call time.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api import get_current_user_dep
from app.models import User
from opensquad import mods_compat

logger = logging.getLogger(__name__)

mods_router = APIRouter()


class ModPermissionRequest(BaseModel):
    """Grant/revoke the gated capabilities for one mod, plus its URL allowlist."""

    granted: list[str] = []
    domains: list[str] = []


class ModImportRequest(BaseModel):
    """Add a mod from a local directory (the mod is copied into the workspace)."""

    path: str
    name: str = ""
    overwrite: bool = False


def _find_mod(name: str) -> tuple[str, str]:
    """``(dir_name, mod_dir)`` for an installed mod, or 404."""
    root = mods_compat.mods_root()
    for info in mods_compat.discover_mods(root):
        if name in (info["dir_name"], info["name"]):
            return info["dir_name"], info["dir"]
    raise HTTPException(status_code=404, detail=f"Mod '{name}' not found")


@mods_router.get("/mods")
async def list_mods(current_user: User = Depends(get_current_user_dep)):
    """Every installed mod with its static compatibility verdict, plus the host's
    own capability table (so the page is informative even with zero mods)."""
    root = mods_compat.mods_root()
    mods = await asyncio.to_thread(mods_compat.discover_mods)
    counts: dict[str, int] = {}
    for info in mods:
        counts[info["verdict"]] = counts.get(info["verdict"], 0) + 1
        # Per-contribution loading: the page must be able to say "will load" and
        # "which contributions stay inert", not just a static verdict.
        info["plan"] = mods_compat.load_plan(info)
        info["permissions"] = mods_compat.read_mod_permissions(info["dir_name"])
    return {
        "ok": True,
        "root": root,
        "mods": mods,
        "counts": counts,
        "matrix": mods_compat.matrix_summary(),
        "host": mods_compat.host_status(),
    }


@mods_router.get("/mods/permissions")
async def list_permissions(current_user: User = Depends(get_current_user_dep)):
    """The four gated capabilities, what each means, and who currently holds them."""
    mods = await asyncio.to_thread(mods_compat.discover_mods)
    return {
        "ok": True,
        "capabilities": [
            {"id": cap, "blurb": mods_compat.PERMISSION_BLURB.get(cap, "")} for cap in mods_compat.GATED_CAPABILITIES
        ],
        "mods": {info["dir_name"]: mods_compat.read_mod_permissions(info["dir_name"]) for info in mods},
    }


@mods_router.put("/mods/{name}/permissions")
async def set_permissions(name: str, body: ModPermissionRequest, current_user: User = Depends(get_current_user_dep)):
    dir_name, _mod_dir = _find_mod(name)
    state = mods_compat.write_mod_permissions(dir_name, granted=body.granted, domains=body.domains)
    logger.info("[Mods] permissions for '%s' → %s", dir_name, state["granted"])
    return {"ok": True, "name": dir_name, "permissions": state}


@mods_router.put("/mods/{name}/enable")
async def enable_mod(name: str, current_user: User = Depends(get_current_user_dep)):
    return _set_enabled(name, True)


@mods_router.put("/mods/{name}/disable")
async def disable_mod(name: str, current_user: User = Depends(get_current_user_dep)):
    return _set_enabled(name, False)


def _set_enabled(name: str, enabled: bool) -> dict:
    dir_name, _mod_dir = _find_mod(name)
    state = mods_compat.write_mod_state(dir_name, enabled=enabled)
    # These are two different facts and the response keeps them apart.
    return {
        "ok": True,
        "name": dir_name,
        "state": state,
        "host": mods_compat.host_status(),
    }


@mods_router.post("/mods/import")
async def import_mod(body: ModImportRequest, current_user: User = Depends(get_current_user_dep)):
    """Copy a mod directory from disk into ``<workspace>/mods``.

    The minimum that makes the page usable before a mods marketplace exists: point
    at a cloned mod repo and manage it.  The source must actually look like a mod
    (a manifest or a hooks file), so a mistyped path fails here instead of
    appearing as an empty entry.

    The source path arrives in the request because that *is* the feature, and the
    reads below are exactly what CodeQL's ``py/path-injection`` query flags. That
    query is suppressed for this module alone, with the reasoning, in
    ``.github/codeql/codeql-config.yml``; the guards that matter here are the
    "must already look like a mod" check and the basename pin on the destination.
    """
    src = os.path.abspath((body.path or "").strip())
    if not src or not os.path.isdir(src):
        raise HTTPException(status_code=400, detail="path must be an existing directory")

    has_manifest = os.path.isfile(os.path.join(src, ".claude-plugin", "plugin.json"))
    has_hooks = os.path.isfile(os.path.join(src, "hooks", "hooks.json"))
    if not (has_manifest or has_hooks):
        raise HTTPException(
            status_code=400,
            detail="not a mod: expected .claude-plugin/plugin.json or hooks/hooks.json",
        )

    # Prefer the name the manifest declares, never a path component the user typed.
    info = await asyncio.to_thread(mods_compat.scan_mod, src)
    dir_name = (body.name or info["dir_name"] or info["name"]).strip()
    dir_name = os.path.basename(dir_name)  # a mod id is one path segment
    if not dir_name or dir_name in (".", ".."):
        raise HTTPException(status_code=400, detail="could not derive a mod name")

    root = mods_compat.mods_root()
    dest = os.path.join(root, dir_name)
    if os.path.exists(dest):
        if not body.overwrite:
            raise HTTPException(status_code=409, detail=f"mod '{dir_name}' already exists")
        await asyncio.to_thread(shutil.rmtree, dest, True)
    os.makedirs(root, exist_ok=True)
    await asyncio.to_thread(shutil.copytree, src, dest)

    logger.info("[mods] imported '%s' from %s", dir_name, src)
    return {"ok": True, "name": dir_name, "mod": await asyncio.to_thread(mods_compat.scan_mod, dest)}
