"""
Plugin Registry Service - Port 9720
Provides plugin metadata, search, pagination, likes, upload, and admin sync endpoints.

Admin endpoints require X-Admin-Key header matching OPENSQUAD_REGISTRY_ADMIN_KEY env var.
"""
import json
import os
import math
import re
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional

app = FastAPI(title="OpenSquad Plugin Registry", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:9555",
        "http://localhost:9530",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:9555",
        "http://127.0.0.1:9530",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = os.path.join(os.path.dirname(__file__), "plugins_db.json")

# Admin key — MUST be set via OPENSQUAD_REGISTRY_ADMIN_KEY env var in production.
# There is no hardcoded default; admin endpoints are disabled if the env var is missing.
ADMIN_KEY = os.environ.get("OPENSQUAD_REGISTRY_ADMIN_KEY", "")


def load_plugins():
    with open(DB_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_plugins(plugins):
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(plugins, f, ensure_ascii=False, indent=2)


def name_to_id(name: str) -> str:
    """Convert a human-readable plugin name to a filesystem-safe id."""
    s = name.lower().strip()
    s = re.sub(r"[\s\-]+", "_", s)
    s = re.sub(r"[^a-z0-9_]", "", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "plugin"


def _require_admin(x_admin_key: Optional[str]):
    """Raise 403 if admin key is missing or wrong."""
    if not x_admin_key or x_admin_key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing X-Admin-Key header")


# ---- Models ----

class PluginUpload(BaseModel):
    name: str
    version: str
    author: str
    description: str
    tags: List[str] = []
    type: str  # tool | platform | hook
    homepage: Optional[str] = None
    git_url: Optional[str] = None
    icon_url: Optional[str] = None
    # Bilingual optional fields
    name_zh: Optional[str] = None
    description_zh: Optional[str] = None
    description_en: Optional[str] = None


class AdminPluginSync(BaseModel):
    """
    Used by GitHub Actions CI after merging a plugin PR.
    """
    name: str
    version: str
    author: str
    github_user: str
    description: str
    tags: List[str] = []
    type: str
    download_url: str
    git_url: Optional[str] = None
    homepage: Optional[str] = None
    icon_url: Optional[str] = None
    plugin_id: Optional[str] = None
    is_featured: bool = True  # New field
    name_zh: Optional[str] = None
    description_zh: Optional[str] = None
    description_en: Optional[str] = None


# ---- Endpoints ----

@app.get("/health")
def health():
    return {"ok": True, "service": "plugin-registry", "version": "1.1.0"}


@app.get("/plugins")
def list_plugins(
    page: int = Query(1, ge=1),
    size: int = Query(9, ge=1, le=200),
    search: str = Query(""),
    type: str = Query(""),
    sort: str = Query("likes"),
    order: str = Query("desc"),
):
    plugins = load_plugins()

    if search:
        q = search.lower()
        plugins = [
            p for p in plugins
            if q in p["name"].lower()
            or q in p["description"].lower()
            or any(q in tag.lower() for tag in p.get("tags", []))
            or q in p["author"].lower()
        ]

    if type and type != "all":
        plugins = [p for p in plugins if p.get("type") == type]

    reverse = (order == "desc")
    if sort == "likes":
        plugins.sort(key=lambda p: p.get("likes", 0), reverse=reverse)
    elif sort == "created_at":
        plugins.sort(key=lambda p: p.get("created_at", ""), reverse=reverse)
    elif sort == "name":
        plugins.sort(key=lambda p: p.get("name", "").lower(), reverse=reverse)

    total = len(plugins)
    pages = max(1, math.ceil(total / size))
    start = (page - 1) * size
    end = start + size

    return {
        "total": total,
        "page": page,
        "size": size,
        "pages": pages,
        "plugins": plugins[start:end],
    }


@app.get("/plugins/{plugin_id}")
def get_plugin(plugin_id: str):
    plugins = load_plugins()
    for p in plugins:
        if p["id"] == plugin_id:
            return p
    raise HTTPException(status_code=404, detail="Plugin not found")


@app.post("/plugins/{plugin_id}/like")
def like_plugin(plugin_id: str):
    plugins = load_plugins()
    for p in plugins:
        if p["id"] == plugin_id:
            p["likes"] = p.get("likes", 0) + 1
            save_plugins(plugins)
            return {"id": plugin_id, "likes": p["likes"]}
    raise HTTPException(status_code=404, detail="Plugin not found")


@app.post("/plugins/upload", status_code=201)
def upload_plugin(body: PluginUpload):
    """
    Upload a new plugin or publish an update (legacy / admin manual entry).

    Rules:
    - Plugin names must be unique (case-insensitive).
    - If a plugin with the same name already exists, return 409 Conflict.
    - Use POST /plugins/{id}/update to publish a new version of an existing plugin.

    Note: For the automated Git PR workflow, use POST /admin/plugins/sync instead.
    """
    plugins = load_plugins()

    # Name uniqueness check (case-insensitive)
    name_lower = body.name.strip().lower()
    for p in plugins:
        if p["name"].lower() == name_lower:
            raise HTTPException(
                status_code=409,
                detail=f"Plugin name '{body.name}' is already taken by plugin id='{p['id']}'. "
                       f"Use a different name or publish an update via POST /plugins/{p['id']}/update.",
            )

    # Derive id from name
    plugin_id = name_to_id(body.name)

    # Ensure id is unique (fallback: append counter)
    existing_ids = {p["id"] for p in plugins}
    if plugin_id in existing_ids:
        counter = 2
        while f"{plugin_id}_{counter}" in existing_ids:
            counter += 1
        plugin_id = f"{plugin_id}_{counter}"

    new_plugin = {
        "id": plugin_id,
        "name": body.name.strip(),
        "version": body.version.strip(),
        "author": body.author.strip(),
        "github_user": "",
        "description": body.description.strip(),
        "name_zh": body.name_zh.strip() if body.name_zh else None,
        "description_zh": body.description_zh.strip() if body.description_zh else None,
        "description_en": body.description_en.strip() if body.description_en else None,
        "tags": [t.strip() for t in body.tags if t.strip()],
        "type": body.type,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "likes": 0,
        "is_featured": False, # Manual uploads are community by default
        "icon_url": body.icon_url,
        "git_url": body.git_url or "",
        "homepage": body.homepage or "",
        "download_url": "",
    }

    plugins.append(new_plugin)
    save_plugins(plugins)
    return new_plugin


@app.post("/plugins/{plugin_id}/update")
def update_plugin_version(plugin_id: str, body: PluginUpload):
    """
    Publish a new version of an existing plugin.
    The name must still match the original; version must be newer.
    """
    plugins = load_plugins()
    for i, p in enumerate(plugins):
        if p["id"] == plugin_id:
            # Author check (simple: must be same author)
            if p["author"].lower() != body.author.strip().lower():
                raise HTTPException(
                    status_code=403,
                    detail=f"Author mismatch: plugin '{plugin_id}' belongs to '{p['author']}'.",
                )
            # Name must not change
            if p["name"].lower() != body.name.strip().lower():
                raise HTTPException(
                    status_code=400,
                    detail="Plugin name cannot be changed in an update.",
                )
            # Update fields
            plugins[i].update({
                "version": body.version.strip(),
                "description": body.description.strip(),
                "tags": [t.strip() for t in body.tags if t.strip()],
                "author": body.author.strip(),
                "homepage": body.homepage or p.get("homepage", ""),
                "git_url": body.git_url or p.get("git_url", ""),
                "icon_url": body.icon_url if body.icon_url is not None else p.get("icon_url"),
            })
            save_plugins(plugins)
            return plugins[i]

    raise HTTPException(status_code=404, detail="Plugin not found")


# ---- Admin Endpoints (require X-Admin-Key) ----

@app.post("/admin/plugins/sync", status_code=200)
def admin_sync_plugin(
    body: AdminPluginSync,
    x_admin_key: Optional[str] = Header(None),
):
    """
    Upsert a plugin record from the official GitHub plugin repository.

    Called automatically by GitHub Actions after a plugin PR is merged.
    Requires X-Admin-Key header.

    Behavior:
    - If plugin does not exist: create new record (download_url = GitHub Release asset URL).
    - If plugin exists: update version, description, tags, download_url.
      Ownership check: github_user must match the original submitter (or be empty).
    - Preserves likes count on update.
    """
    _require_admin(x_admin_key)

    plugins = load_plugins()

    # Resolve plugin_id
    plugin_id = (body.plugin_id or name_to_id(body.name)).strip()
    if not plugin_id:
        raise HTTPException(status_code=400, detail="Cannot derive plugin_id from name")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Check if plugin already exists
    for i, p in enumerate(plugins):
        if p["id"] == plugin_id:
            # Ownership check: if github_user is set on record, new sync must come from same user
            existing_github_user = p.get("github_user", "")
            if existing_github_user and body.github_user.lower() != existing_github_user.lower():
                raise HTTPException(
                    status_code=403,
                    detail=f"Ownership mismatch: plugin '{plugin_id}' is owned by GitHub user "
                           f"'{existing_github_user}'. Sync rejected.",
                )
            plugins[i].update({
                "version": body.version.strip(),
                "author": body.author.strip(),
                "github_user": body.github_user.strip(),
                "description": body.description.strip(),
                "is_featured": body.is_featured, # Update featured status
                "name_zh": body.name_zh.strip() if body.name_zh else p.get("name_zh"),
                "description_zh": body.description_zh.strip() if body.description_zh else p.get("description_zh"),
                "description_en": body.description_en.strip() if body.description_en else p.get("description_en"),
                "tags": [t.strip() for t in body.tags if t.strip()],
                "type": body.type,
                "download_url": body.download_url.strip(),
                "git_url": body.git_url or p.get("git_url", ""),
                "homepage": body.homepage or p.get("homepage", ""),
                "icon_url": body.icon_url if body.icon_url is not None else p.get("icon_url"),
                "updated_at": now,
            })
            save_plugins(plugins)
            return {"action": "updated", "plugin": plugins[i]}

    # New plugin — ensure id is unique
    existing_ids = {p["id"] for p in plugins}
    if plugin_id in existing_ids:
        # id conflict (shouldn't happen since we just checked by id), resolve
        counter = 2
        while f"{plugin_id}_{counter}" in existing_ids:
            counter += 1
        plugin_id = f"{plugin_id}_{counter}"

    new_plugin = {
        "id": plugin_id,
        "name": body.name.strip(),
        "version": body.version.strip(),
        "author": body.author.strip(),
        "github_user": body.github_user.strip(),
        "description": body.description.strip(),
        "name_zh": body.name_zh.strip() if body.name_zh else None,
        "description_zh": body.description_zh.strip() if body.description_zh else None,
        "description_en": body.description_en.strip() if body.description_en else None,
        "tags": [t.strip() for t in body.tags if t.strip()],
        "type": body.type,
        "created_at": now,
        "updated_at": now,
        "is_featured": body.is_featured,
        "likes": 0,
        "icon_url": body.icon_url,
        "git_url": body.git_url or "",
        "homepage": body.homepage or "",
        "download_url": body.download_url.strip(),
    }
    plugins.append(new_plugin)
    save_plugins(plugins)
    return {"action": "created", "plugin": new_plugin}


@app.delete("/admin/plugins/{plugin_id}")
def admin_delete_plugin(
    plugin_id: str,
    x_admin_key: Optional[str] = Header(None),
):
    """Remove a plugin from the registry (admin only). Does not uninstall from users."""
    _require_admin(x_admin_key)
    plugins = load_plugins()
    new_list = [p for p in plugins if p["id"] != plugin_id]
    if len(new_list) == len(plugins):
        raise HTTPException(status_code=404, detail="Plugin not found")
    save_plugins(new_list)
    return {"ok": True, "deleted": plugin_id}


if __name__ == "__main__":
    import uvicorn
    from opensquad.system_config import syscfg

    uvicorn.run(app, host="0.0.0.0", port=syscfg.port("registry"))
