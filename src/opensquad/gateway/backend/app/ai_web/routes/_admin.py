"""Admin Management API -- proxy to launcher.py and related endpoints.
Extracted from routes.py."""

from __future__ import annotations

import base64
import json
import logging
import os
import re

import httpx
from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from app.api import get_current_user_dep
from app.models import User
from opensquad.system_config import syscfg

from .. import model_preset_service
from ..registry import registry
from ..websocket import launcher_handler

logger = logging.getLogger(__name__)
_REPO_ROOT = syscfg.project_root()
_SSL_VERIFY = os.environ.get("OPENQUAD_SSL_VERIFY", "1") != "0"


def _launcher_url() -> str:
    """Lazy access to launcher URL (not evaluated at import time)."""
    return syscfg.launcher_url()


admin_router = APIRouter()  # prefix comes from main router include

# ============================================================
# Admin Management API — proxy to launcher.py :9600
# ============================================================


async def _proxy_get(
    path: str,
    params: dict | None = None,
    launcher_url: str | None = None,
    *,
    http_only: bool = False,
    timeout: float = 5.0,
) -> dict:
    """GET proxy to launcher — prefer WS tunnel, fallback to HTTP"""
    _url = _launcher_url()
    # WS tunnel: no inbound port needed on home machine
    if not http_only and launcher_url is None and launcher_handler.has_connections():
        node_id = launcher_handler.get_any_node_id()
        full_path = path
        if params:
            from urllib.parse import urlencode

            full_path = f"{path}?{urlencode(params)}"
        try:
            return await launcher_handler.rpc(node_id, "GET", full_path, timeout=timeout)
        except Exception:
            pass  # Fall through to HTTP fallback
    # No explicit launcher_url and WS tunnel not connected; raise error directly (no HTTP fallback needed)
    if launcher_url is None and not _url:
        raise HTTPException(503, "Launcher WS tunnel not connected yet. Please wait for Launcher to register.")
    # HTTP fallback (same-machine or explicit launcher_url override)
    base = launcher_url or _url
    if not base:
        raise HTTPException(503, "Launcher not available (no WS tunnel and no HTTP URL configured)")
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.get(f"{base}{path}", params=params)
            if resp.status_code >= 400:
                err = resp.json().get("error", f"Launcher returned {resp.status_code}")
                raise HTTPException(resp.status_code, err)
            return resp.json()
        except httpx.ConnectError:
            raise HTTPException(502, f"Launcher is not running (cannot connect to {base})")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"Launcher proxy error: {e}")


async def _proxy_post(
    path: str, json: dict | None = None, launcher_url: str | None = None, *, timeout: float = 5.0
) -> dict:
    """POST proxy to launcher — prefer WS tunnel, fallback to HTTP"""
    _url = _launcher_url()
    if launcher_url is None and launcher_handler.has_connections():
        node_id = launcher_handler.get_any_node_id()
        try:
            return await launcher_handler.rpc(node_id, "POST", path, body=json, timeout=timeout)
        except Exception:
            pass  # Fall through to HTTP fallback
    if launcher_url is None and not _url:
        raise HTTPException(503, "Launcher WS tunnel not connected yet. Please wait for Launcher to register.")
    base = launcher_url or _url
    if not base:
        raise HTTPException(503, "Launcher not available (no WS tunnel and no HTTP URL configured)")
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.post(f"{base}{path}", json=json)
            if resp.status_code >= 400:
                err = resp.json().get("error", f"Launcher returned {resp.status_code}")
                raise HTTPException(resp.status_code, err)
            return resp.json()
        except httpx.ConnectError:
            raise HTTPException(502, f"Launcher is not running (cannot connect to {base})")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"Launcher proxy error: {e}")


async def _proxy_put(
    path: str, json_body: dict | None = None, launcher_url: str | None = None, *, http_only: bool = False
) -> dict:
    """PUT proxy to launcher — prefer WS tunnel, fallback to HTTP"""
    _url = _launcher_url()
    if not http_only and launcher_url is None and launcher_handler.has_connections():
        node_id = launcher_handler.get_any_node_id()
        try:
            return await launcher_handler.rpc(node_id, "PUT", path, body=json_body, timeout=5.0)
        except Exception:
            pass
    if launcher_url is None and not _url:
        raise HTTPException(503, "Launcher WS tunnel not connected yet. Please wait for Launcher to register.")
    base = launcher_url or _url
    if not base:
        raise HTTPException(503, "Launcher not available (no WS tunnel and no HTTP URL configured)")
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.put(f"{base}{path}", json=json_body)
            if resp.status_code >= 400:
                err = resp.json().get("error", f"Launcher returned {resp.status_code}")
                raise HTTPException(resp.status_code, err)
            return resp.json()
        except httpx.ConnectError:
            raise HTTPException(502, f"Launcher is not running (cannot connect to {base})")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"Launcher proxy error: {e}")


async def _proxy_delete(path: str, launcher_url: str | None = None) -> dict:
    """DELETE proxy to launcher — prefer WS tunnel, fallback to HTTP"""
    _url = _launcher_url()
    if launcher_url is None and launcher_handler.has_connections():
        node_id = launcher_handler.get_any_node_id()
        try:
            return await launcher_handler.rpc(node_id, "DELETE", path, timeout=5.0)
        except Exception:
            pass
    if launcher_url is None and not _url:
        raise HTTPException(503, "Launcher WS tunnel not connected yet. Please wait for Launcher to register.")
    base = launcher_url or _url
    if not base:
        raise HTTPException(503, "Launcher not available (no WS tunnel and no HTTP URL configured)")
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.delete(f"{base}{path}")
            if resp.status_code >= 400:
                err = resp.json().get("error", f"Launcher returned {resp.status_code}")
                raise HTTPException(resp.status_code, err)
            return resp.json()
        except httpx.ConnectError:
            raise HTTPException(502, f"Launcher is not running (cannot connect to {base})")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"Launcher proxy error: {e}")


