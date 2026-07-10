"""
opensquad/skill_loader.py - Skill package loader

Compatible with the Claude Code / AgentSkills.io open standard SKILL.md format.
Supports YAML frontmatter parsing, parameter substitution, and tool module loading.

Skill directory structure:
    skills/
    +-- code_review/
    |   +-- SKILL.md          # Main instruction file (required)
    |   +-- template.md       # Template file (optional)
    |   +-- tools.py          # Additional tool module (optional)
    +-- deploy_helper/
        +-- SKILL.md

SKILL.md format:
    ---
    name: code-review
    description: Code review expert skill
    disable-model-invocation: false
    allowed-tools: filesystem, memory
    ---

    Markdown instruction content...
"""

import importlib
import importlib.util
import json
import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Module-level cache for build_skills_prompt (keyed by id(skills) + cfg hash)
_build_skills_prompt_cache: dict[str, str] = {}


def _build_skills_prompt_cache_key(skills, prompt_preload_cfg) -> str:
    """Generate cache key. Using id(skills) is safe because _loaded_skills is a
    module-level singleton replaced only on hot-reload."""
    cfg_str = json.dumps(prompt_preload_cfg or {}, sort_keys=True)
    return f"{id(skills)}:{hash(cfg_str)}"


class Skill:
    """Represents a loaded skill package."""

    def __init__(self, name: str, directory: str):
        self.name = name
        self.directory = directory
        self.display_name = name
        self.description = ""
        self.content = ""  # Markdown instruction body
        self.disable_model_invocation = False
        self.user_invocable = True
        self.allowed_tools: list[str] = []
        self.has_tools_module = False
        self.tools_module = None
        self.frontmatter: dict[str, Any] = {}
        self.is_private = False  # True = agent-private skill (fully injected), False = public skill (summary only)

    def __repr__(self):
        return f"<Skill '{self.name}' dir={self.directory}>"


def parse_skill_md(filepath: str) -> tuple:
    """
    Parse a SKILL.md file, separating YAML frontmatter from the Markdown body.

    Returns:
        (frontmatter_dict, markdown_content)
    """
    with open(filepath, encoding="utf-8") as f:
        raw = f.read()

    frontmatter = {}
    content = raw

    # Detect YAML frontmatter (starts and ends with ---)
    fm_pattern = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
    match = fm_pattern.match(raw)
    if match:
        fm_text = match.group(1)
        content = raw[match.end() :]

        # Simple YAML parsing (no pyyaml dependency)
        for line in fm_text.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip()

                # Boolean conversion
                if value.lower() in ("true", "yes"):
                    value = True
                elif value.lower() in ("false", "no"):
                    value = False
                # List values (comma-separated)
                elif "," in value:
                    value = [v.strip() for v in value.split(",") if v.strip()]

                frontmatter[key] = value

    return frontmatter, content.strip()


