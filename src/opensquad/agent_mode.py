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
# Note: write_file / replace_in_file / create_directory are gated by path at
# call-time (only `.opensquad/plans/**` allowed) so they stay in the schema.
PLAN_BLOCKED_TOOLS: frozenset[str] = frozenset(
    {
        # filesystem writes / side-effects (except plan-doc whitelist — see below)
        "filesystem__delete_file",
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

# Filesystem writes allowed in Plan ONLY when the target path is a plan doc.
PLAN_DOC_WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "filesystem__write_file",
        "filesystem__replace_in_file",
        "filesystem__create_directory",
    }
)

# Path fragment that marks Cursor-style plan documents (cwd-relative or absolute).
PLAN_DOC_PATH_MARKER = ".opensquad/plans"

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
## Agent Mode: PLAN (design / investigate — Cursor-style)

You are currently in **Plan** mode. Do **not** implement product code yet.
Your job is: investigate → clarify → write an editable Markdown plan document →
emit a `<plan>` checklist → then request Build so the user can approve execution.

ALLOWED:
- Read files, list directories, search code (deep codebase analysis)
- Ask clarifying questions when requirements are vague (prefer
  `choice_tools__propose_options` when there are discrete choices)
- Write/update **only** plan documents under `.opensquad/plans/`
  (e.g. `.opensquad/plans/YYYYMMDD-short-slug.md`)
- Emit `<plan>` checklist aligned with that document

DECISIONS: When you have several viable approaches and the user should decide which
one to pursue, call `choice_tools__propose_options` with a `prompt` and 2–12
`options` (strings or `{id,title,description}` / `{label,value}` dicts; JSON
strings and `{"options":[...]}` wrappers are OK). Pass `allow_multiple=true`
when the user may pick more than one. The card appears in **either** the group
chat (group turn) **or** private Agent Web — never both. STOP this turn after
calling it; you will receive a system message with the chosen option(s).

FORBIDDEN (tools are blocked; do not attempt):
- Edit application/source files outside `.opensquad/plans/`
- Run shell / cmd / bash / powershell / background jobs
- Git write operations, installs, or other mutating tools

EXECUTION-REQUEST RULE (important): If the user asks you to DO something that
requires running commands, starting services, deploying, installing, or editing
code — e.g. "启动服务/运行/部署/执行/测试/修改" — do NOT loop through read-only
investigation forever. Once you understand the codebase well enough to write a
plan (or even earlier, when the request is unambiguous), call
`agent_mode__request_switch` with `target_mode="build"` immediately and wait
for approval. Endless read-only probing without a plan or a switch request is
considered a failure mode.

When the Markdown plan + `<plan>` checklist are ready for implementation, call
`agent_mode__request_switch` with `target_mode="build"` and a short reason that
references the plan file path. Wait for the user to approve before assuming
Build is active.
- In **private AI chat**: user clicks Approve on the card above the composer.
- In a **group chat**: a 确定/拒绝 card is also posted in the group — prefer that
  when the conversation is happening in the group. You may also call
  `im__request_approval(kind="mode_switch", to_mode="build", ...)` explicitly.
""".strip()

_PROMPT_BUILD = """
## Agent Mode: BUILD (implementation)

You are currently in **Build** mode. You may edit files and run shell/cmd/bash/powershell tools as needed to implement changes.

If a Markdown plan exists under `.opensquad/plans/`, follow it (and keep the
`<plan>` checklist updated as you complete steps).

If you only need to explore or draft a plan without making changes, call
`agent_mode__request_switch` with `target_mode="plan"` and a short reason, then wait for user approval
(in private AI chat UI, or the group 确定/拒绝 card when talking in a group).
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


def is_plan_doc_path(path: str | None) -> bool:
    """True if path is under `.opensquad/plans/` (Cursor-style plan MD)."""
    if not path or not str(path).strip():
        return False
    norm = str(path).replace("\\", "/").lower()
    return PLAN_DOC_PATH_MARKER.lower() in norm


def plan_doc_write_allowed(tool_name: str, args: dict | None) -> bool:
    """Whether a Plan-mode filesystem write is allowed for this call."""
    name = _canon_tool_name(tool_name)
    if name not in PLAN_DOC_WRITE_TOOLS:
        return False
    args = args or {}
    path = args.get("path") or args.get("file_path") or args.get("filepath") or ""
    return is_plan_doc_path(str(path))


def is_tool_blocked_in_plan(tool_name: str, args: dict | None = None) -> bool:
    """Return True if this tool must not run while agent_mode=plan.

    When ``args`` is provided, plan-doc writes under `.opensquad/plans/` are allowed.
    Schema filtering calls this without args — plan-doc write tools are kept in the
    schema and enforced at call-time with args.
    """
    name = _canon_tool_name(tool_name)
    if not name:
        return False
    if name in PLAN_DOC_WRITE_TOOLS:
        # Keep in schema; with args, only allow plan-doc paths
        if args is None:
            return False
        return not plan_doc_write_allowed(name, args)
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
        # Plan-doc writes already handled above
        if name in PLAN_DOC_WRITE_TOOLS:
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
    name = _canon_tool_name(tool_name)
    if name in PLAN_DOC_WRITE_TOOLS:
        return (
            f"Blocked in Plan mode: `{name}` — path must be under "
            f"`{PLAN_DOC_PATH_MARKER}/` (Markdown plan documents only). "
            "Other source edits require Build: call `agent_mode__request_switch` "
            'with target_mode="build" and wait for approval.'
        )
    return (
        f"Blocked in Plan mode: `{name}`. "
        "Plan is design-only (except `.opensquad/plans/` docs). Call "
        "`agent_mode__request_switch` with "
        'target_mode="build" and wait for user approval before editing product files or running shell.'
    )


_current_mode: str = DEFAULT_MODE
_mode_provider = None
# Per-session Plan/Build overrides (parallel multi-session panes).
_session_modes: dict[str, str] = {}


def set_mode_provider(provider) -> None:
    global _mode_provider
    _mode_provider = provider


def set_session_mode(session_id: str, mode: str) -> str:
    """Remember Plan/Build for one session without changing the agent default."""
    sid = (session_id or "").strip()
    mode_n = normalize_mode(mode)
    if sid:
        _session_modes[sid] = mode_n
    return mode_n


def set_current_mode(mode: str) -> str:
    global _current_mode
    _current_mode = normalize_mode(mode)
    return _current_mode


def get_current_mode() -> str:
    # Prefer turn-local / per-session mode during parallel multi-session turns.
    try:
        from opensquad.session_parallel import get_turn_local

        tl = get_turn_local()
        if tl is not None and getattr(tl, "agent_mode", ""):
            return normalize_mode(tl.agent_mode)
        sid = (tl.sid if tl is not None else "") or ""
        if sid and sid in _session_modes:
            return normalize_mode(_session_modes[sid])
    except Exception:
        pass
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