@admin_router.get("/admin/agents")
async def admin_list_agents(current_user: User = Depends(get_current_user_dep)):
    """
    Admin panel - Get all Agent list
    Merge launcher process status + Gateway registry online status
    """
    data = await _proxy_get("/api/agents")
    launcher_agents = data.get("agents", [])

    # Get online Agent info from Gateway registry
    registry_agents = registry.list_agents()
    registry_map = {a.agent_id: a for a in registry_agents}

    # Merge info
    merged = []
    for agent in launcher_agents:
        agent_id = agent.get("agent_id", agent.get("dir_name", ""))
        reg = registry_map.pop(agent_id, None)

        # launcher returns alive: bool, convert to status string expected by frontend
        is_alive = agent.get("alive", False)
        if is_alive:
            process_status = "running"
        elif agent.get("should_run", False):
            process_status = "crashed"
        else:
            process_status = "stopped"

        # agent_type / description may be at top level or inside config sub-object
        agent_config = agent.get("config", {})
        agent_type = agent.get("agent_type") or agent_config.get("agent_type", "general")
        description = agent.get("description") or agent_config.get("description", "")
        # Localized description overrides — fall back to ``description`` when
        # a specific language is missing. Mirrors the plugin-market pattern
        # (`description_zh` / `description_en`) so the agent panel can show
        # the version that matches the user's interface language.
        description_zh = agent.get("description_zh") or agent_config.get("description_zh") or description
        description_en = agent.get("description_en") or agent_config.get("description_en") or description

        model_cfg = agent_config.get("model", {})
        model_card = agent.get("model_card")
        if not model_card and isinstance(model_cfg, dict):
            model_card = model_cfg.get("_card")
        role_card = agent.get("role_card")
        if not role_card:
            prompt_cfg = agent_config.get("prompt", {})
            if isinstance(prompt_cfg, dict):
                role_card = prompt_cfg.get("role")

        registry_online = reg is not None and reg.status != "offline" if reg else False
        merged.append(
            {
                # launcher process info
                "dir_name": agent.get("dir_name", ""),
                "agent_id": agent_id,
                "agent_name": agent.get("agent_name", ""),
                "agent_type": agent_type,
                "description": description,
                "description_zh": description_zh,
                "description_en": description_en,
                "process_status": process_status,
                "pid": agent.get("pid"),
                "started_at": agent.get("started_at"),
                "restart_count": agent.get("restart_count", 0),
                # registry online info
                "registry_online": registry_online,
                "registry_status": reg.status if reg else "offline",
                "ready": process_status == "running" and registry_online,
                "load_percent": reg.load_percent if reg else 0,
                "today_chats": reg.today_chats if reg else 0,
                # token consumption stats
                "token_stats": agent.get("token_stats"),
                # Group chat account profile (name, avatar)
                "chat_profile": agent.get("chat_profile"),
                # Card assignments (from launcher or config fallback)
                "model_card": model_card,
                "role_card": role_card,
            }
        )

    # If registry has Agents not tracked by launcher (e.g. started directly via boot.py)
    for agent_id, reg in registry_map.items():
        # Registry agents may or may not carry localized descriptions. Fall
        # back to the base description when a specific language is missing.
        reg_desc = reg.description
        merged.append(
            {
                "dir_name": "",
                "agent_id": reg.agent_id,
                "agent_name": reg.agent_name,
                "agent_type": reg.agent_type,
                "description": reg_desc,
                "description_zh": reg_desc,
                "description_en": reg_desc,
                "process_status": "external",
                "pid": None,
                "started_at": reg.registered_at,
                "restart_count": 0,
                "registry_online": reg.status != "offline",
                "registry_status": reg.status,
                "load_percent": reg.load_percent,
                "today_chats": reg.today_chats,
                "token_stats": None,
                "chat_profile": None,
            }
        )

    return {"agents": merged}


@admin_router.get("/admin/agents/{name}/config")
async def admin_get_config(name: str, current_user: User = Depends(get_current_user_dep)):
    """Get Agent's config.json plus runtime working directory."""
    data = await _proxy_get(f"/api/agents/{name}/config")
    # Runtime working directory used by system tools defaults to workspace root.
    # Expose it so frontend ContextViewer can show the actual execution cwd.
    runtime_cwd = _syscfg.get_workspace()
    if isinstance(data, dict):
        data["runtime_working_directory"] = runtime_cwd
    else:
        data = {"config": data, "runtime_working_directory": runtime_cwd}
    return data


@admin_router.get("/admin/agents/{name}/working-directory")
async def admin_get_working_directory(name: str, current_user: User = Depends(get_current_user_dep)):
    """Get the agent's current session working directory (if set via folder-picker)."""
    return await _proxy_get(f"/api/agents/{name}/working-directory", http_only=True)


@admin_router.put("/admin/agents/{name}/working-directory")
async def admin_set_working_directory(
    name: str, body: dict = Body(...), current_user: User = Depends(get_current_user_dep)
):
    """Set the agent's session-level working directory.

    Writes a ``.session_cwd`` signal file that the agent process picks up
    on the next conversation turn. Takes effect immediately — no restart
    needed.
    """
    return await _proxy_put(f"/api/agents/{name}/working-directory", body, http_only=True)


