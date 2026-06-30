"""
Filesystem Tools v5.0 (Enhanced Safety & Usability)
Provides safe filesystem operation capabilities including grep, glob, paginated reading, and directory creation.
"""

import fnmatch
import glob as glob_mod
import logging
import os
import re
import shutil
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

from opensquad.utils.path_utils import get_workspace_root
from opensquad.utils.path_utils import is_path_safe as _is_path_safe_unified

try:
    from ..system_config import syscfg
except Exception:
    syscfg = None


def _get_workspace_root() -> str:
    """Get current workspace root (delegates to opensquad.utils.path_utils)."""
    return get_workspace_root()


# Filesystem root (workspace-aware)
_PROJECT_ROOT = _get_workspace_root()

# Per-agent extra allowed working directory whitelist (injected by agents_boot.py at startup via set_allowed_dirs())
_EXTRA_ALLOWED_DIRS: list[str] = []

# Agent's config.json path (injected by agents_boot.py at startup via set_config_path())
# Used by add_allowed_dir() to persist new directories back to config.json
_CONFIG_PATH: str | None = None

# Default excluded directory names (exact match)
EXCLUDE_DIRS = {
    ".git",
    ".svn",
    ".hg",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "venv",
    ".venv",
    "env",
    ".env",
    "dist",
    "build",
    ".next",
    ".nuxt",
    ".idea",
    ".vscode",
    "coverage",
    ".nyc_output",
    "eggs",
}

# Default excluded directory suffixes (fnmatch patterns)
EXCLUDE_DIR_PATTERNS = ["*.egg-info"]


def set_allowed_dirs(dirs: list[str]) -> None:
    """
    Set the extra allowed working directory whitelist (called by agents_boot.py at startup).

    Whitelist directories are added on top of the workspace root. Specifying a parent
    directory automatically allows all its subdirectories.
    Supports absolute and relative paths (relative paths are resolved against workspace root).

    Args:
        dirs: List of directory paths to allow access to.
    """
    global _EXTRA_ALLOWED_DIRS
    root = _get_workspace_root()
    resolved = []
    for d in dirs:
        if d:
            p = d if os.path.isabs(d) else os.path.join(root, d)
            resolved.append(os.path.normcase(os.path.abspath(p)))
    _EXTRA_ALLOWED_DIRS = resolved
    # Sync unified path_utils for cross-module path safety checks
    from opensquad.utils.path_utils import set_allowed_dirs as _set_allowed_dirs_unified

    _set_allowed_dirs_unified(dirs)
    logger.info(f"[filesystem] Extra allowed dirs: {_EXTRA_ALLOWED_DIRS}")


def set_config_path(config_path: str) -> None:
    """
    Set the agent's config.json path (called by agents_boot.py at startup).
    Allows add_allowed_dir() to persist new directories back to config.json.

    Args:
        config_path: Absolute path to config.json.
    """
    global _CONFIG_PATH
    _CONFIG_PATH = config_path
    logger.info(f"[filesystem] Config path set to: {_CONFIG_PATH}")


def _should_exclude_dir(dirname: str) -> bool:
    """Check whether a directory name should be excluded."""
    if dirname in EXCLUDE_DIRS or dirname.startswith("."):
        return True
    return any(fnmatch.fnmatch(dirname, pat) for pat in EXCLUDE_DIR_PATTERNS)


def _is_path_safe(path: str) -> bool:
    """
    Check path safety.
    Delegates to opensquad.utils.path_utils.is_path_safe which supports
    workspace root and whitelist directories.
    """
    return _is_path_safe_unified(path, extra_allowed_dirs=_EXTRA_ALLOWED_DIRS)


