"""The git routes: three joins that no other test can see.

A git feature touches four layers with nothing but naming in common — the
frontend URL, the gateway route table, the launcher's dispatch suffixes, and the
mixin handler — and every one of the joins fails *silently*: a typo'd suffix
leaves a perfect handler that nothing calls, and an unregistered gateway route
404s in the browser only. This file pins each join, then drives the handlers
against a real temporary repository.

   R1  the gateway route table really exposes every git endpoint the UI needs
       (the `test_gateway_admin_routes` class of bug, for this feature);
   R2  every dispatch suffix in `_base.py` maps to a handler that exists on the
       composed ``ManagementHandler`` — the launcher-side half of that join;
   R3  git GETs are never served from the gateway's 5s proxy cache, while the
       sibling `/fs/` GETs still are;
   R4  the handlers answer 200 with `{"ok": false, "code": ...}` for a refused
       command, so the gateway proxy cannot flatten the code into a message;
   R5  a network op runs as a task (started, polled, and never doubled up for
       the same repository).

Mutations verified (each applied, run, reverted):
  M1  drop the `@admin_router.post(".../git/push")` decorator   -> R1
  M2  rename the dispatch suffix to `/git/branchlist`           -> R2
  M3  remove `/git/` from `_PROXY_GET_UNCACHEABLE_SUBSTRINGS`   -> R3
  M4  have the handlers return 400 for a refused command        -> R4
  M5  return the running task only when the op matches          -> R5
"""

from __future__ import annotations

import ast
import os
import pathlib
import subprocess
import time

import pytest

from opensquad.launcher.management_api import ManagementHandler
from opensquad.launcher.management_api._git import GitMixin
from opensquad.vcs import git_service as gs

_BACKEND_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "src", "opensquad", "gateway", "backend")
)
if _BACKEND_ROOT not in os.sys.path:
    os.sys.path.insert(0, _BACKEND_ROOT)

from app.ai_web.routes import _admin as admin  # noqa: E402

_BASE_PY = pathlib.Path(__file__).resolve().parents[1] / "src/opensquad/launcher/management_api/_base.py"

#: dispatch suffix -> the symbol its branch must reach
_EXPECTED = {
    "/git/status": "_handle_git_status",
    "/git/branches": "_handle_git_branches",
    "/git/diff": "_handle_git_diff",
    "/git/sync/status": "_handle_git_sync_status",
    "/git/init": "_handle_git_init",
    "/git/checkout": "_handle_git_checkout",
    "/git/branch/delete": "_handle_git_branch_delete",
    "/git/stage": "_handle_git_stage",
    "/git/unstage": "_handle_git_unstage",
    "/git/discard": "_handle_git_discard",
    "/git/commit": "_handle_git_commit",
    "/git/undo-commit": "_handle_git_undo_commit",
    "/git/merge/abort": "_handle_git_merge_abort",
    "/git/fetch": "_handle_git_sync",
    "/git/pull": "_handle_git_sync",
    "/git/push": "_handle_git_sync",
}

#: the three network ops, which all go through the one task-starting handler
_SYNC_OPS = {"fetch", "pull", "push"}


def _registered() -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for route in admin.admin_router.routes:
        for method in getattr(route, "methods", ()) or ():
            if method in {"HEAD", "OPTIONS"}:
                continue
            out.add((method, route.path))
    return out


def _dispatch_source() -> str:
    return _BASE_PY.read_text(encoding="utf-8")


def _dispatch_suffixes() -> set[str]:
    """Every `path.endswith("<literal>")` in the launcher's router."""
    tree = ast.parse(_dispatch_source())
    found: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "endswith"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            found.add(node.args[0].value)
    return found


def _dispatch_exact_paths() -> set[str]:
    """Paths compared with ``==`` (fixed URLs, e.g. the sync-status poll)."""
    tree = ast.parse(_dispatch_source())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
            for side in (node.left, *node.comparators):
                if isinstance(side, ast.Constant) and isinstance(side.value, str):
                    found.add(side.value)
    return found


def _sync_ops_dispatched() -> set[str]:
    """The op literals passed to ``_handle_git_sync(name, body, "<op>")``."""
    tree = ast.parse(_dispatch_source())
    ops: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_handle_git_sync"
            and len(node.args) >= 3
            and isinstance(node.args[2], ast.Constant)
        ):
            ops.add(str(node.args[2].value))
    return ops


