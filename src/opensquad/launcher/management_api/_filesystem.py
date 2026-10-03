"""Workspace filesystem browsing and per-session diff operations for the Launcher management API.

Extracted verbatim from ``launcher_main._start_management_server`` -- method
bodies are byte-identical to the original, only re-indented, so this is a pure
move.  See ``management_api/__init__.py`` for the composed handler.

Shared launcher registries are imported **by value** from ``launcher_main``.
That is safe because launcher_main only ever mutates these containers in place
(``dict[...] = ...`` / ``.append`` / ``.add``); the single rebind of
``_BUILTIN_PLUGINS`` happens at launcher_main module load, i.e. before this
module can be imported -- ``launcher_main`` imports this package lazily, from
inside the function that starts the server.
"""

from __future__ import annotations

from opensquad.launcher_main import (
    _log,
)


class FilesystemMixin:
    """Workspace filesystem browsing and per-session diff operations."""

    def _handle_fs_list(self, name: str, rel_path: str = "", root_override: str = ""):
        """GET /api/agents/{name}/fs/list?path=&root= — one-level directory listing."""
        root, err = self._agent_fs_root(name, root_override)
        if err is not None:
            return err
        from opensquad.utils.project_fs import list_dir

        result = list_dir(root, rel_path)
        if "error" in result:
            return self._send_json({"error": result["error"]}, int(result.get("status") or 400))
        result["agent"] = name
        return self._send_json(result)

    def _handle_fs_tree(self, name: str, root_override: str = "", max_entries: str = "", depth: str = ""):
        """GET /api/agents/{name}/fs/tree?root=&max=&depth= — full project tree (capped).

        Uses utils.fs_index: git ls-files acceleration, TTL cache and a
        bounded walk fallback. ``depth`` limits entries to N path segments
        (UI lazy expansion); ``has_more`` in the response signals deeper
        content is available on demand.
        """
        root, err = self._agent_fs_root(name, root_override)
        if err is not None:
            return err
        from opensquad.utils.fs_index import list_tree

        try:
            mx = int(max_entries) if str(max_entries).strip() else 10000
        except ValueError:
            mx = 10000
        try:
            dp = int(depth) if str(depth).strip() else None
        except ValueError:
            dp = None
        result = list_tree(root, max_entries=mx, max_depth=dp)
        if "error" in result:
            return self._send_json({"error": result["error"]}, int(result.get("status") or 400))
        result["agent"] = name
        return self._send_json(result)

    def _handle_fs_read(self, name: str, rel_path: str = "", root_override: str = ""):
        """GET /api/agents/{name}/fs/read?path=&root= — text file preview."""
        root, err = self._agent_fs_root(name, root_override)
        if err is not None:
            return err
        from opensquad.utils.project_fs import read_file

        result = read_file(root, rel_path)
        if "error" in result:
            return self._send_json({"error": result["error"]}, int(result.get("status") or 400))
        result["agent"] = name
        return self._send_json(result)

    def _handle_fs_changed(self, name: str, root_override: str = ""):
        """GET /api/agents/{name}/fs/changed?root= — git porcelain changes."""
        root, err = self._agent_fs_root(name, root_override)
        if err is not None:
            return err
        from opensquad.utils.project_fs import list_changed

        result = list_changed(root)
        result["agent"] = name
        return self._send_json(result)

    def _handle_fs_session_changes(self, name: str, root_override: str = ""):
        """GET /api/agents/{name}/fs/session-changes?root= — session dirty summary."""
        root, err = self._agent_fs_root(name, root_override)
        if err is not None:
            return err
        from opensquad.utils.session_changeset import summary

        result = summary(root)
        result["agent"] = name
        return self._send_json(result)

    def _handle_fs_session_diff(self, name: str, rel_path: str = "", root_override: str = "", *, collapse: bool = True):
        """GET /api/agents/{name}/fs/session-diff?path=&root=&collapse= — baseline vs disk diff."""
        root, err = self._agent_fs_root(name, root_override)
        if err is not None:
            return err
        from opensquad.utils.session_changeset import diff_file

        result = diff_file(root, rel_path, collapse=collapse)
        if "error" in result:
            return self._send_json({"error": result["error"]}, int(result.get("status") or 400))
        result["agent"] = name
        return self._send_json(result)

    def _handle_fs_session_diffs(self, name: str, body: dict):
        """POST — batch baseline vs disk diffs for session-changed files."""
        root, err = self._agent_fs_root(name, str(body.get("root") or ""))
        if err is not None:
            return err
        from opensquad.utils.session_changeset import diff_files_batch

        raw_paths = body.get("paths")
        paths = None
        if isinstance(raw_paths, list):
            paths = [str(p) for p in raw_paths if str(p or "").strip()]
        collapse = body.get("collapse", True)
        if isinstance(collapse, str):
            collapse = collapse.strip().lower() not in ("0", "false", "no", "off")
        else:
            collapse = bool(collapse)
        result = diff_files_batch(root, paths, collapse=collapse)
        result["agent"] = name
        return self._send_json(result)

    def _handle_fs_session_commit(self, name: str, body: dict):
        """POST — accept current disk state; clear session change stats."""
        root, err = self._agent_fs_root(name, str(body.get("root") or ""))
        if err is not None:
            return err
        from opensquad.utils.session_changeset import accept_reset

        result = accept_reset(root)
        result["agent"] = name
        return self._send_json(result)

    def _handle_fs_session_keep(self, name: str, body: dict):
        """POST — keep/save one path: drop from Changes; baseline retained for withdraw."""
        root, err = self._agent_fs_root(name, str(body.get("root") or ""))
        if err is not None:
            return err
        from opensquad.utils.session_changeset import keep_file

        path = str(body.get("path") or "").strip()
        result = keep_file(root, path)
        result["agent"] = name
        if not result.get("ok"):
            return self._send_json({"error": result.get("error") or "keep failed"}, 400)
        return self._send_json(result)

    def _handle_fs_session_keep_all(self, name: str, body: dict):
        """POST — keep all Changed files; baselines retained so withdraw still works."""
        root, err = self._agent_fs_root(name, str(body.get("root") or ""))
        if err is not None:
            return err
        from opensquad.utils.session_changeset import keep_all

        result = keep_all(root)
        result["agent"] = name
        return self._send_json(result)

    def _handle_fs_session_checkpoint(self, name: str, body: dict):
        """POST — snapshot dirty files at user-send time."""
        root, err = self._agent_fs_root(name, str(body.get("root") or ""))
        if err is not None:
            return err
        from opensquad.utils.session_changeset import checkpoint

        mid = str(body.get("message_id") or "").strip()
        result = checkpoint(root, mid)
        result["agent"] = name
        if not result.get("ok"):
            return self._send_json({"error": result.get("error") or "checkpoint failed"}, 400)
        return self._send_json(result)

    def _handle_fs_session_revert(self, name: str, body: dict):
        """POST — restore one path, a checkpoint, or all files to baseline."""
        root, err = self._agent_fs_root(name, str(body.get("root") or ""))
        if err is not None:
            return err
        from opensquad.utils.session_changeset import revert_all, revert_file, revert_to_checkpoint

        path = str(body.get("path") or "").strip()
        mid = str(body.get("message_id") or "").strip()
        if path:
            result = revert_file(root, path)
        elif mid:
            result = revert_to_checkpoint(root, mid)
        else:
            result = revert_all(root)
        result["agent"] = name
        if not result.get("ok"):
            return self._send_json({"error": result.get("error") or "revert failed"}, 400)
        return self._send_json(result)

    def _handle_fs_write(self, name: str, body: dict):
        root, err = self._agent_fs_root(name, str(body.get("root") or ""))
        if err is not None:
            return err
        from opensquad.utils.project_fs import write_file

        result = write_file(root, body.get("path"), body.get("content") or "")
        if "error" in result:
            return self._send_json({"error": result["error"]}, int(result.get("status") or 400))
        result["agent"] = name
        return self._send_json(result)

    def _handle_fs_mkdir(self, name: str, body: dict):
        root, err = self._agent_fs_root(name, str(body.get("root") or ""))
        if err is not None:
            return err
        from opensquad.utils.project_fs import mkdir

        result = mkdir(root, body.get("path"))
        if "error" in result:
            return self._send_json({"error": result["error"]}, int(result.get("status") or 400))
        result["agent"] = name
        return self._send_json(result)

    def _handle_fs_delete(self, name: str, body: dict):
        root, err = self._agent_fs_root(name, str(body.get("root") or ""))
        if err is not None:
            return err
        from opensquad.utils.project_fs import delete_path

        result = delete_path(root, body.get("path"))
        if "error" in result:
            return self._send_json({"error": result["error"]}, int(result.get("status") or 400))
        result["agent"] = name
        return self._send_json(result)

    def _handle_fs_rename(self, name: str, body: dict):
        root, err = self._agent_fs_root(name, str(body.get("root") or ""))
        if err is not None:
            return err
        from opensquad.utils.project_fs import rename_path

        result = rename_path(root, body.get("from") or body.get("path"), body.get("to"))
        if "error" in result:
            return self._send_json({"error": result["error"]}, int(result.get("status") or 400))
        result["agent"] = name
        return self._send_json(result)

    def _handle_fs_reveal(self, name: str, body: dict):
        root, err = self._agent_fs_root(name, str(body.get("root") or ""))
        if err is not None:
            return err
        from opensquad.utils.project_fs import reveal_in_os

        result = reveal_in_os(root, body.get("path"))
        if "error" in result:
            return self._send_json({"error": result["error"]}, int(result.get("status") or 400))
        result["agent"] = name
        return self._send_json(result)

    def _handle_fs_open_terminal(self, name: str, body: dict):
        root, err = self._agent_fs_root(name, str(body.get("root") or ""))
        if err is not None:
            return err
        from opensquad.utils.project_fs import open_in_terminal

        result = open_in_terminal(root, body.get("path"))
        if "error" in result:
            return self._send_json({"error": result["error"]}, int(result.get("status") or 400))
        result["agent"] = name
        return self._send_json(result)

    # ── Terminal (hosted HERE, not in the agent) ─────────────────────────────
    # A terminal is a workspace tool, not an agent capability: hosting it in the launcher means
    # it works with the agent stopped and needs no agent-side release. The launcher has no push
    # channel to the browser, so output is *polled* (`terminal/read` returns what the caller has
    # not seen yet) — the panel does that every few hundred milliseconds.

    def _handle_terminal_open(self, name: str, body: dict):
        root, err = self._agent_fs_root(name, str(body.get("root") or ""))
        if err is not None:
            return err
        from opensquad import terminal_session

        result = terminal_session.open_terminal(
            terminal_id=str(body.get("terminal_id") or ""),
            cwd=root,
            sid="",
            working_directory=str(body.get("cwd") or ""),
            # The user opened this terminal, not an agent: `_agent_fs_root` already vetted the
            # directory, and the agent's own workspace fence refuses a perfectly good workspace
            # that lives outside get_workspace_root() — the reported case, on the Desktop.
            trusted=True,
            # …and the shell is the one the user picked from the profiles this machine has.
            shell=str(body.get("shell") or ""),
        )
        if not result.get("ok"):
            return self._send_json(result, 400)
        result["agent"] = name
        return self._send_json(result)

    def _handle_terminal_write(self, name: str, body: dict):
        from opensquad import terminal_session

        result = terminal_session.write_terminal(str(body.get("terminal_id") or ""), str(body.get("text") or ""))
        return self._send_json(result, 200 if result.get("ok") else 400)

    def _handle_terminal_interrupt(self, name: str, body: dict):
        from opensquad import terminal_session

        result = terminal_session.interrupt_terminal(str(body.get("terminal_id") or ""))
        return self._send_json(result, 200 if result.get("ok") else 400)

    def _handle_terminal_close(self, name: str, body: dict):
        from opensquad import terminal_session

        return self._send_json(terminal_session.close_terminal(str(body.get("terminal_id") or "")))

    def _handle_terminal_read(self, name: str, body: dict):
        from opensquad import terminal_session

        return self._send_json(
            terminal_session.read_terminal(str(body.get("terminal_id") or ""), int(body.get("since") or 0))
        )

    def _handle_terminal_shells(self, name: str, body: dict):
        """The shells this machine can actually run — what the panel's picker offers."""
        from opensquad import terminal_session

        return self._send_json(
            {"ok": True, "shells": terminal_session.available_shells(), "default": terminal_session.default_shell_id()}
        )

    # ── Built-in browser (hosted HERE) ───────────────────────────────────────
    # Playwright renders on this machine, so the agent's browser_* tools and the panel's 浏览器
    # tab drive ONE page: what the agent clicks is what the user sees. Both sides reach it over
    # these endpoints; nothing keeps a browser of its own.

    def _browser_reply(self, result: dict):
        return self._send_json(result, 200 if result.get("ok") else 400)

    def _handle_browser_open(self, name: str, body: dict):
        from opensquad import browser_session

        return self._browser_reply(
            browser_session.open_session(
                session_id=str(body.get("session_id") or ""),
                headless=bool(body.get("headless", True)),
            )
        )

    def _handle_browser_navigate(self, name: str, body: dict):
        from opensquad import browser_session

        return self._browser_reply(
            browser_session.navigate(str(body.get("session_id") or ""), str(body.get("url") or ""))
        )

    def _handle_browser_back(self, name: str, body: dict):
        from opensquad import browser_session

        return self._browser_reply(browser_session.back(str(body.get("session_id") or "")))

    def _handle_browser_click(self, name: str, body: dict):
        from opensquad import browser_session

        x = body.get("x")
        y = body.get("y")
        return self._browser_reply(
            browser_session.click(
                str(body.get("session_id") or ""),
                selector=str(body.get("selector") or ""),
                x=None if x is None else float(x),
                y=None if y is None else float(y),
            )
        )

    def _handle_browser_type(self, name: str, body: dict):
        from opensquad import browser_session

        return self._browser_reply(
            browser_session.type_text(
                str(body.get("session_id") or ""),
                str(body.get("selector") or ""),
                str(body.get("text") or ""),
                submit=bool(body.get("submit", False)),
            )
        )

    def _handle_browser_press(self, name: str, body: dict):
        from opensquad import browser_session

        return self._browser_reply(
            browser_session.press(str(body.get("session_id") or ""), str(body.get("key") or "Enter"))
        )

    def _handle_browser_snapshot(self, name: str, body: dict):
        from opensquad import browser_session

        return self._browser_reply(browser_session.snapshot(str(body.get("session_id") or "")))

    def _handle_browser_screenshot(self, name: str, body: dict):
        from opensquad import browser_session

        return self._browser_reply(
            browser_session.screenshot(str(body.get("session_id") or ""), full_page=bool(body.get("full_page", False)))
        )

    def _handle_browser_frame(self, name: str, body: dict):
        """The panel's poll: the latest frame, without re-rendering the page."""
        from opensquad import browser_session

        return self._browser_reply(browser_session.frame(str(body.get("session_id") or "")))

    def _handle_browser_close(self, name: str, body: dict):
        from opensquad import browser_session

        return self._browser_reply(browser_session.close_session(str(body.get("session_id") or "")))

    def _handle_pick_directory(self, body: dict | None = None):
        """POST /api/system/pick-directory — native OS folder dialog on this host.

        Body (optional): ``{"initial_dir": "C:\\\\ai_test"}``
        """
        initial = ""
        if isinstance(body, dict):
            initial = str(body.get("initial_dir") or body.get("path") or "").strip()
        try:
            from opensquad.utils.pick_directory import pick_directory

            result = pick_directory(initial or None)
        except Exception as e:
            return self._send_json({"path": None, "error": str(e)}, 500)

        path = result.get("path")
        if path:
            _log.info(f"[Launcher] pick-directory selected: {path}")
            return self._send_json({"path": path, "cancelled": False})
        if result.get("cancelled"):
            return self._send_json({"path": None, "cancelled": True})
        return self._send_json(
            {"path": None, "cancelled": False, "error": result.get("error") or "Folder pick failed"},
            500,
        )
