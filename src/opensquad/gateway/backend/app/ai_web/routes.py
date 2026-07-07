"""
AI Web API routes
HTTP APIs for the frontend:
  - Agent listing & details
  - Gateway session management (in-memory, user-scoped)
  - Agent disk session management (read-only, per-agent)
  - Image upload
  - Admin management (proxy to launcher.py)
"""

import ast
import asyncio
import base64
import io
import json
import logging
import os
import re
import shutil
import time
import uuid
import zipfile

import httpx
from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from opensquad.system_config import syscfg

# SSL context for GitHub API calls (Windows may lack proper CA certificates)
# Default to "1" (verify SSL). Set OPENQUAD_SSL_VERIFY="0" to disable in dev/air-gapped environments.
_SSL_VERIFY = os.environ.get("OPENQUAD_SSL_VERIFY", "1") != "0"
from datetime import datetime, timezone

# Import gateway authentication
from app.api import get_current_user_dep
from app.models import User
from opensquad.collab_board import (
    append_public_discussion as collab_board_append_public_discussion,
)
from opensquad.collab_board import (
    create_task as collab_board_create_task,
)
from opensquad.collab_board import (
    delete_item as collab_board_delete_item,
)
from opensquad.collab_board import (
    delete_task as collab_board_delete_task,
)
from opensquad.collab_board import (
    list_items as collab_board_list_items,
)
from opensquad.collab_board import (
    list_plan_snapshots as collab_board_list_plan_snapshots,
)
from opensquad.collab_board import (
    list_tasks as collab_board_list_tasks,
)
from opensquad.collab_board import (
    save_plan_snapshot as collab_board_save_plan_snapshot,
)
from opensquad.collab_board import (
    update_task as collab_board_update_task,
)
from opensquad.collab_board import (
    upsert_item as collab_board_upsert_item,
)

from . import model_preset_service
from .agent_sessions import async_get_reader as async_get_agent_session_reader
from .audit_routes import router as audit_router
from .registry import registry
from .sessions import gateway_session_cache
from .websocket import launcher_handler

logger = logging.getLogger(__name__)


def _normalize_session_message(msg: dict) -> dict:
    """Normalize legacy/variant session message schema to a unified chat payload."""
    if not isinstance(msg, dict):
        return msg

    out = dict(msg)
    extra = out.get("extra") if isinstance(out.get("extra"), dict) else {}

    # Stable id
    mid = out.get("message_id") or out.get("id") or extra.get("message_id") or extra.get("id")
    if mid:
        out["message_id"] = mid

    # Canonical text field fallback (file_push often stores message in extra.message)
    if not isinstance(out.get("content"), str):
        out["content"] = ""
    if not out.get("content") and isinstance(extra.get("message"), str) and extra.get("message"):
        out["content"] = extra.get("message")

    # Canonical media fields
    if not isinstance(out.get("images"), list):
        out["images"] = []
    else:
        # Canonicalize image list to URL strings (accept legacy object entries)
        out["images"] = [
            (i if isinstance(i, str) else (i.get("url") or i.get("path") or i.get("src") or ""))
            for i in out["images"]
            if isinstance(i, str | dict)
        ]
        out["images"] = [u for u in out["images"] if isinstance(u, str) and u.strip()]
    if not isinstance(out.get("attachments"), list):
        out["attachments"] = []
    if not isinstance(out.get("files"), list):
        out["files"] = []

    if not out["images"] and isinstance(extra.get("images"), list):
        out["images"] = [
            (i if isinstance(i, str) else (i.get("url") or i.get("path") or i.get("src") or ""))
            for i in extra.get("images")
            if isinstance(i, str | dict)
        ]
        out["images"] = [u for u in out["images"] if isinstance(u, str) and u.strip()]
    if not out["attachments"] and isinstance(extra.get("attachments"), list):
        out["attachments"] = extra.get("attachments")
    if not out["files"] and isinstance(extra.get("files"), list):
        out["files"] = extra.get("files")

    # Legacy payload migration-on-read:
    # Parse text markers and lift them to structured files/images for frontend rendering.
    content_text = out.get("content") or ""
    parsed_files = []
    for m in re.finditer(r"\[File:\s*(.*?)\]\((.*?)\)", content_text):
        name = (m.group(1) or "file").strip()
        url = (m.group(2) or "").strip()
        if not url:
            continue
        lower = url.lower()
        is_image = lower.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"))
        is_audio = lower.endswith((".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac"))
        is_video = lower.endswith((".mp4", ".webm", ".mov", ".avi", ".mkv"))
        parsed_files.append(
            {
                "original_name": name,
                "url": url,
                "is_image": is_image,
                "is_audio": is_audio,
                "is_video": is_video,
            }
        )

    # Also parse <image>...</image> markers commonly used by runner/session text.
    for m in re.finditer(r"<image>(.*?)</image>", content_text, flags=re.IGNORECASE | re.DOTALL):
        url = (m.group(1) or "").strip()
        if not url:
            continue
        parsed_files.append(
            {
                "original_name": "image",
                "url": url,
                "is_image": True,
                "is_audio": False,
                "is_video": False,
            }
        )

    if parsed_files and not out["files"]:
        out["files"] = parsed_files

    # Derive image urls from files when needed
    if not out["images"] and out["files"]:
        out["images"] = [
            (f.get("url") or f.get("path") or f.get("src"))
            for f in out["files"]
            if isinstance(f, dict)
            and (f.get("url") or f.get("path") or f.get("src"))
            and (f.get("is_image") or str(f.get("content_type", "")).startswith("image/"))
        ]

    # If attachments are absent, derive non-image files as lightweight attachments.
    if not out["attachments"] and out["files"]:
        out["attachments"] = [
            {
                "name": f.get("original_name") or f.get("filename") or "file",
                "url": f.get("url"),
                "type": "video" if f.get("is_video") else ("audio" if f.get("is_audio") else "file"),
            }
            for f in out["files"]
            if isinstance(f, dict) and f.get("url") and not f.get("is_image")
        ]

    # Remove legacy [File:...](...) markers from visible text to avoid
    # "instruction + many file lines squeezed together" on refresh.
    if isinstance(out.get("content"), str) and "[File:" in out["content"]:
        cleaned = re.sub(r"\n?\s*\[File:\s*.*?\]\(.*?\)", "", out["content"]).strip()
        out["content"] = cleaned

    return out


def _normalize_session_payload(session: dict | None) -> dict | None:
    """Normalize a session payload so frontend can rely on one schema."""
    if not isinstance(session, dict):
        return session
    out = dict(session)
    messages = out.get("messages") if isinstance(out.get("messages"), list) else []
    out["messages"] = [_normalize_session_message(m) for m in messages]
    # Run the same per-message normalisation on archived_messages so the
    # frontend gets a consistent shape whether it is reading live or
    # archived content.
    archived_messages = out.get("archived_messages") if isinstance(out.get("archived_messages"), list) else []
    out["archived_messages"] = [_normalize_session_message(m) for m in archived_messages]
    # archived_events keep their raw shape (no per-message normalisation
    # exists for events); the frontend renders them as workflow events.
    if "archived_events" not in out or not isinstance(out["archived_events"], list):
        out["archived_events"] = []
    return out


# Launcher management API address - from system_config.json
LAUNCHER_URL = syscfg.launcher_url()
_REPO_ROOT = syscfg.project_root()

router = APIRouter(prefix="/api/ai-web")
router.include_router(audit_router)


class ConfigUpdateRequest(BaseModel):
    """Configuration update request"""

    system_prompt: str | None = None
    model: str | None = None
    temperature: float | None = None


@router.get("/agents")
async def list_agents(
    category: str | None = None,
    status: str | None = None,
    search: str | None = None,
    current_user: User = Depends(get_current_user_dep),
):
    """
    Get Agent list
    Returns Agent list categorized by type
    """
    agents = registry.list_agents(status=status, agent_type=category)

    # Group by type
    categorized = {}
    for agent in agents:
        agent_type = agent.agent_type
        if agent_type not in categorized:
            categorized[agent_type] = {
                "name": _get_category_name(agent_type),
                "icon": _get_category_icon(agent_type),
                "agents": [],
            }

        categorized[agent_type]["agents"].append(
            {
                "id": agent.agent_id,
                "name": agent.agent_name,
                "type": agent_type,
                "capabilities": agent.capabilities,
                "description": agent.description,
                "status": agent.status,
                "load_percent": agent.load_percent,
                "today_chats": agent.today_chats,
            }
        )

    # Get stats
    stats = registry.get_stats()

    return {"categories": categorized, "stats": stats}


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str, current_user: User = Depends(get_current_user_dep)):
    """Get details for a single Agent"""
    agent = registry.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    return {
        "id": agent.agent_id,
        "name": agent.agent_name,
        "type": agent.agent_type,
        "capabilities": agent.capabilities,
        "description": agent.description,
        "status": agent.status,
        "load_percent": agent.load_percent,
        "today_chats": agent.today_chats,
        "total_chats": agent.total_chats,
        "registered_at": agent.registered_at,
    }


@router.get("/sessions")
async def get_user_sessions(agent_id: str | None = None, current_user: User = Depends(get_current_user_dep)):
    """Get session list for the current user"""
    sessions = await gateway_session_cache.async_get_user_sessions(current_user.id)

    result = []
    for session in sessions:
        # Get Agent info
        agent = registry.get_agent(session["agent_id"])
        agent_name = agent.agent_name if agent else "Unknown"

        result.append(
            {
                "session_key": session["session_key"],
                "agent_id": session["agent_id"],
                "agent_name": agent_name,
                "message_count": session["message_count"],
                "last_message": session["last_message"],
                "updated_at": session["updated_at"],
                "created_at": session["created_at"],
            }
        )

    return {"sessions": result}


@router.get("/sessions/{agent_id}/history")
async def get_session_history(
    agent_id: str, limit: int = Query(50, ge=1, le=200), current_user: User = Depends(get_current_user_dep)
):
    """Get session history with a specific Agent"""
    # Check permissions (can only view own sessions)
    history = await gateway_session_cache.async_get_history(current_user.id, agent_id, limit)

    return {"agent_id": agent_id, "history": history, "count": len(history)}


@router.post("/sessions/{agent_id}/clear")
async def clear_session(agent_id: str, current_user: User = Depends(get_current_user_dep)):
    """Clear session history with a specific Agent"""
    await gateway_session_cache.async_clear_session(current_user.id, agent_id)
    return {"message": "Session cleared"}


@router.post("/agents/{agent_id}/config")
async def update_agent_config(
    agent_id: str, config: ConfigUpdateRequest, current_user: User = Depends(get_current_user_dep)
):
    """
    Update Agent config (hot reload)
    Send config update command to Agent
    """
    # Check if Agent is online
    agent = registry.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    if agent.status == "offline":
        raise HTTPException(400, "Agent is offline")

    # Build config update command
    update_config = {}
    if config.system_prompt is not None:
        update_config["system_prompt"] = config.system_prompt
    if config.model is not None:
        update_config["model"] = config.model
    if config.temperature is not None:
        update_config["temperature"] = config.temperature

    if not update_config:
        raise HTTPException(400, "No config to update")

    # Send command to Agent
    success = await registry.send_to_agent(
        agent_id, {"type": "command", "command": "update_config", "config": update_config}
    )

    if success:
        return {"message": "Config update sent to agent"}
    else:
        raise HTTPException(500, "Failed to send config to agent")


@router.get("/stats")
async def get_stats(current_user: User = Depends(get_current_user_dep)):
    """Get system statistics"""
    agent_stats = registry.get_stats()
    session_stats = await gateway_session_cache.async_get_stats()

    return {"agents": agent_stats, "sessions": session_stats}


def _get_current_version() -> str:
    """Read the running version from the canonical source.

    Priority order, deliberately defensive so the /version endpoint never
    crashes the gateway:

    1. ``importlib.metadata.version("opensquad")`` — the PEP 517 standard.
       Reads the version field from the installed package's metadata,
       which is generated from ``pyproject.toml`` at ``pip install`` time.
       This is the version the package itself reports to other tools, so
       it can never drift from ``pyproject.toml`` after a real install.
    2. ``opensquad.__version__`` — a fast fallback for source-tree
       invocations (e.g. running tests directly from a checkout) where
       no installed metadata is available. Note: the module-level
       ``__version__`` is hand-maintained and CAN drift from
       ``pyproject.toml`` — that drift was the original bug this
       function used to expose.
    3. ``"unknown"`` — last-resort sentinel so callers can render
       a placeholder rather than crashing.
    """
    # 1. importlib.metadata — canonical, post-install source of truth.
    try:
        from importlib.metadata import version as _pkg_version

        v = _pkg_version("opensquad")
        if v and v != "0.0.0":
            return v
    except Exception:
        pass

    # 2. opensquad.__version__ — dev / source-tree fallback.
    try:
        from opensquad import __version__ as v

        if v and v != "unknown":
            return v
    except Exception:
        pass

    # 3. Last resort.
    return "unknown"


def _compare_versions(current: str, latest: str) -> bool:
    """Return True if latest > current."""
    if not latest or not current:
        return False
    if latest == current:
        return False
    try:
        from packaging.version import Version

        return Version(latest) > Version(current)
    except Exception:
        # Fallback: compare numeric dot-separated parts
        def _parts(v: str):
            return tuple(int(x) for x in v.split(".") if x.isdigit())

        return _parts(latest) > _parts(current)


async def _fetch_latest_github_release(client: httpx.AsyncClient) -> dict | None:
    """Fetch the latest formal GitHub release, or None if none exists."""
    resp = await client.get(
        "https://api.github.com/repos/opensquad-ai/opensquad/releases/latest",
        headers={"Accept": "application/vnd.github.v3+json", "User-Agent": "OpenSquad"},
    )
    if resp.status_code == 200:
        data = resp.json()
        return {
            "latest": data.get("tag_name", "").lstrip("v"),
            "url": data.get("html_url", ""),
        }
    return None


async def _fetch_latest_github_tag(client: httpx.AsyncClient) -> dict | None:
    """Fallback: fetch the most recent Git tag (works without formal releases)."""
    resp = await client.get(
        "https://api.github.com/repos/opensquad-ai/opensquad/tags",
        headers={"Accept": "application/vnd.github.v3+json", "User-Agent": "OpenSquad"},
        params={"per_page": 1},
    )
    if resp.status_code == 200:
        tags = resp.json()
        if tags and isinstance(tags, list):
            first = tags[0]
            tag_name = first.get("name", "").lstrip("v")
            return {
                "latest": tag_name,
                "url": first.get("html_url", ""),
            }
    return None


