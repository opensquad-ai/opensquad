"""
AI Web API routes
HTTP APIs for the frontend:
  - Agent listing & details
  - Gateway session management (in-memory, user-scoped)
  - Agent disk session management (read-only, per-agent)
  - Image upload
  - Admin management (proxy to launcher.py)
"""

import logging
import os
import re
import time
import uuid

import httpx
from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from opensquad.system_config import syscfg

# SSL context for GitHub API calls (Windows may lack proper CA certificates)
# Default to "1" (verify SSL). Set OPENQUAD_SSL_VERIFY="0" to disable in dev/air-gapped environments.
_SSL_VERIFY = os.environ.get("OPENQUAD_SSL_VERIFY", "1") != "0"

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

from ..agent_sessions import async_get_reader as async_get_agent_session_reader
from ..audit_routes import router as audit_router
from ..registry import registry
from ..sessions import gateway_session_cache

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
    # Markdown link form: [File: name (size) ...](/uploads/xxx)
    # Also path= form used by Agent Web voice/file sends.
    file_marker_re = re.compile(
        r"\[File:\s*(.+?)\s*\(([^)]*)\)(?:\s*path=([^\s\]]+))?(?:\s*type=(audio|video|voice|file))?\](?:\(([^)]+)\))?"
    )
    for m in file_marker_re.finditer(content_text):
        name = (m.group(1) or "file").strip()
        path_raw = (m.group(3) or "").strip()
        kind = (m.group(4) or "").strip().lower()
        url = (m.group(5) or "").strip() or path_raw
        if not url:
            continue
        # Prefer web-relative /uploads leaf when given an absolute disk path
        if not url.startswith("/") and not url.startswith("http"):
            url = "/uploads/" + url.replace("\\", "/").split("/")[-1]
        lower = (name + " " + url).lower()
        is_voice = kind in ("audio", "voice") or name.lower().startswith("voice_")
        is_image = lower.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"))
        is_audio = is_voice or lower.endswith((".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac", ".webm"))
        is_video = (not is_audio) and lower.endswith((".mp4", ".webm", ".mov", ".avi", ".mkv"))
        parsed_files.append(
            {
                "original_name": name,
                "url": url,
                "path": path_raw or None,
                "is_image": is_image,
                "is_audio": is_audio and not is_image,
                "is_video": is_video and not is_image,
                "type": "voice"
                if kind == "voice" or name.lower().startswith("voice_")
                else ("audio" if is_audio else ("video" if is_video else "file")),
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
                "type": (
                    "voice"
                    if f.get("type") == "voice" or str(f.get("original_name") or "").lower().startswith("voice_")
                    else ("video" if f.get("is_video") else ("audio" if f.get("is_audio") else "file"))
                ),
            }
            for f in out["files"]
            if isinstance(f, dict) and f.get("url") and not f.get("is_image")
        ]

    # Remove legacy [File:...] markers (markdown and path= forms) from visible text.
    if isinstance(out.get("content"), str) and "[File:" in out["content"]:
        cleaned = re.sub(
            r"\n?\s*\[File:\s*.+?\((?:[^)]*)\)(?:\s*path=[^\s\]]+)?(?:\s*type=(?:audio|video|voice|file))?\](?:\([^)]+\))?",
            "",
            out["content"],
        ).strip()
        out["content"] = cleaned

    return out


def _normalize_session_payload(session: dict | None) -> dict | None:
    """Normalize a session payload so frontend can rely on one schema."""
    if not isinstance(session, dict):
        return session
    out = dict(session)
    messages = out.get("messages") if isinstance(out.get("messages"), list) else []
    out["messages"] = [_normalize_session_message(m) for m in messages]
    return out


# Launcher management API address - from system_config.json
LAUNCHER_URL = syscfg.launcher_url()
_REPO_ROOT = syscfg.project_root()

router = APIRouter(prefix="/api/ai-web")
router.include_router(audit_router)
from ._admin import admin_router
from ._market import market_router

router.include_router(admin_router)
router.include_router(market_router)


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


