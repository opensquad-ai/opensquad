"""
Agent Self-Evolution Tools

Allows agents to manage their own capabilities at runtime:
  1. install_skill          -- Load a Skill from directory or Git URL
  2. remove_skill           -- Unload a Skill
  3. read_skill             -- Read skill content like reading a file (no attach/state)
  4. list_skills            -- View currently loaded skills
  5. list_installed         -- View currently installed MCP servers and Skills
  6. plugin_list            -- List locally enabled plugins (name + description)
  7. reload_plugins         -- Immediately reload plugins from disk without restarting
  8. publish_skill          -- Copy a skill to the public skill library for local development
"""

import json
import logging
import os
import re
import subprocess
import sys

try:
    from ..tool import logger
except ImportError:
    logger = logging.getLogger(__name__)

try:
    from ..system_config import syscfg
except ImportError:
    syscfg = None

# Project root (workspace-aware; frozen mode must never write to the read-only
# install dir, so we resolve against syscfg.workspace_skills_dir).
# _project_root is kept for read-only fallback discovery (publish_skill walks
# up to find pyproject.toml), but all *writes* go through syscfg.
_current_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(os.path.dirname(_current_dir))


# Directory for git-cloned skill repos (writable workspace path).
def _git_installs_dir() -> str:
    if syscfg is not None:
        return syscfg.workspace_skills_dir("_git")
    # Last-resort fallback: temp dir (never the read-only install dir).
    import tempfile

    return os.path.join(tempfile.gettempdir(), "opensquad_skills_git")


def _is_git_url(s: str) -> bool:
    """Return True if the string looks like a Git remote URL."""
    return bool(re.match(r"^(https?://|git@|ssh://)", s))


def _repo_name_from_url(url: str) -> str:
    """Extract a filesystem-safe repo name from a Git URL."""
    name = url.rstrip("/").split("/")[-1]
    name = re.sub(r"\.git$", "", name)
    # Replace any non-alphanumeric chars (except - and _) with _
    name = re.sub(r"[^\w\-]", "_", name)
    return name or "skill_repo"