@router.get("/version")
async def check_version():
    """Get current version and check for updates from GitHub.

    First tries the formal /releases/latest endpoint. If no release exists (404),
    falls back to /tags so that lightweight tag-based workflows still show update
    notifications.
    """
    current = _get_current_version()
    result = {"current": current, "latest": None, "url": None, "update_available": False}

    try:
        async with httpx.AsyncClient(timeout=10, verify=_SSL_VERIFY) as client:
            release = await _fetch_latest_github_release(client)
            if release is None:
                release = await _fetch_latest_github_tag(client)

            if release:
                latest = release["latest"]
                result["latest"] = latest
                result["url"] = release["url"]
                result["update_available"] = _compare_versions(current, latest)
    except Exception as e:
        logger.debug(f"[version] GitHub check failed: {e}")

    return result


# Helper functions
def _get_category_name(agent_type: str) -> str:
    """Get category name"""
    names = {
        "coder": "Programming & Development",
        "writer": "Writing & Creation",
        "analyst": "Data Analysis",
        "general": "General Assistant",
        "translator": "Translation Services",
    }
    return names.get(agent_type, "Other")


def _get_category_icon(agent_type: str) -> str:
    """Get category icon"""
    icons = {"coder": "💻", "writer": "✍️", "analyst": "📊", "general": "🤖", "translator": "🌐"}
    return icons.get(agent_type, "🔧")


# ============================================================
# Admin Management API — proxy to launcher.py :9600
# ============================================================


async def _proxy_get(path: str, params: dict | None = None, launcher_url: str | None = None) -> dict:
    """GET proxy to launcher — prefer WS tunnel, fallback to HTTP"""
    # WS tunnel: no inbound port needed on home machine
    if launcher_url is None and launcher_handler.has_connections():
        node_id = launcher_handler.get_any_node_id()
        full_path = path
        if params:
            from urllib.parse import urlencode

            full_path = f"{path}?{urlencode(params)}"
        try:
            return await launcher_handler.rpc(node_id, "GET", full_path, timeout=5.0)
        except Exception:
            pass  # Fall through to HTTP fallback
    # No explicit launcher_url and WS tunnel not connected; raise error directly (no HTTP fallback needed)
    if launcher_url is None and not LAUNCHER_URL:
        raise HTTPException(503, "Launcher WS tunnel not connected yet. Please wait for Launcher to register.")
    # HTTP fallback (same-machine or explicit launcher_url override)
    base = launcher_url or LAUNCHER_URL
    if not base:
        raise HTTPException(503, "Launcher not available (no WS tunnel and no HTTP URL configured)")
    async with httpx.AsyncClient(timeout=5.0) as client:
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


async def _proxy_post(path: str, json: dict | None = None, launcher_url: str | None = None) -> dict:
    """POST proxy to launcher — prefer WS tunnel, fallback to HTTP"""
    if launcher_url is None and launcher_handler.has_connections():
        node_id = launcher_handler.get_any_node_id()
        try:
            return await launcher_handler.rpc(node_id, "POST", path, body=json, timeout=5.0)
        except Exception:
            pass  # Fall through to HTTP fallback
    if launcher_url is None and not LAUNCHER_URL:
        raise HTTPException(503, "Launcher WS tunnel not connected yet. Please wait for Launcher to register.")
    base = launcher_url or LAUNCHER_URL
    if not base:
        raise HTTPException(503, "Launcher not available (no WS tunnel and no HTTP URL configured)")
    async with httpx.AsyncClient(timeout=5.0) as client:
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


async def _proxy_put(path: str, json_body: dict | None = None, launcher_url: str | None = None) -> dict:
    """PUT proxy to launcher — prefer WS tunnel, fallback to HTTP"""
    if launcher_url is None and launcher_handler.has_connections():
        node_id = launcher_handler.get_any_node_id()
        try:
            return await launcher_handler.rpc(node_id, "PUT", path, body=json_body, timeout=5.0)
        except Exception:
            pass
    if launcher_url is None and not LAUNCHER_URL:
        raise HTTPException(503, "Launcher WS tunnel not connected yet. Please wait for Launcher to register.")
    base = launcher_url or LAUNCHER_URL
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
    if launcher_url is None and launcher_handler.has_connections():
        node_id = launcher_handler.get_any_node_id()
        try:
            return await launcher_handler.rpc(node_id, "DELETE", path, timeout=5.0)
        except Exception:
            pass
    if launcher_url is None and not LAUNCHER_URL:
        raise HTTPException(503, "Launcher WS tunnel not connected yet. Please wait for Launcher to register.")
    base = launcher_url or LAUNCHER_URL
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