@router.post("/sessions/{agent_id}/withdraw")
async def withdraw_turn(agent_id: str, body: dict = Body(...), current_user: User = Depends(get_current_user_dep)):
    """Withdraw a user turn: revert session file changes + truncate conversation.

    Body: { message_id, timestamp, root? }
    File revert goes through Launcher; session truncate is sent as agent command.
    """
    from urllib.parse import quote

    message_id = str(body.get("message_id") or "").strip()
    timestamp = str(body.get("timestamp") or "").strip()
    root = str(body.get("root") or "").strip()
    if not message_id and not timestamp:
        raise HTTPException(400, "message_id or timestamp required")

    # 1) Revert files to checkpoint (via admin proxy → launcher)
    revert_payload: dict = {"message_id": message_id, "root": root}
    try:
        # Prefer agent directory name if agent_id is a UUID-style id — admin uses agent folder name.
        # Frontend typically passes the same agentId used for fs APIs.
        from app.ai_web.routes._admin import _proxy_post

        await _proxy_post(
            f"/api/agents/{quote(agent_id, safe='')}/fs/session-changes/revert",
            revert_payload,
            timeout=60.0,
        )
    except Exception as e:
        # File revert failure should not block chat truncate — surface warning
        logger = __import__("logging").getLogger(__name__)
        logger.warning(f"[withdraw] file revert failed for {agent_id}: {e}")

    # 2) Truncate agent session via command
    sent = await registry.send_to_agent(
        agent_id,
        {
            "type": "command",
            "user_id": current_user.id,
            "command": "withdraw_turn",
            "data": {"message_id": message_id, "timestamp": timestamp},
        },
    )
    if not sent:
        raise HTTPException(503, f"Agent {agent_id} is not connected; withdraw not delivered")

    return {"ok": True, "message_id": message_id, "timestamp": timestamp}


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

    Mirror of the helper in :mod:`app.ai_web.routes`; kept here so
    the legacy ``_main`` router keeps its own copy and stays a
    drop-in replacement.
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


def _detect_channel(version: str) -> str:
    """Wrapper kept for backwards-compat with existing callers.

    New code should import :func:`opensquad.utils.version_channel.
    detect_channel` directly.
    """
    from opensquad.utils.version_channel import detect_channel

    return detect_channel(version)


@router.get("/version")
async def check_version(platform: str | None = None, arch: str | None = None):
    """Get current version and check for updates from GitHub.

    Returns ``{current, channel, latest, url, update_available,
    check_skipped, skip_reason, download_url?, download_name?, download_size?}``.
    The frontend should respect ``check_skipped=True`` and not show an update banner.

    When *platform* is ``win32``, ``darwin``, or ``linux`` and an update exists,
    the response includes a GitHub Release asset URL suitable for in-app install.

    Update checks are only performed for ``channel == "stable"`` builds.
    Dev / hotfix / pre-release / local users get the current version
    echoed back and ``check_skipped=True`` so the UI can render an
    informational message instead of a misleading "new version" hint.
    """
    from opensquad.utils.desktop_release import normalize_desktop_platform, pick_desktop_installer_asset
    from opensquad.utils.version_channel import should_check_for_updates

    current = _get_current_version()
    do_check, channel, skip_reason = should_check_for_updates(current)
    normalized_platform = normalize_desktop_platform(platform)

    result = {
        "current": current,
        "channel": channel,
        "latest": None,
        "url": None,
        "update_available": False,
        "check_skipped": not do_check,
        "skip_reason": skip_reason,
        "download_url": None,
        "download_name": None,
        "download_size": None,
    }

    if not do_check:
        return result

    try:
        async with httpx.AsyncClient(timeout=10, verify=_SSL_VERIFY) as client:
            resp = await client.get(
                "https://api.github.com/repos/opensquad-ai/opensquad/releases/latest",
                headers={"Accept": "application/vnd.github.v3+json", "User-Agent": "OpenSquad"},
            )
            if resp.status_code == 200:
                data = resp.json()
                tag = data.get("tag_name", "").lstrip("v")
                result["latest"] = tag
                result["url"] = data.get("html_url", "")
                # Compare versions
                if tag and current and tag != current:
                    try:
                        from packaging.version import Version

                        result["update_available"] = Version(tag) > Version(current)
                    except Exception:
                        # Fallback: simple string comparison
                        result["update_available"] = tag != current

                if result["update_available"] and normalized_platform:
                    picked = pick_desktop_installer_asset(
                        data.get("assets"),
                        normalized_platform,
                        arch=arch,
                    )
                    if picked:
                        result["download_url"] = picked["url"]
                        result["download_name"] = picked["name"]
                        result["download_size"] = picked["size"]
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
# Agent disk session API — read-only access to Agent session files
# Used by NexusChat Pro's native AI chat component
# ============================================================