@admin_router.post("/admin/system/pick-directory")
async def admin_pick_directory(
    body: dict | None = Body(None),
    current_user: User = Depends(get_current_user_dep),
):
    """Open a native folder dialog on the Launcher host and return the absolute path.

    Used by Agent Web ``Open Folder`` — browsers cannot read absolute paths from
    ``webkitdirectory``, so the local Launcher must pick the folder.
    """
    # Dialog can stay open for several minutes while the user browses.
    return await _proxy_post("/api/system/pick-directory", body or {}, timeout=600.0)


@admin_router.get("/admin/agents/{name}/fs/list")
async def admin_fs_list(
    name: str,
    path: str = "",
    root: str = "",
    current_user: User = Depends(get_current_user_dep),
):
    """List one directory level under the agent's active project cwd."""
    from urllib.parse import quote

    q = quote(path or "", safe="")
    r = f"&root={quote(root, safe='')}" if root else ""
    return await _proxy_get(f"/api/agents/{name}/fs/list?path={q}{r}", http_only=True)


@admin_router.get("/admin/agents/{name}/fs/tree")
async def admin_fs_tree(
    name: str,
    root: str = "",
    max: int = 10000,
    current_user: User = Depends(get_current_user_dep),
):
    """List full project tree (metadata only, capped)."""
    from urllib.parse import quote

    r = (
        f"?root={quote(root, safe='')}&max={int(max) if max else 10000}"
        if root
        else f"?max={int(max) if max else 10000}"
    )
    # Full tree walk can take a few seconds on large projects.
    return await _proxy_get(f"/api/agents/{name}/fs/tree{r}", http_only=True, timeout=60.0)


@admin_router.get("/admin/agents/{name}/fs/read")
async def admin_fs_read(
    name: str,
    path: str = "",
    root: str = "",
    current_user: User = Depends(get_current_user_dep),
):
    """Read a text file or image preview under the agent's project cwd."""
    from urllib.parse import quote

    q = quote(path or "", safe="")
    r = f"&root={quote(root, safe='')}" if root else ""
    # Images may be several MB as base64 — allow a longer proxy window.
    return await _proxy_get(f"/api/agents/{name}/fs/read?path={q}{r}", http_only=True, timeout=30.0)


@admin_router.get("/admin/agents/{name}/fs/changed")
async def admin_fs_changed(
    name: str,
    root: str = "",
    current_user: User = Depends(get_current_user_dep),
):
    """List git-changed files under the project root."""
    from urllib.parse import quote

    r = f"?root={quote(root, safe='')}" if root else ""
    return await _proxy_get(f"/api/agents/{name}/fs/changed{r}", http_only=True)


@admin_router.get("/admin/agents/{name}/fs/session-changes")
async def admin_fs_session_changes(
    name: str,
    root: str = "",
    current_user: User = Depends(get_current_user_dep),
):
    """Session-scoped dirty files + line stats (since last Accept/Commit)."""
    from urllib.parse import quote

    r = f"?root={quote(root, safe='')}" if root else ""
    return await _proxy_get(f"/api/agents/{name}/fs/session-changes{r}", http_only=True)


@admin_router.get("/admin/agents/{name}/fs/session-diff")
async def admin_fs_session_diff(
    name: str,
    path: str = "",
    root: str = "",
    current_user: User = Depends(get_current_user_dep),
):
    """Unified diff for one session-changed file."""
    from urllib.parse import quote

    q = quote(path or "", safe="")
    r = f"&root={quote(root, safe='')}" if root else ""
    return await _proxy_get(f"/api/agents/{name}/fs/session-diff?path={q}{r}", http_only=True)


@admin_router.post("/admin/agents/{name}/fs/session-changes/commit")
async def admin_fs_session_commit(
    name: str, body: dict = Body(...), current_user: User = Depends(get_current_user_dep)
):
    return await _proxy_post(f"/api/agents/{name}/fs/session-changes/commit", body or {}, timeout=30.0)


@admin_router.post("/admin/agents/{name}/fs/session-changes/checkpoint")
async def admin_fs_session_checkpoint(
    name: str, body: dict = Body(...), current_user: User = Depends(get_current_user_dep)
):
    return await _proxy_post(f"/api/agents/{name}/fs/session-changes/checkpoint", body or {}, timeout=30.0)


@admin_router.post("/admin/agents/{name}/fs/session-changes/revert")
async def admin_fs_session_revert(
    name: str, body: dict = Body(...), current_user: User = Depends(get_current_user_dep)
):
    return await _proxy_post(f"/api/agents/{name}/fs/session-changes/revert", body or {}, timeout=60.0)


@admin_router.post("/admin/agents/{name}/fs/write")
async def admin_fs_write(name: str, body: dict = Body(...), current_user: User = Depends(get_current_user_dep)):
    return await _proxy_post(f"/api/agents/{name}/fs/write", body or {}, timeout=30.0)


@admin_router.post("/admin/agents/{name}/fs/mkdir")
async def admin_fs_mkdir(name: str, body: dict = Body(...), current_user: User = Depends(get_current_user_dep)):
    return await _proxy_post(f"/api/agents/{name}/fs/mkdir", body or {}, timeout=15.0)


@admin_router.post("/admin/agents/{name}/fs/delete")
async def admin_fs_delete(name: str, body: dict = Body(...), current_user: User = Depends(get_current_user_dep)):
    return await _proxy_post(f"/api/agents/{name}/fs/delete", body or {}, timeout=30.0)