@router.get("/admin/agents")
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

        model_cfg = agent_config.get("model", {})
        model_card = agent.get("model_card")
        if not model_card and isinstance(model_cfg, dict):
            model_card = model_cfg.get("_card")
        role_card = agent.get("role_card")
        if not role_card:
            prompt_cfg = agent_config.get("prompt", {})
            if isinstance(prompt_cfg, dict):
                role_card = prompt_cfg.get("role")

        merged.append(
            {
                # launcher process info
                "dir_name": agent.get("dir_name", ""),
                "agent_id": agent_id,
                "agent_name": agent.get("agent_name", ""),
                "agent_type": agent_type,
                "description": description,
                "process_status": process_status,
                "pid": agent.get("pid"),
                "started_at": agent.get("started_at"),
                "restart_count": agent.get("restart_count", 0),
                # registry online info
                "registry_online": reg is not None and reg.status != "offline" if reg else False,
                "registry_status": reg.status if reg else "offline",
                "load_percent": reg.load_percent if reg else 0,
                "today_chats": reg.today_chats if reg else 0,
                # token consumption stats
                "token_stats": agent.get("token_stats"),
                # Group chat account profile (name, avatar)
                "chat_profile": agent.get("chat_profile"),
                "model_card": model_card,
                "role_card": role_card,
            }
        )

    # If registry has Agents not tracked by launcher (e.g. started directly via boot.py)
    for agent_id, reg in registry_map.items():
        merged.append(
            {
                "dir_name": "",
                "agent_id": reg.agent_id,
                "agent_name": reg.agent_name,
                "agent_type": reg.agent_type,
                "description": reg.description,
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


@router.get("/admin/agents/{name}/config")
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


@router.get("/admin/agents/{name}/working-directory")
async def admin_get_working_directory(name: str, current_user: User = Depends(get_current_user_dep)):
    """Get the agent's current session working directory (if set via folder-picker)."""
    return await _proxy_get(f"/api/agents/{name}/working-directory")


@router.put("/admin/agents/{name}/working-directory")
async def admin_set_working_directory(
    name: str, body: dict = Body(...), current_user: User = Depends(get_current_user_dep)
):
    """Set the agent's session-level working directory.

    Body: ``{"path": "C:\\Users\\admin\\projects\\my-app"}``
    Send ``{"path": ""}`` to reset to workspace root.

    The launcher writes a ``.session_cwd`` signal file; the agent process
    picks it up at the start of the next conversation turn and applies it
    via ``filesystem.set_session_cwd()``.
    """
    return await _proxy_put(f"/api/agents/{name}/working-directory", body)


@router.put("/admin/agents/{name}/config")
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


@router.get("/admin/agents/{name}/role")
async def admin_get_role(name: str, current_user: User = Depends(get_current_user_dep)):
    """Get Agent's role.md"""
    return await _proxy_get(f"/api/agents/{name}/role")


@router.put("/admin/agents/{name}/role")
async def admin_update_role(name: str, body: dict = Body(...), current_user: User = Depends(get_current_user_dep)):
    """Update Agent's role.md"""
    return await _proxy_put(f"/api/agents/{name}/role", body)


@router.post("/admin/agents/{name}/start")
async def admin_start_agent(name: str, current_user: User = Depends(get_current_user_dep)):
    """Start Agent process"""
    return await _proxy_post(f"/api/agents/{name}/start")


@router.post("/admin/agents/{name}/stop")
async def admin_stop_agent(name: str, current_user: User = Depends(get_current_user_dep)):
    """Stop Agent process"""
    return await _proxy_post(f"/api/agents/{name}/stop")


@router.post("/admin/agents/{name}/restart")
async def admin_restart_agent(name: str, current_user: User = Depends(get_current_user_dep)):
    """Restart Agent process"""
    return await _proxy_post(f"/api/agents/{name}/restart")


@router.get("/admin/agents/{name}/logs")
async def admin_get_logs(
    name: str, lines: int = Query(200, ge=1, le=1000), current_user: User = Depends(get_current_user_dep)
):
    """Get Agent recent logs"""
    return await _proxy_get(f"/api/agents/{name}/logs", {"lines": lines})


@router.post("/admin/agents/create")
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

_GATEWAY_LOG_DIR = os.path.join(_syscfg.project_root(), "data", "logs", "gateway")


def _fill_logging_defaults(data: dict) -> dict:
    """Fill missing logging keys with effective defaults so the UI shows current values."""
    if not isinstance(data.get("logging"), dict):
        data["logging"] = {}
    logging_cfg = data["logging"]
    if "log_dir" not in logging_cfg:
        logging_cfg["log_dir"] = _syscfg.log_dir()
    if "max_size_mb" not in logging_cfg:
        logging_cfg["max_size_mb"] = _syscfg.log_max_size_mb()
    if "backup_count" not in logging_cfg:
        logging_cfg["backup_count"] = _syscfg.log_backup_count()
    if "tool_call_debug" not in logging_cfg:
        logging_cfg["tool_call_debug"] = _syscfg.tool_call_debug()
    if "log_level" not in logging_cfg:
        logging_cfg["log_level"] = _syscfg.log_level()
    return data


@router.get("/admin/system/config")
async def admin_get_system_config(current_user: User = Depends(get_current_user_dep)):
    """Read full contents of system_config.json (with effective logging defaults filled in)."""
    import opensquad.system_config as _sc
    from opensquad._syscfg._config import ensure_workspace_config_file

    ensure_workspace_config_file()
    data = _sc.raw()
    if not data:
        with open(_sc._CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
    return _fill_logging_defaults(data)


@router.put("/admin/system/config")
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


@router.get("/admin/system/log-files")
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


@router.get("/admin/system/logs")
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


@router.get("/admin/system/log-level")
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


@router.put("/admin/system/log-level")
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


@router.delete("/admin/agents/{name}")
async def admin_delete_agent(name: str, current_user: User = Depends(get_current_user_dep)):
    """
    Delete Agent
    Stop the process first, then delete the directory
    """
    return await _proxy_delete(f"/api/agents/{name}")


# ============================================================
# Plugin Management API
# ============================================================


@router.get("/admin/plugins")
async def admin_list_plugins(current_user: User = Depends(get_current_user_dep)):
    """Get all plugins list with metadata"""
    return await _proxy_get("/api/plugins")


@router.post("/admin/plugins/report-view-error")
async def admin_report_plugin_view_error(request: Request, current_user: User = Depends(get_current_user_dep)):
    """Forward a plugin view runtime error to the launcher so it can be logged for the agent."""
    body = await request.json()
    return await _proxy_post("/api/plugin-view-error", body)


@router.put("/admin/plugins/{name}/enable")
async def admin_enable_plugin(name: str, current_user: User = Depends(get_current_user_dep)):
    """Enable a plugin on the local node"""
    return await _proxy_put(f"/api/plugins/{name}/enable")


@router.put("/admin/plugins/{name}/disable")
async def admin_disable_plugin(name: str, current_user: User = Depends(get_current_user_dep)):
    """Disable a plugin on the local node"""
    return await _proxy_put(f"/api/plugins/{name}/disable")


@router.get("/admin/plugins/{name}/config")
async def admin_get_plugin_config(name: str, current_user: User = Depends(get_current_user_dep)):
    """Get plugin config values and schema from the local node"""
    return await _proxy_get(f"/api/plugins/{name}/config")


@router.put("/admin/plugins/{name}/config")
async def admin_put_plugin_config(name: str, request: Request, current_user: User = Depends(get_current_user_dep)):
    """Save plugin config values to the local node."""
    body = await request.json()
    return await _proxy_put(f"/api/plugins/{name}/config", json_body=body)


@router.get("/admin/plugins/{name}/data")
async def admin_get_plugin_data(name: str, request: Request, current_user: User = Depends(get_current_user_dep)):
    """Proxy plugin data query to launcher (e.g. token_analytics dashboard)"""
    # Forward all query params
    params = dict(request.query_params)
    return await _proxy_get(f"/api/plugins/{name}/data", params=params)


@router.post("/admin/plugins/{name}/action")
async def admin_plugin_action(name: str, request: Request, current_user: User = Depends(get_current_user_dep)):
    """Proxy plugin action to launcher"""
    body = await request.json()
    return await _proxy_post(f"/api/plugins/{name}/action", json=body)


@router.delete("/admin/plugins/{name}")
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


@router.get("/admin/services")
async def admin_list_services_manage(current_user: User = Depends(get_current_user_dep)):
    """List all discovered services enriched with plugin metadata and runtime status.
    Used by the standalone Service Management page."""
    return await _proxy_get("/api/services/manage")


@router.get("/admin/plugin-services")
async def admin_list_plugin_services(current_user: User = Depends(get_current_user_dep)):
    """List all plugin services and their runtime status"""
    return await _proxy_get("/api/plugin-services")


@router.post("/admin/plugin-services/{name}/start")
async def admin_start_plugin_service(name: str, current_user: User = Depends(get_current_user_dep)):
    """Start a plugin service"""
    return await _proxy_post(f"/api/plugin-services/{name}/start", json={})


@router.post("/admin/plugin-services/{name}/stop")
async def admin_stop_plugin_service(name: str, current_user: User = Depends(get_current_user_dep)):
    """Stop a plugin service"""
    return await _proxy_post(f"/api/plugin-services/{name}/stop", json={})


@router.post("/admin/plugin-services/{name}/restart")
async def admin_restart_plugin_service(name: str, current_user: User = Depends(get_current_user_dep)):
    """Restart a plugin service"""
    return await _proxy_post(f"/api/plugin-services/{name}/restart", json={})


@router.put("/admin/plugin-services/{name}/auto-start")
async def admin_set_plugin_service_auto_start(
    name: str, body: dict = Body(...), current_user: User = Depends(get_current_user_dep)
):
    """Set service auto-start on boot"""
    return await _proxy_put(f"/api/plugin-services/{name}/auto-start", json_body=body)


@router.get("/admin/plugin-services/{name}/logs")
async def admin_get_plugin_service_logs(
    name: str, lines: int = Query(200), current_user: User = Depends(get_current_user_dep)
):
    """Get plugin service log buffer"""
    return await _proxy_get(f"/api/plugin-services/{name}/logs", params={"lines": str(lines)})


# ============================================================
# MCP Management API
# ============================================================


@router.get("/admin/mcp/config")
async def admin_get_mcp_central(current_user: User = Depends(get_current_user_dep)):
    """Get central (unified) MCP server config"""
    return await _proxy_get("/api/mcp/config")


@router.put("/admin/mcp/config")
async def admin_put_mcp_central(request: Request, current_user: User = Depends(get_current_user_dep)):
    """Save central (unified) MCP server config — syncs to all agents"""
    body = await request.json()
    return await _proxy_put("/api/mcp/config", json_body=body)


@router.get("/admin/agents/{name}/mcp")
async def admin_get_mcp(name: str, current_user: User = Depends(get_current_user_dep)):
    """Get MCP server config for an agent (legacy)"""
    return await _proxy_get(f"/api/agents/{name}/mcp")


@router.put("/admin/agents/{name}/mcp")
async def admin_put_mcp(name: str, request: Request, current_user: User = Depends(get_current_user_dep)):
    """Save MCP server config for an agent (legacy)"""
    body = await request.json()
    return await _proxy_put(f"/api/agents/{name}/mcp", json_body=body)


@router.get("/admin/mcp/global")
async def admin_get_mcp_global(current_user: User = Depends(get_current_user_dep)):
    """Get global per-server MCP enabled state"""
    return await _proxy_get("/api/mcp/global")


@router.put("/admin/mcp/global/servers/{server_name}/enable")
async def admin_enable_mcp_server_global(server_name: str, current_user: User = Depends(get_current_user_dep)):
    """Globally enable a specific MCP server across all agents"""
    return await _proxy_put(f"/api/mcp/global/servers/{server_name}/enable", json_body={})


@router.put("/admin/mcp/global/servers/{server_name}/disable")
async def admin_disable_mcp_server_global(server_name: str, current_user: User = Depends(get_current_user_dep)):
    """Globally disable a specific MCP server across all agents"""
    return await _proxy_put(f"/api/mcp/global/servers/{server_name}/disable", json_body={})


# ============================================================
# Skills API
# ============================================================


@router.get("/admin/skills")
async def admin_list_skills(current_user: User = Depends(get_current_user_dep)):
    """Get all skills list"""
    return await _proxy_get("/api/skills")


@router.get("/admin/skills/{skill_name}/source")
async def admin_get_skill_source(skill_name: str, current_user: User = Depends(get_current_user_dep)):
    """Get skill source files and SKILL.md content"""
    if not re.match(r"^[a-zA-Z0-9_\-]+$", skill_name):
        return JSONResponse({"error": "Invalid skill name"}, status_code=400)
    return await _proxy_get(f"/api/skills/{skill_name}/source")


@router.post("/admin/skills/upload")
async def admin_upload_skill(files: list[UploadFile] = File(...), current_user: User = Depends(get_current_user_dep)):
    """Upload skill folder - proxy to Launcher so it saves locally to the agent machine"""
    import base64

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


@router.post("/admin/plugins/upload")
async def admin_upload_plugin(files: list[UploadFile] = File(...), current_user: User = Depends(get_current_user_dep)):
    """Upload plugin folder - proxy to Launcher so it saves locally to the agent machine"""
    import base64

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


@router.delete("/admin/skills/{skill_name}")
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


@router.get("/admin/role-cards")
async def admin_list_role_cards(current_user: User = Depends(get_current_user_dep)):
    return await _proxy_get("/api/role-cards")


@router.get("/admin/role-cards/{card_name}")
async def admin_get_role_card(card_name: str, current_user: User = Depends(get_current_user_dep)):
    return await _proxy_get(f"/api/role-cards/{card_name}")


@router.put("/admin/role-cards/{card_name}")
async def admin_put_role_card(card_name: str, body: dict, current_user: User = Depends(get_current_user_dep)):
    return await _proxy_put(f"/api/role-cards/{card_name}", body)


@router.delete("/admin/role-cards/{card_name}")
async def admin_delete_role_card(card_name: str, current_user: User = Depends(get_current_user_dep)):
    return await _proxy_delete(f"/api/role-cards/{card_name}")


@router.put("/admin/agents/{name}/role-prompt")
async def admin_put_role_prompt(name: str, body: dict, current_user: User = Depends(get_current_user_dep)):
    return await _proxy_put(f"/api/agents/{name}/role-prompt", body)


@router.delete("/admin/agents/{name}/role-prompt")
async def admin_delete_role_prompt(name: str, current_user: User = Depends(get_current_user_dep)):
    return await _proxy_delete(f"/api/agents/{name}/role-prompt")


# ============================================================
# Collab Cards API
# ============================================================


@router.get("/admin/collab-cards")
async def admin_list_collab_cards(current_user: User = Depends(get_current_user_dep)):
    return await _proxy_get("/api/collab-cards")


@router.get("/admin/collab-cards/{card_name}")
async def admin_get_collab_card(card_name: str, current_user: User = Depends(get_current_user_dep)):
    return await _proxy_get(f"/api/collab-cards/{card_name}")


@router.put("/admin/collab-cards/{card_name}")
async def admin_put_collab_card(card_name: str, body: dict, current_user: User = Depends(get_current_user_dep)):
    return await _proxy_put(f"/api/collab-cards/{card_name}", body)


@router.delete("/admin/collab-cards/{card_name}")
async def admin_delete_collab_card(card_name: str, current_user: User = Depends(get_current_user_dep)):
    return await _proxy_delete(f"/api/collab-cards/{card_name}")


# Model Cards
@router.get("/admin/model-cards")
async def admin_list_model_cards(current_user: User = Depends(get_current_user_dep)):
    return await _proxy_get("/api/model-cards")


# Model Presets (LiteLLM + OpenRouter aggregated, no login required)
@router.get("/model-presets")
async def get_model_presets():
    """
    Returns the aggregated model preset list from LiteLLM (initialized) + OpenRouter (refreshed every 30 min).
    Includes each provider's base_url, provider type, and per-model capability info.
    No login required (for quick autofill when creating new model cards).
    """
    return model_preset_service.get_presets()


@router.post("/model-presets/refresh")
async def refresh_model_presets():
    """
    Manually trigger a re-fetch of LiteLLM + OpenRouter data and update the cache.
    No login required (symmetric with the GET endpoint).
    """
    return await model_preset_service.manual_refresh()


@router.get("/admin/model-cards/{card_name}")
async def admin_get_model_card(card_name: str, current_user: User = Depends(get_current_user_dep)):
    return await _proxy_get(f"/api/model-cards/{card_name}")


@router.put("/admin/model-cards/{card_name}")
async def admin_put_model_card(card_name: str, body: dict, current_user: User = Depends(get_current_user_dep)):
    return await _proxy_put(f"/api/model-cards/{card_name}", body)


@router.delete("/admin/model-cards/{card_name}")
async def admin_delete_model_card(card_name: str, current_user: User = Depends(get_current_user_dep)):
    return await _proxy_delete(f"/api/model-cards/{card_name}")


@router.put("/admin/agents/{name}/model-card")
async def admin_put_model_card_assign(name: str, body: dict, current_user: User = Depends(get_current_user_dep)):
    return await _proxy_put(f"/api/agents/{name}/model-card", body)


@router.delete("/admin/agents/{name}/model-card")
async def admin_delete_model_card_unassign(name: str, current_user: User = Depends(get_current_user_dep)):
    return await _proxy_delete(f"/api/agents/{name}/model-card")


# ============================================================
# Agent disk session API — read-only access to Agent session files
# Used by NexusChat Pro's native AI chat component
# ============================================================

# Root directory for uploads — must match main.py's StaticFiles("/uploads") mount.
# Use syscfg.workspace_uploads_dir() so PyInstaller mode (OPENSQUAD_USER_DATA
# env) is handled centrally and all three upload-path sites stay in sync.
# See issue #43. (This file is shadowed by routes/__init__.py and is kept in
# sync for historical readability only.)
_UPLOAD_DIR = syscfg.workspace_uploads_dir()
os.makedirs(_UPLOAD_DIR, exist_ok=True)


@router.get("/download-file")
async def download_file(
    path: str = Query(..., description="File path or filename within uploads dir"),
):
    """
    Serve a file with Content-Disposition: attachment so the browser
    triggers a real file download instead of opening the file inline.
    The `path` param can be a full /uploads/xxx path or just a filename.
    """
    # Strip /uploads/ prefix if present, then take just the basename for safety
    safe_name = path
    if "/uploads/" in safe_name:
        safe_name = safe_name.rsplit("/uploads/", 1)[-1]
    safe_name = os.path.basename(safe_name)

    if not safe_name:
        raise HTTPException(400, "invalid file path")

    full_path = os.path.normpath(os.path.join(_UPLOAD_DIR, safe_name))
    # Path traversal guard
    if not full_path.startswith(os.path.normpath(_UPLOAD_DIR)):
        raise HTTPException(403, "access denied")
    if not os.path.isfile(full_path):
        raise HTTPException(404, "file not found")

    logger.info(f"Downloading file: {full_path}")
    return FileResponse(
        full_path,
        filename=safe_name,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


@router.get("/agent-sessions/{agent_id}/list")
async def agent_session_list(
    agent_id: str,
    current_user: User = Depends(get_current_user_dep),
):
    """
    Get session list for an agent (current + history).
    Reads from agent's disk session files.
    """
    reader = await async_get_agent_session_reader(agent_id)
    if not reader:
        raise HTTPException(404, f"Agent not found: {agent_id}")

    sessions = await reader.async_get_session_list()
    current_id = await reader.async_get_current_session_id()

    return {
        "agent_id": agent_id,
        "current_session_id": current_id,
        "sessions": sessions,
    }


@router.get("/agent-sessions/{agent_id}/current")
async def agent_current_session(
    agent_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user_dep),
):
    """
    Get the agent's current session ID and its first page of messages in one call.
    Replaces the previous getSessionList + getSessionHistoryPaged two-step flow,
    reducing session load from 2 sequential HTTP round trips to 1.
    """
    reader = await async_get_agent_session_reader(agent_id)
    if not reader:
        raise HTTPException(404, f"Agent not found: {agent_id}")

    current_id = await reader.async_get_current_session_id()
    if not current_id or current_id == "unknown":
        return {"agent_id": agent_id, "current_session_id": None, "session": None}

    data = await reader.async_get_session_history_paged(current_id, offset=offset, limit=limit)
    return {"agent_id": agent_id, "current_session_id": current_id, "session": _normalize_session_payload(data)}


# ---- Upload routes MUST be defined BEFORE the {session_id} catch-all ----
# Otherwise {session_id} matches "upload-file" etc. and returns 405.


@router.post("/agent-sessions/{agent_id}/upload-image")
async def agent_upload_image(
    agent_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user_dep),
):
    """
    Upload an image for use in chat messages.
    Returns the saved file path and filename.
    """
    ext = os.path.splitext(file.filename)[1] if file.filename else ".jpg"
    if not ext:
        ext = ".jpg"
    filename = f"{uuid.uuid4().hex[:8]}{ext}"
    filepath = os.path.join(_UPLOAD_DIR, filename)

    content = await file.read()
    with open(filepath, "wb") as fw:
        fw.write(content)

    logger.info(f"Image uploaded for {agent_id}: {filepath} ({len(content)} bytes)")

    return {
        "path": filepath,
        "filename": filename,
        "url": f"/uploads/{filename}",
    }


@router.post("/agent-sessions/{agent_id}/upload-file")
async def agent_upload_file(
    agent_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user_dep),
):
    """
    Upload any file (image, document, etc.) for use in chat messages.
    Returns the saved file path, filename, original name, size, and MIME type.
    """
    original_name = file.filename or "unknown"
    ext = os.path.splitext(original_name)[1] if original_name else ""
    filename = f"{uuid.uuid4().hex[:8]}{ext}"
    filepath = os.path.join(_UPLOAD_DIR, filename)

    content = await file.read()
    with open(filepath, "wb") as fw:
        fw.write(content)

    file_size = len(content)
    content_type = file.content_type or "application/octet-stream"

    # Determine media types
    is_image = content_type.startswith("image/")
    is_audio = content_type.startswith("audio/")
    is_video = content_type.startswith("video/")

    logger.info(
        f"File uploaded for {agent_id}: {filepath} ({file_size} bytes, type={content_type}, original={original_name})"
    )

    return {
        "path": filepath,
        "filename": filename,
        "original_name": original_name,
        "url": f"/uploads/{filename}",
        "size": file_size,
        "content_type": content_type,
        "is_image": is_image,
        "is_audio": is_audio,
        "is_video": is_video,
    }


@router.post("/agent-sessions/{agent_id}/upload-files")
async def agent_upload_files(
    agent_id: str,
    files: list[UploadFile] = File(...),
    current_user: User = Depends(get_current_user_dep),
):
    """
    Upload multiple files at once (drag-and-drop support).
    Returns a list of uploaded file info.
    """
    results = []
    for file in files:
        original_name = file.filename or "unknown"
        ext = os.path.splitext(original_name)[1] if original_name else ""
        filename = f"{uuid.uuid4().hex[:8]}{ext}"
        filepath = os.path.join(_UPLOAD_DIR, filename)

        content = await file.read()
        with open(filepath, "wb") as fw:
            fw.write(content)

        file_size = len(content)
        content_type = file.content_type or "application/octet-stream"
        is_image = content_type.startswith("image/")
        is_audio = content_type.startswith("audio/")
        is_video = content_type.startswith("video/")

        results.append(
            {
                "path": filepath,
                "filename": filename,
                "original_name": original_name,
                "url": f"/uploads/{filename}",
                "size": file_size,
                "content_type": content_type,
                "is_image": is_image,
                "is_audio": is_audio,
                "is_video": is_video,
            }
        )

    logger.info(f"Batch upload for {agent_id}: {len(results)} files")

    return {"files": results}


# ============================================================
# Collaboration Board API
# ============================================================


def _user_identity_set(current_user: User) -> set[str]:
    vals = {
        str(getattr(current_user, "id", "") or "").strip(),
        str(getattr(current_user, "name", "") or "").strip(),
        str(getattr(current_user, "email", "") or "").strip(),
    }
    email = str(getattr(current_user, "email", "") or "").strip()
    if "@" in email:
        vals.add(email.split("@", 1)[0].strip())
    # Compatibility alias used by current web UI
    vals.add("web_user")
    return {v for v in vals if v}


def _is_pm_for_collab(collab_id: str, current_user: User) -> bool:
    identities = _user_identity_set(current_user)
    task = next((t for t in collab_board_list_tasks() if str(t.get("task_id", "")) == str(collab_id)), None)
    if not isinstance(task, dict):
        return False
    created_by = str(task.get("created_by", "") or "").strip()
    return created_by in identities if created_by else False


class CollabBoardUpsertRequest(BaseModel):
    collab_id: str
    task_name: str | None = None
    agent_id: str
    item_type: str = "task"
    item_key: str | None = ""
    title: str | None = ""
    content: str | None = ""
    status: str = "doing"
    progress: int = 0
    visibility: str = "public"
    latest_tool_name: str | None = None
    latest_tool_summary: str | None = None
    extra: dict | None = None


class CollabBoardDiscussionRequest(BaseModel):
    collab_id: str
    task_name: str | None = None
    agent_id: str
    title: str = "Public discussion"
    content: str = ""


class CollabTaskCreateRequest(BaseModel):
    task_name: str
    created_by: str = "web_user"


class CollabTaskUpdateRequest(BaseModel):
    task_name: str | None = None
    progress: int | None = None
    status: str | None = None


@router.get("/collab-board/tasks")
async def get_collab_board_tasks(
    current_user: User = Depends(get_current_user_dep),
):
    tasks = collab_board_list_tasks()
    return {"tasks": tasks, "count": len(tasks)}


@router.post("/collab-board/tasks")
async def create_collab_board_task(
    body: CollabTaskCreateRequest,
    current_user: User = Depends(get_current_user_dep),
):
    rec = collab_board_create_task(task_name=body.task_name, created_by=body.created_by)
    return {"ok": True, "task": rec}


@router.put("/collab-board/tasks/{task_id}")
async def update_collab_board_task(
    task_id: str,
    body: CollabTaskUpdateRequest,
    current_user: User = Depends(get_current_user_dep),
):
    rec = collab_board_update_task(
        task_id=task_id,
        task_name=body.task_name,
        progress=body.progress,
        status=body.status,
    )
    return {"ok": True, "task": rec}


@router.delete("/collab-board/tasks/{task_id}")
async def delete_collab_board_task(
    task_id: str,
    current_user: User = Depends(get_current_user_dep),
):
    result = collab_board_delete_task(task_id=task_id)
    if not result.get("deleted"):
        raise HTTPException(status_code=404, detail=result.get("reason", "Task not found"))
    return {"ok": True, **result}


@router.get("/collab-board/items")
async def get_collab_board_items(
    collab_id: str = Query(..., description="Required collaboration task id"),
    agent_id: str = Query("", description="Optional agent id filter"),
    scope: str = Query("public", description="public|all"),
    current_user: User = Depends(get_current_user_dep),
):
    items = collab_board_list_items(
        collab_id=collab_id,
        agent_id=agent_id or None,
        visibility="public" if scope != "all" else "all",
    )
    return {"items": items, "count": len(items)}


@router.post("/collab-board/items")
async def upsert_collab_board_item(
    body: CollabBoardUpsertRequest,
    current_user: User = Depends(get_current_user_dep),
):
    # Role-like policy:
    # - PM (task creator) can create/update all board items.
    # - Worker can only update own task items (agent_id must match caller identity).
    # - Worker updates are limited to progress-related fields for task items.
    identities = _user_identity_set(current_user)
    is_pm = _is_pm_for_collab(body.collab_id, current_user)

    if not is_pm:
        if body.item_type != "task":
            raise HTTPException(403, "Only PM can create/update non-task board items")

        if str(body.agent_id or "").strip() not in identities:
            raise HTTPException(403, "Workers can only update their own task items")

        # Worker restriction: cannot rewrite assignment identity fields of existing records
        existing_items = collab_board_list_items(
            collab_id=body.collab_id,
            agent_id=body.agent_id,
            visibility="all",
        )
        target = next(
            (
                i
                for i in existing_items
                if str(i.get("item_type", "")) == str(body.item_type)
                and str(i.get("item_key", "")) == str(body.item_key or "")
            ),
            None,
        )
        if isinstance(target, dict) and (body.title or "") and str(body.title) != str(target.get("title", "")):
            raise HTTPException(403, "Workers cannot change task title; update progress content only")

    item = collab_board_upsert_item(
        collab_id=body.collab_id,
        task_name=body.task_name or body.collab_id,
        agent_id=body.agent_id,
        item_type=body.item_type,
        item_key=body.item_key or "",
        title=body.title or "",
        content=body.content or "",
        status=body.status,
        progress=body.progress,
        visibility=body.visibility,
        latest_tool_name=body.latest_tool_name,
        latest_tool_summary=body.latest_tool_summary,
        extra=body.extra or {},
    )
    return {"ok": True, "item": item}


@router.post("/collab-board/discussions")
async def add_collab_board_discussion(
    body: CollabBoardDiscussionRequest,
    current_user: User = Depends(get_current_user_dep),
):
    item = collab_board_append_public_discussion(
        collab_id=body.collab_id,
        task_name=body.task_name or body.collab_id,
        author_agent_id=body.agent_id,
        title=body.title,
        content=body.content,
    )
    return {"ok": True, "item": item}


@router.delete("/collab-board/items/{item_id}")
async def delete_collab_board_item(
    item_id: str,
    current_user: User = Depends(get_current_user_dep),
):
    deleted = collab_board_delete_item(item_id=item_id)
    return {"ok": True, "deleted": deleted}


@router.post("/collab-board/plan-snapshots")
async def save_plan_snapshot(
    body: CollabBoardUpsertRequest,
    current_user: User = Depends(get_current_user_dep),
):
    """Save current plan as a snapshot before overwriting."""
    snapshot = collab_board_save_plan_snapshot(
        collab_id=body.collab_id,
        content=body.content or "",
        title=body.title or "",
        author_agent_id=body.agent_id,
    )
    return {"ok": True, "snapshot": snapshot}


@router.get("/collab-board/plan-snapshots/{collab_id}")
async def list_plan_snapshots(
    collab_id: str,
    current_user: User = Depends(get_current_user_dep),
):
    snapshots = collab_board_list_plan_snapshots(collab_id=collab_id)
    return {"ok": True, "snapshots": snapshots, "count": len(snapshots)}


# ---- Catch-all {session_id} routes AFTER specific routes ----


@router.get("/agent-sessions/{agent_id}/{session_id}/paged")
async def agent_session_history_paged(
    agent_id: str,
    session_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user_dep),
):
    """
    Get paginated session data (messages + events), newest first.
    offset=0 returns the most recent `limit` messages.
    """
    reader = await async_get_agent_session_reader(agent_id)
    if not reader:
        raise HTTPException(404, f"Agent not found: {agent_id}")

    data = await reader.async_get_session_history_paged(session_id, offset=offset, limit=limit)
    if not data:
        raise HTTPException(404, f"Session not found: {session_id}")

    return {
        "agent_id": agent_id,
        "session": _normalize_session_payload(data),
    }