def _clone_or_update(git_url: str) -> dict:
    """
    Clone a git repo to skills/_git/{repo_name}/ or pull if already present.

    Returns:
        {"success": bool, "clone_dir": str, "action": "cloned"|"updated", "error": str}
    """
    repo_name = _repo_name_from_url(git_url)
    _git_root = _git_installs_dir()
    clone_dest = os.path.join(_git_root, repo_name)

    os.makedirs(_git_root, exist_ok=True)

    if os.path.isdir(os.path.join(clone_dest, ".git")):
        # Already cloned -- update
        logger.info(f"[install_skill] Updating existing clone at {clone_dest}")
        result = subprocess.run(
            ["git", "pull"],
            cwd=clone_dest,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            # Non-fatal: repo might be dirty; log and continue with existing files
            logger.warning(f"[install_skill] git pull warning: {result.stderr.strip()}")
        return {"success": True, "clone_dir": clone_dest, "action": "updated"}
    else:
        # Fresh clone
        logger.info(f"[install_skill] Cloning {git_url} -> {clone_dest}")
        result = subprocess.run(
            ["git", "clone", "--depth=1", git_url, clone_dest],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            return {
                "success": False,
                "error": f"git clone failed: {result.stderr.strip() or result.stdout.strip()}",
            }
        return {"success": True, "clone_dir": clone_dest, "action": "cloned"}


def _install_pip_deps_from_skill_json(skill_dir: str) -> list:
    """
    Read skill.json and auto-install any pip dependencies declared under install[].
    Returns list of installed package names (empty if none / no skill.json).
    """
    skill_json_path = os.path.join(skill_dir, "skill.json")
    if not os.path.isfile(skill_json_path):
        return []

    try:
        with open(skill_json_path, encoding="utf-8") as f:
            meta = json.load(f)
    except Exception as e:
        logger.warning(f"[install_skill] Could not parse skill.json: {e}")
        return []

    installed = []
    for step in meta.get("install", []):
        if step.get("kind") == "pip":
            packages = [p for p in step.get("packages", []) if p]
            if packages:
                logger.info(f"[install_skill] pip install {packages}")
                proc = subprocess.run(
                    [sys.executable, "-m", "pip", "install", *packages],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                if proc.returncode == 0:
                    installed.extend(packages)
                else:
                    logger.warning(f"[install_skill] pip install {packages} failed: {proc.stderr.strip()}")
    return installed


def _find_public_skills_dir() -> tuple:
    """
    Locate the public skills library directory.
    Returns (path, is_writable) or (None, False) if not found.
    """
    # Method 1: _project_root/skills/ (standard src/ layout, matches agents_boot.py)
    candidate = os.path.join(_project_root, "skills")
    if os.path.isdir(candidate):
        try:
            test = os.path.join(candidate, ".publish_test")
            with open(test, "w") as f:
                f.write("")
            os.remove(test)
            return candidate, True
        except (OSError, PermissionError):
            return candidate, False

    # Method 2: Look for pyproject.toml above _project_root, then src/skills/
    current = os.path.abspath(_project_root)
    for _ in range(5):
        parent = os.path.dirname(current)
        if parent == current:
            break
        if os.path.isfile(os.path.join(parent, "pyproject.toml")):
            candidate = os.path.join(parent, "src", "skills")
            if os.path.isdir(candidate):
                try:
                    test = os.path.join(candidate, ".publish_test")
                    with open(test, "w") as f:
                        f.write("")
                    os.remove(test)
                    return candidate, True
                except (OSError, PermissionError):
                    return candidate, False
        current = parent

    return None, False


def _parse_frontmatter_name(filepath: str) -> str:
    """Extract the name field from a SKILL.md YAML frontmatter. Returns empty string if not found."""
    fm_pattern = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
    try:
        with open(filepath, encoding="utf-8") as f:
            raw = f.read()
    except Exception:
        return ""
    match = fm_pattern.match(raw)
    if not match:
        return ""
    for line in match.group(1).strip().split("\n"):
        line = line.strip()
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip()
    return ""


def _parse_frontmatter_full(filepath: str) -> dict:
    """Parse full YAML frontmatter from a SKILL.md file into a dict."""
    fm_pattern = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
    result = {}
    try:
        with open(filepath, encoding="utf-8") as f:
            raw = f.read()
    except Exception:
        return result
    match = fm_pattern.match(raw)
    if not match:
        return result
    for line in match.group(1).strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if value.lower() in ("true", "yes"):
                value = True
            elif value.lower() in ("false", "no"):
                value = False
            elif "," in value:
                value = [v.strip() for v in value.split(",") if v.strip()]
            result[key] = value
    return result


def _generate_skill_json(skill_dir: str, name: str) -> dict:
    """Generate a basic skill.json from SKILL.md frontmatter."""
    fm = _parse_frontmatter_full(os.path.join(skill_dir, "SKILL.md"))
    sj = {
        "name": name,
        "display_name": fm.get("display_name", fm.get("name", name)),
        "version": fm.get("version", "1.0.0"),
        "description": fm.get("description", ""),
    }
    author = fm.get("author")
    if author:
        sj["author"] = author
    tags = fm.get("tags")
    if isinstance(tags, list) and tags:
        sj["tags"] = tags
    elif isinstance(tags, str) and tags.strip():
        sj["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
    category = fm.get("category")
    if category:
        sj["category"] = category
    license_val = fm.get("license")
    if license_val:
        sj["license"] = license_val
    return sj


def publish_skill(skill_dir: str, overwrite: bool = False) -> str:
    """
    Publish a skill from a local directory to the public skill library.

    Copies the skill files to src/skills/<name>/ for local development use.
    Does NOT submit a PR -- this is for repository contributors.

    Requirements:
      - skill_dir must exist and contain SKILL.md
      - SKILL.md must have valid YAML frontmatter (--- delimited)
      - If skill.json exists, its name field must match the skill name
      - If skill.json does not exist, a basic one is auto-generated
      - Public skills directory must be found and writable (development environment)

    Args:
        skill_dir: Path to the skill directory (absolute or relative)
        overwrite: If True, replace an existing skill with the same name

    Returns:
        JSON string with operation result
    """
    import shutil

    abs_dir = os.path.abspath(skill_dir)

    # --- Validate source ---
    if not os.path.isdir(abs_dir):
        return json.dumps(
            {"success": False, "error": f"Directory not found: {skill_dir}"},
            ensure_ascii=False,
        )

    skill_md = os.path.join(abs_dir, "SKILL.md")
    if not os.path.isfile(skill_md):
        return json.dumps(
            {"success": False, "error": "SKILL.md not found in skill directory"},
            ensure_ascii=False,
        )

    name = _parse_frontmatter_name(skill_md)
    if not name:
        name = os.path.basename(abs_dir)
    name = name.strip().lower().replace(" ", "-")
    if not re.match(r"^[a-z0-9][a-z0-9_-]*$", name):
        return json.dumps(
            {
                "success": False,
                "error": f"Invalid skill name '{name}'. Must start with a letter/digit and contain only lowercase alphanumeric, hyphens, underscores.",
            },
            ensure_ascii=False,
        )

    # --- Validate frontmatter is well-formed ---
    fm_pattern = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
    try:
        with open(skill_md, encoding="utf-8") as f:
            raw = f.read()
    except Exception as e:
        return json.dumps(
            {"success": False, "error": f"Failed to read SKILL.md: {e}"},
            ensure_ascii=False,
        )
    if not fm_pattern.match(raw):
        return json.dumps(
            {
                "success": False,
                "error": "SKILL.md must have YAML frontmatter delimited by ---",
            },
            ensure_ascii=False,
        )

    # --- Validate / generate skill.json ---
    skill_json_path = os.path.join(abs_dir, "skill.json")
    generated = False
    if os.path.isfile(skill_json_path):
        try:
            with open(skill_json_path, encoding="utf-8") as f:
                sj = json.load(f)
            sj_name = (sj.get("name") or "").strip().lower().replace(" ", "-")
            if sj_name and sj_name != name:
                return json.dumps(
                    {
                        "success": False,
                        "error": f"skill.json name '{sj.get('name')}' does not match SKILL.md name '{name}'. Fix or remove skill.json.",
                    },
                    ensure_ascii=False,
                )
        except Exception as e:
            return json.dumps(
                {"success": False, "error": f"Invalid skill.json: {e}"},
                ensure_ascii=False,
            )
    else:
        sj = _generate_skill_json(abs_dir, name)
        generated = True

    # --- Find target ---
    pub_dir, writable = _find_public_skills_dir()
    if not pub_dir:
        return json.dumps(
            {
                "success": False,
                "error": "Public skills directory not found. This tool only works in a development environment with a src/skills/ directory.",
            },
            ensure_ascii=False,
        )
    if not writable:
        return json.dumps(
            {
                "success": False,
                "error": f"Public skills directory is not writable: {pub_dir}",
            },
            ensure_ascii=False,
        )

    target_dir = os.path.join(pub_dir, name)
    if os.path.exists(target_dir):
        if not overwrite:
            return json.dumps(
                {
                    "success": False,
                    "error": f"Skill '{name}' already exists in public library. Set overwrite=True to replace.",
                },
                ensure_ascii=False,
            )
        shutil.rmtree(target_dir)

    # --- Copy all files then write/generated skill.json ---
    try:
        shutil.copytree(
            abs_dir,
            target_dir,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", ".git", "*.pyc", ".pytest_cache"),
        )
    except Exception as e:
        return json.dumps(
            {"success": False, "error": f"Failed to copy skill files: {e}"},
            ensure_ascii=False,
        )

    if generated:
        dst_json = os.path.join(target_dir, "skill.json")
        try:
            with open(dst_json, "w", encoding="utf-8") as f:
                json.dump(sj, f, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps(
                {"success": False, "error": f"Failed to write skill.json: {e}"},
                ensure_ascii=False,
            )

    # Hot-load into runtime so list_skills() sees it immediately without restart
    from ..skill_loader import add_skill as _hot_add_skill

    load_result = _hot_add_skill(target_dir, name)
    hot_loaded = load_result.get("success", False)

    return json.dumps(
        {
            "success": True,
            "skill": name,
            "source": abs_dir,
            "target": target_dir,
            "skill_json": "generated" if generated else "validated",
            "hot_loaded": hot_loaded,
            "message": f"Skill '{name}' published to {target_dir} and {'loaded immediately' if hot_loaded else 'available after restart'}.",
        },
        ensure_ascii=False,
    )


def install_skill(
    skill_dir: str,
    skill_name: str = "",
    subdir: str = "",
) -> str:
    """
    Load a new Skill from a local directory OR a Git URL. Takes effect immediately.

    --- Local directory ---
        install_skill(skill_dir="/abs/path/to/skill")

    --- Git URL (whole repo is one skill) ---
        install_skill(skill_dir="https://github.com/user/my-skill")

    --- Git URL with subdirectory (repo contains multiple skills) ---
        install_skill(
            skill_dir="https://github.com/user/skills-collection",
            subdir="category/skill_name",
        )

    After cloning, if a skill.json is present with pip dependencies under
    install[], those packages are installed automatically via pip.

    Args:
        skill_dir: Absolute local path OR Git URL to clone from
        skill_name: Skill name override (optional, defaults to directory name)
        subdir: Subdirectory inside the cloned repo where SKILL.md lives
                (only meaningful when skill_dir is a Git URL)

    Returns:
        JSON string with loading result including clone/pip info when applicable
    """
    from ..skill_loader import add_skill as _add_skill

    extra: dict = {}

    # -- Git URL path ------------------------------------------------------------
    if _is_git_url(skill_dir):
        clone_result = _clone_or_update(skill_dir)
        if not clone_result["success"]:
            return json.dumps(clone_result, ensure_ascii=False)

        extra["git_action"] = clone_result["action"]
        extra["clone_dir"] = clone_result["clone_dir"]

        # Resolve actual skill directory (optionally inside a subdir)
        if subdir:
            local_skill_dir = os.path.join(clone_result["clone_dir"], subdir.strip("/\\"))
        else:
            local_skill_dir = clone_result["clone_dir"]

        if not os.path.isdir(local_skill_dir):
            return json.dumps(
                {
                    "success": False,
                    "error": f"Skill directory not found after clone: {local_skill_dir}",
                    **extra,
                },
                ensure_ascii=False,
            )

    # -- Local directory path ----------------------------------------------------
    else:
        local_skill_dir = skill_dir

    # Auto-install pip dependencies declared in skill.json (if any)
    pip_installed = _install_pip_deps_from_skill_json(local_skill_dir)
    if pip_installed:
        extra["pip_installed"] = pip_installed

    # Load the skill
    name = skill_name if skill_name else None
    result = _add_skill(local_skill_dir, name)
    result.update(extra)
    return json.dumps(result, ensure_ascii=False)


def remove_skill(skill_name: str) -> str:
    """
    Unload a currently loaded Skill.

    Args:
        skill_name: Name of the Skill to remove

    Returns:
        JSON string with operation result
    """
    from ..skill_loader import remove_skill as _remove_skill

    result = _remove_skill(skill_name)
    return json.dumps(result, ensure_ascii=False)


def read_skill(skill_name: str, mode: str = "full") -> str:
    """
    Read a loaded skill like reading a file. No attach/state management is performed.

    This is intended for temporary usage: fetch skill instructions on demand,
    consume them in the current turn, and avoid persistent prompt/context mutation.

    Args:
        skill_name: Skill identifier (directory name)
        mode: "full" (default) or "summary"

    Returns:
        JSON string with content/summary or an error message if not found
    """
    from ..skill_loader import get_loaded_skills

    mode_norm = (mode or "full").strip().lower()
    if mode_norm not in ("full", "summary"):
        return json.dumps(
            {
                "success": False,
                "error": f"Invalid mode '{mode}'. Supported: full, summary",
            },
            ensure_ascii=False,
        )

    for skill in get_loaded_skills():
        if skill.name == skill_name:
            if mode_norm == "summary":
                return json.dumps(
                    {
                        "success": True,
                        "skill": skill_name,
                        "display_name": skill.display_name,
                        "description": skill.description,
                        "is_private": skill.is_private,
                        "mode": "summary",
                        "content": (skill.description or "").strip(),
                    },
                    ensure_ascii=False,
                )

            return json.dumps(
                {
                    "success": True,
                    "skill": skill_name,
                    "display_name": skill.display_name,
                    "description": skill.description,
                    "is_private": skill.is_private,
                    "mode": "full",
                    "content": skill.content,
                },
                ensure_ascii=False,
            )

    return json.dumps(
        {
            "success": False,
            "error": f"Skill '{skill_name}' not found in loaded skills",
        },
        ensure_ascii=False,
    )


def list_skills() -> str:
    """
    View all currently loaded skills (public and private).

    Returns:
        JSON string with a skills array
    """
    try:
        from ..skill_loader import list_skills as _list_skills

        skills = _list_skills()
    except Exception as e:
        skills = {"_status": f"Skill loader unavailable: {e}"}

    return json.dumps({"skills": skills}, ensure_ascii=False, indent=2)


def list_installed() -> str:
    """
    View all currently installed MCP servers and Skills.

    Returns:
        JSON string with mcp_servers and skills sections
    """
    # Graceful degradation: MCP adapter may not be available
    try:
        from .mcp_adapter import get_mcp_adapter

        adapter = get_mcp_adapter()
        mcp_servers = adapter.list_servers() if adapter else {"_status": "MCP adapter not initialized or disabled"}
    except Exception as e:
        mcp_servers = {"_status": f"MCP adapter unavailable: {e}"}

    # Graceful degradation: skill_loader may not be available
    try:
        from ..skill_loader import list_skills

        skills = list_skills()
    except Exception as e:
        skills = {"_status": f"Skill loader unavailable: {e}"}

    result = {
        "mcp_servers": mcp_servers,
        "skills": skills,
    }

    return json.dumps(result, ensure_ascii=False, indent=2)


def plugin_list(enabled_only: bool = True) -> str:
    """
    List local plugins (name + display_name + description).

    Useful when some plugins are hidden from prompt summaries (summary preload disabled).

    Args:
        enabled_only: If True, only return enabled plugins. If False, return all discovered plugins.

    Returns:
        JSON string: {"plugins": [...], "count": N}
    """
    plugins_dir = os.path.join(_project_root, "plugins")
    results = []

    if not os.path.isdir(plugins_dir):
        return json.dumps({"plugins": [], "count": 0}, ensure_ascii=False)

    for entry in sorted(os.listdir(plugins_dir)):
        manifest = os.path.join(plugins_dir, entry, "plugin.json")
        if not os.path.isfile(manifest):
            continue
        try:
            with open(manifest, encoding="utf-8") as f:
                meta = json.load(f) or {}
            enabled = bool(meta.get("enabled", True))
            if enabled_only and not enabled:
                continue
            name = meta.get("name", entry)
            results.append(
                {
                    "name": name,
                    "display_name": meta.get("display_name", name),
                    "description": (meta.get("description", "") or "").strip(),
                    "enabled": enabled,
                    "version": meta.get("version", ""),
                    "category": meta.get("category", ""),
                }
            )
        except Exception:
            continue

    return json.dumps({"plugins": results, "count": len(results)}, ensure_ascii=False)


def reload_plugins() -> str:
    """
    Immediately reload plugins from disk without restarting the agent.

    Re-reads config.json to get the latest tools list, then:
    - Loads any newly added plugins (new entries in tools[])
    - Unloads any removed/disabled plugins
    - Registers all tools so they are available in the very next turn

    Call this after deploying a new plugin or editing config.json tools[],
    so you can use the new tools without waiting for idle or restarting.

    Returns:
        JSON string with loaded[], unloaded[], and active_tools[]
    """
    from opensquad.runner import do_plugin_reload

    result = do_plugin_reload()
    return json.dumps(result, ensure_ascii=False)
