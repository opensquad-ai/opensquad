"""Every gateway admin route the web client calls must actually be registered.

Found in the live deployment: the Agent Workstation's **Role Prompt** tab showed
``Error: Not Found`` for every agent.  Nothing was wrong with the data — the
launcher answers ``GET /api/agents/<name>/role`` from ``role.md`` just fine — and
nothing was wrong with the frontend, which calls
``/api/ai-web/admin/agents/<name>/role`` exactly as the docs say.

The handler had simply lost its decorator:

    async def admin_get_role(name: str, ...):      # <-- @admin_router.get(...) gone
        return await _proxy_get(f"/api/agents/{name}/role")

so ``admin_get_role`` was a perfectly good, perfectly unreferenced coroutine and
FastAPI never bound a path for it.  The route table kept ``PUT`` on the same URL,
which is what made it look like a permission or path problem: the request 404'd
with the framework default ``{"detail": "Not Found"}`` instead of the ``401 Token
required`` that every *registered* admin route returns before auth.

No test could see it, because the client's URL and the server's function were both
still present — only the line joining them was gone.  So this file pins the join two
ways: the exact endpoint, then the whole module for the same accident.

Mutations verified (each was applied, run, and reverted):
  M1 delete ``@admin_router.get("/admin/agents/{name}/role")`` -> R1 and R2 fail
  M2 register the same handler under ``/admin/agents/{name}/role2`` -> R1 fails
  M3 add an undecorated ``async def admin_new_thing()``        -> R2 fails

A third check was tried and dropped: scanning ``api.ts`` for every
``/ai-web/admin/...`` literal and requiring a registered route.  It reported 14
misses, and all 14 were artifacts of matching a path literal and its verb from
separate call sites (query strings glued onto the path, and a ``method: 'PUT'``
two functions down attributed to a plain GET).  Comparing the client surface with
the route table is worth doing, but only with a real TypeScript parse.
"""

from __future__ import annotations

import ast
import os
import pathlib

_BACKEND_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "src", "opensquad", "gateway", "backend")
)
if _BACKEND_ROOT not in os.sys.path:
    os.sys.path.insert(0, _BACKEND_ROOT)

from app.ai_web.routes import _admin as admin  # noqa: E402

ADMIN_MODULE = pathlib.Path(admin.__file__)


def _registered() -> set[tuple[str, str]]:
    """(method, path) pairs the admin router really exposes."""
    out: set[tuple[str, str]] = set()
    for route in admin.admin_router.routes:
        for method in getattr(route, "methods", ()) or ():
            if method in {"HEAD", "OPTIONS"}:
                continue
            out.add((method, route.path))
    return out


def test_role_endpoints_are_registered() -> None:
    """R1 — the exact 404: GET on the role URL, PUT was the only verb left."""
    routes = _registered()
    assert ("GET", "/admin/agents/{name}/role") in routes, (
        "adminAPI.getRole calls this; without it the Role Prompt tab renders 'Error: Not Found' for every agent"
    )
    assert ("PUT", "/admin/agents/{name}/role") in routes
    assert ("GET", "/admin/agents/{name}/config") in routes


def test_no_admin_handler_lost_its_decorator() -> None:
    """R2 — the class of bug, not just this instance.

    Every ``admin_*`` coroutine in this module is a route handler by convention, so
    an undecorated one is dead code the framework will never reach.
    """
    tree = ast.parse(ADMIN_MODULE.read_text(encoding="utf-8"))
    orphans = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
        and node.name.startswith("admin_")
        and not node.decorator_list
    ]
    assert not orphans, f"handlers with no @admin_router decorator: {orphans}"