@router.get("/agent-sessions/{agent_id}/{session_id}")
async def agent_session_history(
    agent_id: str,
    session_id: str,
    current_user: User = Depends(get_current_user_dep),
):
    """
    Get full session data (messages + events) for a specific session.
    """
    reader = await async_get_agent_session_reader(agent_id)
    if not reader:
        raise HTTPException(404, f"Agent not found: {agent_id}")

    data = await reader.async_get_session_history(session_id)
    if not data:
        raise HTTPException(404, f"Session not found: {session_id}")

    return {
        "agent_id": agent_id,
        "session": _normalize_session_payload(data),
    }


@router.post("/agent-sessions/{agent_id}/{session_id}/delete")
async def agent_session_delete(
    agent_id: str,
    session_id: str,
    current_user: User = Depends(get_current_user_dep),
):
    """
    Delete a history session file.
    Cannot delete the agent's current active session.
    """
    reader = await async_get_agent_session_reader(agent_id)
    if not reader:
        raise HTTPException(404, f"Agent not found: {agent_id}")

    success = await reader.async_delete_session(session_id)
    if not success:
        raise HTTPException(
            400,
            "Cannot delete session (it may be the current active session or does not exist)",
        )

    return {"message": "Session deleted", "session_id": session_id}


# ============================================================
# Agent Push API - Agents push files/messages to chat & groups
# ============================================================


class AgentPushRequest(BaseModel):
    """Request body for agent push to AI chat"""

    agent_id: str
    user_id: str | None = None  # if None, broadcast to all connected users
    message: str | None = None
    files: list[dict] | None = None  # [{path, original_name, url, size, content_type, is_image}]


def _check_node_secret(request: Request) -> bool:
    """Validate node_secret from header or query param."""
    secret = request.headers.get("X-Node-Secret", "") or request.query_params.get("node_secret", "")
    expected = syscfg.node_secret()
    if not expected:
        return True  # No key configured, allow through (local dev)
    return secret == expected


def _require_agent_auth(request: Request):
    """Auth dependency for agent-invoked endpoints. Uses node_secret."""
    from fastapi import HTTPException, status

    if not _check_node_secret(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: valid X-Node-Secret header required",
        )


@router.post("/agent-push/chat")
async def agent_push_to_chat(
    body: AgentPushRequest,
    request: Request,
):
    """
    Agent pushes files/messages to AI chat dialog.
    Auth via X-Node-Secret header (matches system node_secret).
    The message is forwarded to the connected user via WebSocket.
    """
    _require_agent_auth(request)
    from .websocket import user_handler

    agent_id = body.agent_id
    agent = registry.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, f"Agent not found: {agent_id}")

    # Build file_push WS message
    file_push_message_id = f"fp_{uuid.uuid4().hex[:12]}"
    ws_message = {
        "type": "file_push",
        "agent_id": agent_id,
        "message_id": file_push_message_id,
        "message": body.message or "",
        "files": body.files or [],
    }

    # Forward to connected users
    sent_count = 0
    delivered_user_ids: set[str] = set()
    if body.user_id:
        # Send to specific user
        success = await user_handler.forward_to_user(body.user_id, agent_id, ws_message)
        if success:
            sent_count = 1
            delivered_user_ids.add(body.user_id)
    else:
        # Broadcast to all users (and all their devices) connected to this agent
        delivered = await user_handler.broadcast_to_agent(agent_id, ws_message)
        sent_count = len(delivered) if delivered else 0
        for uid in delivered or []:
            if uid:
                delivered_user_ids.add(uid)

    # Persist pushed content to session history (so refresh can restore it)
    if sent_count > 0 and (body.message or body.files):
        persisted_text = body.message or ""
        for f in body.files or []:
            name = f.get("original_name") or f.get("filename") or "file"
            size = f.get("size", 0)
            ctype = f.get("content_type", "application/octet-stream")
            media = (
                "image"
                if str(ctype).startswith("image/")
                else (
                    "video"
                    if str(ctype).startswith("video/")
                    else ("audio" if str(ctype).startswith("audio/") else "file")
                )
            )
            url = f.get("url") or ""
            line = (
                f"[File: {name} ({size} B) type={media}]({url})" if url else f"[File: {name} ({size} B) type={media}]"
            )
            persisted_text = f"{persisted_text}\n\n{line}" if persisted_text else line

        for uid in delivered_user_ids:
            try:
                all_files = body.files or []
                image_urls = [
                    f.get("url")
                    for f in all_files
                    if isinstance(f, dict)
                    and (f.get("is_image") or str(f.get("content_type", "")).startswith("image/"))
                    and f.get("url")
                ]
                await gateway_session_cache.async_add_message(
                    uid,
                    agent_id,
                    "assistant",
                    persisted_text or "(files)",
                    msg_type="file_push",
                    message_id=file_push_message_id,
                    images=image_urls,
                    files=all_files,
                    extra={
                        "message_id": file_push_message_id,
                        "files": all_files,
                        "message": body.message or "",
                    },
                )
            except Exception as e:
                logger.warning("Failed to persist agent push chat message (uid=%s, agent=%s): %s", uid, agent_id, e)

    return {
        "ok": True,
        "sent_to": sent_count,
        "message": f"Pushed to {sent_count} connected user(s)",
    }


@router.post("/agent-push/upload-and-chat")
async def agent_push_upload_and_chat(
    agent_id: str = Query(...),
    message: str | None = Query(None),
    user_id: str | None = Query(None),
    files: list[UploadFile] = File(...),
    request: Request = None,
):
    """
    Agent uploads files and pushes them to AI chat in one step.
    Auth via X-Node-Secret header (matches system node_secret).
    Files are saved to uploads/ and then pushed via WebSocket.
    """
    if request:
        _require_agent_auth(request)
    from .websocket import user_handler

    agent = registry.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, f"Agent not found: {agent_id}")

    # Save uploaded files
    file_infos = []
    for file in files:
        original_name = file.filename or "unknown"
        ext = os.path.splitext(original_name)[1] if original_name else ""
        filename = f"{uuid.uuid4().hex[:8]}{ext}"
        filepath = os.path.join(_UPLOAD_DIR, filename)

        content = await file.read()
        with open(filepath, "wb") as fw:
            fw.write(content)

        file_size = len(content)
        content_type = file.content_type or "application/octet-stream"
        is_image = content_type.startswith("image/")
        is_audio = content_type.startswith("audio/")
        is_video = content_type.startswith("video/")

        file_infos.append(
            {
                "path": filepath,
                "filename": filename,
                "original_name": original_name,
                "url": f"/uploads/{filename}",
                "size": file_size,
                "content_type": content_type,
                "is_image": is_image,
                "is_audio": is_audio,
                "is_video": is_video,
            }
        )

    # Build WS message
    file_push_message_id = f"fp_{uuid.uuid4().hex[:12]}"
    ws_message = {
        "type": "file_push",
        "agent_id": agent_id,
        "message_id": file_push_message_id,
        "message": message or "",
        "files": file_infos,
    }

    # Forward to connected users
    sent_count = 0
    delivered_user_ids: set[str] = set()
    targeted_conn_keys = []
    all_conn_keys = list(user_handler.user_connections.keys())
    if user_id:
        success = await user_handler.forward_to_user(user_id, agent_id, ws_message)
        if success:
            sent_count = 1
            delivered_user_ids.add(user_id)
        targeted_conn_keys = [f"{user_id}:{agent_id}"]
    else:
        delivered = await user_handler.broadcast_to_agent(agent_id, ws_message)
        sent_count = len(delivered) if delivered else 0
        for uid in delivered or []:
            if uid:
                delivered_user_ids.add(uid)
        targeted_conn_keys = [f"{u}:{agent_id}" for u in delivered] or [f"*:{agent_id}"]

    logger.info(
        "Agent %s pushed %s file(s) to %s user(s) | targeted=%s | active_connections=%s",
        agent_id,
        len(file_infos),
        sent_count,
        targeted_conn_keys,
        all_conn_keys,
    )
    if sent_count == 0:
        logger.warning(
            "Agent push had zero recipients (agent_id=%s, user_id=%s). "
            "Likely no active AI Web chat connection for this agent.",
            agent_id,
            user_id,
        )

    # Persist pushed file message to session history (so refresh can restore it)
    if sent_count > 0:
        persisted_text = message or ""
        for f in file_infos:
            media = (
                "image"
                if f.get("is_image")
                else ("video" if f.get("is_video") else ("audio" if f.get("is_audio") else "file"))
            )
            line = f"[File: {f.get('original_name') or f.get('filename')} ({f.get('size', 0)} B) type={media}]({f.get('url')})"
            persisted_text = f"{persisted_text}\n\n{line}" if persisted_text else line

        for uid in delivered_user_ids:
            try:
                image_urls = [
                    f.get("url") for f in file_infos if isinstance(f, dict) and f.get("is_image") and f.get("url")
                ]
                await gateway_session_cache.async_add_message(
                    uid,
                    agent_id,
                    "assistant",
                    persisted_text or "(files)",
                    msg_type="file_push",
                    message_id=file_push_message_id,
                    images=image_urls,
                    files=file_infos,
                    extra={
                        "message_id": file_push_message_id,
                        "files": file_infos,
                        "message": message or "",
                    },
                )
            except Exception as e:
                logger.warning("Failed to persist uploaded file push message (uid=%s, agent=%s): %s", uid, agent_id, e)

    return {
        "ok": True,
        "sent_to": sent_count,
        "files": file_infos,
    }