@admin_router.post("/admin/agents/{name}/fs/rename")
async def admin_fs_rename(name: str, body: dict = Body(...), current_user: User = Depends(get_current_user_dep)):
    return await _proxy_post(f"/api/agents/{name}/fs/rename", body or {}, timeout=15.0)


@admin_router.post("/admin/agents/{name}/fs/reveal")
async def admin_fs_reveal(name: str, body: dict = Body(...), current_user: User = Depends(get_current_user_dep)):
    return await _proxy_post(f"/api/agents/{name}/fs/reveal", body or {}, timeout=10.0)


@admin_router.post("/admin/agents/{name}/fs/open-terminal")
async def admin_fs_open_terminal(name: str, body: dict = Body(...), current_user: User = Depends(get_current_user_dep)):
    return await _proxy_post(f"/api/agents/{name}/fs/open-terminal", body or {}, timeout=10.0)


@admin_router.put("/admin/agents/{name}/config")
async def admin_update_config(name: str, body: dict = Body(...), current_user: User = Depends(get_current_user_dep)):
    """Update Agent's config.json and sync agent_name to the bound account's display name"""
    result = await _proxy_put(f"/api/agents/{name}/config", body)

    # Extract config from body (frontend sends as {"config": {...}})
    cfg = body.get("config", body)
    agent_id = cfg.get("agent_id")
    new_name = cfg.get("agent_name")

    if agent_id and new_name:
        from sqlalchemy import select

        from app.database import AsyncSessionLocal
        from app.models import User as DBUser

        try:
            async with AsyncSessionLocal() as db:
                res = await db.execute(select(DBUser).where(DBUser.id == str(agent_id)))
                bound_user = res.scalar_one_or_none()
                if bound_user and bound_user.name != new_name:
                    bound_user.name = new_name
                    await db.commit()
        except Exception:
            pass  # Sync failure does not affect main flow

    return result


@admin_router.get("/admin/agents/{name}/role")
async def admin_get_role(name: str, current_user: User = Depends(get_current_user_dep)):
    """Get Agent's role.md"""
    return await _proxy_get(f"/api/agents/{name}/role")


@admin_router.put("/admin/agents/{name}/role")
async def admin_update_role(name: str, body: dict = Body(...), current_user: User = Depends(get_current_user_dep)):
    """Update Agent's role.md"""
    return await _proxy_put(f"/api/agents/{name}/role", body)


@admin_router.post("/admin/agents/{name}/start")
async def admin_start_agent(name: str, current_user: User = Depends(get_current_user_dep)):
    """Start Agent process"""
    return await _proxy_post(f"/api/agents/{name}/start")


@admin_router.post("/admin/agents/{name}/stop")
async def admin_stop_agent(name: str, current_user: User = Depends(get_current_user_dep)):
    """Stop Agent process"""
    return await _proxy_post(f"/api/agents/{name}/stop")


@admin_router.post("/admin/agents/{name}/restart")
async def admin_restart_agent(name: str, current_user: User = Depends(get_current_user_dep)):
    """Restart Agent process"""
    return await _proxy_post(f"/api/agents/{name}/restart")


@admin_router.get("/admin/agents/{name}/logs")
async def admin_get_logs(
    name: str, lines: int = Query(200, ge=1, le=1000), current_user: User = Depends(get_current_user_dep)
):
    """Get Agent recent logs"""
    return await _proxy_get(f"/api/agents/{name}/logs", {"lines": lines})


@admin_router.post("/admin/agents/create")
async def admin_create_agent(body: dict = Body(...), current_user: User = Depends(get_current_user_dep)):
    """
    Create a new Agent and bind it to an existing group chat account.
    body: {name, agent_type, description, chat_email, chat_password}
    """
    from app.auth import get_user_by_email
    from app.database import AsyncSessionLocal

    chat_email = body.get("chat_email", "").strip()
    if not chat_email:
        raise HTTPException(400, "chat_email is required")

    chat_password = body.get("chat_password", "").strip()
    if not chat_password:
        raise HTTPException(400, "chat_password is required")

    # Find existing account
    async with AsyncSessionLocal() as db:
        existing_user = await get_user_by_email(db, chat_email)

    if existing_user is None:
        raise HTTPException(400, f"No account found with email '{chat_email}'. Please create the account first.")

    agent_id = existing_user.id
    agent_name = existing_user.name

    # Proxy to Launcher to create agent directory
    launcher_body = {
        **body,
        "agent_id": agent_id,
        "agent_name": agent_name,
        "chat_email": chat_email,
        "chat_password": chat_password,
    }
    return await _proxy_post("/api/agents/create", launcher_body)


# ============================================================
# System Logs API (Gateway backend log files)
# ============================================================

# Gateway log files directory: {project_root}/data/logs/gateway/
# Matches the write path configured in app/main.py
from opensquad.system_config import syscfg as _syscfg

_GATEWAY_LOG_DIR = _syscfg.workspace_logs_dir("gateway")


def _fill_logging_defaults(data: dict) -> dict:
    """Fill missing logging keys so the settings UI shows effective values."""
    if not isinstance(data.get("logging"), dict):
        data["logging"] = {}
    logging_cfg = data["logging"]
    if "log_dir" not in logging_cfg:
        logging_cfg["log_dir"] = syscfg.log_dir()
    if "max_size_mb" not in logging_cfg:
        logging_cfg["max_size_mb"] = syscfg.log_max_size_mb()
    if "backup_count" not in logging_cfg:
        logging_cfg["backup_count"] = syscfg.log_backup_count()
    if "tool_call_debug" not in logging_cfg:
        logging_cfg["tool_call_debug"] = syscfg.tool_call_debug()
    if "log_level" not in logging_cfg:
        logging_cfg["log_level"] = syscfg.log_level()
    return data