def add_allowed_dir(path: str) -> dict[str, Any]:
    """
    Dynamically add a working directory to the whitelist (takes effect immediately at runtime)
    and persist it back to config.json.

    Takes effect immediately without restarting the agent. Also writes the directory to the
    agent's config.json under the filesystem.workspace_dirs field so it is loaded automatically
    on the next startup.

    Args:
        path: Directory path to add (absolute or relative path).
              Relative paths are resolved against workspace root.
              Example: "C:\\\\work\\\\shared_data" or "../shared_data"
    """
    global _EXTRA_ALLOWED_DIRS, _CONFIG_PATH

    if not path or not path.strip():
        return {"status": "error", "message": "Path cannot be empty."}

    # Resolve to absolute path and normalize
    resolved = os.path.normcase(os.path.abspath(path.strip()))

    # Check whether the directory exists (allow pre-configuring non-existent directories, with a warning)
    dir_exists = os.path.isdir(resolved)

    # Add to in-memory whitelist (deduplicated)
    if resolved not in _EXTRA_ALLOWED_DIRS:
        _EXTRA_ALLOWED_DIRS.append(resolved)
        logger.info(f"[filesystem] Added to allowed dirs: {resolved}")

    # Persist back to config.json
    persisted = False
    persist_error = None
    if _CONFIG_PATH and os.path.isfile(_CONFIG_PATH):
        try:
            import json as _json

            with open(_CONFIG_PATH, encoding="utf-8") as f:
                cfg = _json.load(f)

            # Ensure filesystem.workspace_dirs field exists
            if "filesystem" not in cfg or not isinstance(cfg["filesystem"], dict):
                cfg["filesystem"] = {}
            existing = cfg["filesystem"].get("workspace_dirs", [])
            if not isinstance(existing, list):
                existing = []

            # Write original path (preserve user input format), deduplicated
            raw = path.strip()
            if raw not in existing:
                existing.append(raw)
                cfg["filesystem"]["workspace_dirs"] = existing
                with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
                    _json.dump(cfg, f, ensure_ascii=False, indent=2)
                persisted = True
                logger.info(f"[filesystem] Persisted workspace_dir to config.json: {raw}")
            else:
                persisted = True  # already in config
        except Exception as e:
            persist_error = str(e)
            logger.warning(f"[filesystem] Failed to persist workspace_dir: {e}")
    else:
        persist_error = "config.json path not set or file not found"

    result: dict[str, Any] = {
        "status": "success",
        "message": f"Directory '{resolved}' added to whitelist.",
        "resolved_path": resolved,
        "dir_exists": dir_exists,
        "persisted_to_config": persisted,
        "current_allowed_dirs": list(_EXTRA_ALLOWED_DIRS),
    }
    if not dir_exists:
        result["warning"] = f"Directory does not exist yet: {resolved}. It will be accessible once created."
    if persist_error:
        result["persist_warning"] = f"Could not persist to config.json: {persist_error}. Change is in-memory only."
    return result