@router.post("/agent-push/group")
async def agent_push_to_group(
    body: dict = Body(...),
    request: Request = None,
):
    """
    Agent pushes a message (with optional file attachments) to a group chat.
    Auth via X-Node-Secret header (matches system node_secret).

    Body:
      - agent_id: str (required)
      - group_id: str (required)
      - content: str (message text)
      - attachments: list[{url, type, name, size}] (optional)
    """
    if request:
        _require_agent_auth(request)
    agent_id = body.get("agent_id")
    group_id = body.get("group_id")
    content = body.get("content", "")
    attach_list = body.get("attachments", [])

    if not agent_id or not group_id:
        raise HTTPException(400, "agent_id and group_id are required")

    agent = registry.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, f"Agent not found: {agent_id}")

    from sqlalchemy import select

    from app.database import AsyncSessionLocal
    from app.models import Attachment as DBAttachment
    from app.models import Message as DBMessage
    from app.models import MessageType as DBMessageType
    from app.models import User as DBUser
    from app.models import beijing_now, group_members

    async with AsyncSessionLocal() as db:
        # Find agent's user account
        # Prefer User.id == agent_id (unified identity format for newly created agents)
        stmt = select(DBUser).where(DBUser.id == agent_id)
        result = await db.execute(stmt)
        agent_user = result.scalar_one_or_none()

        # Fallback: old agent chat accounts may be linked by name
        if not agent_user:
            stmt = select(DBUser).where(DBUser.name == agent_id)
            result = await db.execute(stmt)
            agent_user = result.scalar_one_or_none()

        if not agent_user and agent:
            stmt = select(DBUser).where(DBUser.name == agent.agent_name)
            result = await db.execute(stmt)
            agent_user = result.scalar_one_or_none()

        if not agent_user:
            raise HTTPException(404, f"No chat user account found for agent: {agent_id}")

        # Check membership
        stmt = select(group_members).where(
            group_members.c.user_id == agent_user.id, group_members.c.group_id == group_id
        )
        result = await db.execute(stmt)
        membership = result.first()
        if not membership:
            raise HTTPException(403, f"Agent {agent_id} is not a member of group {group_id}")

        # Create the message
        now = beijing_now()
        msg_id = f"m_{now.timestamp()}" if hasattr(now, "timestamp") else f"m_{datetime.now(timezone.utc).timestamp()}"
        db_msg = DBMessage(
            id=msg_id,
            group_id=group_id,
            sender_id=agent_user.id,
            content=content,
            type=DBMessageType.TEXT,
            timestamp=now,
        )
        db.add(db_msg)
        await db.flush()

        # Create attachments (if any)
        attachments_for_response = []
        for i, att in enumerate(attach_list):
            att_id = f"a_{datetime.now(timezone.utc).timestamp()}_{i}"
            db.add(
                DBAttachment(
                    id=att_id,
                    message_id=msg_id,
                    name=att.get("name", "file"),
                    size=str(att.get("size", "0")),
                    url=att.get("url", ""),
                    type=att.get("type", "file"),
                    duration=att.get("duration"),
                )
            )
            attachments_for_response.append(
                {
                    "id": att_id,
                    "name": att.get("name", "file"),
                    "size": att.get("size", "0"),
                    "url": att.get("url", ""),
                    "type": att.get("type", "file"),
                    "duration": att.get("duration"),
                }
            )

        # Build response BEFORE commit (async SQLAlchemy golden rule)
        response_data = {
            "ok": True,
            "message_id": msg_id,
            "group_id": group_id,
            "sender_id": agent_user.id,
            "sender_name": agent_user.name,
            "content": content,
            "attachments": attachments_for_response,
            "timestamp": now.isoformat() if hasattr(now, "isoformat") else str(now),
        }

        await db.commit()

    # Notify group chat WebSocket subscribers
    try:
        from app.websocket import manager as ws_manager

        await ws_manager.broadcast_to_group(
            group_id,
            {
                "type": "new_message",
                "message": response_data,
            },
        )
    except Exception as e:
        logger.warning(f"Failed to broadcast group message: {e}")

    return response_data


# ============================================================
# Market Routes (Plugins / Skills / Roles / Collabs)
# ============================================================

# GitHub registry URLs
PLUGIN_REGISTRY_URL = "https://raw.githubusercontent.com/opensquad-ai/opensquad-plugins/main/index.json"
SKILL_REGISTRY_URL = "https://raw.githubusercontent.com/opensquad-ai/opensquad-skills/main/index.json"
ROLE_REGISTRY_URL = "https://raw.githubusercontent.com/opensquad-ai/opensquad-roles/main/index.json"
COLLAB_REGISTRY_URL = "https://raw.githubusercontent.com/opensquad-ai/opensquad-collabs/main/index.json"

# Local install/storage directories — 使用 builtin root（项目安装目录），与 launcher 读取目录保持一致
_BUILTIN_ROOT = syscfg.get_builtin_root()
PLUGINS_DIR = os.path.join(_BUILTIN_ROOT, "plugins")
_SKILLS_DIR = os.path.join(_BUILTIN_ROOT, "skills")
_ROLE_CARDS_DIR = os.path.join(_BUILTIN_ROOT, "role_cards")
_COLLAB_CARDS_DIR = os.path.join(_BUILTIN_ROOT, "collab_cards")
_MODEL_CARDS_DIR = os.path.join(_BUILTIN_ROOT, "model_cards")

# Local liked items record: tracks item_id sets already liked on this node per category, to prevent duplicate likes
# Format: {"plugins": ["id1", ...], "skills": [...], "roles": [...], "collabs": [...]}
_LIKED_ITEMS_PATH = os.path.join(_REPO_ROOT, "data", "liked_items.json")