class TestRoutes:
    def test_gateway_exposes_every_git_route(self):
        """R1 — a missing decorator leaves a live coroutine nothing can reach."""
        routes = _registered()
        gets = {"/git/status", "/git/branches", "/git/diff", "/git/sync/status"}
        for path in _EXPECTED:
            method = "GET" if path in gets else "POST"
            prefix = "/admin/git/sync/status" if path == "/git/sync/status" else f"/admin/agents/{{name}}{path}"
            assert (method, prefix) in routes, f"{method} {prefix} is not registered"

    def test_launcher_dispatch_reaches_a_real_handler(self):
        """R2 — the suffix literal and the handler name must agree."""
        suffixes = _dispatch_suffixes()
        for suffix, handler in _EXPECTED.items():
            if suffix == "/git/sync/status":
                # A fixed URL, dispatched by equality rather than by suffix.
                assert "/api/git/sync/status" in _dispatch_exact_paths()
            else:
                assert suffix in suffixes, f"_base.py has no dispatch branch ending in {suffix}"
            assert hasattr(ManagementHandler, handler), f"{handler} is missing from the composed handler"
        assert _sync_ops_dispatched() == _SYNC_OPS

    def test_route_names_cannot_shadow_each_other(self):
        """`endswith` matching means one suffix must never end with another."""
        git_suffixes = [s for s in _dispatch_suffixes() if "/git" in s]
        for a in git_suffixes:
            for b in git_suffixes:
                if a != b:
                    assert not b.endswith(a), f"{b} also matches {a}, so one branch is unreachable"


class TestProxyCache:
    def test_git_gets_are_never_cached_but_their_neighbours_still_are(self):
        """R3 — a stale status bar looks like the click did nothing."""
        assert admin._proxy_cacheable("/api/agents/a/fs/tree") is True
        assert admin._proxy_cacheable("/api/agents/a/git/status") is False
        assert admin._proxy_cacheable("/api/agents/a/git/branches") is False
        assert admin._proxy_cacheable("/api/agents/a/git/diff") is False

    def test_the_cache_really_ignores_a_git_path(self):
        admin._PROXY_GET_CACHE.clear()
        admin._proxy_cache_set("/api/agents/a/git/status", None, {"is_repo": True})
        assert admin._proxy_cache_get("/api/agents/a/git/status", None) is None
        admin._proxy_cache_set("/api/agents/a/fs/tree", None, {"files": []})
        assert admin._proxy_cache_get("/api/agents/a/fs/tree", None) == {"files": []}
        admin._PROXY_GET_CACHE.clear()


# ---------------------------------------------------------------------------
# handlers, driven for real
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_sync_tasks():
    from opensquad.launcher.management_api import _git as mod

    mod._GIT_SYNC_TASKS.clear()
    mod._GIT_SYNC_ACTIVE.clear()
    yield
    mod._GIT_SYNC_TASKS.clear()
    mod._GIT_SYNC_ACTIVE.clear()


_GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


