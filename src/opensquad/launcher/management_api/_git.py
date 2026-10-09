"""Git routes for the Agent Web UI.

Handlers are thin: the work lives in :mod:`opensquad.vcs.git_service`, which is
the *user's* git path — deliberately not the agent's ``git.*`` tools. Two
contract decisions live here rather than in the service:

* **Commands answer 200 with a structured payload** (``{"ok": false, "code":
  ...}``) instead of a 4xx. The gateway proxy turns any non-2xx launcher response
  into a single message string, which would flatten the code the UI branches on
  (``dirty_worktree`` → offer to stash, ``non_fast_forward`` → offer a pull,
  ``locked`` → retry). Infrastructure failures (unknown agent, missing root)
  still answer 4xx, because those are not decisions the UI can act on.
* **Network operations run as a background task** and are polled by
  ``/git/sync/status``: a push can outlive a gateway request, and the task record
  doubles as the progress channel. One sync per repository at a time — a second
  request while one is running returns the running task instead of queueing a
  second ``git pull`` behind the same repository lock.
"""

from __future__ import annotations

import hashlib
import os
import threading
import uuid
from typing import Any

from opensquad.vcs import git_service as gs

#: task_id -> {status, op, result, error, started_at}
_GIT_SYNC_TASKS: dict[str, dict[str, Any]] = {}
#: repo lock key -> task_id of the sync currently in flight
_GIT_SYNC_ACTIVE: dict[str, str] = {}

#: Stop signal for "the root could not be resolved and the response is already
#: out". It is never sent anywhere — see :meth:`GitMixin._git_root`.
_ALREADY_SENT: dict[str, Any] = {"ok": False, "code": "no_root"}


