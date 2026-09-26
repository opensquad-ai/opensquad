"""Surface tests for the split Launcher management API package.

``launcher_main._start_management_server`` used to be one ~3.6k-line function
holding a ~120-method ``ManagementHandler``.  It was extracted into
``opensquad.launcher.management_api``, where the handler is composed from
per-domain mixins and the four ``_do_*_impl`` dispatch tables stayed put in
``_base.py``.

That split has one property the rest of the suite cannot see: the package is
imported **lazily**, from inside ``_start_management_server`` -- nothing else
in the codebase touches it.  A plain ``test_*`` collection therefore never
imports it, so a broken export name, a bad mixin import or an MRO conflict
would stay invisible until a launcher actually tried to bind port 9600.

This file closes that hole.  It pins, in order of increasing strength:

1. the package imports and exports what callers import,
2. the handler is composed from all ten domain mixins (and nothing shadows
   ``__init__``),
3. every extracted method is still present, and the four dispatch tables
   still expose the same number of branches,
4. the composed class really serves HTTP -- a live socket, a live
   request/response, real auth.

A regression this guards against, found while writing it: ``_server.py``
defined the class as ``_ExclusiveHTTPServer`` (the underscore was meaningful
when it was function-local) while ``__init__`` and ``launcher_main`` both
imported ``ExclusiveHTTPServer``.  ``pytest`` was green; the launcher was
not importable.
"""

import ast
import json
import os
import pathlib
import socket
import threading
import urllib.error

import pytest

import opensquad.launcher.management_api as management_api
from opensquad.launcher.management_api import ExclusiveHTTPServer, ManagementHandler
from opensquad.utils.local_http import open_local

PKG_DIR = pathlib.Path(management_api.__file__).parent

# The ten per-domain mixins, in the order ``__init__`` composes them.
MIXIN_MODULE_NAMES = [
    "_base",
    "_agents",
    "_filesystem",
    "_plugins",
    "_plugin_services",
    "_sessions",
    "_mcp",
    "_skills",
    "_cards",
    "_workspace",
]

# Class defined by each module above -- module names do not map to class names
# by any simple rule, so they are listed rather than derived.
EXPECTED_MIXIN_CLASSES = [
    "BaseHandlerMixin",
    "AgentsMixin",
    "FilesystemMixin",
    "PluginsMixin",
    "PluginServicesMixin",
    "SessionsMixin",
    "McpMixin",
    "SkillsMixin",
    "CardsMixin",
    "WorkspaceMixin",
]

# Method count per mixin module, as produced by the extraction.  Pinned so a
# method silently vanishing (or its module being dropped from the MRO) fails
# loudly instead of quietly shrinking the route surface.
EXPECTED_METHODS_PER_MODULE = {
    "_base": 18,
    # 17 = 16 + ``_handle_put_agent_profile`` (PUT /api/agents/{name}/profile),
    # which backs the custom-agent-avatar upload.
    "_agents": 17,
    "_filesystem": 19,
    "_plugins": 15,
    "_plugin_services": 10,
    "_sessions": 10,
    "_mcp": 6,
    "_skills": 2,
    # 18 = 17 + ``_apply_card_to_agents``, which pushes a saved model card's
    # capability switches and model fields (base_url / model_name / api_key /
    # image_size / …) into every agent whose model block references it — the card
    # is a template, each agent owns a copy, so flipping a switch there used to
    # change nothing for the agents that use it.  Per-agent tuning (temperature,
    # top_k, render_mode, …) is not synced.
    "_cards": 18,
    "_workspace": 6,
}
EXPECTED_TOTAL_MIXIN_METHODS = 121

# ``_do_*_impl`` if/elif chain lengths -- the URL surface of each verb.
EXPECTED_DISPATCH_BRANCHES = {
    "_do_get_impl": 43,
    "_do_post_impl": 31,
    # 18 = 17 + the ``/api/agents/{name}/profile`` PUT branch.
    "_do_put_impl": 18,
    "_do_delete_impl": 7,
}