@admin_router.get("/admin/system/config")
async def admin_get_system_config(current_user: User = Depends(get_current_user_dep)):
    """Read full contents of system_config.json (with effective logging defaults)."""
    import opensquad.system_config as _sc
    from opensquad._syscfg._config import ensure_workspace_config_file

    ensure_workspace_config_file()
    data = _sc.raw()
    if not data:
        config_path = _sc._CONFIG_PATH
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)
    return _fill_logging_defaults(data)


@admin_router.put("/admin/system/config")
async def admin_update_system_config(body: dict, current_user: User = Depends(get_current_user_dep)):
    """Write back system_config.json, reload syscfg cache, and dynamically apply new log level"""
    import opensquad.system_config as _sc
    from opensquad._syscfg._config import ensure_workspace_config_file

    config_path = ensure_workspace_config_file()
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False, indent=2)
    _sc._cache = None  # Clear cache, force reload on next access

    # Dynamically apply new log level to app-related logger handlers
    # Exclude uvicorn/fastapi loggers (their handler level=WARNING is intentional, used to filter noise)
    _SKIP_LOGGERS = {"uvicorn", "uvicorn.access", "uvicorn.error", "fastapi"}
    try:
        new_level_str = (
            body.get("logging", {}).get("log_level", "INFO") if isinstance(body.get("logging"), dict) else "INFO"
        )
        new_level = getattr(logging, new_level_str.upper(), logging.INFO)
        for name, lg in logging.Logger.manager.loggerDict.items():
            if name in _SKIP_LOGGERS:
                continue
            if isinstance(lg, logging.Logger):
                for h in lg.handlers:
                    h.setLevel(new_level)
    except Exception:
        pass  # Log level update failure should not affect config save

    return {"ok": True}


# Known log files (relative to _GATEWAY_LOG_DIR)
_SYSTEM_LOG_FILES = {
    "backend": "backend.log",
    "backend_startup": "backend_startup.log",
    "websocket": "websocket.log",
    "ws_auth": "ws_auth.log",
    "database": "database.log",
    "auth": "auth.log",
    "api": "api.log",
    "launcher": "launcher.log",
}


@admin_router.get("/admin/system/log-files")
async def admin_list_log_files(current_user: User = Depends(get_current_user_dep)):
    """List available backend log files"""
    files = []
    for key, filename in _SYSTEM_LOG_FILES.items():
        path = os.path.join(_GATEWAY_LOG_DIR, filename)
        files.append(
            {
                "key": key,
                "filename": filename,
                "exists": os.path.isfile(path),
                "size": os.path.getsize(path) if os.path.isfile(path) else 0,
            }
        )
    # Also discover plugin service logs from {workspace}/data/logs/
    _plugin_log_dir = _syscfg.workspace_data_dir("logs")
    if os.path.isdir(_plugin_log_dir):
        for fn in sorted(os.listdir(_plugin_log_dir)):
            if fn.endswith("_service.log"):
                key = f"plugin_{fn.replace('_service.log', '')}"
                path = os.path.join(_plugin_log_dir, fn)
                files.append(
                    {
                        "key": key,
                        "filename": fn,
                        "exists": True,
                        "size": os.path.getsize(path) if os.path.isfile(path) else 0,
                        "dir": "plugin",
                    }
                )
    return {"files": files}


@admin_router.get("/admin/system/logs")
async def admin_get_system_logs(
    file: str = Query("backend", description="Log file key"),
    lines: int = Query(500, ge=1, le=5000),
    current_user: User = Depends(get_current_user_dep),
):
    """Get backend system logs"""
    # Plugin logs are in a different directory
    if file.startswith("plugin_"):
        plugin_name = file[len("plugin_") :]
        _plugin_log_dir = _syscfg.workspace_data_dir("logs")
        path = os.path.join(_plugin_log_dir, f"{plugin_name}_service.log")
    else:
        filename = _SYSTEM_LOG_FILES.get(file)
        if not filename:
            raise HTTPException(400, f"Unknown log file key: {file}. Available: {list(_SYSTEM_LOG_FILES.keys())}")
        path = os.path.join(_GATEWAY_LOG_DIR, filename)

    if not os.path.isfile(path):
        return {"file": file, "logs": [], "total": 0}

    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        # Return last N lines, skip blank lines
        result = [ln.rstrip("\n\r") for ln in all_lines[-lines:] if ln.strip()]
        return {"file": file, "logs": result, "total": len(result)}
    except Exception as e:
        raise HTTPException(500, f"Failed to read log file: {e}")


@admin_router.get("/admin/system/log-level")
async def admin_get_log_level(current_user: User = Depends(get_current_user_dep)):
    """Get current effective log level for all managed loggers."""
    _SKIP_LOGGERS = {"uvicorn", "uvicorn.access", "uvicorn.error", "fastapi"}
    result = {}
    for name, lg in sorted(logging.Logger.manager.loggerDict.items()):
        if name in _SKIP_LOGGERS:
            continue
        if isinstance(lg, logging.Logger):
            eff = lg.getEffectiveLevel()
            result[name] = {
                "level": logging.getLevelName(eff),
                "handlers": [{"name": type(h).__name__, "level": logging.getLevelName(h.level)} for h in lg.handlers],
            }
    result["<root>"] = {
        "level": logging.getLevelName(logging.root.getEffectiveLevel()),
        "handlers": [{"name": type(h).__name__, "level": logging.getLevelName(h.level)} for h in logging.root.handlers],
    }
    return {"loggers": result}


