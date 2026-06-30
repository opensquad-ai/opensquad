"""Market Routes (Plugins / Skills / Roles / Collabs / PR Review).
Extracted from routes.py."""

from __future__ import annotations

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
import zipfile
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api import get_current_user_dep
from app.models import User
from opensquad.system_config import syscfg

from ..routes._admin import _proxy_get

logger = logging.getLogger(__name__)
_REPO_ROOT = syscfg.project_root()
_SSL_VERIFY = os.environ.get("OPENQUAD_SSL_VERIFY", "1") != "0"

market_router = APIRouter()  # prefix comes from main router include

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


@market_router.get("/market/icon/{kind}/{item_id}")
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


@market_router.get("/market/plugins")
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


@market_router.get("/market/installed")
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


@market_router.get("/market/plugins/{plugin_id}")
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


@market_router.post("/market/plugins/{plugin_id}/like")
async def market_like_plugin(
    plugin_id: str,
    current_user: User = Depends(get_current_user_dep),
):
    """Like a plugin. Delegates to the shared _market_like_item() helper."""
    return await _market_like_item(plugin_id, PLUGIN_REGISTRY_URL, "plugins")


from app.ai_web.builder import builder


@market_router.get("/market/build/env")
async def market_check_build_env():
    """Check if Node/NPM are ready for local builds."""
    return await builder.check_env()


@market_router.post("/market/plugins/{plugin_id}/build")
async def market_trigger_plugin_build(plugin_id: str):
    """Trigger a local build for a plugin's UI."""
    return builder.start_build(plugin_id)


@market_router.get("/market/plugins/{plugin_id}/build/log")
async def market_get_plugin_build_log(plugin_id: str):
    """Return the current build log for a plugin."""
    log_path = builder.get_log_path(plugin_id)
    status = builder.active_builds.get(plugin_id, "idle")

    content = ""
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8") as f:
            content = f.read()

    return {"status": status, "log": content}


@market_router.post("/market/plugins/{plugin_id}/install")
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


@market_router.post("/market/plugins/upload")
async def market_upload_plugin(
    body: dict = Body(...),
    current_user: User = Depends(get_current_user_dep),
):
    """Plugin submission stub (registry is a static GitHub file; use PR workflow)."""
    raise HTTPException(
        status_code=501, detail="Direct upload not supported. Submit a Pull Request to the GitHub registry instead."
    )


@market_router.delete("/market/plugins/{plugin_id}/uninstall")
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


@market_router.post("/market/plugins/install-from-git")
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


@market_router.get("/market/plugins/jobs/{job_id}")
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


@market_router.get("/market/skills")
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


@market_router.post("/market/skills/{item_id}/like")
async def market_like_skill(
    item_id: str,
    current_user: User = Depends(get_current_user_dep),
):
    return await _market_like_item(item_id, SKILL_REGISTRY_URL, "skills")


@market_router.post("/market/skills/{item_id}/install")
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


@market_router.get("/market/roles")
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


@market_router.post("/market/roles/{item_id}/like")
async def market_like_role(
    item_id: str,
    current_user: User = Depends(get_current_user_dep),
):
    return await _market_like_item(item_id, ROLE_REGISTRY_URL, "roles")


@market_router.post("/market/roles/{item_id}/install")
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


@market_router.get("/market/collabs")
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


@market_router.post("/market/collabs/{item_id}/like")
async def market_like_collab(
    item_id: str,
    current_user: User = Depends(get_current_user_dep),
):
    return await _market_like_item(item_id, COLLAB_REGISTRY_URL, "collabs")


@market_router.post("/market/collabs/{item_id}/install")
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


@market_router.post("/market/review-pr")
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
