"""Agent Plan / Build mode — Cursor-style exploration vs implementation.

Plan: read-only exploration and planning (no file edits, no shell, no destructive ops).
Build: full tool access for editing files and running commands.

Mode switches can be user-driven (UI) or agent-requested (approval card).
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

MODE_PLAN = "plan"
MODE_BUILD = "build"
VALID_MODES = (MODE_PLAN, MODE_BUILD)
DEFAULT_MODE = MODE_BUILD

# Exact FC names blocked in Plan (namespace__function).
PLAN_BLOCKED_TOOLS: frozenset[str] = frozenset(
    {
        # filesystem writes / side-effects
        "filesystem__write_file",
        "filesystem__replace_in_file",
        "filesystem__delete_file",
        "filesystem__create_directory",
        "filesystem__set_session_cwd",
        "filesystem__add_allowed_dir",
        # shell / jobs / binary write
        "system__create_shell_session",
        "system__run_session_job",
        "system__restart_shell_session",
        "system__close_shell_session",
        "system__start_job",
        "system__stop_job",
        "system__write_binary_file",
        # workspace mutations
        "workspace__create",
        "workspace__switch",
        "workspace__migrate",
        # agent setup mutations
        "agent_setup__publish_skill",
        "agent_setup__install_skill",
        "agent_setup__remove_skill",
        "agent_setup__reload_plugins",
        # mcp config mutations
        "mcp_query__add_server",
        "mcp_query__remove_server",
    }
)

# Entire namespaces blocked in Plan (all functions).
PLAN_BLOCKED_NAMESPACES: frozenset[str] = frozenset(
    {
        "delegate_task",
        "agent_factory",
    }
)

# git / vcs write-ish function names (matched after namespace__)
_GIT_WRITE_FUNCS = frozenset(
    {
        "init",
        "add",
        "commit",
        "checkout",
        "merge",
        "clone",
        "pull",
        "push",
        "rebase",
        "reset",
        "stash",
        "cherry_pick",
        "branch_delete",
        "delete_branch",
        "remote_add",
        "remote_remove",
        "remote_set_url",
        "tag",
        "issue_create",
        "pr_create",
        "pr_merge",
        "pr_checkout",
        "repo_fork",
        "repo_clone",
        "repo_create",
    }
)

_MCP_BLOCK_RE = re.compile(
    r"(write|edit|delete|remove|create|mkdir|rmdir|move|rename|execute|shell|bash|"
    r"powershell|cmd|run_command|terminal|install|uninstall|apply_patch|patch)",
    re.I,
)

_PROMPT_PLAN = """
## Agent Mode: PLAN (read-only)

You are currently in **Plan** mode. Your job is to explore the codebase and produce a clear plan.

ALLOWED:
- Read files, list directories, search code
- Ask clarifying questions
- Output a structured plan / checklist for the user

FORBIDDEN (tools are blocked; do not attempt):
- Create, edit, move, or delete files
- Run shell / cmd / bash / powershell / background jobs
- Git write operations, installs, or other mutating tools

If you need to edit files or run commands to implement the plan, call
`agent_mode__request_switch` with `target_mode="build"` and a short reason.
Wait for the user to approve in the UI before assuming Build is active.
""".strip()

_PROMPT_BUILD = """
## Agent Mode: BUILD (implementation)

You are currently in **Build** mode. You may edit files and run shell/cmd/bash/powershell tools as needed to implement changes.

If you only need to explore or draft a plan without making changes, call
`agent_mode__request_switch` with `target_mode="plan"` and a short reason, then wait for user approval.
""".strip()


def normalize_mode(value: str | None) -> str:
    v = (value or DEFAULT_MODE).strip().lower()
    return v if v in VALID_MODES else DEFAULT_MODE


def _canon_tool_name(tool_name: str) -> str:
    name = (tool_name or "").strip()
    if "." in name and "__" not in name:
        ns, fn = name.split(".", 1)
        return f"{ns}__{fn}"
    return name


def is_tool_blocked_in_plan(tool_name: str) -> bool:
    """Return True if this tool must not run while agent_mode=plan."""
    name = _canon_tool_name(tool_name)
    if not name:
        return False
    if name in PLAN_BLOCKED_TOOLS:
        return True
    if name.startswith("mcp__") and _MCP_BLOCK_RE.search(name):
        return True
    if "__" not in name:
        return False
    ns, fn = name.split("__", 1)
    if ns in PLAN_BLOCKED_NAMESPACES:
        return True
    if ns in ("git", "vcs") and (fn in _GIT_WRITE_FUNCS or fn.startswith("remote_")):
        return True
    if ns == "git" and ("delete" in fn or fn.endswith("_rm")):
        return True
    # Catch-all mutating filesystem / shell helpers by function name
    if ns in ("filesystem", "system", "workspace") and _MCP_BLOCK_RE.search(fn):
        # Allow read-ish names that match the regex poorly — keep list_directory etc.
        if fn.startswith(("list_", "read_", "search_", "find_", "get_", "stat_", "check_")):
            return False
        return fn not in ("pwd", "cwd", "whoami", "env_get")
    return False


def filter_tools_for_mode(tools: list[dict] | None, mode: str) -> list[dict] | None:
    """Filter OpenAI tools schema for the active mode."""
    if not tools:
        return tools
    mode_n = normalize_mode(mode)
    if mode_n != MODE_PLAN:
        return tools
    out: list[dict] = []
    for t in tools:
        fn = (t.get("function") or {}).get("name") or ""
        if is_tool_blocked_in_plan(fn):
            continue
        out.append(t)
    return out


def mode_prompt_section(mode: str) -> str:
    return _PROMPT_PLAN if normalize_mode(mode) == MODE_PLAN else _PROMPT_BUILD


def plan_block_message(tool_name: str) -> str:
    return (
        f"Blocked in Plan mode: `{_canon_tool_name(tool_name)}`. "
        "Plan is read-only. Call `agent_mode__request_switch` with "
        'target_mode="build" and wait for user approval before editing files or running shell.'
    )


_current_mode: str = DEFAULT_MODE
_mode_provider = None


def set_mode_provider(provider) -> None:
    global _mode_provider
    _mode_provider = provider


def set_current_mode(mode: str) -> str:
    global _current_mode
    _current_mode = normalize_mode(mode)
    return _current_mode


def get_current_mode() -> str:
    if _mode_provider:
        try:
            return normalize_mode(_mode_provider())
        except Exception:
            pass
    return normalize_mode(_current_mode)


def apply_plan_gate_to_llm_params(llm_params: dict[str, Any], mode: str | None = None) -> dict[str, Any]:
    """Mutate llm_params tools list for Plan mode; return same dict."""
    m = normalize_mode(mode or get_current_mode())
    if "tools" in llm_params:
        llm_params["tools"] = filter_tools_for_mode(llm_params.get("tools"), m)
    return llm_params