@admin_router.put("/admin/system/log-level")
async def admin_set_log_level(body: dict, current_user: User = Depends(get_current_user_dep)):
    """Dynamically set log level for all managed loggers (takes effect immediately, no restart needed).

    Body: {"level": "DEBUG"|"INFO"|"WARNING"|"ERROR"}
    Optionally: {"logger": "app.websocket", "level": "DEBUG"} to target a specific logger.
    """
    level_str = body.get("level", "INFO").upper()
    level = getattr(logging, level_str, None)
    if level is None:
        raise HTTPException(400, f"Invalid log level: {level_str}. Use DEBUG/INFO/WARNING/ERROR")

    target_logger = body.get("logger")
    _SKIP_LOGGERS = {"uvicorn", "uvicorn.access", "uvicorn.error", "fastapi"}
    changed = []

    def _apply(lg: logging.Logger):
        lg.setLevel(level)
        for h in lg.handlers:
            h.setLevel(level)
        changed.append(lg.name)

    if target_logger:
        lg = logging.getLogger(target_logger)
        _apply(lg)
    else:
        for name, lg in logging.Logger.manager.loggerDict.items():
            if name in _SKIP_LOGGERS:
                continue
            if isinstance(lg, logging.Logger):
                _apply(lg)

    try:
        import opensquad.system_config as _sc

        cfg_path = _sc._CONFIG_PATH
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
        cfg.setdefault("logging", {})["log_level"] = level_str
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        _sc._cache = None
    except Exception:
        pass

    return {"ok": True, "level": level_str, "changed_loggers": changed}


@admin_router.delete("/admin/agents/{name}")
async def admin_delete_agent(name: str, current_user: User = Depends(get_current_user_dep)):
    """
    Delete Agent
    Stop the process first, then delete the directory
    """
    return await _proxy_delete(f"/api/agents/{name}")


# ============================================================
# Plugin Management API
# ============================================================


@admin_router.get("/admin/plugins")
async def admin_list_plugins(current_user: User = Depends(get_current_user_dep)):
    """Get all plugins list with metadata"""
    return await _proxy_get("/api/plugins")


@admin_router.post("/admin/plugins/report-view-error")
async def admin_report_plugin_view_error(request: Request, current_user: User = Depends(get_current_user_dep)):
    """Forward a plugin view runtime error to the launcher so it can be logged for the agent."""
    body = await request.json()
    return await _proxy_post("/api/plugin-view-error", body)


@admin_router.put("/admin/plugins/{name}/enable")
async def admin_enable_plugin(name: str, current_user: User = Depends(get_current_user_dep)):
    """Enable a plugin on the local node"""
    return await _proxy_put(f"/api/plugins/{name}/enable")


@admin_router.put("/admin/plugins/{name}/disable")
async def admin_disable_plugin(name: str, current_user: User = Depends(get_current_user_dep)):
    """Disable a plugin on the local node"""
    return await _proxy_put(f"/api/plugins/{name}/disable")


@admin_router.get("/admin/plugins/{name}/config")
async def admin_get_plugin_config(name: str, current_user: User = Depends(get_current_user_dep)):
    """Get plugin config values and schema from the local node"""
    return await _proxy_get(f"/api/plugins/{name}/config")


@admin_router.put("/admin/plugins/{name}/config")
async def admin_put_plugin_config(name: str, request: Request, current_user: User = Depends(get_current_user_dep)):
    """Save plugin config values to the local node."""
    body = await request.json()
    return await _proxy_put(f"/api/plugins/{name}/config", json_body=body)


@admin_router.get("/admin/plugins/{name}/data")
async def admin_get_plugin_data(name: str, request: Request, current_user: User = Depends(get_current_user_dep)):
    """Proxy plugin data query to launcher (e.g. token_analytics dashboard)"""
    # Forward all query params
    params = dict(request.query_params)
    return await _proxy_get(f"/api/plugins/{name}/data", params=params)


@admin_router.post("/admin/plugins/{name}/action")
async def admin_plugin_action(name: str, request: Request, current_user: User = Depends(get_current_user_dep)):
    """Proxy plugin action to launcher"""
    body = await request.json()
    return await _proxy_post(f"/api/plugins/{name}/action", json=body)


