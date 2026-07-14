"""Project file browse helpers for Launcher Agent-Web file panel.

Sandboxed under an agent active cwd (session_cwd or workspace root).
"""

from __future__ import annotations

import base64
import os
from typing import Any

# Max bytes returned by text read (UI preview).
_MAX_READ_BYTES = 500_000
# Images can be larger; still capped for UI safety (base64 over JSON).
_MAX_IMAGE_BYTES = 8_000_000

_IMAGE_MIME: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}

_TEXT_EXTS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".json",
    ".md",
    ".txt",
    ".yml",
    ".yaml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".css",
    ".scss",
    ".html",
    ".htm",
    ".xml",
    ".svg",
    ".sh",
    ".bash",
    ".zsh",
    ".ps1",
    ".bat",
    ".cmd",
    ".sql",
    ".rs",
    ".go",
    ".java",
    ".kt",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cs",
    ".rb",
    ".php",
    ".swift",
    ".r",
    ".lua",
    ".vue",
    ".svelte",
    ".env",
    ".gitignore",
    ".dockerignore",
    ".editorconfig",
    ".csv",
    ".log",
}


def resolve_agent_root(agent_dir: str, workspace_root: str) -> str:
    """Return absolute active cwd for an agent (session override or workspace)."""
    session_cwd = ""
    try:
        from opensquad.utils.session_cwd import read_session_cwd

        data = read_session_cwd(agent_dir)
        if data:
            session_cwd = str(data.get("path") or "").strip()
    except Exception:
        pass
    if session_cwd and os.path.isdir(session_cwd):
        return os.path.normcase(os.path.abspath(session_cwd))
    if workspace_root and os.path.isdir(workspace_root):
        return os.path.normcase(os.path.abspath(workspace_root))
    return os.path.normcase(os.path.abspath(os.getcwd()))


def _under_root(root: str, abs_path: str) -> bool:
    try:
        root_n = os.path.normcase(os.path.abspath(root))
        path_n = os.path.normcase(os.path.abspath(abs_path))
        return os.path.commonpath([root_n, path_n]) == root_n
    except Exception:
        return False


def resolve_under_root(root: str, rel_or_abs: str | None) -> str | None:
    """Resolve *rel_or_abs* under *root*. Empty / '.' → root itself."""
    root_abs = os.path.normcase(os.path.abspath(root))
    raw = (rel_or_abs or "").strip()
    if not raw or raw in (".", "./"):
        return root_abs
    if os.path.isabs(raw):
        candidate = os.path.normcase(os.path.abspath(raw))
    else:
        # Allow absolute-looking paths that are still under root via join
        candidate = os.path.normcase(os.path.abspath(os.path.join(root_abs, raw)))
    if not _under_root(root_abs, candidate):
        return None
    return candidate


def list_dir(root: str, rel_path: str | None = None) -> dict[str, Any]:
    """List one directory level under *root*."""
    root_abs = os.path.normcase(os.path.abspath(root))
    target = resolve_under_root(root_abs, rel_path)
    if target is None:
        return {"error": "Path outside project root", "status": 403}
    if not os.path.isdir(target):
        return {"error": f"Not a directory: {rel_path or '.'}", "status": 404}

    entries: list[dict[str, Any]] = []
    try:
        names = os.listdir(target)
    except OSError as e:
        return {"error": str(e), "status": 500}

    for name in names:
        if name in (".", ".."):
            continue
        full = os.path.join(target, name)
        try:
            is_dir = os.path.isdir(full)
            size = None if is_dir else os.path.getsize(full)
        except OSError:
            continue
        entries.append(
            {
                "name": name,
                "type": "dir" if is_dir else "file",
                "size": size,
            }
        )

    entries.sort(key=lambda e: (0 if e["type"] == "dir" else 1, e["name"].lower()))

    try:
        rel = os.path.relpath(target, root_abs)
    except ValueError:
        rel = "."
    if rel in (".", ""):
        rel = ""

    return {
        "cwd": root_abs,
        "path": rel.replace("\\", "/"),
        "absolute": target,
        "entries": entries,
    }


def _looks_text(path: str, sample: bytes) -> bool:
    ext = os.path.splitext(path)[1].lower()
    if ext in _TEXT_EXTS or ext == "":
        return b"\x00" not in sample
    # Unknown extension: allow if no NUL and mostly printable
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def read_file(root: str, rel_path: str | None) -> dict[str, Any]:
    """Read a text or image file under *root* (truncated for UI)."""
    root_abs = os.path.normcase(os.path.abspath(root))
    if not rel_path or not str(rel_path).strip():
        return {"error": "path is required", "status": 400}
    target = resolve_under_root(root_abs, rel_path)
    if target is None:
        return {"error": "Path outside project root", "status": 403}
    if not os.path.isfile(target):
        return {"error": f"Not a file: {rel_path}", "status": 404}

    try:
        rel = os.path.relpath(target, root_abs).replace("\\", "/")
    except ValueError:
        rel = os.path.basename(target)

    ext = os.path.splitext(target)[1].lower()
    mime = _IMAGE_MIME.get(ext)
    if mime:
        try:
            size = os.path.getsize(target)
            if size > _MAX_IMAGE_BYTES:
                return {
                    "error": f"Image too large to preview ({size} bytes, max {_MAX_IMAGE_BYTES})",
                    "status": 413,
                }
            with open(target, "rb") as f:
                raw = f.read()
        except OSError as e:
            return {"error": str(e), "status": 500}

        return {
            "cwd": root_abs,
            "path": rel,
            "absolute": target,
            "kind": "image",
            "mime": mime,
            "content_base64": base64.b64encode(raw).decode("ascii"),
            "content": "",
            "size": size,
            "truncated": False,
            "language": ext.lstrip(".") or "image",
        }

    try:
        size = os.path.getsize(target)
        with open(target, "rb") as f:
            raw = f.read(_MAX_READ_BYTES + 1)
    except OSError as e:
        return {"error": str(e), "status": 500}

    truncated = len(raw) > _MAX_READ_BYTES
    if truncated:
        raw = raw[:_MAX_READ_BYTES]
    if not _looks_text(target, raw[:4096]):
        return {"error": "Binary or non-text file cannot be previewed", "status": 415}

    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        content = raw.decode("utf-8", errors="replace")

    return {
        "cwd": root_abs,
        "path": rel,
        "absolute": target,
        "kind": "text",
        "content": content,
        "size": size,
        "truncated": truncated,
        "language": ext.lstrip(".") or "plaintext",
    }
