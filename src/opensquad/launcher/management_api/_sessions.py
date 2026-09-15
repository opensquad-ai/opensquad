"""Session listing/read plus token stats for the Launcher management API.

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

import json
import os

from opensquad.launcher_main import (
    AGENTS_DIR,
    BOOT_SCRIPT_DIR,
    _log,
)


class SessionsMixin:
    """Session listing/read plus token stats."""

    def _get_session_reader(self, agent_id: str):
        """Get an AgentSessionReader for the given agent_id, or None."""
        try:
            import importlib.util as _ilu

            # BOOT_SCRIPT_DIR == dirname(abspath(launcher_main.__file__)), i.e.
            # the opensquad package dir. Spelled as a constant rather than
            # ``__file__`` so this keeps working once the handler lives in its
            # own module (launcher/management_api/_sessions.py).
            _mod_path = os.path.join(
                BOOT_SCRIPT_DIR,
                "gateway",
                "backend",
                "app",
                "ai_web",
                "agent_sessions.py",
            )
            _spec = _ilu.spec_from_file_location("opensquad._agent_sessions_standalone", _mod_path)
            _mod = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            return _mod.get_reader(agent_id)
        except Exception as e:
            _log.error(f"[Launcher] Failed to get session reader for {agent_id}: {e}")
            return None

    def _handle_session_list(self, agent_id: str, limit: int | None = None, offset: int = 0):
        """GET /api/sessions/{agent_id}/list"""
        reader = self._get_session_reader(agent_id)
        if reader is None:
            return self._send_json({"error": f"Agent not found: {agent_id}"}, 404)
        try:
            sessions = reader.get_session_list(limit=limit, offset=offset)
            current_id = reader.get_current_session_id()
        except Exception as e:
            import httpx

            if isinstance(e, httpx.TimeoutException):
                return self._send_json(
                    {
                        "error": "Agent session request timed out",
                        "sessions": [],
                        "current_session_id": None,
                        "has_more": False,
                    },
                    504,
                )
            return self._send_json({"error": f"Failed to get sessions: {e!s}"}, 500)
        return self._send_json(
            {
                "sessions": sessions,
                "current_session_id": current_id,
                "has_more": bool(limit and len(sessions) >= limit),
            }
        )

    def _handle_session_current(self, agent_id: str, offset: int, limit: int):
        """GET /api/sessions/{agent_id}/current?offset=0&limit=50"""
        reader = self._get_session_reader(agent_id)
        if reader is None:
            return self._send_json({"error": f"Agent not found: {agent_id}"}, 404)
        try:
            current_id = reader.get_current_session_id()
            session = reader.get_session_history_paged(current_id, offset, limit)
        except Exception as e:
            import httpx

            if isinstance(e, httpx.TimeoutException):
                return self._send_json({"error": "Agent session request timed out"}, 504)
            return self._send_json({"error": f"Failed to get current session: {e!s}"}, 500)
        return self._send_json({"current_session_id": current_id, "session": session})

    def _handle_session_paged(self, agent_id: str, session_id: str, offset: int, limit: int):
        """GET /api/sessions/{agent_id}/{session_id}/paged?offset=0&limit=50"""
        reader = self._get_session_reader(agent_id)
        if reader is None:
            return self._send_json({"error": f"Agent not found: {agent_id}"}, 404)
        try:
            session = reader.get_session_history_paged(session_id, offset, limit)
        except Exception as e:
            import httpx

            if isinstance(e, httpx.TimeoutException):
                return self._send_json({"error": "Agent session request timed out"}, 504)
            return self._send_json({"error": f"Failed to get session: {e!s}"}, 500)
        if session is None:
            return self._send_json({"error": f"Session not found: {session_id}"}, 404)
        return self._send_json({"session": session})

    def _handle_session_get(self, agent_id: str, session_id: str):
        """GET /api/sessions/{agent_id}/{session_id}"""
        reader = self._get_session_reader(agent_id)
        if reader is None:
            return self._send_json({"error": f"Agent not found: {agent_id}"}, 404)
        try:
            session = reader.get_session_history(session_id)
        except Exception as e:
            import httpx

            if isinstance(e, httpx.TimeoutException):
                return self._send_json({"error": "Agent session request timed out"}, 504)
            return self._send_json({"error": f"Failed to get session: {e!s}"}, 500)
        if session is None:
            return self._send_json({"error": f"Session not found: {session_id}"}, 404)
        return self._send_json({"session": session})

    def _handle_session_delete(self, agent_id: str, session_id: str):
        """POST /api/sessions/{agent_id}/{session_id}/delete"""
        reader = self._get_session_reader(agent_id)
        if reader is None:
            return self._send_json({"error": f"Agent not found: {agent_id}"}, 404)
        ok = reader.delete_session(session_id)
        return self._send_json({"ok": ok})

    def _handle_session_rename(self, agent_id: str, session_id: str, body: bytes | str | None):
        """POST /api/sessions/{agent_id}/{session_id}/rename"""
        reader = self._get_session_reader(agent_id)
        if reader is None:
            return self._send_json({"error": f"Agent not found: {agent_id}"}, 404)
        try:
            import json as _json

            raw = body if isinstance(body, bytes | bytearray | str) else b""
            data = _json.loads(raw or b"{}") if raw else {}
            title = (data.get("title") or "").strip()
        except Exception:
            return self._send_json({"error": "Invalid JSON body"}, 400)
        if not title:
            return self._send_json({"error": "Title is required"}, 400)
        rename = getattr(reader, "rename_session", None)
        if rename is None:
            return self._send_json({"error": "Rename not supported"}, 501)
        ok = rename(session_id, title)
        if not ok:
            return self._send_json({"error": f"Session not found: {session_id}", "ok": False}, 404)
        return self._send_json({"ok": True, "session_id": session_id, "title": title})

    def _read_token_stats(self, name: str) -> dict | None:
        """Read the agent's token_stats.json file"""
        # Try multiple possible paths
        candidates = [
            os.path.join(AGENTS_DIR, name, "data", "ai_his_talk", "token_stats.json"),
            os.path.join(AGENTS_DIR, name, "ai_his_talk", "token_stats.json"),
        ]
        for path in candidates:
            if os.path.isfile(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    pass
        return None

    def _read_chat_profile(self, name: str) -> dict | None:
        """Read the agent's profile.json (group chat account name and avatar)"""
        from opensquad.avatar_utils import read_agent_profile_file

        profile = read_agent_profile_file(AGENTS_DIR, name)
        # Empty normalized profile → None so callers keep prior "missing" semantics
        if not profile.get("name") and not profile.get("avatar"):
            return None
        return profile

    def _handle_get_stats(self, name: str):
        """Return the agent's token statistics"""
        stats = self._read_token_stats(name)
        if stats is None:
            return self._send_json({"agent": name, "token_stats": None})
        return self._send_json({"agent": name, "token_stats": stats})
