"""
Workspace management API (Gateway proxy layer)

All workspace operations are forwarded to the Launcher node for execution.
Launcher holds the file system permissions for the agent server and is the sole authority for workspace operations.
Gateway itself does not directly operate any workspace file system.
"""

import os
import sys
import time

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

from app.http_clients import get_local_http_client
from opensquad.system_config import syscfg

router = APIRouter(prefix="/api/workspace", tags=["workspace"])

_GET_CACHE_TTL_S = 5.0
_GET_CACHE_MAX = 32
_GET_CACHE: dict[str, tuple[float, dict]] = {}
_GET_CACHEABLE = frozenset(
    {
        "/api/workspace/list",
        "/api/workspace/detect-legacy",
    }
)


def _cache_get(path: str) -> dict | None:
    if path not in _GET_CACHEABLE:
        return None
    entry = _GET_CACHE.get(path)
    if entry is None:
        return None
    expires_at, result = entry
    if expires_at <= time.monotonic():
        _GET_CACHE.pop(path, None)
        return None
    return result


def _cache_set(path: str, result: dict) -> None:
    if path not in _GET_CACHEABLE:
        return
    if len(_GET_CACHE) >= _GET_CACHE_MAX:
        now = time.monotonic()
        expired = [k for k, (exp, _) in _GET_CACHE.items() if exp <= now]
        for k in expired:
            _GET_CACHE.pop(k, None)
        if len(_GET_CACHE) >= _GET_CACHE_MAX:
            _GET_CACHE.clear()
    _GET_CACHE[path] = (time.monotonic() + _GET_CACHE_TTL_S, result)


# ==================== Pydantic Models ====================


class CreateWorkspaceRequest(BaseModel):
    path: str | None = None
    name: str | None = None


class SwitchWorkspaceRequest(BaseModel):
    path: str


class MigrationRequest(BaseModel):
    source: str  # Source workspace directory (path on Launcher server)
    target: str  # Target workspace directory (path on Launcher server)
    mode: str = "copy"  # "copy"=keep source files  "move"=delete source files after migration
    conflict: str = "skip"  # "skip" | "overwrite"


# ==================== Proxy Helper Functions ====================


async def _proxy_get(path: str, timeout: float = 10.0) -> dict:
    """Send a GET request to Launcher"""
    cached = _cache_get(path)
    if cached is not None:
        return cached
    launcher_url = syscfg.launcher_url()
    client = get_local_http_client()
    try:
        resp = await client.get(f"{launcher_url}{path}", timeout=timeout)
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503, detail="Cannot connect to Launcher node, please confirm Launcher is running"
        )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Launcher node response timed out")

    if resp.status_code >= 400:
        try:
            detail = resp.json().get("error", resp.text)
        except Exception:
            detail = resp.text
        raise HTTPException(status_code=resp.status_code, detail=detail)

    result = resp.json()
    _cache_set(path, result)
    return result


async def _proxy_post(path: str, body: dict, timeout: float = 10.0) -> dict:
    """Send a POST request to Launcher"""
    launcher_url = syscfg.launcher_url()
    client = get_local_http_client()
    try:
        resp = await client.post(f"{launcher_url}{path}", json=body, timeout=timeout)
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503, detail="Cannot connect to Launcher node, please confirm Launcher is running"
        )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Launcher node response timed out")

    if resp.status_code >= 400:
        try:
            detail = resp.json().get("error", resp.text)
        except Exception:
            detail = resp.text
        raise HTTPException(status_code=resp.status_code, detail=detail)

    if path.startswith("/api/workspace"):
        _GET_CACHE.clear()
    return resp.json()


# ==================== API Endpoints ====================


@router.get("/list")
async def list_workspaces():
    """
    Get the list of workspaces on the Launcher server.
    The active workspace and historical workspace paths all come from the Launcher node.
    """
    return await _proxy_get("/api/workspace/list")


@router.get("/current")
async def get_current_workspace():
    """
    Get the currently active workspace information from Launcher.
    """
    return await _proxy_get("/api/workspace")


@router.post("/create")
async def create_workspace(request: CreateWorkspaceRequest):
    """
    Create or register a workspace on the Launcher server.

    - path: Absolute path of the workspace (path on the Launcher server)
    - name: Workspace name (used to generate the default path when path is not specified)

    Does not auto-switch after creation; call /switch to activate manually.
    """
    return await _proxy_post(
        "/api/workspace/create",
        {
            "path": request.path,
            "name": request.name,
        },
    )


@router.post("/switch")
async def switch_workspace(request: SwitchWorkspaceRequest):
    """
    Switch Launcher's current workspace.

    - path: Path of an existing and initialized workspace on the Launcher server

    Note: A Launcher restart is required for the switch to take full effect.
    """
    return await _proxy_post("/api/workspace/switch", {"path": request.path})


@router.get("/detect-legacy")
async def detect_legacy():
    """
    Detect whether legacy data exists in the Launcher installation directory.
    """
    return await _proxy_get("/api/workspace/detect-legacy")


@router.post("/migrate")
async def start_migration(request: MigrationRequest):
    """
    Start a workspace migration task on the Launcher server (async background execution).

    - source: Source workspace directory
    - target: Target workspace directory
    - mode: "copy" (keep source files) or "move" (delete source files after migration)
    - conflict: "skip" (skip existing items) or "overwrite" (backup then overwrite)

    Returns task_id; poll progress via /migrate/status/{task_id}.
    """
    return await _proxy_post(
        "/api/workspace/migrate",
        {
            "source": request.source,
            "target": request.target,
            "mode": request.mode,
            "conflict": request.conflict,
        },
    )


@router.get("/migrate/status/{task_id}")
async def get_migration_status(task_id: str):
    """
    Query migration task progress.
    Returns status: pending / running / completed / failed
    """
    return await _proxy_get(f"/api/workspace/migrate/status/{task_id}")