# Root directory for uploads — must match main.py's StaticFiles("/uploads") mount.
# Use syscfg.workspace_uploads_dir() so PyInstaller mode (OPENSQUAD_USER_DATA
# env) is handled centrally and all three upload-path sites stay in sync.
# See issue #43.
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
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user_dep),
):
    """
    Get session list for an agent (current + history).
    Reads from agent's disk session files.
    """
    reader = await async_get_agent_session_reader(agent_id)
    if not reader:
        raise HTTPException(404, f"Agent not found: {agent_id}")

    sessions = await reader.async_get_session_list(limit=limit, offset=offset)
    current_id = await reader.async_get_current_session_id()

    return {
        "agent_id": agent_id,
        "current_session_id": current_id,
        "sessions": sessions,
        "has_more": len(sessions) >= limit,
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


class SynthesizeSpeechRequest(BaseModel):
    """Text-to-speech for Agent Web message bubble (uses agent voice.tts_card)."""

    text: str


@router.post("/agent-sessions/{agent_id}/synthesize")
async def agent_synthesize_speech(
    agent_id: str,
    body: SynthesizeSpeechRequest,
    current_user: User = Depends(get_current_user_dep),
):
    """
    Synthesize speech from message text using the agent's configured voice.tts_card.
    Returns an /uploads/... URL the frontend can play.
    """
    import time

    from opensquad.audio import resolve_voice_card
    from opensquad.audio.openai_tts import synthesize_with_card
    from opensquad.audio.realtime_manager import sanitize_for_tts

    from ..agent_sessions import _build_agent_id_map

    text = sanitize_for_tts((body.text or "").strip())
    if not text:
        raise HTTPException(400, "text is required")

    # Short-lived cache: auto-speech sends many sentence chunks for the same agent.
    cache = getattr(agent_synthesize_speech, "_tts_cfg_cache", None)
    if cache is None:
        cache = {}
        agent_synthesize_speech._tts_cfg_cache = cache
    now = time.monotonic()
    cached = cache.get(agent_id)
    if cached and now - cached["ts"] < 30:
        card = cached["card"]
        voice = cached["voice"]
        instruction = cached["instruction"]
    else:
        id_map = _build_agent_id_map()
        agent_dir = id_map.get(agent_id)
        if not agent_dir:
            raise HTTPException(404, f"Agent not found: {agent_id}")

        cfg_path = os.path.join(agent_dir, "config.json")
        if not os.path.isfile(cfg_path):
            raise HTTPException(404, f"Agent config not found: {agent_id}")
        try:
            import json

            with open(cfg_path, encoding="utf-8") as f:
                agent_config = json.load(f)
        except Exception as e:
            raise HTTPException(500, f"Failed to read agent config: {e}") from e

        card = resolve_voice_card(agent_config, "tts")
        if not card:
            raise HTTPException(
                400,
                "Agent has no TTS configured. Set voice.tts_model (+ base_url/api_key) or voice.tts_card.",
            )

        voice_cfg = agent_config.get("voice") or {}
        voice = (voice_cfg.get("tts_voice") or "").strip()
        instruction = (voice_cfg.get("tts_instruction") or "").strip()
        cache[agent_id] = {
            "ts": now,
            "card": card,
            "voice": voice,
            "instruction": instruction,
        }

    try:
        result = await synthesize_with_card(
            card,
            text,
            voice=voice,
            instruction=instruction,
            output_dir=_UPLOAD_DIR,
        )
    except Exception as e:
        logger.exception("TTS synthesize failed for %s", agent_id)
        raise HTTPException(502, f"TTS failed: {e}") from e

    if not result.get("success"):
        raise HTTPException(502, result.get("error") or "TTS synthesis failed")

    logger.info(
        "TTS synthesized for %s by %s: %s (%s chars)",
        agent_id,
        getattr(current_user, "id", "?"),
        result.get("url"),
        len(text),
    )
    return {
        "url": result.get("url"),
        "path": result.get("path"),
        "mime": result.get("mime") or "audio/mpeg",
        "filename": os.path.basename(result.get("path") or "") or None,
    }


@router.post("/agent-sessions/{agent_id}/transcribe")
async def agent_transcribe_audio(
    agent_id: str,
    current_user: User = Depends(get_current_user_dep),
    file: UploadFile | None = File(None),
    path: str | None = Form(None),
    language: str = Form("zh"),
):
    """
    Speech-to-text using the agent's voice.asr_card / inline ASR.
    Accepts multipart audio ``file`` and/or an already-uploaded ``path`` under uploads.
    """
    from opensquad.audio import resolve_voice_card
    from opensquad.audio.stepfun_asr import transcribe_with_card

    from ..agent_sessions import _build_agent_id_map

    id_map = _build_agent_id_map()
    agent_dir = id_map.get(agent_id)
    if not agent_dir:
        raise HTTPException(404, f"Agent not found: {agent_id}")

    cfg_path = os.path.join(agent_dir, "config.json")
    if not os.path.isfile(cfg_path):
        raise HTTPException(404, f"Agent config not found: {agent_id}")
    try:
        import json as _json

        with open(cfg_path, encoding="utf-8") as f:
            agent_config = _json.load(f)
    except Exception as e:
        raise HTTPException(500, f"Failed to read agent config: {e}") from e

    card = resolve_voice_card(agent_config, "asr")
    if not card:
        raise HTTPException(
            400,
            "Agent has no ASR configured. Set voice.asr_model (+ base_url/api_key) or voice.asr_card.",
        )

    audio_path: str | None = None
    cleanup = False
    if file is not None and getattr(file, "filename", None):
        ext = os.path.splitext(file.filename or "")[1] or ".webm"
        filename = f"asr_{uuid.uuid4().hex[:10]}{ext}"
        audio_path = os.path.join(_UPLOAD_DIR, filename)
        content = await file.read()
        if not content:
            raise HTTPException(400, "empty audio file")
        with open(audio_path, "wb") as fw:
            fw.write(content)
        cleanup = True
    elif path:
        # Allow relative upload names or absolute paths under _UPLOAD_DIR
        candidate = path.strip()
        if not os.path.isabs(candidate):
            candidate = os.path.join(_UPLOAD_DIR, os.path.basename(candidate))
        real_upload = os.path.realpath(_UPLOAD_DIR)
        real_cand = os.path.realpath(candidate)
        if not real_cand.startswith(real_upload + os.sep) and real_cand != real_upload:
            raise HTTPException(400, "path must be under uploads directory")
        if not os.path.isfile(real_cand):
            raise HTTPException(404, f"audio file not found: {os.path.basename(real_cand)}")
        audio_path = real_cand
    else:
        raise HTTPException(400, "file or path is required")

    try:
        result = await transcribe_with_card(card, audio_path, language=language or "zh")
    except Exception as e:
        logger.exception("ASR transcribe failed for %s", agent_id)
        raise HTTPException(502, f"ASR failed: {e}") from e
    finally:
        if cleanup and audio_path and os.path.isfile(audio_path):
            try:
                os.remove(audio_path)
            except OSError:
                pass

    if not result.get("success"):
        err = result.get("error") or "ASR transcription failed"
        # Empty / format issues are client-recoverable; avoid opaque 502.
        code = 422 if "empty transcript" in str(err).lower() else 502
        raise HTTPException(code, err)

    text = (result.get("text") or "").strip()
    logger.info(
        "ASR transcribed for %s by %s: %s chars",
        agent_id,
        getattr(current_user, "id", "?"),
        len(text),
    )
    return {"text": text, "language": language or "zh"}


@router.post("/group-transcribe")
async def group_transcribe_audio(
    current_user: User = Depends(get_current_user_dep),
    file: UploadFile | None = File(None),
    path: str | None = Form(None),
    language: str = Form("zh"),
):
    """
    Speech-to-text for group chat using the model card marked ``group_asr: true``.
    """
    from opensquad.audio import resolve_group_asr_card
    from opensquad.audio.stepfun_asr import transcribe_with_card

    card = resolve_group_asr_card()
    if not card:
        raise HTTPException(
            400,
            "No group ASR model card. Open Models → ASR card → enable「设为群聊语音转文本」(group_asr).",
        )

    audio_path: str | None = None
    cleanup = False
    if file is not None and getattr(file, "filename", None):
        ext = os.path.splitext(file.filename or "")[1] or ".webm"
        filename = f"group_asr_{uuid.uuid4().hex[:10]}{ext}"
        audio_path = os.path.join(_UPLOAD_DIR, filename)
        content = await file.read()
        if not content:
            raise HTTPException(400, "empty audio file")
        with open(audio_path, "wb") as fw:
            fw.write(content)
        cleanup = True
    elif path:
        candidate = path.strip()
        if not os.path.isabs(candidate):
            candidate = os.path.join(_UPLOAD_DIR, os.path.basename(candidate))
        real_upload = os.path.realpath(_UPLOAD_DIR)
        real_cand = os.path.realpath(candidate)
        if not real_cand.startswith(real_upload + os.sep) and real_cand != real_upload:
            raise HTTPException(400, "path must be under uploads directory")
        if not os.path.isfile(real_cand):
            raise HTTPException(404, f"audio file not found: {os.path.basename(real_cand)}")
        audio_path = real_cand
    else:
        raise HTTPException(400, "file or path is required")

    try:
        result = await transcribe_with_card(card, audio_path, language=language or "zh")
    except Exception as e:
        logger.exception("Group ASR transcribe failed")
        raise HTTPException(502, f"ASR failed: {e}") from e
    finally:
        if cleanup and audio_path and os.path.isfile(audio_path):
            try:
                os.remove(audio_path)
            except OSError:
                pass

    if not result.get("success"):
        err = result.get("error") or "ASR transcription failed"
        # Empty / format issues are client-recoverable; avoid opaque 502.
        code = 422 if "empty transcript" in str(err).lower() else 502
        raise HTTPException(code, err)

    text = (result.get("text") or "").strip()
    logger.info(
        "Group ASR transcribed by %s via card %s: %s chars",
        getattr(current_user, "id", "?"),
        card.get("_card") or card.get("name"),
        len(text),
    )
    return {
        "text": text,
        "language": language or "zh",
        "card": card.get("_card") or card.get("name"),
    }


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


class SessionRenameRequest(BaseModel):
    title: str


@router.post("/agent-sessions/{agent_id}/{session_id}/rename")
async def agent_session_rename(
    agent_id: str,
    session_id: str,
    body: SessionRenameRequest,
    current_user: User = Depends(get_current_user_dep),
):
    """Rename a session with a user-chosen title (sticky / title_locked)."""
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(400, "Title is required")
    if len(title) > 200:
        raise HTTPException(400, "Title too long (max 200)")

    reader = await async_get_agent_session_reader(agent_id)
    if not reader:
        raise HTTPException(404, f"Agent not found: {agent_id}")

    rename = getattr(reader, "async_rename_session", None)
    if rename is None:
        raise HTTPException(501, "Rename not supported for this agent session reader")

    success = await rename(session_id, title)
    if not success:
        raise HTTPException(404, f"Session not found or rename failed: {session_id}")

    return {"ok": True, "session_id": session_id, "title": title}


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
    from ..websocket import user_handler

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
    from ..websocket import user_handler

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

    import datetime

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
        msg_id = (
            f"m_{now.timestamp()}"
            if hasattr(now, "timestamp")
            else f"m_{datetime.datetime.now(datetime.timezone.utc).timestamp()}"
        )
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
            att_id = f"a_{datetime.datetime.now(datetime.timezone.utc).timestamp()}_{i}"
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
# Multi-node registry (in-memory)
# Launcher nodes register themselves on startup via heartbeat.
# ============================================================

_node_registry: dict = {}  # node_id -> {node_id, node_label, launcher_url, last_seen, agent_count}


def _verify_node_token(request: Request) -> str:
    """Verify Bearer token from Authorization header against configured gateway_token or node_secret.

    Reads token from ``auth.gateway_token`` or ``auth.node_secret`` in system_config.
    Returns the validated node_id derived from the token on success.
    Raises HTTPException(401) on failure.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized: Bearer token required")

    provided = auth_header[7:]
    expected_gw = syscfg.auth("gateway_token")
    expected_ns = syscfg.node_secret()

    # Accept either gateway_token or node_secret
    if provided == expected_gw or (expected_ns and provided == expected_ns):
        return "gateway"

    raise HTTPException(status_code=403, detail="Forbidden: invalid token")


@router.post("/nodes/register")
async def register_node(request: Request):
    """POST /api/ai-web/nodes/register — called by Launcher on startup."""
    _verify_node_token(request)
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