def _read_local_liked() -> dict:
    """Read local liked_items.json; return empty dict on failure."""
    try:
        with open(_LIKED_ITEMS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_local_liked(data: dict) -> None:
    """Write to local liked_items.json (silently ignore failures)."""
    try:
        os.makedirs(os.path.dirname(_LIKED_ITEMS_PATH), exist_ok=True)
        with open(_LIKED_ITEMS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[likes] Failed to write liked_items.json: {e}")


def _local_icon_path(kind: str, item_id: str) -> str:
    """Return the absolute path to the local icon file (file may not exist)"""
    if kind == "skills":
        return os.path.join(_SKILLS_DIR, item_id, "icon.svg")
    elif kind == "roles":
        return os.path.join(_ROLE_CARDS_DIR, f"{item_id}_icon.svg")
    elif kind == "collabs":
        return os.path.join(_COLLAB_CARDS_DIR, f"{item_id}_icon.svg")
    return ""


@router.get("/market/icon/{kind}/{item_id}")
async def market_serve_icon(
    kind: str,
    item_id: str,
    current_user: User = Depends(get_current_user_dep),
):
    """Return the local installed icon file (SVG)"""
    allowed_kinds = {"skills", "roles", "collabs"}
    if kind not in allowed_kinds:
        raise HTTPException(status_code=400, detail="Invalid kind")
    path = _local_icon_path(kind, item_id)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Icon not found locally")
    return FileResponse(path, media_type="image/svg+xml")


# GitHub Contents API — likes.json for each repo (internal key is always "plugins")
_LIKES_REPOS = {
    "plugins": "opensquad-ai/opensquad-plugins",
    "skills": "opensquad-ai/opensquad-skills",
    "roles": "opensquad-ai/opensquad-roles",
    "collabs": "opensquad-ai/opensquad-collabs",
}


def _likes_api_url(kind: str) -> str:
    """Return the GitHub Contents API URL for likes.json in the given repo kind."""
    repo = _LIKES_REPOS.get(kind, _LIKES_REPOS["plugins"])
    return f"https://api.github.com/repos/{repo}/contents/likes.json"


async def _get_likes_data(kind: str = "plugins") -> tuple:
    """Read likes.json from GitHub.  Returns (data_dict, file_sha | None).

    data_dict schema:
        {"updated_at": "...", "plugins": {"item_id": {"count": N, "voters": [...]}}}
    Returns ({}, None) if the file doesn't exist yet or token is missing.
    """
    token = syscfg.github_plugins_token()
    if not token:
        return {}, None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = _likes_api_url(kind)
    try:
        async with httpx.AsyncClient(timeout=10, verify=_SSL_VERIFY) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 404:
                return {}, None
            resp.raise_for_status()
            payload = resp.json()
            sha = payload.get("sha")
            content = base64.b64decode(payload["content"]).decode("utf-8")
            data = json.loads(content)
            return data, sha
    except Exception as e:
        logger.warning(f"[likes] Failed to read likes.json ({kind}): {e}")
        return {}, None


async def _update_likes_data(data: dict, sha, kind: str = "plugins") -> bool:
    """Write back to GitHub using optimistic locking.

    Returns True on success, False on 409 conflict (caller should retry).
    Automatically strips legacy voters field before writing (migration compatibility).
    """
    token = syscfg.github_plugins_token()
    if not token:
        return False
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    # Migration compatibility: strip legacy voters field from old data
    for entry in data.get("plugins", {}).values():
        if isinstance(entry, dict):
            entry.pop("voters", None)
    data["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    content_b64 = base64.b64encode(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")).decode("ascii")
    body: dict = {
        "message": "chore: update likes",
        "content": content_b64,
    }
    if sha:
        body["sha"] = sha
    url = _likes_api_url(kind)
    try:
        async with httpx.AsyncClient(timeout=10, verify=_SSL_VERIFY) as client:
            resp = await client.put(url, headers=headers, json=body)
            if resp.status_code == 409:
                return False
            resp.raise_for_status()
            return True
    except Exception as e:
        logger.warning(f"[likes] Failed to write likes.json ({kind}): {e}")
        return False


async def _market_like_item(item_id: str, registry_url: str, kind: str) -> dict:
    """Shared like logic for plugins/skills/roles/collabs.

    Deduplication strategy: local data/liked_items.json tracks item_ids already liked
    on this node. GitHub likes.json only stores {"count": N}, no voters array.

    - Already liked: return current count + already_liked=True without any write.
    - No token: optimistic +1, write local record, do not persist to GitHub.
    - Has token: read-modify-write GitHub, supports 3 optimistic lock retries, write local on success.
    """
    # --- Local dedup check ---
    local_liked = _read_local_liked()
    liked_set: set = set(local_liked.get(kind, []))
    if item_id in liked_set:
        # Already liked — get real count from GitHub (return 0 on failure)
        likes_data, _ = await _get_likes_data(kind)
        count = likes_data.get("plugins", {}).get(item_id, {}).get("count", 0)
        return {"likes": count, "already_liked": True}

    token = syscfg.github_plugins_token()

    if not token:
        # No token: optimistic +1, record locally only
        liked_set.add(item_id)
        local_liked[kind] = list(liked_set)
        _write_local_liked(local_liked)
        try:
            async with httpx.AsyncClient(timeout=15, verify=_SSL_VERIFY) as client:
                resp = await client.get(registry_url)
                resp.raise_for_status()
                items = resp.json()
                meta = next((p for p in items if p.get("id") == item_id), None)
                if not meta:
                    raise HTTPException(status_code=404, detail="Item not found")
                return {"likes": meta.get("likes", 0) + 1, "already_liked": False}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Registry unavailable: {e}")

    # Has token: read-modify-write GitHub (count only, no voters)
    new_count = 0
    for attempt in range(3):
        likes_data, sha = await _get_likes_data(kind)
        items_map: dict = likes_data.get("plugins", {})
        new_count = items_map.get(item_id, {}).get("count", 0) + 1
        items_map[item_id] = {"count": new_count}
        likes_data["plugins"] = items_map

        ok = await _update_likes_data(likes_data, sha, kind)
        if ok:
            liked_set.add(item_id)
            local_liked[kind] = list(liked_set)
            _write_local_liked(local_liked)
            return {"likes": new_count, "already_liked": False}
        logger.info(f"[likes] Conflict on attempt {attempt + 1} ({kind}/{item_id}), retrying…")

    # All retries failed: still record locally (prevent spam), return optimistic result
    liked_set.add(item_id)
    local_liked[kind] = list(liked_set)
    _write_local_liked(local_liked)
    logger.warning(f"[likes] All retries failed for {kind}/{item_id}, returning optimistic result")
    return {"likes": new_count, "already_liked": False}


@router.get("/market/plugins")
async def market_list_plugins(
    page: int = Query(1, ge=1),
    size: int = Query(9, ge=1, le=200),
    search: str = Query(""),
    category: str = Query("", alias="type"),
    plugin_category: str = Query("", alias="category"),
    sort: str = Query("likes"),
    order: str = Query("desc"),
    current_user: User = Depends(get_current_user_dep),
):
    """Fetch plugin index.json from GitHub in real-time, paginate locally; likes.json is the source of truth for like counts"""
    try:
        async with httpx.AsyncClient(timeout=15, verify=_SSL_VERIFY) as client:
            resp = await client.get(PLUGIN_REGISTRY_URL)
            resp.raise_for_status()
            all_plugins = resp.json()

        # Fetch likes.json, overwrite static values in index.json with real like counts (failure does not affect main flow)
        likes_data, _ = await _get_likes_data()
        likes_map: dict = likes_data.get("plugins", {})
        if likes_map:
            for p in all_plugins:
                pid = p.get("id", "")
                if pid in likes_map:
                    p["likes"] = likes_map[pid].get("count", p.get("likes", 0))

        # 1. Search filter
        if search:
            q = search.lower()
            all_plugins = [
                p
                for p in all_plugins
                if q in p.get("name", "").lower()
                or q in p.get("description", "").lower()
                or q in p.get("id", "").lower()
            ]

        # 2. Type filter
        if category and category != "all":
            all_plugins = [p for p in all_plugins if p.get("type") == category]

        # 2.5. Category filter
        if plugin_category and plugin_category != "all":
            all_plugins = [p for p in all_plugins if p.get("category") == plugin_category]

        # 3. Sort
        reverse = order == "desc"
        if sort == "likes":
            all_plugins.sort(key=lambda p: p.get("likes", 0), reverse=reverse)
        elif sort == "name":
            all_plugins.sort(key=lambda p: p.get("name", "").lower(), reverse=reverse)

        # 4. Local slice pagination
        total = len(all_plugins)
        start = (page - 1) * size
        end = start + size
        paged_plugins = all_plugins[start:end]

        # 5. Data cleanup: ensure tags exist
        for p in paged_plugins:
            if "tags" not in p or p["tags"] is None:
                p["tags"] = []

        return {
            "total": total,
            "page": page,
            "size": size,
            "pages": (total + size - 1) // size,
            "plugins": paged_plugins,
        }
    except Exception as e:
        logger.error(f"Failed to fetch registry from GitHub: {e}")
        raise HTTPException(status_code=503, detail=f"Plugin registry unavailable: {e}")


@router.get("/market/installed")
async def market_list_installed(
    current_user: User = Depends(get_current_user_dep),
):
    """Get list of locally installed plugins (for update checking)."""
    try:
        # Query local Launcher
        # Launcher returns: {"plugins": [{"name": "foo", "version": "1.0", "enabled": true}, ...]}
        resp = await _proxy_get("/api/plugins")
        plugins_list = resp.get("plugins", []) if isinstance(resp, dict) else resp

        # Convert list -> dict map for frontend.
        # Launcher returns "name" (not "id"); accept either field.
        result = {}
        for p in plugins_list:
            if not isinstance(p, dict):
                continue
            plugin_id = p.get("id") or p.get("name")
            if plugin_id:
                result[plugin_id] = {"version": p.get("version", "0.0.0"), "enabled": p.get("enabled", True)}
        return {"installed": result}
    except Exception as e:
        logger.error(f"Failed to get installed plugins: {e}")
        # Return empty map on error to prevent frontend crash
        return {"installed": {}}


@router.get("/market/plugins/{plugin_id}")
async def market_get_plugin(
    plugin_id: str,
    current_user: User = Depends(get_current_user_dep),
):
    """Fetch single plugin detail from full GitHub data"""
    try:
        async with httpx.AsyncClient(timeout=15, verify=_SSL_VERIFY) as client:
            resp = await client.get(PLUGIN_REGISTRY_URL)
            resp.raise_for_status()
            all_plugins = resp.json()

            for p in all_plugins:
                if p.get("id") == plugin_id:
                    return p
            raise HTTPException(status_code=404, detail="Plugin not found in GitHub registry")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching plugin detail {plugin_id}: {e}")
        raise HTTPException(status_code=503, detail=f"Registry error: {e}")


@router.post("/market/plugins/{plugin_id}/like")
async def market_like_plugin(
    plugin_id: str,
    current_user: User = Depends(get_current_user_dep),
):
    """Like a plugin. Delegates to the shared _market_like_item() helper."""
    return await _market_like_item(plugin_id, PLUGIN_REGISTRY_URL, "plugins")


from app.ai_web.builder import builder


@router.get("/market/build/env")
async def market_check_build_env():
    """Check if Node/NPM are ready for local builds."""
    return await builder.check_env()


@router.post("/market/plugins/{plugin_id}/build")
async def market_trigger_plugin_build(plugin_id: str):
    """Trigger a local build for a plugin's UI."""
    return builder.start_build(plugin_id)


@router.get("/market/plugins/{plugin_id}/build/log")
async def market_get_plugin_build_log(plugin_id: str):
    """Return the current build log for a plugin."""
    log_path = builder.get_log_path(plugin_id)
    status = builder.active_builds.get(plugin_id, "idle")

    content = ""
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8") as f:
            content = f.read()

    return {"status": status, "log": content}


@router.post("/market/plugins/{plugin_id}/install")
async def market_install_plugin(
    plugin_id: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user_dep),
    mode: str = Query("smart"),
):
    """
    Install or update a plugin:
    1. Fetch plugin metadata from registry.
    2. Check if git_url exists -> Delegate to install-from-git.
    3. Else use download_url -> Download zip, preserve 'enabled' state, extract, trigger hot-reload.
    """
    # 1. Fetch plugin metadata from registry (search in full index.json)
    try:
        async with httpx.AsyncClient(timeout=15, verify=_SSL_VERIFY) as client:
            idx_resp = await client.get(PLUGIN_REGISTRY_URL)
            idx_resp.raise_for_status()
            all_plugins = idx_resp.json()
            plugin_meta = next((p for p in all_plugins if p.get("id") == plugin_id), None)
            if not plugin_meta:
                raise HTTPException(status_code=404, detail="Plugin not found in registry")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Registry fetch failed: {e}")
        raise HTTPException(status_code=503, detail=f"Plugin registry unavailable: {e}")

    # Smart Install Logic: Prefer Git if available and no direct download, or if explicitly requested (TODO)
    # Current policy: If no download_url but git_url exists, use Git.
    download_url = plugin_meta.get("download_url")
    git_url = plugin_meta.get("git_url")

    if not download_url and git_url:
        logger.info(f"Delegating install of '{plugin_id}' to Git handler (repo: {git_url})")
        git_req = GitInstallRequest(git_url=git_url, plugin_id=plugin_id, mode=mode)
        return await market_install_plugin_from_git(git_req, background_tasks, current_user)

    if not download_url:
        raise HTTPException(status_code=400, detail="Plugin has no download_url and no git_url")

    registry_version = plugin_meta.get("version", "0.0.0")
    is_featured = plugin_meta.get("is_featured", False)

    # 2. Check existing installation
    plugin_dest = os.path.join(PLUGINS_DIR, plugin_id)
    existing_enabled = True
    existing_version = None
    existing_manifest = os.path.join(plugin_dest, "plugin.json")
    existing_plugin_py_path = os.path.join(plugin_dest, "plugin.py")
    # Preserve the real plugin.py content if it already exists on disk.
    existing_plugin_py: bytes | None = None
    existing_category = None
    if os.path.isfile(existing_manifest):
        try:
            with open(existing_manifest, encoding="utf-8") as f:
                existing_data = json.load(f)
            existing_enabled = existing_data.get("enabled", True)
            existing_version = existing_data.get("version")
            existing_category = existing_data.get("category")
        except Exception:
            pass
    if os.path.isfile(existing_plugin_py_path):
        try:
            with open(existing_plugin_py_path, "rb") as f:
                existing_plugin_py = f.read()
        except Exception:
            pass

    # 3. Download zip from Mock Git Server
    try:
        async with httpx.AsyncClient(timeout=30, verify=_SSL_VERIFY) as client:
            zip_resp = await client.get(download_url)
            if zip_resp.status_code == 404:
                raise HTTPException(status_code=404, detail="Plugin archive not found")
            zip_resp.raise_for_status()
            zip_bytes = zip_resp.content
    except HTTPException:
        raise
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Failed to download plugin archive: {e}")

    # 4. Extract zip to plugins/{plugin_id}/ (overwrite all files)
    zip_size_kb = len(zip_bytes) / 1024
    os.makedirs(PLUGINS_DIR, exist_ok=True)
    file_count = 0
    try:
        buf = io.BytesIO(zip_bytes)
        with zipfile.ZipFile(buf) as zf:
            for member in zf.infolist():
                parts = member.filename.split("/")
                relative = "/".join(parts[1:]) if len(parts) > 1 else parts[0]
                if not relative:
                    continue
                dest_path = os.path.join(plugin_dest, relative)
                if not os.path.abspath(dest_path).startswith(os.path.abspath(plugin_dest)):
                    continue
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                if not member.is_dir():
                    with zf.open(member) as src, open(dest_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    file_count += 1
    except zipfile.BadZipFile as e:
        raise HTTPException(status_code=422, detail=f"Invalid zip archive: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to extract plugin: {e}")

    # 5a. Restore the real plugin.py if we had one before.
    if existing_plugin_py is not None:
        try:
            with open(existing_plugin_py_path, "wb") as f:
                f.write(existing_plugin_py)
        except Exception as e:
            logger.warning(f"Failed to restore plugin.py for '{plugin_id}': {e}")

    # 5b. Restore preserved 'enabled' state and 'category' into the extracted plugin.json
    if os.path.isfile(existing_manifest):
        try:
            with open(existing_manifest, encoding="utf-8") as f:
                new_manifest_data = json.load(f)
            new_manifest_data["enabled"] = existing_enabled
            # Restore category only if the new zip doesn't carry one
            if existing_category and not new_manifest_data.get("category"):
                new_manifest_data["category"] = existing_category
            with open(existing_manifest, "w", encoding="utf-8") as f:
                json.dump(new_manifest_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Failed to restore enabled state for '{plugin_id}': {e}")

    # 6. Trigger hot-reload
    try:
        reload_ts_path = os.path.join(PLUGINS_DIR, ".reload_ts")
        with open(reload_ts_path, "w") as f:
            f.write(str(time.time()))
    except Exception as e:
        logger.warning(f"Failed to write .reload_ts: {e}")

    # 7. Verify installation: check plugin.py exists and launcher can see it
    plugin_py_path = os.path.join(plugin_dest, "plugin.py")
    if not os.path.isfile(plugin_py_path):
        logger.warning(f"Plugin '{plugin_id}' installed but plugin.py NOT found at {plugin_py_path}")
        # List top-level files for debugging
        try:
            top_files = os.listdir(plugin_dest)
            logger.warning(f"Files in {plugin_dest}: {top_files}")
        except Exception:
            pass

    action = "updated" if existing_version and existing_version != registry_version else "installed"
    logger.info(
        f"Plugin '{plugin_id}' {action} (v{existing_version} -> v{registry_version}), {file_count} files, {zip_size_kb:.1f} KB"
    )

    return {
        "ok": True,
        "action": action,
        "is_featured": is_featured,
        "message": f"Plugin '{plugin_id}' {action} v{registry_version} — {file_count} files ({zip_size_kb:.1f} KB)",
        "plugin": plugin_meta,
        "previous_version": existing_version,
        "files": file_count,
        "size_kb": round(zip_size_kb, 1),
    }


@router.post("/market/plugins/upload")
async def market_upload_plugin(
    body: dict = Body(...),
    current_user: User = Depends(get_current_user_dep),
):
    """Plugin submission stub (registry is a static GitHub file; use PR workflow)."""
    raise HTTPException(
        status_code=501, detail="Direct upload not supported. Submit a Pull Request to the GitHub registry instead."
    )


@router.delete("/market/plugins/{plugin_id}/uninstall")
async def market_uninstall_plugin(
    plugin_id: str,
    current_user: User = Depends(get_current_user_dep),
):
    """
    Uninstall a plugin by removing its directory from plugins/.
    Triggers hot-reload after removal.
    """
    # Sanitize plugin_id: must be a simple directory name (no path traversal)
    if not re.match(r"^[a-zA-Z0-9_\-]+$", plugin_id):
        raise HTTPException(status_code=400, detail="Invalid plugin id")

    plugin_dest = os.path.join(PLUGINS_DIR, plugin_id)
    # Ensure the resolved path is strictly inside PLUGINS_DIR
    if not os.path.abspath(plugin_dest).startswith(os.path.abspath(PLUGINS_DIR) + os.sep):
        raise HTTPException(status_code=400, detail="Invalid plugin id")

    if not os.path.isdir(plugin_dest):
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' is not installed")

    try:

        def _remove_readonly(func, path, exc_info):
            """Windows: files inside .git directory are often read-only; chmod then retry"""
            import stat

            os.chmod(path, stat.S_IWRITE)
            func(path)

        shutil.rmtree(plugin_dest, onerror=_remove_readonly)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to uninstall plugin: {e}")

    # Trigger hot-reload
    try:
        reload_ts_path = os.path.join(PLUGINS_DIR, ".reload_ts")
        with open(reload_ts_path, "w") as f:
            f.write(str(time.time()))
    except Exception as e:
        logger.warning(f"Failed to write .reload_ts after uninstall of '{plugin_id}': {e}")

    logger.info(f"Plugin '{plugin_id}' uninstalled successfully")
    return {"ok": True, "plugin_id": plugin_id, "message": f"Plugin '{plugin_id}' uninstalled successfully"}


class GitInstallRequest(BaseModel):
    """Git plugin install request"""

    git_url: str
    plugin_id: str | None = None
    mode: str = "smart"  # "smart" | "build"


# ---- Background Git install job state storage ----
# key: job_id, value: {"status": "pending|running|done|failed", "plugin_id", "started_at", ...}
_git_install_jobs: dict = {}


def _resolve_plugin_id(git_url: str, plugin_id: str | None) -> str:
    """Infer plugin_id from git_url, or use the provided value directly"""
    if plugin_id:
        return plugin_id
    # e.g., https://github.com/user/opensquad-plugin-websearch -> websearch
    return git_url.split("/")[-1].replace(".git", "").replace("opensquad-plugin-", "")


async def _run_git_install_job(job_id: str, p_id: str, git_url: str, mode: str = "smart") -> None:
    """Execute git clone/pull in a thread pool, build if needed based on mode, update job status.
    Uses asyncio.to_thread to avoid blocking the event loop.
    mode="smart": after clone, check for ui/dist/index.js; if present complete immediately, otherwise auto-build
    mode="build":  after clone, always run pnpm install + pnpm run build
    """
    import subprocess as _subprocess

    target_dir = os.path.join(PLUGINS_DIR, p_id)
    _git_install_jobs[job_id]["status"] = "cloning"

    def _do_git() -> tuple:
        if os.path.exists(target_dir):
            cmd = ["git", "pull"]
            cwd = target_dir
        else:
            cmd = ["git", "clone", git_url, target_dir]
            cwd = None
        result = _subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        return result.returncode, result.stdout, result.stderr

    try:
        returncode, stdout, stderr = await asyncio.to_thread(_do_git)
        if returncode != 0:
            err_msg = stderr.strip() or "Unknown git error"
            logger.error(f"Git job {job_id} failed for {p_id}: {err_msg}")
            _git_install_jobs[job_id].update({"status": "failed", "error": err_msg})
            return

        # Write .reload_ts to trigger hot reload
        try:
            reload_ts_path = os.path.join(PLUGINS_DIR, ".reload_ts")
            with open(reload_ts_path, "w") as f:
                f.write(str(time.time()))
        except Exception as e:
            logger.warning(f"Failed to write .reload_ts: {e}")

        # Phase 2: Check if UI directory exists
        ui_dir = os.path.join(target_dir, "ui")
        dist_index = os.path.join(ui_dir, "dist", "index.js")

        _git_install_jobs[job_id]["status"] = "checking"

        if not os.path.exists(ui_dir):
            # Pure Python plugin, no build needed, complete immediately
            _git_install_jobs[job_id].update(
                {
                    "status": "done",
                    "has_ui": False,
                    "finished_at": time.time(),
                }
            )
            logger.info(f"Git job {job_id} completed for {p_id} (no UI dir)")
            return

        # Has UI directory: decide whether to build based on mode
        if mode == "smart" and os.path.exists(dist_index):
            # smart mode and pre-built file exists, complete immediately
            _git_install_jobs[job_id].update(
                {
                    "status": "done",
                    "has_ui": True,
                    "dist_found": True,
                    "finished_at": time.time(),
                }
            )
            logger.info(f"Git job {job_id} completed for {p_id} (smart mode, dist found)")
            return

        # Phase 3: Build required (build mode, or smart mode but no pre-built file)
        _git_install_jobs[job_id].update(
            {
                "status": "building",
                "build_log_path": builder.get_log_path(p_id),
            }
        )
        logger.info(f"Git job {job_id} starting build for {p_id} (mode={mode})")
        await builder.run_build_task(p_id)

        build_result = builder.active_builds.get(p_id, "error")
        if build_result == "success":
            _git_install_jobs[job_id].update(
                {
                    "status": "done",
                    "has_ui": True,
                    "finished_at": time.time(),
                }
            )
            logger.info(f"Git job {job_id} completed for {p_id} (build success)")
        else:
            _git_install_jobs[job_id].update(
                {
                    "status": "failed",
                    "error": f"Build failed: {build_result}",
                }
            )
            logger.error(f"Git job {job_id} build failed for {p_id}: {build_result}")

    except Exception as e:
        logger.error(f"Git job {job_id} exception for {p_id}: {e}")
        _git_install_jobs[job_id].update({"status": "failed", "error": f"{type(e).__name__}: {e}"})


@router.post("/market/plugins/install-from-git")
async def market_install_plugin_from_git(
    body: GitInstallRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user_dep),
):
    """Install or update a plugin from any Git repo (non-blocking, returns job_id immediately).
    Client polls status via GET /market/plugins/jobs/{job_id}.
    """
    if not body.git_url:
        raise HTTPException(400, "Missing git_url")

    p_id = _resolve_plugin_id(body.git_url, body.plugin_id)

    if not re.match(r"^[a-zA-Z0-9_\-]+$", p_id):
        raise HTTPException(status_code=400, detail="Invalid plugin id")

    import uuid

    job_id = uuid.uuid4().hex[:12]
    _git_install_jobs[job_id] = {
        "job_id": job_id,
        "plugin_id": p_id,
        "git_url": body.git_url,
        "status": "pending",
        "started_at": time.time(),
    }

    # BackgroundTasks executes after the response is sent, truly non-blocking
    background_tasks.add_task(_run_git_install_job, job_id, p_id, body.git_url, body.mode)

    return {
        "ok": True,
        "job_id": job_id,
        "plugin_id": p_id,
        "status": "pending",
        "message": f"Git install started for {p_id}",
    }


@router.get("/market/plugins/jobs/{job_id}")
async def get_git_install_job(
    job_id: str,
    current_user: User = Depends(get_current_user_dep),
):
    """Poll Git install job status.
    status: pending | running | done | failed
    """
    job = _git_install_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# ============================================================
# Market Routes — Skills
# ============================================================


@router.get("/market/skills")
async def market_list_skills(
    page: int = Query(1, ge=1),
    size: int = Query(9, ge=1, le=200),
    search: str = Query(""),
    category: str = Query("", alias="category"),
    sort: str = Query("likes"),
    order: str = Query("desc"),
    current_user: User = Depends(get_current_user_dep),
):
    """Fetch skills index.json from GitHub in real-time, paginate locally"""
    try:
        async with httpx.AsyncClient(timeout=15, verify=_SSL_VERIFY) as client:
            resp = await client.get(SKILL_REGISTRY_URL)
            resp.raise_for_status()
            items = resp.json()

        likes_data, _ = await _get_likes_data("skills")
        likes_map: dict = likes_data.get("plugins", {})
        if likes_map:
            for p in items:
                pid = p.get("id", "")
                if pid in likes_map:
                    p["likes"] = likes_map[pid].get("count", p.get("likes", 0))

        if search:
            q = search.lower()
            items = [
                p
                for p in items
                if q in p.get("name", "").lower()
                or q in p.get("description", "").lower()
                or q in p.get("id", "").lower()
            ]

        if category and category != "all":
            items = [p for p in items if p.get("category") == category]

        reverse = order == "desc"
        if sort == "likes":
            items.sort(key=lambda p: p.get("likes", 0), reverse=reverse)
        elif sort == "name":
            items.sort(key=lambda p: p.get("name", "").lower(), reverse=reverse)

        total = len(items)
        start = (page - 1) * size
        paged = items[start : start + size]
        for p in paged:
            if "tags" not in p or p["tags"] is None:
                p["tags"] = []
            # If local icon already exists, replace with local API URL
            icon_path = _local_icon_path("skills", p["id"])
            if os.path.isfile(icon_path):
                p["icon_url"] = f"/api/ai-web/market/icon/skills/{p['id']}"

        return {"total": total, "page": page, "size": size, "pages": (total + size - 1) // size, "items": paged}
    except Exception as e:
        logger.error(f"Failed to fetch skills registry: {e}")
        raise HTTPException(status_code=503, detail=f"Skills registry unavailable: {e}")


@router.post("/market/skills/{item_id}/like")
async def market_like_skill(
    item_id: str,
    current_user: User = Depends(get_current_user_dep),
):
    return await _market_like_item(item_id, SKILL_REGISTRY_URL, "skills")


@router.post("/market/skills/{item_id}/install")
async def market_install_skill(
    item_id: str,
    current_user: User = Depends(get_current_user_dep),
):
    """Download skill zip and extract to skills/{id}/"""
    try:
        async with httpx.AsyncClient(timeout=15, verify=_SSL_VERIFY) as client:
            resp = await client.get(SKILL_REGISTRY_URL)
            resp.raise_for_status()
            items = resp.json()
        meta = next((p for p in items if p.get("id") == item_id), None)
        if not meta:
            raise HTTPException(status_code=404, detail="Skill not found in registry")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Skills registry unavailable: {e}")

    download_url = meta.get("download_url")
    if not download_url:
        raise HTTPException(status_code=400, detail="Skill has no download_url")

    dest = os.path.join(_SKILLS_DIR, item_id)
    installed_files: list[str] = []
    total_size = 0
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True, verify=_SSL_VERIFY) as client:
            zip_resp = await client.get(download_url)
            zip_resp.raise_for_status()
        zip_bytes_raw = zip_resp.content
        zip_size_kb = len(zip_bytes_raw) / 1024
        zip_bytes = io.BytesIO(zip_bytes_raw)
        with zipfile.ZipFile(zip_bytes) as zf:
            members = zf.namelist()
            prefix = members[0] if len(members) > 0 and members[0].endswith("/") else ""
            os.makedirs(dest, exist_ok=True)
            for member in members:
                rel = member[len(prefix) :] if prefix and member.startswith(prefix) else member
                if not rel:
                    continue
                target = os.path.join(dest, rel)
                if member.endswith("/"):
                    os.makedirs(target, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with zf.open(member) as src, open(target, "wb") as dst:
                        data = src.read()
                        dst.write(data)
                        installed_files.append(rel)
                        total_size += len(data)
        total_size_kb = total_size / 1024
        logger.info(
            f"[Install] Skill '{item_id}': {zip_size_kb:.1f} KB zip → {len(installed_files)} files ({total_size_kb:.1f} KB) → {dest}"
        )
        return {
            "ok": True,
            "message": f"Skill '{item_id}' installed: {len(installed_files)} files ({total_size_kb:.1f} KB)",
            "dest": f"skills/{item_id}/",
            "files": len(installed_files),
            "size_kb": round(total_size_kb, 1),
            "files_list": installed_files[:20],  # 最多返回前 20 个文件名
        }
    except Exception as e:
        logger.error(f"Failed to install skill {item_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Install failed: {e}")


# ============================================================
# Market Routes — Roles
# ============================================================


@router.get("/market/roles")
async def market_list_roles(
    page: int = Query(1, ge=1),
    size: int = Query(9, ge=1, le=200),
    search: str = Query(""),
    category: str = Query("", alias="category"),
    sort: str = Query("likes"),
    order: str = Query("desc"),
    current_user: User = Depends(get_current_user_dep),
):
    """Fetch role card index.json from GitHub in real-time, paginate locally"""
    try:
        async with httpx.AsyncClient(timeout=15, verify=_SSL_VERIFY) as client:
            resp = await client.get(ROLE_REGISTRY_URL)
            resp.raise_for_status()
            items = resp.json()

        likes_data, _ = await _get_likes_data("roles")
        likes_map: dict = likes_data.get("plugins", {})
        if likes_map:
            for p in items:
                pid = p.get("id", "")
                if pid in likes_map:
                    p["likes"] = likes_map[pid].get("count", p.get("likes", 0))

        if search:
            q = search.lower()
            items = [
                p
                for p in items
                if q in p.get("name", "").lower()
                or q in p.get("description", "").lower()
                or q in p.get("id", "").lower()
            ]

        if category and category != "all":
            items = [p for p in items if p.get("category") == category]

        reverse = order == "desc"
        if sort == "likes":
            items.sort(key=lambda p: p.get("likes", 0), reverse=reverse)
        elif sort == "name":
            items.sort(key=lambda p: p.get("name", "").lower(), reverse=reverse)

        total = len(items)
        start = (page - 1) * size
        paged = items[start : start + size]
        for p in paged:
            if "tags" not in p or p["tags"] is None:
                p["tags"] = []
            # If local icon already exists, replace with local API URL
            icon_path = _local_icon_path("roles", p["id"])
            if os.path.isfile(icon_path):
                p["icon_url"] = f"/api/ai-web/market/icon/roles/{p['id']}"

        return {"total": total, "page": page, "size": size, "pages": (total + size - 1) // size, "items": paged}
    except Exception as e:
        logger.error(f"Failed to fetch roles registry: {e}")
        raise HTTPException(status_code=503, detail=f"Roles registry unavailable: {e}")


@router.post("/market/roles/{item_id}/like")
async def market_like_role(
    item_id: str,
    current_user: User = Depends(get_current_user_dep),
):
    return await _market_like_item(item_id, ROLE_REGISTRY_URL, "roles")


@router.post("/market/roles/{item_id}/install")
async def market_install_role(
    item_id: str,
    current_user: User = Depends(get_current_user_dep),
):
    """Directly download role card .md file(s); supports download_urls array (multi-file role packs).
    Each URL's filename is extracted from the URL tail and written to role_cards/{filename}.
    """
    try:
        async with httpx.AsyncClient(timeout=15, verify=_SSL_VERIFY) as client:
            resp = await client.get(ROLE_REGISTRY_URL)
            resp.raise_for_status()
            items = resp.json()
        meta = next((p for p in items if p.get("id") == item_id), None)
        if not meta:
            raise HTTPException(status_code=404, detail="Role not found in registry")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Roles registry unavailable: {e}")

    download_urls = meta.get("download_urls") or []
    if not download_urls:
        raise HTTPException(status_code=400, detail="Role has no download_urls")

    os.makedirs(_ROLE_CARDS_DIR, exist_ok=True)
    installed_files: list[str] = []
    total_size = 0
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True, verify=_SSL_VERIFY) as client:
            for url in download_urls:
                # Extract filename from URL tail, ensure .md extension
                fname = url.rstrip("/").split("/")[-1]
                if not fname.endswith(".md"):
                    fname = fname + ".md"
                md_resp = await client.get(url)
                md_resp.raise_for_status()
                dest_path = os.path.join(_ROLE_CARDS_DIR, fname)
                text = md_resp.text
                with open(dest_path, "w", encoding="utf-8") as f:
                    f.write(text)
                installed_files.append(fname)
                total_size += len(text.encode("utf-8"))

            # Also download icon if index.json has icon_url
            icon_url = meta.get("icon_url")
            icon_downloaded = False
            if icon_url:
                try:
                    icon_resp = await client.get(icon_url)
                    if icon_resp.status_code == 200:
                        icon_path = os.path.join(_ROLE_CARDS_DIR, f"{item_id}_icon.svg")
                        with open(icon_path, "wb") as f:
                            f.write(icon_resp.content)
                        icon_downloaded = True
                except Exception as icon_err:
                    logger.warning(f"Role icon download failed for '{item_id}': {icon_err}")

        total_size_kb = total_size / 1024
        logger.info(
            f"[Install] Role '{item_id}': {len(installed_files)} md files ({total_size_kb:.1f} KB) → {_ROLE_CARDS_DIR}"
        )
        return {
            "ok": True,
            "message": f"Role '{item_id}' installed: {len(installed_files)} file(s) ({total_size_kb:.1f} KB)",
            "dest": "role_cards/",
            "files": len(installed_files),
            "size_kb": round(total_size_kb, 1),
            "files_list": installed_files,
            "icon_downloaded": icon_downloaded,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to install role {item_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Install failed: {e}")


# ============================================================
# Market Routes — Collabs
# ============================================================


@router.get("/market/collabs")
async def market_list_collabs(
    page: int = Query(1, ge=1),
    size: int = Query(9, ge=1, le=200),
    search: str = Query(""),
    category: str = Query("", alias="category"),
    sort: str = Query("likes"),
    order: str = Query("desc"),
    current_user: User = Depends(get_current_user_dep),
):
    """Fetch collab card index.json from GitHub in real-time, paginate locally"""
    try:
        async with httpx.AsyncClient(timeout=15, verify=_SSL_VERIFY) as client:
            resp = await client.get(COLLAB_REGISTRY_URL)
            resp.raise_for_status()
            items = resp.json()

        likes_data, _ = await _get_likes_data("collabs")
        likes_map: dict = likes_data.get("plugins", {})
        if likes_map:
            for p in items:
                pid = p.get("id", "")
                if pid in likes_map:
                    p["likes"] = likes_map[pid].get("count", p.get("likes", 0))

        if search:
            q = search.lower()
            items = [
                p
                for p in items
                if q in p.get("name", "").lower()
                or q in p.get("description", "").lower()
                or q in p.get("id", "").lower()
            ]

        if category and category != "all":
            items = [p for p in items if p.get("category") == category]

        reverse = order == "desc"
        if sort == "likes":
            items.sort(key=lambda p: p.get("likes", 0), reverse=reverse)
        elif sort == "name":
            items.sort(key=lambda p: p.get("name", "").lower(), reverse=reverse)

        total = len(items)
        start = (page - 1) * size
        paged = items[start : start + size]
        for p in paged:
            if "tags" not in p or p["tags"] is None:
                p["tags"] = []
            # If local icon already exists, replace with local API URL
            icon_path = _local_icon_path("collabs", p["id"])
            if os.path.isfile(icon_path):
                p["icon_url"] = f"/api/ai-web/market/icon/collabs/{p['id']}"

        return {"total": total, "page": page, "size": size, "pages": (total + size - 1) // size, "items": paged}
    except Exception as e:
        logger.error(f"Failed to fetch collabs registry: {e}")
        raise HTTPException(status_code=503, detail=f"Collabs registry unavailable: {e}")


@router.post("/market/collabs/{item_id}/like")
async def market_like_collab(
    item_id: str,
    current_user: User = Depends(get_current_user_dep),
):
    return await _market_like_item(item_id, COLLAB_REGISTRY_URL, "collabs")


@router.post("/market/collabs/{item_id}/install")
async def market_install_collab(
    item_id: str,
    current_user: User = Depends(get_current_user_dep),
):
    """Directly download collab card .md file and write to collab_cards/{id}.md"""
    try:
        async with httpx.AsyncClient(timeout=15, verify=_SSL_VERIFY) as client:
            resp = await client.get(COLLAB_REGISTRY_URL)
            resp.raise_for_status()
            items = resp.json()
        meta = next((p for p in items if p.get("id") == item_id), None)
        if not meta:
            raise HTTPException(status_code=404, detail="Collab not found in registry")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Collabs registry unavailable: {e}")

    download_url = meta.get("download_url")
    if not download_url:
        raise HTTPException(status_code=400, detail="Collab has no download_url")

    os.makedirs(_COLLAB_CARDS_DIR, exist_ok=True)
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True, verify=_SSL_VERIFY) as client:
            md_resp = await client.get(download_url)
            md_resp.raise_for_status()
            text = md_resp.text
            text_size_kb = len(text.encode("utf-8")) / 1024
            dest_path = os.path.join(_COLLAB_CARDS_DIR, f"{item_id}.md")
            with open(dest_path, "w", encoding="utf-8") as f:
                f.write(text)

            # Also download icon if index.json has icon_url
            icon_url = meta.get("icon_url")
            icon_downloaded = False
            if icon_url:
                try:
                    icon_resp = await client.get(icon_url)
                    if icon_resp.status_code == 200:
                        icon_path = os.path.join(_COLLAB_CARDS_DIR, f"{item_id}_icon.svg")
                        with open(icon_path, "wb") as f:
                            f.write(icon_resp.content)
                        icon_downloaded = True
                except Exception as icon_err:
                    logger.warning(f"Collab icon download failed for '{item_id}': {icon_err}")

        logger.info(f"[Install] Collab '{item_id}': {text_size_kb:.1f} KB → collab_cards/{item_id}.md")
        return {
            "ok": True,
            "message": f"Collab '{item_id}' installed ({text_size_kb:.1f} KB)",
            "dest": f"collab_cards/{item_id}.md",
            "size_kb": round(text_size_kb, 1),
            "icon_downloaded": icon_downloaded,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to install collab {item_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Install failed: {e}")


# ============================================================
# Plugin PR Review
# ============================================================

REVIEW_TOKEN = os.environ.get("OPENSQUAD_REVIEW_TOKEN", "")

# _MODEL_CARDS_DIR 已在前面定义为 builtin root 路径

# Dangerous builtins / modules that should not appear in plugin code
_SECURITY_PATTERNS = [
    r"\bsubprocess\b",
    r"\beval\s*\(",
    r"\bexec\s*\(",
    r"\b__import__\s*\(",
    r"\bos\.system\s*\(",
    r"\bos\.popen\s*\(",
    r"\bshutil\.rmtree\s*\(",
    r'\bopen\s*\(.*["\']w["\']',
]


class PRReviewRequest(BaseModel):
    admin_key: str
    pr_number: int
    repo: str  # "owner/repo"
    plugin_id: str
    plugin_py: str  # file content (plain text)
    plugin_json: str  # file content (plain text)
    readme: str | None = None
    github_token: str | None = None  # for posting PR comment


def _static_check_plugin(plugin_id: str, plugin_py: str, plugin_json_str: str):
    """
    Run static checks on submitted plugin files.
    Returns a list of issue strings (empty = all pass).
    """
    issues: list[str] = []

    # --- plugin.json checks ---
    try:
        meta = json.loads(plugin_json_str)
    except json.JSONDecodeError as e:
        issues.append(f"plugin.json parse failed: {e}")
        return issues  # Can't proceed further

    required_fields = ["id", "name", "version", "description", "author", "type"]
    for field in required_fields:
        if not meta.get(field):
            issues.append(f"plugin.json missing required field: {field}")

    if meta.get("id") and meta["id"] != plugin_id:
        issues.append(f"plugin.json id={meta['id']!r} does not match directory name {plugin_id!r}")

    # --- plugin.py syntax check ---
    try:
        tree = ast.parse(plugin_py)
    except SyntaxError as e:
        issues.append(f"plugin.py syntax error: {e}")
        return issues

    # Check @register decorator present
    has_register = any(
        isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and any(
            (isinstance(d, ast.Name) and d.id == "register")
            or (isinstance(d, ast.Attribute) and d.attr == "register")
            or (
                isinstance(d, ast.Call)
                and (
                    (isinstance(d.func, ast.Name) and d.func.id == "register")
                    or (isinstance(d.func, ast.Attribute) and d.func.attr == "register")
                )
            )
            for d in node.decorator_list
        )
        for node in ast.walk(tree)
    )
    if not has_register:
        issues.append("plugin.py missing @register decorator")

    # Check class inheriting Plugin
    has_plugin_class = any(
        isinstance(node, ast.ClassDef)
        and any(
            (isinstance(b, ast.Name) and b.id == "Plugin") or (isinstance(b, ast.Attribute) and b.attr == "Plugin")
            for b in node.bases
        )
        for node in ast.walk(tree)
    )
    if not has_plugin_class:
        issues.append("plugin.py has no class definition inheriting Plugin")

    # Security scan
    for pattern in _SECURITY_PATTERNS:
        if re.search(pattern, plugin_py):
            issues.append(f"plugin.py contains potentially dangerous code pattern: {pattern}")

    return issues


async def _ai_review_plugin(plugin_id: str, plugin_py: str, plugin_json_str: str, readme: str | None) -> str:
    """
    Call DeepSeek via OpenAI-compatible API to review the plugin.
    Returns the AI review text.
    """
    card_path = os.path.join(_MODEL_CARDS_DIR, "deepseek_chat.json")
    try:
        with open(card_path, encoding="utf-8") as f:
            card = json.load(f)
    except Exception as e:
        return f"[AI review skipped: unable to load model config — {e}]"

    api_key = card.get("api_key", "")
    base_url = card.get("base_url", "https://api.deepseek.com")
    model_name = card.get("model_name", "deepseek-chat")

    if not api_key:
        return "[AI review skipped: api_key not set in model config]"

    readme_section = f"\n\n**README.md:**\n```\n{readme[:3000]}\n```" if readme else ""

    prompt = f"""You are a code reviewer for the OpenSquad plugin marketplace. Please review the following plugin PR submission and provide a professional assessment.

Plugin ID: {plugin_id}

**plugin.json:**
```json
{plugin_json_str[:2000]}
```

**plugin.py:**
```python
{plugin_py[:4000]}
```{readme_section}

Please review from the following angles:
1. Code quality and readability
2. Security (dangerous operations, improper network requests, filesystem access)
3. Compliance with OpenSquad Plugin API spec (inherits Plugin class, uses @register decorator)
4. plugin.json metadata completeness
5. Whether functionality description matches implementation

Finally provide an overall verdict: **PASS** (recommend merge), **WARN** (recommend merge after fixes), or **FAIL** (recommend reject).
"""

    try:
        import openai

        client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)
        response = await client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
            temperature=0.3,
        )
        return response.choices[0].message.content or "[AI returned no content]"
    except Exception as e:
        return f"[AI review failed: {e}]"


async def _post_github_pr_comment(repo: str, pr_number: int, github_token: str, body: str):
    """Post a comment to a GitHub PR via the GitHub API."""
    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        async with httpx.AsyncClient(timeout=15, verify=_SSL_VERIFY) as client:
            resp = await client.post(url, headers=headers, json={"body": body})
            resp.raise_for_status()
    except Exception as e:
        logger.warning(f"Failed to post GitHub PR comment: {e}")


@router.post("/market/review-pr")
async def market_review_pr(body: PRReviewRequest):
    """
    Review a plugin PR submission:
    1. Auth via admin_key == REVIEW_TOKEN
    2. Static checks (syntax, required fields, security scan, @register, Plugin base class)
    3. AI review via DeepSeek
    4. Post summary comment to GitHub PR (if github_token provided)
    5. Return { verdict, issues, ai_review }
    """
    # 1. Auth
    if not REVIEW_TOKEN or body.admin_key != REVIEW_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid admin_key")

    # 2. Static checks
    issues = _static_check_plugin(body.plugin_id, body.plugin_py, body.plugin_json)

    # 3. AI review
    ai_review = await _ai_review_plugin(body.plugin_id, body.plugin_py, body.plugin_json, body.readme)

    # 4. Determine verdict
    if issues or "FAIL" in ai_review.upper():
        verdict = "fail"
    elif "WARN" in ai_review.upper():
        verdict = "warn"
    else:
        verdict = "pass"

    # 5. Post GitHub PR comment
    if body.github_token:
        verdict_emoji = {"pass": "✅", "warn": "⚠️", "fail": "❌"}.get(verdict, "")
        verdict_label = {
            "pass": "PASS — Recommend merge",
            "warn": "WARN — Recommend merge after fixes",
            "fail": "FAIL — Recommend reject",
        }.get(verdict, verdict)
        issues_md = "\n".join(f"- {i}" for i in issues) if issues else "No static check issues"
        comment_body = (
            f"## OpenSquad Plugin Auto Review Report {verdict_emoji}\n\n"
            f"**Overall verdict**: {verdict_label}\n\n"
            f"### Static Checks\n{issues_md}\n\n"
            f"### AI Code Review\n{ai_review}\n\n"
            f"---\n*Auto-generated by OpenSquad Gateway*"
        )
        await _post_github_pr_comment(body.repo, body.pr_number, body.github_token, comment_body)

    logger.info(f"PR review completed for {body.repo}#{body.pr_number} plugin={body.plugin_id} verdict={verdict}")
    return {
        "ok": True,
        "verdict": verdict,
        "issues": issues,
        "ai_review": ai_review,
    }


# ============================================================
# Multi-node registry (in-memory)
# Launcher nodes register themselves on startup via heartbeat.
# ============================================================

_node_registry: dict = {}  # node_id -> {node_id, node_label, launcher_url, last_seen, agent_count}


@router.post("/nodes/register")
async def register_node(request: Request):
    """POST /api/ai-web/nodes/register — called by Launcher on startup."""
    data = await request.json()
    node_id = data.get("node_id", "")
    node_label = data.get("node_label", node_id)
    launcher_url = data.get("launcher_url", "")
    if not node_id:
        raise HTTPException(status_code=400, detail="node_id is required")
    _node_registry[node_id] = {
        "node_id": node_id,
        "node_label": node_label,
        "launcher_url": launcher_url,
        "last_seen": time.time(),
        "agent_count": 0,
    }
    logger.info(f"Node registered: {node_id} ({node_label}) launcher={launcher_url}")
    return {"ok": True}


@router.put("/nodes/{node_id}/heartbeat")
async def node_heartbeat(node_id: str, request: Request):
    """PUT /api/ai-web/nodes/{node_id}/heartbeat — periodic keepalive from Launcher."""
    data = await request.json()
    if node_id in _node_registry:
        _node_registry[node_id]["last_seen"] = time.time()
        _node_registry[node_id]["agent_count"] = data.get("agent_count", 0)
    else:
        # Auto-register on heartbeat if not yet in registry
        _node_registry[node_id] = {
            "node_id": node_id,
            "node_label": data.get("node_label", node_id),
            "launcher_url": data.get("launcher_url", ""),
            "last_seen": time.time(),
            "agent_count": data.get("agent_count", 0),
        }
    return {"ok": True}


@router.get("/nodes")
async def list_nodes(current_user: User = Depends(get_current_user_dep)):
    """GET /api/ai-web/nodes — list all registered Launcher nodes."""
    now = time.time()
    nodes = []
    for n in _node_registry.values():
        nodes.append(
            {
                **n,
                "online": (now - n["last_seen"]) < 120,  # consider offline after 2 min
            }
        )
    return {"nodes": nodes}