def list_directory(path: str = ".") -> dict[str, Any]:
    """
    List directory contents.
    """
    if not _is_path_safe(path):
        return {"status": "error", "message": "Security Denied: Path outside project."}

    try:
        items = os.listdir(path)
        # BUGFIX: filter out symlinks that escape the project workspace
        _PROJECT_ROOT = os.path.realpath(os.getcwd())
        safe_items = []
        for item in items:
            full_path = os.path.join(path, item)
            if os.path.islink(full_path):
                real_target = os.path.realpath(full_path)
                root = os.path.realpath(_PROJECT_ROOT)
                if not (real_target == root or real_target.startswith(root + os.sep)):
                    safe_items.append(f"{item} -> (external symlink, hidden)")
                    continue
            safe_items.append(item)
        return {
            "status": "success",
            "data": {
                "directories": [d for d in safe_items if os.path.isdir(os.path.join(path, d))],
                "files": [f for f in safe_items if os.path.isfile(os.path.join(path, f))],
            },
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


def read_file(path: str, start_line: int = 1, end_line: int = -1, max_lines: int = 200) -> dict[str, Any]:
    """
    Read file content with pagination support; output includes line numbers.

    Args:
        path: File path.
        start_line: Starting line number (1-indexed), default 1.
        end_line: Ending line number (-1 means auto-limit to max_lines), default -1.
        max_lines: Maximum lines to read when end_line=-1, default 200. Set to -1 to read all.
    """
    if not _is_path_safe(path):
        return {"status": "error", "message": "Security Denied: Path outside project."}

    try:
        if not os.path.isfile(path):
            return {"status": "error", "message": "File not found."}

        # Use utf-8-sig to handle Windows BOM files written by write_file
        with open(path, encoding="utf-8-sig", errors="ignore") as f:
            lines = f.readlines()

        total_lines = len(lines)
        start_idx = max(0, start_line - 1)

        if end_line == -1:
            # Smart default: read at most max_lines (unless max_lines=-1 means all)
            end_idx = min(total_lines, start_idx + max_lines) if max_lines > 0 else total_lines
        else:
            end_idx = min(total_lines, end_line)

        # If the read range exceeds the file
        if start_idx >= total_lines:
            content = ""
            truncated = False
        else:
            # Output with line numbers
            numbered_lines = []
            for i in range(start_idx, end_idx):
                numbered_lines.append(f"{i + 1}: {lines[i].rstrip()}")
            content = "\n".join(numbered_lines)
            truncated = end_idx < total_lines

        result = {
            "status": "success",
            "content": content,
            "meta": {"total_lines": total_lines, "read_range": f"{start_idx + 1}-{end_idx}", "truncated": truncated},
        }
        if truncated:
            result["meta"]["hint"] = f"File has {total_lines} lines total. Use start_line={end_idx + 1} to read more."
        return result
    except Exception as e:
        return {"status": "error", "message": str(e)}


def search_files(
    path: str = ".",
    pattern: str = "",
    include: str = "*",
    case_sensitive: bool = False,
    context_lines: int = 0,
    max_results: int = 100,
) -> dict[str, Any]:
    """
    Full-text search (Grep). Search file contents in the specified directory and subdirectories
    for lines matching a regular expression. Automatically skips node_modules, __pycache__,
    .git, venv, and other common irrelevant directories.
    If ripgrep (rg) is installed on the system, it will be used automatically to speed up search.

    Args:
        path: Root directory to search, default ".".
        pattern: Regular expression match pattern (Regex).
        include: Filename match pattern (Glob), e.g. "*.py". Default "*".
        case_sensitive: Whether to be case-sensitive, default False.
        context_lines: Show N lines of context before and after each match (like grep -C N), default 0.
        max_results: Maximum number of matches to return, default 100.
    """
    if not _is_path_safe(path):
        return {"status": "error", "message": "Security Denied: Path outside project."}

    if not pattern:
        return {"status": "error", "message": "Pattern cannot be empty."}

    # Try ripgrep first
    rg_path = shutil.which("rg")
    if rg_path:
        return _search_with_ripgrep(rg_path, path, pattern, include, case_sensitive, context_lines, max_results)

    # Fall back to pure Python implementation
    return _search_pure_python(path, pattern, include, case_sensitive, context_lines, max_results)


def _search_with_ripgrep(
    rg_path: str, path: str, pattern: str, include: str, case_sensitive: bool, context_lines: int, max_results: int
) -> dict[str, Any]:
    """Use ripgrep for fast search."""
    try:
        cmd = [rg_path, "--line-number", "--no-heading", "--color=never", f"--max-count={max_results}"]

        if not case_sensitive:
            cmd.append("--ignore-case")
        if context_lines > 0:
            cmd.append(f"-C{context_lines}")
        if include != "*":
            cmd.extend(["--glob", include])

        cmd.extend([pattern, path])

        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)

        lines = result.stdout.strip().split("\n") if result.stdout.strip() else []

        # Convert to relative paths
        base_cwd = os.getcwd()
        results = []
        for line in lines[:max_results]:
            if line.strip():
                # ripgrep output format: path:line:content
                try:
                    parts = line.split(":", 2)
                    if len(parts) >= 2:
                        rel = os.path.relpath(parts[0], base_cwd)
                        rest = ":".join(parts[1:])
                        results.append(f"{rel}:{rest[:150]}")
                    else:
                        results.append(line[:200])
                except Exception:
                    results.append(line[:200])

        return {
            "status": "success",
            "matches": results,
            "count": len(results),
            "files_searched": -1,  # ripgrep does not report file count
            "truncated": len(results) >= max_results,
            "engine": "ripgrep",
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "ripgrep search timed out after 30s"}
    except Exception as e:
        logger.warning(f"ripgrep failed, falling back to Python: {e}")
        return _search_pure_python(path, pattern, include, case_sensitive, context_lines, max_results)