def load_skill(skill_dir: str, skill_name: str) -> Optional["Skill"]:
    """
    Load a single Skill (or Blueprint) from a directory.

    Supports two filenames: SKILL.md (skill) and BLUEPRINT.md (collaboration architecture).
    SKILL.md is checked first; if not found, BLUEPRINT.md is tried.

    Args:
        skill_dir: Path to the skill directory (e.g. skills/code_review/)
        skill_name: Skill directory name

    Returns:
        Skill object, or None if loading fails
    """
    skill_md_path = os.path.join(skill_dir, "SKILL.md")
    if not os.path.exists(skill_md_path):
        # Fallback: try BLUEPRINT.md (legacy support)
        blueprint_path = os.path.join(skill_dir, "BLUEPRINT.md")
        if os.path.exists(blueprint_path):
            skill_md_path = blueprint_path
        else:
            logger.warning(f"[SkillLoader] SKILL.md / BLUEPRINT.md not found in {skill_dir}")
            return None

    try:
        frontmatter, content = parse_skill_md(skill_md_path)
    except Exception as e:
        logger.error(f"[SkillLoader] Failed to parse {skill_md_path}: {e}")
        return None

    skill = Skill(name=skill_name, directory=skill_dir)
    skill.frontmatter = frontmatter
    skill.content = content

    # Extract fields from frontmatter
    skill.display_name = frontmatter.get("name", skill_name)
    skill.description = frontmatter.get("description", "")
    skill.disable_model_invocation = frontmatter.get("disable-model-invocation", False)
    skill.user_invocable = frontmatter.get("user-invocable", True)

    # allowed-tools can be a comma-separated string or an already-parsed list
    allowed = frontmatter.get("allowed-tools", [])
    if isinstance(allowed, str):
        skill.allowed_tools = [t.strip() for t in allowed.split(",") if t.strip()]
    elif isinstance(allowed, list):
        skill.allowed_tools = allowed

    # Check for tools.py
    tools_path = os.path.join(skill_dir, "tools.py")
    if os.path.exists(tools_path):
        skill.has_tools_module = True
        try:
            spec = importlib.util.spec_from_file_location(f"skill_{skill_name}_tools", tools_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            skill.tools_module = module
            logger.info(f"[SkillLoader] Loaded tools.py for skill '{skill_name}'")
        except Exception as e:
            logger.error(f"[SkillLoader] Failed to load tools.py for '{skill_name}': {e}")

    # If no description, use the first paragraph of the body
    if not skill.description and content:
        first_para = content.split("\n\n")[0].strip()
        # Strip Markdown heading markers
        first_para = re.sub(r"^#+\s*", "", first_para)
        skill.description = first_para[:200]

    logger.info(f"[SkillLoader] Loaded skill: {skill.display_name} ({skill_name})")
    return skill


def discover_skills(skills_base_dir: str) -> list[str]:
    """
    Auto-discover all skills under the skills directory (subdirectories containing SKILL.md).

    Args:
        skills_base_dir: Root skills directory

    Returns:
        List of skill names
    """
    if not os.path.isdir(skills_base_dir):
        return []

    found = []
    for entry in os.listdir(skills_base_dir):
        entry_path = os.path.join(skills_base_dir, entry)
        if os.path.isdir(entry_path):
            skill_md = os.path.join(entry_path, "SKILL.md")
            if os.path.exists(skill_md):
                found.append(entry)

    return sorted(found)


def load_skills_from_config(
    config: dict,
    agent_dir: str,
    project_root: str,
) -> list[Skill]:
    """
    Load skill packages based on the skills config in config.json (public/private separation).

    config.json example:
        {
          "skills": {
            "enabled": true,
            "private": ["code_review", "task_planner"]
          }
        }

    Loading strategy:
        - Private skills: names listed under "private" in config, loaded from agent_dir/skills/.
          is_private=True; full content is injected into the system prompt.
        - Public skills: all skills auto-discovered under project_root/skills/.
          is_private=False; only the name and description summary are injected into the system prompt.
          The AI retrieves full content on demand via read_skill().
        - When names conflict, private takes precedence (public is not loaded again).

    Args:
        config: Agent's config.json dict
        agent_dir: Agent directory path
        project_root: Project root directory

    Returns:
        List of successfully loaded Skill objects (private first, then public)
    """
    skills_cfg = config.get("skills", {})
    # Public skill library is enabled by default; disable explicitly with "skills": {"enabled": false}
    if not skills_cfg.get("enabled", True):
        return []

    loaded = []
    private_names = set()

    # -- 1. Load private skills (agent_dir/skills/; determined by config "active" field, with "private" as legacy fallback) --
    private_list = skills_cfg.get("active", None)
    if private_list is None:
        private_list = skills_cfg.get("private", [])
    if private_list:
        agent_skills_dir = os.path.join(agent_dir, "skills")
        if os.path.isdir(agent_skills_dir):
            logger.info(f"[SkillLoader] Private skills dir: {agent_skills_dir}")
            for skill_name in private_list:
                skill_dir = os.path.join(agent_skills_dir, skill_name)
                if not os.path.isdir(skill_dir):
                    logger.warning(f"[SkillLoader] Private skill dir not found: {skill_dir}")
                    continue
                skill = load_skill(skill_dir, skill_name)
                if skill:
                    skill.is_private = True
                    loaded.append(skill)
                    private_names.add(skill_name)
            logger.info(f"[SkillLoader] Private skills loaded: {list(private_names)}")
        else:
            logger.warning(f"[SkillLoader] Agent skills dir not found: {os.path.join(agent_dir, 'skills')}")

    # -- 2. Load public skills (workspace first, then builtin seeds) --
    from opensquad.system_config import syscfg

    public_names: set[str] = set()
    for public_skills_dir in syscfg.resource_search_dirs("skills"):
        if not os.path.isdir(public_skills_dir):
            continue
        discovered = discover_skills(public_skills_dir)
        logger.info(f"[SkillLoader] Public skills discovered in {public_skills_dir}: {discovered}")
        for skill_name in discovered:
            if skill_name in private_names or skill_name in public_names:
                logger.info(f"[SkillLoader] Skipping public '{skill_name}' (overridden)")
                continue
            skill_dir = os.path.join(public_skills_dir, skill_name)
            skill = load_skill(skill_dir, skill_name)
            if skill:
                skill.is_private = False
                loaded.append(skill)
                public_names.add(skill_name)

    private_count = sum(1 for s in loaded if s.is_private)
    public_count = sum(1 for s in loaded if not s.is_private)
    logger.info(f"[SkillLoader] Total loaded: {len(loaded)} (private={private_count}, public={public_count})")
    return loaded


def build_skills_prompt(skills: list[Skill], prompt_preload_cfg: dict[str, Any] | None = None) -> str:
    """
    Build the skill injection block for the system prompt.

    Supports three levels for non-private skills:
      - full    : inject full skill content
      - summary : inject name+description only (default)
      - hidden  : do not inject into prompt

    Rules:
      1) Private skills (is_private=True) are always full-injected.
      2) Public skills may be overridden by prompt_preload config:
         - prompt_preload.full_skills:   [skill_name, ...]
         - prompt_preload.hidden_skills: [skill_name, ...]
         - prompt_preload.include_skills: bool (if false, non-full skills become hidden)

    Args:
        skills: Loaded Skill objects
        prompt_preload_cfg: Optional config dict from config.json.prompt_preload

    Returns:
        Concatenated prompt text
    """
    if not skills:
        return ""

    # Fast path: cache hit (skills object identity + config hash)
    cache_key = _build_skills_prompt_cache_key(skills, prompt_preload_cfg)
    if cache_key in _build_skills_prompt_cache:
        return _build_skills_prompt_cache[cache_key]

    cfg = prompt_preload_cfg if isinstance(prompt_preload_cfg, dict) else {}

    def _on(v, default=False):
        if v is None:
            return default
        if isinstance(v, bool):
            return v
        if isinstance(v, int | float):
            return bool(v)
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on", "enabled")
        return default

    include_summaries = _on(cfg.get("include_skills", True), True)
    full_set = {str(x).strip() for x in (cfg.get("full_skills", []) or []) if str(x).strip()}
    hidden_set = {str(x).strip() for x in (cfg.get("hidden_skills", []) or []) if str(x).strip()}

    full_skills: list[Skill] = []
    summary_skills: list[Skill] = []
    hidden_skills: list[Skill] = []

    for s in skills:
        if getattr(s, "is_private", False):
            full_skills.append(s)
            continue

        if s.name in hidden_set:
            hidden_skills.append(s)
            continue
        if s.name in full_set:
            full_skills.append(s)
            continue
        if include_summaries:
            summary_skills.append(s)
        else:
            hidden_skills.append(s)

    sections = ["## Skills\n"]

    if full_skills:
        sections.append(f"### Full-injected Skills ({len(full_skills)})\n")
        for skill in full_skills:
            header = f"#### {skill.display_name}"
            if skill.description:
                header += f" - {skill.description}"
            sections.append(header)

            if skill.allowed_tools:
                sections.append(f"*Allowed tools: {', '.join(skill.allowed_tools)}*")

            sections.append(skill.content)
            sections.append("")

    if summary_skills:
        sections.append(f"### Summary Skills ({len(summary_skills)}, activate/read on demand)\n")
        sections.append(
            "**Important: Before starting any complex task, first check if a relevant skill exists in the library.**\n"
            "The skill library contains packaged expertise for common workflows (research, data analysis, code review, "
            "frontend design, collaboration, agent creation, etc.). Using a skill saves time and ensures best practices.\n\n"
            "How to use skills:\n"
            "- Use `agent_setup.list_skills()` to see all available skills with descriptions.\n"
            "- Use `agent_setup.read_skill(skill_name)` for one-time lookup.\n"
            "- Use `agent_setup.publish_skill(skill_dir)` to contribute a skill to the shared library for all agents to use (takes effect immediately, no restart needed).\n"
            "- When you find a skill that matches your current task, use `agent_setup.read_skill()` to load and apply it.\n"
            "- If the user message includes `<user_send_skill>name</user_send_skill>`, treat that as an explicit request to apply that skill to the accompanying user text.\n"
        )
        for skill in summary_skills:
            desc = skill.description or "(no description)"
            sections.append(f"- **{skill.display_name}** (`{skill.name}`): {desc}")
        sections.append("")

    if hidden_skills:
        sections.append(
            f"### Hidden Skills ({len(hidden_skills)})\n"
            "These skills are intentionally not listed in prompt summaries. "
            "Use `agent_setup.list_skills()` to discover installed skills when needed.\n"
        )

    result = "\n".join(sections)
    _build_skills_prompt_cache[cache_key] = result
    return result


def expand_user_send_skill(user_text: str) -> str:
    """
    Expand <user_send_skill>name</user_send_skill> tags in a user message.

    The UI composer may wrap a selected skill so the agent is instructed to
    follow that skill for the accompanying user text. This expands the tag
    into the skill's full SKILL.md content (when loaded) plus the remaining
    user text, so behavior does not depend solely on the model calling
    read_skill().
    """
    if not user_text or "<user_send_skill>" not in user_text.lower():
        return user_text

    pattern = re.compile(
        r"<user_send_skill>\s*([^<]+?)\s*</user_send_skill>",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(user_text)
    if not match:
        return user_text

    skill_name = (match.group(1) or "").strip()
    remainder = (user_text[: match.start()] + user_text[match.end() :]).strip()

    skill_body = ""
    skill_label = skill_name
    for skill in get_loaded_skills():
        if skill.name == skill_name or skill.display_name == skill_name:
            skill_body = (skill.content or "").strip()
            skill_label = skill.display_name or skill.name
            break

    if not skill_body:
        # Keep the tag visible so the model can still try read_skill(name).
        logger.warning(
            "[skill_loader] user_send_skill '%s' not found in loaded skills; leaving tag for model",
            skill_name,
        )
        hint = (
            f"[User requested skill `{skill_name}` via <user_send_skill>. "
            f"Call agent_setup.read_skill('{skill_name}') to load it, then follow the user's request.]\n\n"
        )
        return hint + (remainder if remainder else user_text)

    parts = [
        f"[User-selected skill: {skill_label} (`{skill_name}`)]",
        "Follow the skill instructions below to complete the user's request.",
        "",
        "----- BEGIN SKILL -----",
        skill_body,
        "----- END SKILL -----",
    ]
    if remainder:
        parts.extend(["", "[User request]", remainder])
    else:
        parts.extend(["", "[User request]", f"(Apply the `{skill_name}` skill.)"])
    return "\n".join(parts)


# ===================================================================
# Runtime state: loaded skills and associated registry
# ===================================================================
_loaded_skills: list[Skill] = []
_active_registry = None


def init_skill_runtime(skills: list[Skill], registry):
    """
    Called by boot.py at startup to record runtime state for hot-reload support.

    Args:
        skills: List of Skill objects loaded at startup
        registry: ToolRegistry instance
    """
    global _loaded_skills, _active_registry
    _loaded_skills = list(skills)
    _active_registry = registry


def get_loaded_skills() -> list[Skill]:
    """Return the currently loaded Skill list (used by runner.py to build the skills prompt each turn)."""
    return _loaded_skills


def add_skill(skill_dir: str, skill_name: str | None = None) -> dict:
    """
    Hot-load a new Skill at runtime.

    Steps:
      1. Load SKILL.md -> Skill object
      2. Register tools.py (if present)
      3. Update _loaded_skills list

    Prompt updates take effect automatically on the next _setup_prompt() call
    (re-injected from template each turn).

    Args:
        skill_dir: Skill directory path (absolute, containing SKILL.md)
        skill_name: Skill name (optional; defaults to directory name)

    Returns:
        {"success": True/False, "skill": name, "error": "..."}
    """
    global _loaded_skills

    if _active_registry is None:
        return {"success": False, "error": "Skill runtime not initialized (call init_skill_runtime first)"}

    if not os.path.isdir(skill_dir):
        return {"success": False, "error": f"Directory not found: {skill_dir}"}

    if skill_name is None:
        skill_name = os.path.basename(skill_dir.rstrip("/\\"))

    # Remove existing skill with the same name before loading
    _loaded_skills = [s for s in _loaded_skills if s.name != skill_name]

    skill = load_skill(skill_dir, skill_name)
    if skill is None:
        return {"success": False, "skill": skill_name, "error": f"Failed to load skill from {skill_dir}"}

    _loaded_skills.append(skill)

    # Register tools.py (if present)
    if skill.has_tools_module and skill.tools_module:
        try:
            _active_registry.register(
                skill.tools_module,
                f"skill_{skill.name}",
                level="extended",
            )
        except Exception as e:
            logger.error(f"[SkillLoader] Failed to register tools for hot-loaded skill '{skill_name}': {e}")

    # Prompt update takes effect automatically on the next _setup_prompt() call

    logger.info(f"[SkillLoader] Hot-loaded skill: {skill.display_name} ({skill_name})")
    return {
        "success": True,
        "skill": skill_name,
        "display_name": skill.display_name,
        "description": skill.description,
        "has_tools": skill.has_tools_module,
    }


def add_skill_from_file(filepath: str, skill_name: str | None = None) -> dict:
    """
    Hot-load a Skill at runtime directly from a single .md file (not a directory).

    Difference from add_skill(): no directory structure required; a Skill object is
    created directly from the .md file.
    Used for loading flat-structured collaboration cards such as collab_cards/*.md.

    Args:
        filepath: .md file path (absolute)
        skill_name: Skill name (optional; defaults to filename without extension)

    Returns:
        {"success": True/False, "skill": name, "error": "..."}
    """
    global _loaded_skills

    if _active_registry is None:
        return {"success": False, "error": "Skill runtime not initialized (call init_skill_runtime first)"}

    if not os.path.isfile(filepath):
        return {"success": False, "error": f"File not found: {filepath}"}

    if skill_name is None:
        skill_name = os.path.splitext(os.path.basename(filepath))[0]

    # Remove existing skill with the same name before loading
    _loaded_skills = [s for s in _loaded_skills if s.name != skill_name]

    try:
        frontmatter, content = parse_skill_md(filepath)
    except Exception as e:
        return {"success": False, "skill": skill_name, "error": f"Failed to parse {filepath}: {e}"}

    skill = Skill(name=skill_name, directory=os.path.dirname(filepath))
    skill.frontmatter = frontmatter
    skill.content = content
    skill.display_name = frontmatter.get("name", skill_name)
    skill.description = frontmatter.get("description", "")
    skill.disable_model_invocation = frontmatter.get("disable-model-invocation", False)
    skill.user_invocable = frontmatter.get("user-invocable", True)

    allowed = frontmatter.get("allowed-tools", [])
    if isinstance(allowed, str):
        skill.allowed_tools = [t.strip() for t in allowed.split(",") if t.strip()]
    elif isinstance(allowed, list):
        skill.allowed_tools = allowed

    # If no description, use the first paragraph of the body
    if not skill.description and content:
        first_para = content.split("\n\n")[0].strip()
        first_para = re.sub(r"^#+\s*", "", first_para)
        skill.description = first_para[:200]

    _loaded_skills.append(skill)

    logger.info(f"[SkillLoader] Hot-loaded skill from file: {skill.display_name} ({skill_name})")
    return {
        "success": True,
        "skill": skill_name,
        "display_name": skill.display_name,
        "description": skill.description,
        "has_tools": False,
    }


def remove_skill(skill_name: str) -> dict:
    """
    Remove a loaded Skill at runtime.

    Args:
        skill_name: Skill name

    Returns:
        {"success": True/False, "skill": name, "error": "..."}
    """
    global _loaded_skills

    found = [s for s in _loaded_skills if s.name == skill_name]
    if not found:
        return {"success": False, "skill": skill_name, "error": f"Skill '{skill_name}' not loaded"}

    _loaded_skills = [s for s in _loaded_skills if s.name != skill_name]

    # Note: already-registered tools are NOT removed from the registry (no unregister method),
    # but this is harmless because the skill prompt has been removed and the agent
    # will no longer invoke those tools.
    # Prompt update takes effect automatically on the next _setup_prompt() call.

    logger.info(f"[SkillLoader] Removed skill: {skill_name}")
    return {"success": True, "skill": skill_name}


def list_skills() -> list:
    """List all currently loaded skills."""
    return [
        {
            "name": s.name,
            "display_name": s.display_name,
            "description": s.description,
            "is_private": s.is_private,
            "has_tools": s.has_tools_module,
            "directory": s.directory,
        }
        for s in _loaded_skills
    ]


def register_skill_tools(skills: list[Skill], registry) -> None:
    """
    Register tools.py modules from skills into the ToolRegistry.

    Args:
        skills: List of Skill objects
        registry: ToolRegistry instance
    """
    for skill in skills:
        if skill.has_tools_module and skill.tools_module:
            try:
                registry.register(
                    skill.tools_module,
                    f"skill_{skill.name}",
                    level="extended",
                )
                logger.info(f"[SkillLoader] Registered tools from skill '{skill.name}'")
            except Exception as e:
                logger.error(f"[SkillLoader] Failed to register tools for '{skill.name}': {e}")
