"""Auth, JSON/body helpers and the HTTP verb dispatch table for the Launcher management API.

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

import contextlib
import hashlib
import json
import re
import secrets
import time
import urllib.parse

from opensquad.launcher_main import (
    STALL_THRESHOLD,
    _task_watch_heartbeats,
    _task_watch_stalled_notified,
)
from opensquad.system_config import syscfg


class BaseHandlerMixin:
    """Auth, JSON/body helpers and the HTTP verb dispatch table."""

    @staticmethod
    def _get_launcher_token() -> str:
        """Read launcher token from system_config, fallback to empty string."""
        try:
            return syscfg.get("launcher_token", "")
        except Exception:
            return ""

    @staticmethod
    def _encrypt_password(password: str) -> str:
        """Encrypt password using SHA-256 with a salt."""
        salt = secrets.token_hex(16)
        hashed = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
        return f"{salt}${hashed}"

    @staticmethod
    def _verify_password(password: str, stored: str) -> bool:
        """Verify password against stored salt$hash."""
        if "$" not in stored:
            # Backward compatibility: plain text fallback (will be upgraded on next write)
            return password == stored
        salt, hashed = stored.split("$", 1)
        return hashlib.sha256((salt + password).encode("utf-8")).hexdigest() == hashed

    def _check_auth(self) -> bool:
        """Verify Bearer token from Authorization header. Returns True if valid or no token required."""
        token = self._get_launcher_token()
        if not token:
            return True  # No token configured, allow all
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            self._send_json({"error": "Unauthorized", "message": "Bearer token required"}, 401)
            return False
        provided = auth_header[7:]
        if provided != token:
            self._send_json({"error": "Forbidden", "message": "Invalid token"}, 403)
            return False
        return True

    def log_message(self, format, *args):
        # Silence logs to avoid console spam
        pass

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError, OSError):
            # Client already disconnected — nothing we can do, just skip
            pass

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self._send_json({"error": f"Invalid JSON body: {e}"}, 400)
            raise  # Let the caller catch this and abort processing

    def _require_auth_and_call(self, handler_fn):
        """Wrapper: check auth, then call the actual handler function."""
        if self._check_auth():
            try:
                handler_fn()
            except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError, OSError):
                # Client disconnected before we could respond — skip silently
                pass
            except Exception as e:
                with contextlib.suppress(ConnectionAbortedError, BrokenPipeError, ConnectionResetError, OSError):
                    self._send_json({"error": f"Internal server error: {e!s}"}, 500)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        self._require_auth_and_call(self._do_get_impl)

    def _do_get_impl(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/api/agents":
            return self._handle_list_agents()
        elif path.startswith("/api/agents/") and path.endswith("/stats"):
            name = path.split("/")[3]
            return self._handle_get_stats(name)
        elif path.startswith("/api/agents/") and path.endswith("/logs"):
            name = path.split("/")[3]
            lines = int(qs.get("lines", ["200"])[0])
            return self._handle_get_logs(name, lines)
        elif path.startswith("/api/agents/") and path.endswith("/config"):
            name = path.split("/")[3]
            return self._handle_get_config(name)
        elif path.startswith("/api/agents/") and path.endswith("/working-directory"):
            # GET /api/agents/{name}/working-directory
            # Returns the agent's current session working directory (if set)
            # plus the permanent workspace root.
            name = path.split("/")[3]
            return self._handle_get_working_directory(name)
        elif path.startswith("/api/agents/") and path.endswith("/web-ui-state"):
            # GET /api/agents/{name}/web-ui-state — Agent Web workspace chrome
            # + session↔project bindings (shared across browser origins / LAN).
            name = path.split("/")[3]
            return self._handle_get_web_ui_state(name)
        elif path.startswith("/api/agents/") and path.endswith("/fs/list"):
            # GET /api/agents/{name}/fs/list?path=&root=
            name = path.split("/")[3]
            rel = (qs.get("path") or [""])[0]
            root = (qs.get("root") or [""])[0]
            return self._handle_fs_list(name, rel, root)
        elif path.startswith("/api/agents/") and path.endswith("/fs/tree"):
            # GET /api/agents/{name}/fs/tree?root=&max=&depth=
            name = path.split("/")[3]
            root = (qs.get("root") or [""])[0]
            max_e = (qs.get("max") or [""])[0]
            depth = (qs.get("depth") or [""])[0]
            return self._handle_fs_tree(name, root, max_e, depth)
        elif path.startswith("/api/agents/") and path.endswith("/fs/read"):
            # GET /api/agents/{name}/fs/read?path=&root=
            name = path.split("/")[3]
            rel = (qs.get("path") or [""])[0]
            root = (qs.get("root") or [""])[0]
            return self._handle_fs_read(name, rel, root)
        elif path.startswith("/api/agents/") and path.endswith("/fs/changed"):
            # GET /api/agents/{name}/fs/changed?root=
            name = path.split("/")[3]
            root = (qs.get("root") or [""])[0]
            return self._handle_fs_changed(name, root)
        elif path.startswith("/api/agents/") and path.endswith("/fs/session-changes"):
            # GET /api/agents/{name}/fs/session-changes?root=
            name = path.split("/")[3]
            root = (qs.get("root") or [""])[0]
            return self._handle_fs_session_changes(name, root)
        elif path.startswith("/api/agents/") and path.endswith("/fs/session-diff"):
            # GET /api/agents/{name}/fs/session-diff?path=&root=&collapse=0|1
            name = path.split("/")[3]
            rel = (qs.get("path") or [""])[0]
            root = (qs.get("root") or [""])[0]
            collapse_raw = (qs.get("collapse") or ["1"])[0].strip().lower()
            collapse = collapse_raw not in ("0", "false", "no", "off")
            return self._handle_fs_session_diff(name, rel, root, collapse=collapse)
        elif path.startswith("/api/agents/") and path.endswith("/role"):
            name = path.split("/")[3]
            return self._handle_get_role(name)
        elif path == "/api/plugins":
            return self._handle_list_plugins()
        elif path.startswith("/api/plugins/") and path.endswith("/config"):
            name = path.split("/")[3]
            return self._handle_get_plugin_config(name)
        elif path.startswith("/api/plugins/") and path.endswith("/data"):
            name = path.split("/")[3]
            return self._handle_get_plugin_data(name, qs)
        elif path == "/api/skills":
            return self._handle_list_skills()
        elif path.startswith("/api/skills/") and path.endswith("/source"):
            skill_name = path.split("/")[3]
            return self._handle_get_skill_source(skill_name)
        elif path == "/api/role-cards":
            return self._handle_list_role_cards()
        elif path.startswith("/api/role-cards/"):
            card_name = path[len("/api/role-cards/") :]
            return self._handle_get_role_card(card_name)
        elif path == "/api/collab-cards":
            return self._handle_list_collab_cards()
        elif path.startswith("/api/collab-cards/"):
            card_name = path[len("/api/collab-cards/") :]
            return self._handle_get_collab_card(card_name)
        elif path == "/api/model-cards":
            return self._handle_list_model_cards()
        elif path.startswith("/api/model-cards/"):
            card_name = path[len("/api/model-cards/") :]
            return self._handle_get_model_card(card_name)
        elif path.startswith("/api/agents/") and path.endswith("/mcp"):
            name = path.split("/")[3]
            return self._handle_get_mcp(name)
        elif path == "/api/mcp/config":
            return self._handle_get_mcp_central()
        elif path == "/api/mcp/global":
            return self._handle_get_mcp_global()
        elif path == "/api/task_watch_status":
            # PM can query all workers' heartbeats
            result = {}
            now = time.time()
            for aid, hb in _task_watch_heartbeats.items():
                result[aid] = {
                    "event": hb.get("event", "unknown"),
                    "detail": hb.get("detail", ""),
                    "elapsed_sec": round(now - hb.get("last_update", 0), 1),
                    "stalled": (now - hb.get("last_update", 0)) > STALL_THRESHOLD,
                }
            return self._send_json({"workers": result})
        elif path == "/api/ping":
            return self._send_json({"status": "ok", "service": "launcher"})
        elif path == "/api/system/pick-directory":
            # GET kept for discovery; real pick is POST (blocks until dialog closes)
            return self._send_json(
                {
                    "status": "ok",
                    "message": "POST /api/system/pick-directory to open a native folder dialog",
                }
            )
        elif path == "/api/workspace":
            return self._send_json(
                {
                    "workspace": syscfg.get_workspace(),
                    "agents_dir": syscfg.workspace_agents_dir(),
                }
            )
        elif path == "/api/workspace/list":
            return self._handle_workspace_list()
        elif path == "/api/workspace/detect-legacy":
            return self._handle_workspace_detect_legacy()
        elif path.startswith("/api/workspace/migrate/status/"):
            task_id = path[len("/api/workspace/migrate/status/") :]
            return self._handle_workspace_migrate_status(task_id)
        elif path == "/api/services/manage":
            return self._handle_services_manage()
        elif path == "/api/plugin-services":
            return self._handle_list_plugin_services()
        elif path.startswith("/api/plugin-services/") and path.endswith("/logs"):
            pid = path.split("/")[3]
            lines = int(qs.get("lines", ["200"])[0])
            return self._handle_plugin_service_logs(pid, lines)
        elif path == "/api/shutdown":
            body = self._read_body()
            return self._handle_shutdown(body)
        elif path == "/api/runtime/list":
            return self._handle_runtime_list()
        # ── Agent session endpoints (for remote Gateway access) ──
        elif path.startswith("/api/sessions/") and path.endswith("/list"):
            agent_id = path.split("/")[3]
            limit = int(qs.get("limit", ["0"])[0])
            offset = int(qs.get("offset", ["0"])[0])
            return self._handle_session_list(agent_id, limit or None, offset)
        elif path.startswith("/api/sessions/") and path.endswith("/current"):
            agent_id = path.split("/")[3]
            offset = int(qs.get("offset", ["0"])[0])
            limit = int(qs.get("limit", ["50"])[0])
            return self._handle_session_current(agent_id, offset, limit)
        elif re.search(r"^/api/sessions/[^/]+/[^/]+/paged$", path):
            parts = path.split("/")
            agent_id, session_id = parts[3], parts[4]
            offset = int(qs.get("offset", ["0"])[0])
            limit = int(qs.get("limit", ["50"])[0])
            return self._handle_session_paged(agent_id, session_id, offset, limit)
        elif re.search(r"^/api/sessions/[^/]+/[^/]+$", path):
            parts = path.split("/")
            agent_id, session_id = parts[3], parts[4]
            return self._handle_session_get(agent_id, session_id)
        else:
            return self._send_json({"error": "Not found"}, 404)

    def do_POST(self):
        # Internal endpoints: bypass auth (agent → launcher communication on localhost)
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        if path == "/_internal/task_watch_heartbeat":
            self._handle_task_watch_heartbeat()
            return
        self._require_auth_and_call(self._do_post_impl)

    def _do_post_impl(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path.startswith("/api/agents/") and path.endswith("/start"):
            name = path.split("/")[3]
            return self._handle_start(name)
        elif path.startswith("/api/agents/") and path.endswith("/stop"):
            name = path.split("/")[3]
            return self._handle_stop(name)
        elif path.startswith("/api/agents/") and path.endswith("/restart"):
            name = path.split("/")[3]
            return self._handle_restart(name)
        elif path.startswith("/api/agents/") and path.endswith("/fs/write"):
            name = path.split("/")[3]
            body = self._read_body() or {}
            return self._handle_fs_write(name, body)
        elif path.startswith("/api/agents/") and path.endswith("/fs/mkdir"):
            name = path.split("/")[3]
            body = self._read_body() or {}
            return self._handle_fs_mkdir(name, body)
        elif path.startswith("/api/agents/") and path.endswith("/fs/delete"):
            name = path.split("/")[3]
            body = self._read_body() or {}
            return self._handle_fs_delete(name, body)
        elif path.startswith("/api/agents/") and path.endswith("/fs/rename"):
            name = path.split("/")[3]
            body = self._read_body() or {}
            return self._handle_fs_rename(name, body)
        elif path.startswith("/api/agents/") and path.endswith("/fs/reveal"):
            name = path.split("/")[3]
            body = self._read_body() or {}
            return self._handle_fs_reveal(name, body)
        elif path.startswith("/api/agents/") and path.endswith("/fs/open-terminal"):
            name = path.split("/")[3]
            body = self._read_body() or {}
            return self._handle_fs_open_terminal(name, body)
        elif path.startswith("/api/agents/") and path.endswith("/fs/session-diffs"):
            name = path.split("/")[3]
            body = self._read_body() or {}
            return self._handle_fs_session_diffs(name, body)
        elif path.startswith("/api/agents/") and path.endswith("/fs/session-changes/commit"):
            name = path.split("/")[3]
            body = self._read_body() or {}
            return self._handle_fs_session_commit(name, body)
        elif path.startswith("/api/agents/") and path.endswith("/fs/session-changes/keep"):
            name = path.split("/")[3]
            body = self._read_body() or {}
            return self._handle_fs_session_keep(name, body)
        elif path.startswith("/api/agents/") and path.endswith("/fs/session-changes/keep-all"):
            name = path.split("/")[3]
            body = self._read_body() or {}
            return self._handle_fs_session_keep_all(name, body)
        elif path.startswith("/api/agents/") and path.endswith("/fs/session-changes/checkpoint"):
            name = path.split("/")[3]
            body = self._read_body() or {}
            return self._handle_fs_session_checkpoint(name, body)
        elif path.startswith("/api/agents/") and path.endswith("/fs/session-changes/revert"):
            name = path.split("/")[3]
            body = self._read_body() or {}
            return self._handle_fs_session_revert(name, body)
        elif path == "/api/agents/create":
            body = self._read_body()
            return self._handle_create(body)
        elif path == "/api/agents/rescan":
            return self._handle_rescan()
        elif path == "/api/plugin-view-error":
            body = self._read_body()
            return self._handle_plugin_view_error(body)
        elif path == "/api/resources/upload":
            body = self._read_body()
            return self._handle_resource_upload(body)
        elif path.startswith("/api/plugins/") and path.endswith("/action"):
            name = path.split("/")[3]
            body = self._read_body()
            return self._handle_plugin_action(name, body)
        elif path == "/api/plugins/install-zip":
            body = self._read_body()
            return self._handle_install_zip_plugin(body)
        elif path.startswith("/api/plugin-services/") and path.endswith("/restart"):
            pid = path.split("/")[3]
            return self._handle_plugin_service_restart(pid)
        elif path.startswith("/api/plugin-services/") and path.endswith("/start"):
            pid = path.split("/")[3]
            return self._handle_plugin_service_start(pid)
        elif path.startswith("/api/plugin-services/") and path.endswith("/stop"):
            pid = path.split("/")[3]
            return self._handle_plugin_service_stop(pid)
        # ── Agent session delete / rename ──
        elif re.search(r"^/api/sessions/[^/]+/[^/]+/delete$", path):
            parts = path.split("/")
            agent_id, session_id = parts[3], parts[4]
            return self._handle_session_delete(agent_id, session_id)
        elif re.search(r"^/api/sessions/[^/]+/[^/]+/rename$", path):
            parts = path.split("/")
            agent_id, session_id = parts[3], parts[4]
            body = self._read_body()
            return self._handle_session_rename(agent_id, session_id, body)
        elif path == "/api/workspace/create":
            body = self._read_body()
            return self._handle_workspace_create(body)
        elif path == "/api/workspace/switch":
            body = self._read_body()
            return self._handle_workspace_switch(body)
        elif path == "/api/workspace/migrate":
            body = self._read_body()
            return self._handle_workspace_migrate(body)
        elif path == "/api/system/pick-directory":
            body = self._read_body() or {}
            return self._handle_pick_directory(body)
        elif path == "/api/runtime/cleanup":
            body = self._read_body()
            return self._handle_runtime_cleanup(body)
        else:
            return self._send_json({"error": "Not found"}, 404)

    def do_PUT(self):
        self._require_auth_and_call(self._do_put_impl)

    def _do_put_impl(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path.startswith("/api/agents/") and path.endswith("/config"):
            name = path.split("/")[3]
            body = self._read_body()
            return self._handle_put_config(name, body)
        elif path.startswith("/api/agents/") and path.endswith("/role"):
            name = path.split("/")[3]
            body = self._read_body()
            return self._handle_put_role(name, body)
        elif path.startswith("/api/plugins/") and path.endswith("/enable"):
            name = path.split("/")[3]
            return self._handle_plugin_set_enabled(name, True)
        elif path.startswith("/api/plugins/") and path.endswith("/disable"):
            name = path.split("/")[3]
            return self._handle_plugin_set_enabled(name, False)
        elif path.startswith("/api/plugins/") and path.endswith("/config"):
            name = path.split("/")[3]
            body = self._read_body()
            return self._handle_put_plugin_config(name, body)
        elif path.startswith("/api/plugin-services/") and path.endswith("/auto-start"):
            name = path.split("/")[3]
            body = self._read_body()
            return self._handle_plugin_service_auto_start(name, body)
        elif path.startswith("/api/agents/") and path.endswith("/mcp"):
            # PUT /api/agents/{name}/mcp  — replace entire mcp_config.json
            name = path.split("/")[3]
            body = self._read_body()
            return self._handle_put_mcp(name, body)
        elif path == "/api/mcp/config":
            body = self._read_body()
            return self._handle_put_mcp_central(body)
        elif path.startswith("/api/mcp/global/servers/") and path.endswith("/enable"):
            srv = path.split("/")[5]
            return self._handle_put_mcp_server_global(srv, True)
        elif path.startswith("/api/mcp/global/servers/") and path.endswith("/disable"):
            srv = path.split("/")[5]
            return self._handle_put_mcp_server_global(srv, False)
        elif path.startswith("/api/role-cards/"):
            card_name = path[len("/api/role-cards/") :]
            body = self._read_body()
            return self._handle_put_role_card(card_name, body)
        elif path.startswith("/api/collab-cards/"):
            card_name = path[len("/api/collab-cards/") :]
            body = self._read_body()
            return self._handle_put_collab_card(card_name, body)
        elif path.startswith("/api/model-cards/"):
            card_name = path[len("/api/model-cards/") :]
            body = self._read_body()
            return self._handle_put_model_card(card_name, body)
        elif path.startswith("/api/agents/") and path.endswith("/model-card"):
            name = path.split("/")[3]
            body = self._read_body()
            # Assign card → agent config — do NOT write model_cards/{agent}.json
            return self._handle_put_model_card_assign(name, body)
        elif path.startswith("/api/agents/") and path.endswith("/working-directory"):
            # PUT /api/agents/{name}/working-directory
            # Sets the agent's session-level working directory (cwd) for
            # shell commands and file operations. Writes a .session_cwd
            # signal file that the agent process picks up at the start
            # of the next conversation turn.
            name = path.split("/")[3]
            body = self._read_body()
            return self._handle_set_working_directory(name, body)
        elif path.startswith("/api/agents/") and path.endswith("/web-ui-state"):
            name = path.split("/")[3]
            body = self._read_body()
            return self._handle_put_web_ui_state(name, body)
        elif path.startswith("/api/agents/") and path.endswith("/role-prompt"):
            name = path.split("/")[3]
            body = self._read_body()
            return self._handle_put_role_prompt(name, body)
        else:
            return self._send_json({"error": "Not found"}, 404)

    def do_DELETE(self):
        self._require_auth_and_call(self._do_delete_impl)

    def _do_delete_impl(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        parts = path.split("/")
        # DELETE /api/agents/{name}
        if len(parts) == 4 and parts[1] == "api" and parts[2] == "agents":
            name = parts[3]
            return self._handle_delete(name)
        # DELETE /api/agents/{name}/role-prompt
        elif len(parts) == 5 and parts[1] == "api" and parts[2] == "agents" and parts[4] == "role-prompt":
            return self._handle_delete_role_prompt(parts[3])
        # DELETE /api/agents/{name}/model-card
        elif len(parts) == 5 and parts[1] == "api" and parts[2] == "agents" and parts[4] == "model-card":
            return self._handle_delete_model_card_unassign(parts[3])
        # DELETE /api/role-cards/{name}
        elif len(parts) == 4 and parts[1] == "api" and parts[2] == "role-cards":
            return self._handle_delete_role_card(parts[3])
        # DELETE /api/collab-cards/{name}
        elif len(parts) == 4 and parts[1] == "api" and parts[2] == "collab-cards":
            return self._handle_delete_collab_card(parts[3])
        # DELETE /api/model-cards/{name}
        elif len(parts) == 4 and parts[1] == "api" and parts[2] == "model-cards":
            return self._handle_delete_model_card(parts[3])
        # DELETE /api/resources/{type}/{name}
        elif len(parts) == 5 and parts[1] == "api" and parts[2] == "resources":
            return self._handle_delete_resource(parts[3], parts[4])
        else:
            return self._send_json({"error": "Not found"}, 404)

    def _handle_task_watch_heartbeat(self):
        """POST /_internal/task_watch_heartbeat — agent reports task progress to launcher."""
        try:
            body = self._read_body()
            import json

            data = json.loads(body)
            agent_id = data.get("agent_id", "")
            if not agent_id:
                return self._send_json({"error": "Missing agent_id"}, 400)
            global _task_watch_heartbeats, _task_watch_stalled_notified
            _task_watch_heartbeats[agent_id] = {
                "event": data.get("event", "unknown"),
                "task_id": data.get("task_id", ""),
                "detail": data.get("detail", ""),
                "last_update": data.get("timestamp", time.time()),
            }
            # Worker recovered: clear stall notification flag so future stalls are caught again
            _task_watch_stalled_notified.discard(agent_id)
            return self._send_json({"ok": True})
        except Exception as e:
            return self._send_json({"error": str(e)}, 500)