# Dispatch targets that do NOT resolve on the composed handler.  Every entry is
# a route whose handler was lost, so a request returns 500 rather than doing
# work.  Empty is the goal; the list is a debt marker, not an allowance --
# ``test_every_dispatch_branch_delegates_to_a_handler`` fails both when a new
# broken target appears AND when a listed one starts resolving again.
KNOWN_UNRESOLVABLE_DISPATCH_TARGETS = {
    # POST /api/runtime/cleanup.
    # PRE-EXISTING, not introduced by the mixin split: at HEAD (before this
    # split) the route and its handler were already out of sync.  Commit
    # 3c5ebb9 ("perf: compact tool schemas ... Drop the unwired _launcher_api
    # package so launcher_main is the only HTTP API") deleted
    # ``_launcher_api/_system_handler.py``, the only place
    # ``_handle_runtime_cleanup`` was ever defined, while re-adding the route to
    # ``_do_post_impl`` without its body.  The v0.4.0 implementation was::
    #
    #     def _handle_runtime_cleanup(self, body: dict):
    #         """POST /api/runtime/cleanup -- clean up runtime registry."""
    #         force_kill = body.get("force_kill", False) if isinstance(body, dict) else False
    #         result = self.state.cln_reg(force_kill=force_kill)
    #         return self._send_json(result)
    #
    # Observed live: 500 {"error": "Internal server error: 'ManagementHandler'
    # object has no attribute '_handle_runtime_cleanup'"}.
    "_handle_runtime_cleanup",
}

TEST_TOKEN = "test-launcher-token"


# ── helpers ──


def _module_tree(name: str) -> ast.Module:
    return ast.parse((PKG_DIR / f"{name}.py").read_text(encoding="utf-8"))


def _methods_per_module() -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in MIXIN_MODULE_NAMES:
        tree = _module_tree(name)
        counts[name] = sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
            for item in node.body
            if isinstance(item, ast.FunctionDef)
        )
    return counts


def _branch_count(module: str, class_name: str, func_name: str) -> int:
    """Length of the top-level if/elif chain inside ``class_name.func_name``."""
    for node in ast.walk(_module_tree(module)):
        if not (isinstance(node, ast.ClassDef) and node.name == class_name):
            continue
        for item in node.body:
            if not (isinstance(item, ast.FunctionDef) and item.name == func_name):
                continue
            chain = next((st for st in item.body if isinstance(st, ast.If)), None)
            if chain is None:
                return 0
            count = 1
            while chain.orelse and len(chain.orelse) == 1 and isinstance(chain.orelse[0], ast.If):
                count += 1
                chain = chain.orelse[0]
            return count
    raise AssertionError(f"{class_name}.{func_name} not found in {module}.py")


@pytest.fixture
def _no_launcher_token(monkeypatch):
    """Remove any ambient launcher token so auth is an explicit per-test choice."""
    monkeypatch.setattr(ManagementHandler, "_get_launcher_token", staticmethod(lambda: ""))


@pytest.fixture
def _fixed_launcher_token(monkeypatch):
    """Require a known Bearer token, exercising ``_check_auth`` for real."""
    monkeypatch.setattr(ManagementHandler, "_get_launcher_token", staticmethod(lambda: TEST_TOKEN))