class GitMixin:
    """``/api/agents/{name}/git/*`` — repository status, branches and sync."""

    # ---------------------------------------------------------------- helpers

    def _git_send(self, result: dict[str, Any]) -> None:
        """Always 200: the payload carries ``ok``/``code`` (see module docstring)."""
        return self._send_json(result)

    def _git_root(self, name: str, root_override: str = ""):
        """Resolve the workspace root, or a stop signal.

        ``_agent_fs_root`` **writes its own 4xx** and returns ``(None, None)``
        when it cannot resolve a root; ``_require_auth_and_call`` then throws
        away whatever the handler returns. So the failure here is indistinguishable
        from success by ``err``, and the only safe move is to stop without
        sending: a second ``_send_json`` would append a body to a response that
        was already written.
        """
        root, err = self._agent_fs_root(name, root_override)
        if err is not None:
            return None, err
        if not root or not os.path.isdir(str(root)):
            return None, _ALREADY_SENT
        return root, None

    # ------------------------------------------------------------------- GET

    def _handle_git_status(self, name: str, root_override: str = ""):
        """GET /api/agents/{name}/git/status?root= — branch, ahead/behind, dirty counts."""
        root, err = self._git_root(name, root_override)
        if err is not None:
            return err
        result = gs.status(root)
        result["agent"] = name
        return self._git_send(result)

    def _handle_git_branches(self, name: str, root_override: str = ""):
        """GET /api/agents/{name}/git/branches?root= — local + remote branches."""
        root, err = self._git_root(name, root_override)
        if err is not None:
            return err
        try:
            result = gs.branches(root)
        except ValueError as exc:
            return self._git_send({"ok": False, "code": "not_a_repo", "error": str(exc)})
        except RuntimeError as exc:
            return self._git_send({"ok": False, "code": "branch_list_failed", "error": str(exc)})
        result["ok"] = True
        result["agent"] = name
        return self._git_send(result)

    def _handle_git_diff(
        self, name: str, rel_path: str = "", root_override: str = "", *, mode: str = "worktree", collapse: bool = True
    ):
        """GET /api/agents/{name}/git/diff?path=&root=&mode=&collapse= — one file's diff."""
        root, err = self._git_root(name, root_override)
        if err is not None:
            return err
        result = gs.diff_file(root, rel_path, mode=mode, collapse=collapse)
        result["agent"] = name
        return self._git_send(result)

    def _handle_git_sync_status(self, task_id: str):
        """GET /api/git/sync/status?task_id= — poll a fetch/pull/push task."""
        task = _GIT_SYNC_TASKS.get(task_id)
        if task is None:
            return self._send_json({"ok": False, "code": "unknown_task", "error": f"Task not found: {task_id}"}, 404)
        return self._send_json(task)

    # ------------------------------------------------------------------ POST

    def _handle_git_init(self, name: str, body: dict):
        """POST — initialise a repository in a workspace that has none."""
        root, err = self._git_root(name, str(body.get("root") or ""))
        if err is not None:
            return err
        result = gs.init_repo(str(root), initial_branch=str(body.get("initial_branch") or "main"))
        result["agent"] = name
        return self._git_send(result)

    def _handle_git_checkout(self, name: str, body: dict):
        """POST — switch branch (optionally creating it / stashing first)."""
        root, err = self._git_root(name, str(body.get("root") or ""))
        if err is not None:
            return err
        result = gs.checkout(
            str(root),
            str(body.get("branch") or ""),
            create=bool(body.get("create")),
            base=(str(body.get("base")) if body.get("base") else None),
            stash_dirty=bool(body.get("stash_dirty")),
        )
        result["agent"] = name
        return self._git_send(result)

    def _handle_git_branch_delete(self, name: str, body: dict):
        """POST — delete a branch (``force`` is the UI's second confirmation)."""
        root, err = self._git_root(name, str(body.get("root") or ""))
        if err is not None:
            return err
        result = gs.delete_branch(str(root), str(body.get("branch") or ""), force=bool(body.get("force")))
        result["agent"] = name
        return self._git_send(result)

    def _handle_git_stage(self, name: str, body: dict):
        """POST — stage the given paths."""
        return self._git_paths_op(name, body, gs.stage)

    def _handle_git_worktree(self, name: str, body: dict):
        """POST — prepare (create or reuse) the workspace's mode-switch worktree.

        The composer's 本地/Worktree mode needs one stable worktree per root:
        the id is hashed from the root path, so toggling the mode back and
        forth reuses the same worktree instead of littering ``.os-worktrees``
        with one per click. Uncommitted changes in the main tree are NOT
        carried in (``worktree add`` branches from the committed base ref).
        """
        root, err = self._git_root(name, str(body.get("root") or ""))
        if err is not None:
            return err
        from opensquad.workspace import worktree_manager as wm

        mgr = wm.manager_for(str(root))
        if mgr is None:
            return self._git_send({"ok": False, "code": "not_a_repo"})
        tid = (
            "ws-"
            + hashlib.sha1(
                os.path.normcase(os.path.abspath(str(root))).encode("utf-8"),
                usedforsecurity=False,
            ).hexdigest()[:10]
        )
        try:
            meta = mgr.create(tid)
            created = True
        except FileExistsError:
            created = False
            meta = mgr.get(tid) or {}
        except Exception as exc:  # noqa: BLE001
            return self._git_send({"ok": False, "code": "worktree_failed", "error": str(exc)})
        if not meta.get("worktree_path"):
            # The dir may outlive its _refs entry (crash between the two);
            # without metadata we cannot tell the UI where to point the session.
            return self._git_send(
                {"ok": False, "code": "worktree_failed", "error": f"worktree exists but has no metadata: {tid}"}
            )
        return self._git_send(
            {
                "ok": True,
                "id": tid,
                "created": created,
                "worktree_path": meta.get("worktree_path", ""),
                "branch": meta.get("branch") or "",
                "base_ref": meta.get("base_ref", ""),
                "agent": name,
            }
        )

    def _handle_git_unstage(self, name: str, body: dict):
        """POST — take the given paths back out of the index."""
        return self._git_paths_op(name, body, gs.unstage)

    def _handle_git_discard(self, name: str, body: dict):
        """POST — throw away local changes (untracked files need ``confirm_untracked``)."""
        root, err = self._git_root(name, str(body.get("root") or ""))
        if err is not None:
            return err
        raw = body.get("paths")
        paths = [str(p) for p in raw] if isinstance(raw, list) else []
        result = gs.discard(str(root), paths, remove_untracked=bool(body.get("confirm_untracked")))
        result["agent"] = name
        return self._git_send(result)

    def _git_paths_op(self, name: str, body: dict, fn):
        root, err = self._git_root(name, str(body.get("root") or ""))
        if err is not None:
            return err
        raw = body.get("paths")
        paths = [str(p) for p in raw] if isinstance(raw, list) else []
        result = fn(str(root), paths)
        result["agent"] = name
        return self._git_send(result)

    def _handle_git_commit(self, name: str, body: dict):
        """POST — commit the staged index."""
        root, err = self._git_root(name, str(body.get("root") or ""))
        if err is not None:
            return err
        result = gs.commit(str(root), str(body.get("title") or ""), str(body.get("description") or ""))
        result["agent"] = name
        return self._git_send(result)

    def _handle_git_undo_commit(self, name: str, body: dict):
        """POST — undo the last commit when it has not been pushed."""
        root, err = self._git_root(name, str(body.get("root") or ""))
        if err is not None:
            return err
        result = gs.undo_last_commit(str(root))
        result["agent"] = name
        return self._git_send(result)

    def _handle_git_merge_abort(self, name: str, body: dict):
        """POST — ``git merge --abort``: the way out of a conflicted pull."""
        root, err = self._git_root(name, str(body.get("root") or ""))
        if err is not None:
            return err
        result = gs.abort_merge(str(root))
        result["agent"] = name
        return self._git_send(result)

    def _handle_git_sync(self, name: str, body: dict, op: str):
        """POST — start fetch/pull/push as a background task; poll with sync/status."""
        root, err = self._git_root(name, str(body.get("root") or ""))
        if err is not None:
            return err
        key = gs.repo_key(str(root))
        running = _GIT_SYNC_ACTIVE.get(key)
        if running and _GIT_SYNC_TASKS.get(running, {}).get("status") in ("pending", "running"):
            # The repository lock would serialise these anyway; answering with the
            # running task is clearer than a second identical pull queued behind it.
            return self._git_send({**_GIT_SYNC_TASKS[running], "reused": True})

        task_id = str(uuid.uuid4())
        task: dict[str, Any] = {
            "ok": True,
            "task_id": task_id,
            "op": op,
            "status": "pending",
            "root": str(root),
        }
        _GIT_SYNC_TASKS[task_id] = task
        _GIT_SYNC_ACTIVE[key] = task_id
        params = {
            "remote": (str(body.get("remote")) if body.get("remote") else None),
            "branch": (str(body.get("branch")) if body.get("branch") else None),
            "autostash": bool(body.get("autostash", True)),
            "set_upstream": bool(body.get("set_upstream")),
            "force_with_lease": bool(body.get("force_with_lease")),
        }

        def _run() -> None:
            try:
                task["status"] = "running"
                if op == "fetch":
                    result = gs.fetch(str(root), remote=params["remote"])
                elif op == "pull":
                    result = gs.pull(
                        str(root), remote=params["remote"], branch=params["branch"], autostash=params["autostash"]
                    )
                elif op == "push":
                    result = gs.push(
                        str(root),
                        remote=params["remote"],
                        branch=params["branch"],
                        set_upstream=params["set_upstream"],
                        force_with_lease=params["force_with_lease"],
                    )
                else:
                    result = {"ok": False, "code": "bad_op", "error": f"Unknown sync op: {op}"}
                task.update(result)
                task["status"] = "completed" if result.get("ok") else "failed"
            except Exception as exc:  # noqa: BLE001 - a dead thread would leave the UI polling
                task.update({"ok": False, "code": "sync_failed", "error": str(exc), "status": "failed"})
            finally:
                _GIT_SYNC_ACTIVE.pop(key, None)

        threading.Thread(target=_run, daemon=True, name=f"git-{op}-{task_id[:8]}").start()
        return self._git_send(task)