def _search_pure_python(
    path: str, pattern: str, include: str, case_sensitive: bool, context_lines: int, max_results: int
) -> dict[str, Any]:
    """Pure Python search implementation (fallback when ripgrep is unavailable)."""
    results = []
    files_searched = 0
    try:
        flags = 0 if case_sensitive else re.IGNORECASE
        regex = re.compile(pattern, flags)

        matches_count = 0

        for root, dirs, files in os.walk(path):
            # Filter excluded directories
            dirs[:] = [d for d in dirs if not _should_exclude_dir(d)]

            for file in files:
                if not fnmatch.fnmatch(file, include):
                    continue

                file_path = os.path.join(root, file)
                files_searched += 1
                try:
                    with open(file_path, encoding="utf-8", errors="ignore") as f:
                        all_lines = f.readlines()

                    for i, line in enumerate(all_lines):
                        if regex.search(line):
                            rel_path = os.path.relpath(file_path, os.getcwd())

                            if context_lines > 0:
                                ctx_start = max(0, i - context_lines)
                                ctx_end = min(len(all_lines), i + context_lines + 1)
                                ctx_block = []
                                for ci in range(ctx_start, ctx_end):
                                    prefix = ">>>" if ci == i else "   "
                                    ctx_block.append(f"  {prefix} {ci + 1}: {all_lines[ci].rstrip()[:120]}")
                                results.append(f"{rel_path}:{i + 1}:\n" + "\n".join(ctx_block))
                            else:
                                results.append(f"{rel_path}:{i + 1}: {line.strip()[:150]}")

                            matches_count += 1
                            if matches_count >= max_results:
                                break
                except Exception:
                    continue

            if matches_count >= max_results:
                break

        return {
            "status": "success",
            "matches": results,
            "count": matches_count,
            "files_searched": files_searched,
            "truncated": matches_count >= max_results,
            "engine": "python",
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# Alias: grep = search_files
grep = search_files


def find_files(path: str = ".", pattern: str = "**/*", sort_by: str = "name", max_results: int = 200) -> dict[str, Any]:
    """
    Find files (Glob). Find file paths matching a filename pattern.
    Automatically skips node_modules, __pycache__, .git, venv, and other common irrelevant directories.

    Args:
        path: Root directory, default ".".
        pattern: Glob pattern, e.g. "**/*.py" or "src/components/*.tsx".
        sort_by: Sort order: "name" (default, by name) or "mtime" (by modification time, newest first).
        max_results: Maximum number of results, default 200.
    """
    if not _is_path_safe(path):
        return {"status": "error", "message": "Security Denied: Path outside project."}

    try:
        full_search_pattern = os.path.join(path, pattern)
        files = glob_mod.glob(full_search_pattern, recursive=True)

        base_cwd = os.getcwd()
        rel_files = []

        for f in files:
            if not _is_path_safe(f) or not os.path.isfile(f):
                continue

            # Check if the path contains any excluded directories
            rel = os.path.relpath(f, base_cwd)
            parts = rel.replace("\\", "/").split("/")
            if any(_should_exclude_dir(p) for p in parts):
                continue

            rel_files.append(rel)

        # Sort
        if sort_by == "mtime":
            rel_files.sort(key=lambda f: os.path.getmtime(f), reverse=True)
        else:
            rel_files.sort()

        total_found = len(rel_files)
        truncated = total_found > max_results

        return {"status": "success", "files": rel_files[:max_results], "count": total_found, "truncated": truncated}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# Alias: glob = find_files
glob = find_files


def write_file(path: str, content: str) -> dict[str, Any]:
    """
    Write file (overwrite).
    """
    if not _is_path_safe(path):
        return {"status": "error", "message": "Security Denied: Path outside project."}

    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        # Use plain utf-8 (no BOM). The earlier utf-8-sig writer was used to
        # handle Windows BOMs from external editors, but writing with utf-8-sig
        # *adds* a BOM to every file produced by this tool — which then breaks
        # downstream json.load() callers that read with plain "utf-8"
        # (notably all mcp_config.json / mcp_global.json handlers). Read paths
        # elsewhere now use "utf-8-sig" so they are BOM-tolerant, and writers
        # should stay BOM-free so freshly produced files round-trip cleanly.
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"status": "success", "message": f"File '{path}' written successfully."}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def replace_in_file(path: str, old_str: str, new_str: str, replace_all: bool = False) -> dict[str, Any]:
    """
    Replace a string in a file. By default, only replaces the first match.

    Args:
        path: File path.
        old_str: String to be replaced.
        new_str: Replacement string.
        replace_all: Whether to replace all occurrences, default False (only first).
    """
    if not _is_path_safe(path):
        return {"status": "error", "message": "Security Denied: Path outside project."}

    try:
        # Use utf-8-sig to handle Windows BOM files written by write_file
        with open(path, encoding="utf-8-sig", errors="ignore") as f:
            content = f.read()

        if old_str not in content:
            return {"status": "error", "message": "Target string not found in file."}

        count = content.count(old_str)

        if replace_all or count == 1:
            new_content = content.replace(old_str, new_str)
            replaced = count
        else:
            # Replace only the first one
            new_content = content.replace(old_str, new_str, 1)
            replaced = 1

        # See write_file() for why we use plain utf-8 here: we must not add a
        # BOM on write, otherwise subsequent json.load() calls (which read with
        # utf-8-sig but still treat the BOM as content) will see corrupted data.
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)

        msg = f"Replaced {replaced} of {count} occurrences in '{path}'."
        if count > 1 and not replace_all:
            msg += f" WARNING: {count - 1} more occurrences remain. Set replace_all=True to replace all."

        return {"status": "success", "message": msg, "total_matches": count, "replaced": replaced}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def delete_file(path: str) -> dict[str, Any]:
    """
    Delete the specified file.
    """
    if not _is_path_safe(path):
        return {"status": "error", "message": "Security Denied: Path outside project."}

    try:
        if os.path.isfile(path):
            os.remove(path)
            return {"status": "success", "message": f"Deleted file '{path}'."}
        return {"status": "error", "message": "Not a file or file not found."}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def create_directory(path: str, parents: bool = True) -> dict[str, Any]:
    """
    Create a directory.

    Args:
        path: Directory path.
        parents: Whether to automatically create parent directories, default True.
    """
    if not _is_path_safe(path):
        return {"status": "error", "message": "Security Denied: Path outside project."}

    try:
        if os.path.exists(path):
            if os.path.isdir(path):
                return {"status": "success", "message": f"Directory '{path}' already exists."}
            return {"status": "error", "message": f"Path '{path}' exists but is not a directory."}

        if parents:
            os.makedirs(path, exist_ok=True)
        else:
            os.mkdir(path)
        return {"status": "success", "message": f"Directory '{path}' created."}
    except Exception as e:
        return {"status": "error", "message": str(e)}