@pytest.fixture()
def repo(tmp_path: pathlib.Path) -> str:
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True, env=_GIT_ENV)
    (root / "a.txt").write_text("1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True, env=_GIT_ENV)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True, env=_GIT_ENV)
    return str(root)


class _Handler(GitMixin):
    """GitMixin with the two members the real handler pulls from its siblings."""

    def __init__(self, root: str | None, root_error: dict | None = None):
        self._root = root
        self._root_error = root_error
        self.sent: list[tuple[int, dict]] = []

    def _agent_fs_root(self, name: str, root_override: str = ""):
        """Mirrors AgentsMixin: on failure the 4xx is sent *here*, and (None, None)."""
        if self._root_error is not None:
            self._send_json(self._root_error, 404)
            return None, None
        return self._root, None

    def _send_json(self, payload, status: int = 200):  # type: ignore[override]
        self.sent.append((status, payload))

    @property
    def last(self) -> tuple[int, dict]:
        return self.sent[-1]


def _wait(predicate, timeout: float = 15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class TestHandlers:
    def test_status_echoes_the_agent_and_the_payload(self, repo: str):
        h = _Handler(repo)
        h._handle_git_status("agent1", "")
        status, payload = h.last
        assert status == 200 and payload["is_repo"] is True and payload["agent"] == "agent1"

    def test_diff_reuses_the_session_diff_shape(self, repo: str):
        pathlib.Path(repo, "a.txt").write_text("1\n2\n", encoding="utf-8")
        h = _Handler(repo)
        h._handle_git_diff("agent1", "a.txt", "", mode="worktree", collapse=True)
        _status, payload = h.last
        assert payload["additions"] == 1
        assert {"type", "old_lineno", "new_lineno", "text"} <= set(payload["lines"][0])

    def test_a_refused_command_keeps_its_code_at_http_200(self, repo: str):
        """R4 — a 4xx here would reach the UI as one flat message string."""
        h = _Handler(repo)
        h._handle_git_checkout("agent1", {"branch": "-D"})
        status, payload = h.last
        assert status == 200
        assert payload["ok"] is False and payload["code"] == "bad_ref" and payload["agent"] == "agent1"

        h._handle_git_stage("agent1", {"paths": ["../outside"]})
        assert h.last[1]["code"] == "bad_path"

    def test_the_index_is_committed_and_reported(self, repo: str):
        pathlib.Path(repo, "b.txt").write_text("x\n", encoding="utf-8")
        h = _Handler(repo)
        h._handle_git_stage("agent1", {"paths": ["b.txt"]})
        assert h.last[1]["ok"] is True
        h._handle_git_commit("agent1", {"title": "feat: b", "description": "why"})
        _status, payload = h.last
        assert payload["ok"] is True and payload["sha"]
        assert gs.status(repo)["counts"]["total"] == 0

    def test_init_runs_in_a_plain_folder(self, tmp_path: pathlib.Path):
        plain = tmp_path / "plain"
        plain.mkdir()
        h = _Handler(str(plain))
        h._handle_git_init("agent1", {})
        assert h.last[1]["ok"] is True
        assert gs.status(str(plain))["is_repo"] is True

    def test_a_failed_root_produces_exactly_one_response(self):
        """The root helper already answered; a second send would corrupt the body.

        ``_agent_fs_root`` writes its own 4xx and returns ``(None, None)``, so a
        handler that checks only ``err`` would carry on with ``root=None`` and
        send a second body onto the same response.
        """
        h = _Handler(None, {"error": "Agent directory not found"})
        h._handle_git_status("ghost", "")
        assert h.sent == [(404, {"error": "Agent directory not found"})]

        h2 = _Handler(None)  # no override, no agent dir: same stop, no second send
        h2._handle_git_branches("ghost", "")
        assert h2.sent == []


class TestSyncTasks:
    def test_a_sync_runs_as_a_pollable_task(self, repo: str):
        """R5 — the op outlives a gateway request, so it is a task with a result."""
        h = _Handler(repo)
        h._handle_git_sync("agent1", {}, "fetch")  # no remote configured
        _status, started = h.last
        task_id = started["task_id"]
        assert started["status"] in ("pending", "running")

        assert _wait(lambda: _task_status(task_id) in ("completed", "failed")), "the task never finished"
        final = _task(task_id)
        assert final["status"] == "failed" and final["code"] == "no_remote"

    def test_a_second_sync_for_the_same_repo_reuses_the_running_task(self, repo: str, monkeypatch):
        """Two clicks must not queue two pulls behind the same repository lock."""
        monkeypatch.setattr(gs, "fetch", lambda *a, **k: (time.sleep(0.6), {"ok": True})[1])
        h = _Handler(repo)
        h._handle_git_sync("agent1", {}, "fetch")
        first = h.last[1]
        h._handle_git_sync("agent1", {}, "fetch")
        second = h.last[1]
        assert second["task_id"] == first["task_id"] and second["reused"] is True
        assert _wait(lambda: _task_status(first["task_id"]) == "completed")

    def test_an_unknown_task_is_a_404(self):
        h = _Handler(None)
        h._handle_git_sync_status("nope")
        assert h.last[0] == 404


def _task(task_id: str) -> dict:
    from opensquad.launcher.management_api import _git as mod

    return mod._GIT_SYNC_TASKS.get(task_id, {})


def _task_status(task_id: str) -> str:
    return _task(task_id).get("status", "")