@admin_router.delete("/admin/plugins/{name}")
async def admin_uninstall_plugin(
    name: str,
    current_user: User = Depends(get_current_user_dep),
):
    """
    Uninstall (delete) a plugin. Proxied to Launcher to delete from agent machine.
    """
    # Sanitize: only allow simple directory names (prevent path traversal)
    # Allow dot for plugin names like "my.plugin" (launcher also allows dot)
    if not re.match(r"^[a-zA-Z0-9_\-\.]+$", name):
        raise HTTPException(status_code=400, detail="Invalid plugin name")

    try:
        return await _proxy_delete(f"/api/resources/plugins/{name}")
    except Exception as e:
        logger.error(f"Plugin delete proxy error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Plugin Services API (plugin embedded HTTP service management)
# ============================================================


@admin_router.get("/admin/services")
async def admin_list_services_manage(current_user: User = Depends(get_current_user_dep)):
    """List all discovered services enriched with plugin metadata and runtime status.
    Used by the standalone Service Management page."""
    return await _proxy_get("/api/services/manage")


@admin_router.get("/admin/plugin-services")
async def admin_list_plugin_services(current_user: User = Depends(get_current_user_dep)):
    """List all plugin services and their runtime status"""
    return await _proxy_get("/api/plugin-services")


@admin_router.post("/admin/plugin-services/{name}/start")
async def admin_start_plugin_service(name: str, current_user: User = Depends(get_current_user_dep)):
    """Start a plugin service"""
    return await _proxy_post(f"/api/plugin-services/{name}/start", json={})


@admin_router.post("/admin/plugin-services/{name}/stop")
async def admin_stop_plugin_service(name: str, current_user: User = Depends(get_current_user_dep)):
    """Stop a plugin service"""
    return await _proxy_post(f"/api/plugin-services/{name}/stop", json={})


@admin_router.post("/admin/plugin-services/{name}/restart")
async def admin_restart_plugin_service(name: str, current_user: User = Depends(get_current_user_dep)):
    """Restart a plugin service"""
    return await _proxy_post(f"/api/plugin-services/{name}/restart", json={})


@admin_router.put("/admin/plugin-services/{name}/auto-start")
async def admin_set_plugin_service_auto_start(
    name: str, body: dict = Body(...), current_user: User = Depends(get_current_user_dep)
):
    """Set service auto-start on boot"""
    return await _proxy_put(f"/api/plugin-services/{name}/auto-start", json_body=body)


@admin_router.get("/admin/plugin-services/{name}/logs")
async def admin_get_plugin_service_logs(
    name: str, lines: int = Query(200), current_user: User = Depends(get_current_user_dep)
):
    """Get plugin service log buffer"""
    return await _proxy_get(f"/api/plugin-services/{name}/logs", params={"lines": str(lines)})


# ============================================================
# MCP Management API
# ============================================================


@admin_router.get("/admin/mcp/config")
async def admin_get_mcp_central(current_user: User = Depends(get_current_user_dep)):
    """Get central (unified) MCP server config"""
    return await _proxy_get("/api/mcp/config")


@admin_router.put("/admin/mcp/config")
async def admin_put_mcp_central(request: Request, current_user: User = Depends(get_current_user_dep)):
    """Save central (unified) MCP server config — syncs to all agents"""
    body = await request.json()
    return await _proxy_put("/api/mcp/config", json_body=body)


@admin_router.get("/admin/agents/{name}/mcp")
async def admin_get_mcp(name: str, current_user: User = Depends(get_current_user_dep)):
    """Get MCP server config for an agent (legacy)"""
    return await _proxy_get(f"/api/agents/{name}/mcp")


@admin_router.put("/admin/agents/{name}/mcp")
async def admin_put_mcp(name: str, request: Request, current_user: User = Depends(get_current_user_dep)):
    """Save MCP server config for an agent (legacy)"""
    body = await request.json()
    return await _proxy_put(f"/api/agents/{name}/mcp", json_body=body)


@admin_router.get("/admin/mcp/global")
async def admin_get_mcp_global(current_user: User = Depends(get_current_user_dep)):
    """Get global per-server MCP enabled state"""
    return await _proxy_get("/api/mcp/global")


@admin_router.put("/admin/mcp/global/servers/{server_name}/enable")
async def admin_enable_mcp_server_global(server_name: str, current_user: User = Depends(get_current_user_dep)):
    """Globally enable a specific MCP server across all agents"""
    return await _proxy_put(f"/api/mcp/global/servers/{server_name}/enable", json_body={})


@admin_router.put("/admin/mcp/global/servers/{server_name}/disable")
async def admin_disable_mcp_server_global(server_name: str, current_user: User = Depends(get_current_user_dep)):
    """Globally disable a specific MCP server across all agents"""
    return await _proxy_put(f"/api/mcp/global/servers/{server_name}/disable", json_body={})


# ============================================================
# Skills API
# ============================================================


@admin_router.get("/admin/skills")
async def admin_list_skills(current_user: User = Depends(get_current_user_dep)):
    """Get all skills list"""
    return await _proxy_get("/api/skills")


@admin_router.get("/admin/skills/{skill_name}/source")
async def admin_get_skill_source(skill_name: str, current_user: User = Depends(get_current_user_dep)):
    """Get skill source files and SKILL.md content"""
    if not re.match(r"^[a-zA-Z0-9_\-]+$", skill_name):
        return JSONResponse({"error": "Invalid skill name"}, status_code=400)
    return await _proxy_get(f"/api/skills/{skill_name}/source")


@admin_router.post("/admin/skills/upload")
async def admin_upload_skill(files: list[UploadFile] = File(...), current_user: User = Depends(get_current_user_dep)):
    """Upload skill folder - proxy to Launcher so it saves locally to the agent machine"""
    payload_files = []

    for upload_file in files:
        if not upload_file.filename:
            continue
        content = await upload_file.read()
        b64_content = base64.b64encode(content).decode("utf-8")
        payload_files.append({"filename": upload_file.filename, "content": b64_content})

    if not payload_files:
        return JSONResponse({"success": False, "error": "No valid files provided"})

    try:
        # Proxy to Launcher
        result = await _proxy_post("/api/resources/upload", json={"resource_type": "skills", "files": payload_files})
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Skill upload proxy error: {e}")
        return JSONResponse({"success": False, "error": str(e)})


@admin_router.post("/admin/plugins/upload")
async def admin_upload_plugin(files: list[UploadFile] = File(...), current_user: User = Depends(get_current_user_dep)):
    """Upload plugin folder - proxy to Launcher so it saves locally to the agent machine"""
    payload_files = []

    for upload_file in files:
        if not upload_file.filename:
            continue
        content = await upload_file.read()
        b64_content = base64.b64encode(content).decode("utf-8")
        payload_files.append({"filename": upload_file.filename, "content": b64_content})

    if not payload_files:
        return JSONResponse({"success": False, "error": "No valid files provided"})

    try:
        # Proxy to Launcher
        result = await _proxy_post("/api/resources/upload", json={"resource_type": "plugins", "files": payload_files})
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Plugin upload proxy error: {e}")
        return JSONResponse({"success": False, "error": str(e)})


@admin_router.delete("/admin/skills/{skill_name}")
async def admin_delete_skill(skill_name: str, current_user: User = Depends(get_current_user_dep)):
    """Delete a skill by name - proxied to Launcher to delete from agent machine"""
    if not re.match(r"^[a-zA-Z0-9_\-]+$", skill_name):
        return JSONResponse({"success": False, "error": "Invalid skill name"})

    try:
        return await _proxy_delete(f"/api/resources/skills/{skill_name}")
    except Exception as e:
        logger.error(f"Skill delete proxy error: {e}")
        return JSONResponse({"success": False, "error": str(e)})


# ============================================================
# Role Cards API
# ============================================================


@admin_router.get("/admin/role-cards")
async def admin_list_role_cards(current_user: User = Depends(get_current_user_dep)):
    return await _proxy_get("/api/role-cards")


@admin_router.get("/admin/role-cards/{card_name}")
async def admin_get_role_card(card_name: str, current_user: User = Depends(get_current_user_dep)):
    return await _proxy_get(f"/api/role-cards/{card_name}")


@admin_router.put("/admin/role-cards/{card_name}")
async def admin_put_role_card(card_name: str, body: dict, current_user: User = Depends(get_current_user_dep)):
    return await _proxy_put(f"/api/role-cards/{card_name}", body)


@admin_router.delete("/admin/role-cards/{card_name}")
async def admin_delete_role_card(card_name: str, current_user: User = Depends(get_current_user_dep)):
    return await _proxy_delete(f"/api/role-cards/{card_name}")


@admin_router.put("/admin/agents/{name}/role-prompt")
async def admin_put_role_prompt(name: str, body: dict, current_user: User = Depends(get_current_user_dep)):
    return await _proxy_put(f"/api/agents/{name}/role-prompt", body)


@admin_router.delete("/admin/agents/{name}/role-prompt")
async def admin_delete_role_prompt(name: str, current_user: User = Depends(get_current_user_dep)):
    return await _proxy_delete(f"/api/agents/{name}/role-prompt")


# ============================================================
# Collab Cards API
# ============================================================


@admin_router.get("/admin/collab-cards")
async def admin_list_collab_cards(current_user: User = Depends(get_current_user_dep)):
    return await _proxy_get("/api/collab-cards")


@admin_router.get("/admin/collab-cards/{card_name}")
async def admin_get_collab_card(card_name: str, current_user: User = Depends(get_current_user_dep)):
    return await _proxy_get(f"/api/collab-cards/{card_name}")


@admin_router.put("/admin/collab-cards/{card_name}")
async def admin_put_collab_card(card_name: str, body: dict, current_user: User = Depends(get_current_user_dep)):
    return await _proxy_put(f"/api/collab-cards/{card_name}", body)


@admin_router.delete("/admin/collab-cards/{card_name}")
async def admin_delete_collab_card(card_name: str, current_user: User = Depends(get_current_user_dep)):
    return await _proxy_delete(f"/api/collab-cards/{card_name}")


# Model Cards
@admin_router.get("/admin/model-cards")
async def admin_list_model_cards(current_user: User = Depends(get_current_user_dep)):
    return await _proxy_get("/api/model-cards")


# Model Presets (LiteLLM + OpenRouter aggregated, no login required)
@admin_router.get("/model-presets")
async def get_model_presets():
    """
    Returns the aggregated model preset list from LiteLLM (initialized) + OpenRouter (refreshed every 30 min).
    Includes each provider's base_url, provider type, and per-model capability info.
    No login required (for quick autofill when creating new model cards).
    """
    return model_preset_service.get_presets()


@admin_router.post("/model-presets/refresh")
async def refresh_model_presets():
    """
    Manually trigger a re-fetch of LiteLLM + OpenRouter data and update the cache.
    No login required (symmetric with the GET endpoint).
    """
    return await model_preset_service.manual_refresh()


@admin_router.get("/admin/model-cards/{card_name}")
async def admin_get_model_card(card_name: str, current_user: User = Depends(get_current_user_dep)):
    return await _proxy_get(f"/api/model-cards/{card_name}")


@admin_router.put("/admin/model-cards/{card_name}")
async def admin_put_model_card(card_name: str, body: dict, current_user: User = Depends(get_current_user_dep)):
    return await _proxy_put(f"/api/model-cards/{card_name}", body)


@admin_router.delete("/admin/model-cards/{card_name}")
async def admin_delete_model_card(card_name: str, current_user: User = Depends(get_current_user_dep)):
    return await _proxy_delete(f"/api/model-cards/{card_name}")


@admin_router.put("/admin/agents/{name}/model-card")
async def admin_put_model_card_assign(name: str, body: dict, current_user: User = Depends(get_current_user_dep)):
    return await _proxy_put(f"/api/agents/{name}/model-card", body)


@admin_router.delete("/admin/agents/{name}/model-card")
async def admin_delete_model_card_unassign(name: str, current_user: User = Depends(get_current_user_dep)):
    return await _proxy_delete(f"/api/agents/{name}/model-card")