@pytest.fixture
def live_server():
    """A real ``ExclusiveHTTPServer`` on an ephemeral port, serving in a thread."""
    server = ExclusiveHTTPServer(("127.0.0.1", 0), ManagementHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _get(url: str, token: str | None = None, timeout: float = 5):
    headers = {"Authorization": f"Bearer {token}"} if token is not None else None
    return open_local(url, timeout=timeout, headers=headers)


# ── 1. import & export surface ──


def test_package_exports_both_public_names():
    """The names ``launcher_main`` imports must exist on the package."""
    assert management_api.__all__ == ["ExclusiveHTTPServer", "ManagementHandler"]
    assert isinstance(ManagementHandler, type)
    assert isinstance(ExclusiveHTTPServer, type)


def test_launcher_main_import_site_matches_package_exports():
    """``_start_management_server`` imports exactly the names the package exports.

    This is the assertion that would have caught the ``_ExclusiveHTTPServer`` /
    ``ExclusiveHTTPServer`` mismatch: the two sides are now checked against each
    other rather than each being assumed correct.
    """
    launcher_src = (PKG_DIR.parents[1] / "launcher_main.py").read_text(encoding="utf-8")
    tree = ast.parse(launcher_src)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "opensquad.launcher.management_api":
            imported.update(alias.name for alias in node.names)

    assert imported == set(management_api.__all__), (
        f"launcher_main imports {sorted(imported)} but the package exports {sorted(management_api.__all__)}"
    )
    for name in imported:
        assert getattr(management_api, name, None) is not None


def test_server_class_owns_the_exclusive_bind_override():
    """The Windows dual-bind guard must survive the move out of launcher_main."""
    assert ExclusiveHTTPServer.server_bind.__qualname__.startswith("ExclusiveHTTPServer.")
    # Windows: not reusing means a losing launcher gets EADDRINUSE and exits.
    assert ExclusiveHTTPServer.allow_reuse_address == (os.name != "nt")


# ── 2. mixin composition ──


def test_handler_is_composed_from_every_domain_mixin():
    package_classes = [
        cls.__name__
        for cls in ManagementHandler.__mro__
        if cls.__module__.startswith("opensquad.launcher.management_api")
    ]
    assert package_classes == ["ManagementHandler", *EXPECTED_MIXIN_CLASSES]


def test_mixins_are_composable_and_reach_base_http_handler():
    mro_names = [cls.__name__ for cls in ManagementHandler.__mro__]
    assert mro_names[-1] == "object"
    assert "BaseHTTPRequestHandler" in mro_names
    # BaseHTTPRequestHandler must come after every mixin, otherwise its
    # log_message()/handle_one_request() would shadow the mixin overrides.
    assert mro_names.index("BaseHTTPRequestHandler") > mro_names.index("BaseHandlerMixin")


def test_no_mixin_defines_init():
    """MRO composition stays linear as long as no mixin takes over __init__.

    The pre-split class defined no ``__init__`` either -- construction is
    ``BaseHTTPRequestHandler``'s job.  A mixin adding one (or a ``__getattr__``)
    would change how every handler instance is built, so fail loudly here.
    """
    offenders = []
    for name in MIXIN_MODULE_NAMES:
        for node in ast.walk(_module_tree(name)):
            if not isinstance(node, ast.ClassDef):
                continue
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name in {"__init__", "__getattr__", "__setattr__"}:
                    offenders.append(f"{name}.py::{node.name}.{item.name}")
    assert not offenders, f"mixins must not define __init__/__getattr__/__setattr__: {offenders}"


def test_mixin_modules_define_their_documented_class():
    """``MIXIN_MODULE_NAMES`` and ``EXPECTED_MIXIN_CLASSES`` stay in lockstep."""
    for module_name, class_name in zip(MIXIN_MODULE_NAMES, EXPECTED_MIXIN_CLASSES, strict=True):
        classes = [n.name for n in ast.walk(_module_tree(module_name)) if isinstance(n, ast.ClassDef)]
        assert class_name in classes, f"{module_name}.py should define {class_name}, defines {classes}"


def test_handler_methods_live_on_the_composed_class():
    """Every extracted method must be reachable through the MRO, not orphaned."""
    own_methods = {
        item.name
        for name in MIXIN_MODULE_NAMES
        for node in ast.walk(_module_tree(name))
        if isinstance(node, ast.ClassDef)
        for item in node.body
        if isinstance(item, ast.FunctionDef)
    }
    missing = sorted(m for m in own_methods if not callable(getattr(ManagementHandler, m, None)))
    assert not missing, f"methods defined in the package but not resolvable on ManagementHandler: {missing}"

    # The routing entry points must resolve to the mixin implementations, not
    # to BaseHTTPRequestHandler's stubs.
    for router in ("do_GET", "do_POST", "do_PUT", "do_DELETE", "do_OPTIONS"):
        assert getattr(ManagementHandler, router).__module__ == "opensquad.launcher.management_api._base"


# ── 3. extraction completeness & route surface ──


def test_method_inventory_is_unchanged():
    per_module = _methods_per_module()
    assert per_module == EXPECTED_METHODS_PER_MODULE
    assert sum(per_module.values()) == EXPECTED_TOTAL_MIXIN_METHODS


def test_dispatch_tables_keep_their_branch_count():
    """Pin the URL surface: one branch == one routed pattern."""
    actual = {fn: _branch_count("_base", "BaseHandlerMixin", fn) for fn in EXPECTED_DISPATCH_BRANCHES}
    assert actual == EXPECTED_DISPATCH_BRANCHES


def test_every_dispatch_branch_delegates_to_a_handler():
    """Each dispatch branch ends in ``return self._handle_*`` (or a JSON send).

    Branch counts alone would not notice a branch being rewritten to do
    nothing, so check the call targets too.
    """
    targets = set()
    for node in ast.walk(_module_tree("_base")):
        if isinstance(node, ast.ClassDef) and node.name == "BaseHandlerMixin":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name in EXPECTED_DISPATCH_BRANCHES:
                    for call in ast.walk(item):
                        if (
                            isinstance(call, ast.Call)
                            and isinstance(call.func, ast.Attribute)
                            and call.func.attr.startswith("_handle_")
                        ):
                            targets.add(call.func.attr)
    assert targets, "no _handle_* delegation found in the dispatch tables"
    missing = set(t for t in targets if not callable(getattr(ManagementHandler, t, None)))

    unexpected = sorted(missing - KNOWN_UNRESOLVABLE_DISPATCH_TARGETS)
    assert not unexpected, f"new broken route(s) -- router delegates to a method that does not exist: {unexpected}"

    fixed = sorted(KNOWN_UNRESOLVABLE_DISPATCH_TARGETS - missing)
    assert not fixed, (
        f"these dispatch targets resolve again, so the debt marker is stale -- "
        f"remove them from KNOWN_UNRESOLVABLE_DISPATCH_TARGETS: {fixed}"
    )


@pytest.mark.xfail(
    strict=True,
    reason="pre-existing: POST /api/runtime/cleanup lost its handler in 3c5ebb9",
)
def test_runtime_cleanup_route_is_wired_before_its_handler_was_lost(live_server, _no_launcher_token):
    """Documents the one broken route: dispatch *reaches* it, but it 500s.

    A 404 here would mean the route itself was dropped (fine); a 200 would mean
    the handler came back.  Either one makes this strict xfail fail, which is
    the point -- the outcome changes only when someone touches the route.
    """
    url = f"http://127.0.0.1:{live_server}/api/runtime/cleanup"
    with open_local(
        url, timeout=5, method="POST", data=b'{"force_kill": false}', headers={"Content-Type": "application/json"}
    ) as resp:
        assert resp.status == 200


# ── 4. live request/response ──


def test_ping_route_serves_real_http(live_server, _no_launcher_token):
    url = f"http://127.0.0.1:{live_server}/api/ping"
    with _get(url) as resp:
        assert resp.status == 200
        assert json.loads(resp.read().decode("utf-8")) == {"status": "ok", "service": "launcher"}


def test_unknown_route_returns_404(live_server, _no_launcher_token):
    url = f"http://127.0.0.1:{live_server}/api/definitely-not-a-route"
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _get(url)
    assert exc_info.value.code == 404
    assert json.loads(exc_info.value.read().decode("utf-8")) == {"error": "Not found"}


def test_auth_wrapper_runs_through_the_composed_class(live_server, _fixed_launcher_token):
    """A configured token gates GET: missing -> 401, wrong -> 403, right -> 200."""
    url = f"http://127.0.0.1:{live_server}/api/ping"

    with pytest.raises(urllib.error.HTTPError) as missing:
        _get(url)
    assert missing.value.code == 401

    with pytest.raises(urllib.error.HTTPError) as wrong:
        _get(url, token="not-the-token")
    assert wrong.value.code == 403

    with _get(url, token=TEST_TOKEN) as ok:
        assert ok.status == 200
        assert json.loads(ok.read().decode("utf-8"))["status"] == "ok"


def test_ephemeral_bind_does_not_collide_with_a_running_launcher():
    """Binding, then releasing, an ephemeral port twice must both succeed.

    Guards the ``SO_EXCLUSIVEADDRUSE`` override on Windows from being turned
    into an unconditional exclusive bind (which would break TIME_WAIT restarts
    on POSIX).
    """
    for _ in range(2):
        server = ExclusiveHTTPServer(("127.0.0.1", 0), ManagementHandler)
        try:
            assert server.server_address[1] > 0
        finally:
            server.server_close()


def test_server_survives_an_occupied_port_probe():
    """Sanity: a bound port is genuinely exclusive while held."""
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port = holder.getsockname()[1]
    try:
        with pytest.raises(OSError):
            ExclusiveHTTPServer(("127.0.0.1", port), ManagementHandler)
    finally:
        holder.close()


def test_list_agents_does_not_nameerror_on_cache_globals(live_server, _no_launcher_token):
    """Regression: mixin ``global`` looked up cache vars in launcher_main.

    After the management API split they were unbound in ``_agents.py``, so
    GET /api/agents returned 500 ``_agents_list_cache_result is not defined``
    and the system-config Logs page (Promise.all with getAgents) stayed empty.
    """
    url = f"http://127.0.0.1:{live_server}/api/agents"
    with _get(url) as ok:
        assert ok.status == 200
        body = json.loads(ok.read().decode("utf-8"))
    assert "agents" in body
    assert isinstance(body["agents"], list)


def test_runtime_list_cache_globals_live_in_mixin_module(live_server, _no_launcher_token):
    """Same split bug as list-agents: cache rebind must be module-local."""
    url = f"http://127.0.0.1:{live_server}/api/runtime/list"
    with _get(url) as ok:
        assert ok.status == 200
        body = json.loads(ok.read().decode("utf-8"))
    assert "runtime_registry" in body
    assert "managed" in body
