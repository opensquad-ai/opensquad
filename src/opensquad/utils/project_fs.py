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


# Heavy / generated dirs skipped during full-tree walk so the UI can load instantly.
_TREE_SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".next",
        ".turbo",
        ".cache",
        "dist",
        "build",
        "coverage",
        "target",
        ".idea",
        ".vs",
    }
)

_MAX_TREE_ENTRIES = 10_000


def list_tree(
    root: str,
    *,
    max_entries: int = _MAX_TREE_ENTRIES,
    skip_heavy: bool = True,
) -> dict[str, Any]:
    """Recursively list project files/dirs as a flat path list (cap *max_entries*).

    Does not read file contents. Returns flat entries with relative paths.
    """
    root_abs = os.path.normcase(os.path.abspath(root))
    if not os.path.isdir(root_abs):
        return {"error": f"Not a directory: {root}", "status": 404}

    limit = max(1, min(int(max_entries or _MAX_TREE_ENTRIES), _MAX_TREE_ENTRIES))
    entries: list[dict[str, Any]] = []
    truncated = False
    skipped: list[str] = []

    # Stack DFS: (abs_dir, rel_dir). Push dirs reversed for alpha order.
    stack: list[tuple[str, str]] = [(root_abs, "")]

    while stack:
        abs_dir, rel_dir = stack.pop()
        try:
            names = os.listdir(abs_dir)
        except OSError:
            continue

        dirs: list[str] = []
        files: list[str] = []
        for name in names:
            if name in (".", ".."):
                continue
            full = os.path.join(abs_dir, name)
            try:
                if os.path.isdir(full):
                    if skip_heavy and name in _TREE_SKIP_DIRS:
                        rel_skip = f"{rel_dir}/{name}" if rel_dir else name
                        rel_skip = rel_skip.replace("\\", "/")
                        skipped.append(rel_skip)
                        if len(entries) >= limit:
                            truncated = True
                            break
                        entries.append(
                            {
                                "path": rel_skip,
                                "name": name,
                                "type": "dir",
                                "size": None,
                                "skipped": True,
                            }
                        )
                        continue
                    dirs.append(name)
                else:
                    files.append(name)
            except OSError:
                continue
        if truncated:
            break

        dirs.sort(key=str.lower)
        files.sort(key=str.lower)

        for name in reversed(dirs):
            child_rel = f"{rel_dir}/{name}" if rel_dir else name
            child_rel = child_rel.replace("\\", "/")
            if len(entries) >= limit:
                truncated = True
                break
            entries.append(
                {
                    "path": child_rel,
                    "name": name,
                    "type": "dir",
                    "size": None,
                }
            )
            stack.append((os.path.join(abs_dir, name), child_rel))
        if truncated:
            break

        for name in files:
            child_rel = f"{rel_dir}/{name}" if rel_dir else name
            child_rel = child_rel.replace("\\", "/")
            if len(entries) >= limit:
                truncated = True
                break
            size = None
            try:
                size = os.path.getsize(os.path.join(abs_dir, name))
            except OSError:
                pass
            entries.append(
                {
                    "path": child_rel,
                    "name": name,
                    "type": "file",
                    "size": size,
                }
            )
        if truncated:
            break

    return {
        "cwd": root_abs,
        "path": "",
        "absolute": root_abs,
        "entries": entries,
        "count": len(entries),
        "truncated": truncated,
        "max_entries": limit,
        "skipped": skipped[:50],
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


def _rel_of(root_abs: str, abs_path: str) -> str:
    try:
        rel = os.path.relpath(abs_path, root_abs)
    except ValueError:
        rel = "."
    if rel in (".", ""):
        return ""
    return rel.replace("\\", "/")


def write_file(root: str, rel_path: str | None, content: str = "") -> dict[str, Any]:
    """Create or overwrite a text file under *root*."""
    root_abs = os.path.normcase(os.path.abspath(root))
    if not rel_path or not str(rel_path).strip():
        return {"error": "path is required", "status": 400}
    target = resolve_under_root(root_abs, rel_path)
    if target is None:
        return {"error": "Path outside project root", "status": 403}
    if os.path.isdir(target):
        return {"error": "Path is a directory", "status": 400}
    parent = os.path.dirname(target)
    try:
        os.makedirs(parent, exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as f:
            f.write(content if content is not None else "")
    except OSError as e:
        return {"error": str(e), "status": 500}
    return {
        "ok": True,
        "cwd": root_abs,
        "path": _rel_of(root_abs, target),
        "absolute": target,
        "type": "file",
    }


def mkdir(root: str, rel_path: str | None) -> dict[str, Any]:
    """Create a directory (and parents) under *root*."""
    root_abs = os.path.normcase(os.path.abspath(root))
    if not rel_path or not str(rel_path).strip():
        return {"error": "path is required", "status": 400}
    target = resolve_under_root(root_abs, rel_path)
    if target is None:
        return {"error": "Path outside project root", "status": 403}
    if os.path.exists(target):
        if os.path.isdir(target):
            return {
                "ok": True,
                "cwd": root_abs,
                "path": _rel_of(root_abs, target),
                "absolute": target,
                "type": "dir",
                "existed": True,
            }
        return {"error": "A file already exists at this path", "status": 409}
    try:
        os.makedirs(target, exist_ok=False)
    except OSError as e:
        return {"error": str(e), "status": 500}
    return {
        "ok": True,
        "cwd": root_abs,
        "path": _rel_of(root_abs, target),
        "absolute": target,
        "type": "dir",
    }


def delete_path(root: str, rel_path: str | None) -> dict[str, Any]:
    """Delete a file or empty/non-empty directory under *root*."""
    import shutil

    root_abs = os.path.normcase(os.path.abspath(root))
    if not rel_path or not str(rel_path).strip():
        return {"error": "path is required", "status": 400}
    target = resolve_under_root(root_abs, rel_path)
    if target is None:
        return {"error": "Path outside project root", "status": 403}
    if os.path.normcase(target) == root_abs:
        return {"error": "Cannot delete project root", "status": 403}
    if not os.path.exists(target):
        return {"error": "Path not found", "status": 404}
    kind = "dir" if os.path.isdir(target) else "file"
    try:
        if kind == "dir":
            shutil.rmtree(target)
        else:
            os.remove(target)
    except OSError as e:
        return {"error": str(e), "status": 500}
    return {
        "ok": True,
        "cwd": root_abs,
        "path": str(rel_path).replace("\\", "/"),
        "type": kind,
        "deleted": True,
    }


def rename_path(root: str, from_path: str | None, to_path: str | None) -> dict[str, Any]:
    """Rename or move a path within *root*."""
    root_abs = os.path.normcase(os.path.abspath(root))
    if not from_path or not str(from_path).strip():
        return {"error": "from is required", "status": 400}
    if not to_path or not str(to_path).strip():
        return {"error": "to is required", "status": 400}
    src = resolve_under_root(root_abs, from_path)
    dst = resolve_under_root(root_abs, to_path)
    if src is None or dst is None:
        return {"error": "Path outside project root", "status": 403}
    if os.path.normcase(src) == root_abs:
        return {"error": "Cannot rename project root", "status": 403}
    if not os.path.exists(src):
        return {"error": "Source not found", "status": 404}
    if os.path.exists(dst):
        return {"error": "Destination already exists", "status": 409}
    parent = os.path.dirname(dst)
    try:
        os.makedirs(parent, exist_ok=True)
        os.rename(src, dst)
    except OSError as e:
        return {"error": str(e), "status": 500}
    return {
        "ok": True,
        "cwd": root_abs,
        "from": _rel_of(root_abs, src),
        "to": _rel_of(root_abs, dst),
        "absolute": dst,
        "type": "dir" if os.path.isdir(dst) else "file",
    }


def reveal_in_os(root: str, rel_path: str | None = None) -> dict[str, Any]:
    """Reveal path in OS file manager (Explorer / Finder / xdg-open)."""
    import subprocess
    import sys

    root_abs = os.path.normcase(os.path.abspath(root))
    target = resolve_under_root(root_abs, rel_path) if rel_path else root_abs
    if target is None:
        return {"error": "Path outside project root", "status": 403}
    if not os.path.exists(target):
        return {"error": "Path not found", "status": 404}
    try:
        if sys.platform == "win32":
            if os.path.isdir(target):
                subprocess.Popen(["explorer", target], shell=False)
            else:
                subprocess.Popen(["explorer", f"/select,{target}"], shell=False)
        elif sys.platform == "darwin":
            if os.path.isdir(target):
                subprocess.Popen(["open", target])
            else:
                subprocess.Popen(["open", "-R", target])
        else:
            folder = target if os.path.isdir(target) else os.path.dirname(target)
            subprocess.Popen(["xdg-open", folder])
    except Exception as e:
        return {"error": str(e), "status": 500}
    return {"ok": True, "path": _rel_of(root_abs, target), "absolute": target}


def open_in_terminal(root: str, rel_path: str | None = None) -> dict[str, Any]:
    """Open a terminal at the given directory (file → parent dir)."""
    import subprocess
    import sys

    root_abs = os.path.normcase(os.path.abspath(root))
    target = resolve_under_root(root_abs, rel_path) if rel_path else root_abs
    if target is None:
        return {"error": "Path outside project root", "status": 403}
    if not os.path.exists(target):
        return {"error": "Path not found", "status": 404}
    folder = target if os.path.isdir(target) else os.path.dirname(target)
    try:
        if sys.platform == "win32":
            # Prefer Windows Terminal when available
            try:
                subprocess.Popen(["wt", "-d", folder], shell=False)
            except FileNotFoundError:
                subprocess.Popen(
                    ["cmd", "/c", "start", "cmd", "/k", f'cd /d "{folder}"'],
                    shell=False,
                )
        elif sys.platform == "darwin":
            subprocess.Popen(
                ["open", "-a", "Terminal", folder],
            )
        else:
            for cmd in (
                ["x-terminal-emulator", "--working-directory", folder],
                ["gnome-terminal", f"--working-directory={folder}"],
                ["konsole", "--workdir", folder],
                ["xfce4-terminal", f"--working-directory={folder}"],
            ):
                try:
                    subprocess.Popen(cmd)
                    break
                except FileNotFoundError:
                    continue
            else:
                return {"error": "No terminal emulator found", "status": 500}
    except Exception as e:
        return {"error": str(e), "status": 500}
    return {"ok": True, "path": _rel_of(root_abs, folder), "absolute": folder}


def list_changed(root: str) -> dict[str, Any]:
    """List git-changed files under *root* (porcelain). Empty if not a git repo."""
    import subprocess

    root_abs = os.path.normcase(os.path.abspath(root))
    if not os.path.isdir(os.path.join(root_abs, ".git")):
        # Also accept git worktrees where .git is a file
        git_marker = os.path.join(root_abs, ".git")
        if not os.path.exists(git_marker):
            return {"cwd": root_abs, "entries": [], "git": False}

    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "-uall"],
            cwd=root_abs,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except Exception as e:
        return {"cwd": root_abs, "entries": [], "git": True, "error": str(e)}

    if proc.returncode != 0:
        return {
            "cwd": root_abs,
            "entries": [],
            "git": True,
            "error": (proc.stderr or proc.stdout or "git status failed").strip(),
        }

    entries: list[dict[str, Any]] = []
    for line in (proc.stdout or "").splitlines():
        if not line or len(line) < 4:
            continue
        code = line[:2]
        rest = line[3:].strip()
        # Handle renames: "R  old -> new"
        if " -> " in rest:
            rest = rest.split(" -> ", 1)[-1].strip()
        rest = rest.strip('"')
        full = os.path.join(root_abs, rest.replace("/", os.sep))
        kind = "dir" if os.path.isdir(full) else "file"
        entries.append(
            {
                "name": os.path.basename(rest) or rest,
                "path": rest.replace("\\", "/"),
                "type": kind,
                "status": code.strip() or code,
            }
        )
    return {"cwd": root_abs, "entries": entries, "git": True}
