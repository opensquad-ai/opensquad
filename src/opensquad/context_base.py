"""
opensquad/context_base.py - Common standard context injection base layer

inject_standard() returns (system_vars, dynamic_vars) two dicts,
corresponding to the system prompt stable layer (leveraging LLM prefix cache)
and the user message dynamic prefix layer respectively:

    system_vars (low-frequency/static, injected into system prompt):
        - AGENT_PROFILE      <- agent.md file content (permanent memory)
        - CONTEXT_SUMMARY    <- chat_api._latest_summary (context summary, changes only on compression)
        - AGENT_WORKSPACE    <- Session project cwd + OpenSquad data root (updates when session_cwd changes)
        - TEAM_COLLAB_CARDS  <- Collab card directory table + usage instructions (stable during tasks)

    dynamic_vars (high-frequency/dynamic, injected into each turn's user message prefix):
        - RUNTIME_STATE  <- time + source + state + wakeup level (changes every turn)
        - MEMORY_CONTEXT <- MemoryManager.auto_recall() (recalled per query each turn)

Design principles:
    - This module lives in the engine layer (opensquad/), not in any role directory
    - init_standard_context() is called by boot.py at startup
    - inject_standard() is called by runner.py _setup_prompt() each turn to get standard variables
    - A role's context.py before_input() may return the same key to override (role takes priority)
"""

import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

# Module-level cache
_memory_manager = None
_agent_md_path = None
_agent_dir = None  # Agent's own directory path (e.g. C:\...\agents\coder)
_project_root = None  # Project root directory path (e.g. C:\...\opensquad)
_data_root = None  # OpenSquad data/runtime root (syscfg.get_workspace()) — NOT the user project
_bridge = None  # ChatPro Bridge instance (for retrieving group member list)
_agent_config = None  # Current agent's config.json (for checking collaboration config)
_agents_dir = None  # agents/ directory path (for reading other agents' config.json)


def _resolve_session_project_cwd() -> str:
    """Return the user project directory for this session, or '' if not set.

    The project path is chosen when a session starts (folder picker / working-directory
    API). It must NOT be confused with the OpenSquad data root (agents/, data/, …).
    """
    try:
        from opensquad.utils.path_utils import get_session_cwd_override

        override = get_session_cwd_override()
        if override and os.path.isdir(override):
            return os.path.normcase(os.path.abspath(override))
    except Exception:
        pass
    try:
        from opensquad._context import get_current_context

        ctx = get_current_context()
        if ctx and ctx.session_cwd and os.path.isdir(ctx.session_cwd):
            return os.path.normcase(os.path.abspath(ctx.session_cwd))
    except Exception:
        pass
    return ""


def init_standard_context(agent_md_path: str, memory_manager=None, bridge=None, agent_config=None, agents_dir=None):
    """
    Initialize at startup. Called by boot.py.
    Caches memory_manager, agent.md path, bridge, and agent_config.
    """
    global _memory_manager, _agent_md_path, _agent_dir, _project_root, _data_root, _bridge, _agent_config, _agents_dir
    _memory_manager = memory_manager
    _agent_md_path = agent_md_path
    _bridge = bridge
    _agent_config = agent_config or {}
    _agents_dir = agents_dir

    # Derive agent_dir and project_root from agent_md_path
    # agent_md_path = agents/{name}/agent.md -> agent_dir = agents/{name}
    if _agent_md_path:
        _agent_dir = os.path.dirname(os.path.abspath(_agent_md_path))
        # project_root = agents/{name}/../../ = opensquad/ (repo root)
        _project_root = os.path.dirname(os.path.dirname(_agent_dir))
        logger.info(f"[ContextBase] Agent directory: {_agent_dir}")
        logger.info(f"[ContextBase] Project root: {_project_root}")

    # Data/runtime root: where OpenSquad stores agents/, data/, config — not the user project
    try:
        from opensquad.system_config import syscfg as _syscfg

        _data_root = _syscfg.get_workspace()
    except Exception:
        _data_root = _project_root
    logger.info(f"[ContextBase] OpenSquad data root: {_data_root}")

    if _memory_manager:
        logger.info(
            f"[ContextBase] MemoryManager connected "
            f"(window={_memory_manager._window_size}, budget={_memory_manager._token_budget})"
        )

    # Ensure agent.md exists (create empty template on first startup)
    if _agent_md_path and not os.path.exists(_agent_md_path):
        try:
            os.makedirs(os.path.dirname(_agent_md_path), exist_ok=True)
            with open(_agent_md_path, "w", encoding="utf-8") as f:
                f.write(
                    "# Permanent Memory\n\n"
                    "(This is your permanent memory document. Content the user asks you to remember, "
                    "and information you decide should be retained long-term, goes here.)\n"
                    "(You can see this document's content every conversation; nothing written here will be forgotten.)\n"
                    "(You can use the filesystem.write_file tool to update this document directly.)\n\n"
                    "## User Preferences\n\n\n"
                    "## Standing Instructions\n\n\n"
                    "## Key Information\n\n"
                )
            logger.info(f"[ContextBase] Created initial agent.md at {_agent_md_path}")
        except Exception as e:
            logger.error(f"[ContextBase] Failed to create agent.md: {e}")
    elif _agent_md_path:
        logger.info(f"[ContextBase] agent.md loaded from {_agent_md_path}")


def _read_agent_md() -> str:
    """Read agent.md file content. Re-reads on every call (agent may have modified it)."""
    if not _agent_md_path or not os.path.exists(_agent_md_path):
        return ""
    try:
        with open(_agent_md_path, encoding="utf-8") as f:
            return f.read().strip()
    except Exception as e:
        logger.warning(f"[ContextBase] Failed to read agent.md: {e}")
        return ""


def inject_standard(context: dict) -> tuple:
    """
    Called each turn; returns (system_vars, dynamic_vars) two dicts.

    system_vars: low-frequency/static content, injected into system prompt (leverages LLM prefix cache)
        - AGENT_PROFILE, CONTEXT_SUMMARY, AGENT_WORKSPACE, TEAM_COLLAB_CARDS

    dynamic_vars: high-frequency/dynamic content, injected into each turn's user message prefix
        - RUNTIME_STATE, MEMORY_CONTEXT

    context must include:
        - query:            current user input
        - source:           input source (cli/web/chatpro/gateway)
        - chat_api:         ChatAPI instance (to read _latest_summary)
        - memory_manager:   MemoryManager instance (optional, can use module cache)
        - recent_messages:  recent message list
        - current_state:    current working state
        - current_wake:     current wakeup level
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    source = context.get("source", "unknown")
    query = context.get("query", "")
    current_state = context.get("current_state", "unknown")
    current_wake = context.get("current_wake", "unknown")

    system_vars = {}
    dynamic_vars = {}

    # ======== system_vars (stable layer, injected into system prompt) ========

    # --- Permanent memory (agent.md) ---
    agent_profile = _read_agent_md()
    if agent_profile:
        system_vars["AGENT_PROFILE"] = agent_profile
    else:
        system_vars["AGENT_PROFILE"] = (
            "(No permanent memory yet. When the user asks you to remember something, update agent.md using filesystem.write_file.)"
        )

    # --- Context summary (changes only on compression) ---
    chat_api = context.get("chat_api")
    latest_summary = getattr(chat_api, "_latest_summary", "") if chat_api else ""
    if latest_summary and latest_summary.strip():
        system_vars["CONTEXT_SUMMARY"] = latest_summary
    else:
        system_vars["CONTEXT_SUMMARY"] = (
            "(Context compression has not been triggered yet; no historical summary available.)"
        )

    # --- Paths: session project cwd vs OpenSquad data root ---
    if _agent_dir:
        data_root = _data_root or _project_root or ""
        project_cwd = _resolve_session_project_cwd()
        if project_cwd:
            project_block = (
                f"**Current Project Working Directory** (user project for this session):\n"
                f"- Path: `{project_cwd}`\n"
                f"- This is the project folder chosen when the session started (folder picker / "
                f"working-directory API). Put code, documents, and collaboration outputs here.\n"
                f"- Default cwd for shell and file tools resolves to this path.\n"
            )
        else:
            project_block = (
                "**Current Project Working Directory**:\n"
                "- (Not set for this session yet.)\n"
                "- Do **not** treat the OpenSquad data root below as the user project. "
                "The project path is determined when a new session starts (folder picker).\n"
                "- Call `workspace.get_current()` and read `session_cwd` / `workspace_root` "
                "after the user selects a project folder.\n"
            )
        system_vars["AGENT_WORKSPACE"] = (
            f"{project_block}\n"
            f"**OpenSquad Data Root** (agent runtime / data storage — NOT the user project):\n"
            f"- Path: `{data_root}`\n"
            f"- Contains: agents/ (each agent's directory), data/ (logs/history), "
            f"collab_cards/, model_cards/, plugins/, skills/, and optionally workspace/ "
            f"(legacy default shared folder — only a fallback when no session project is set)\n"
            f"- Never assume user project files live here.\n\n"
            f"**Private Directory (your personal space)**:\n"
            f"- Path: `{_agent_dir}`\n"
            f"- Contains: agent.md (permanent memory), config.json (config), role.md (role definition), "
            f"data/ (session/state), skills/ (skill packages)\n"
            f"- Note: Only you read/write here. agent.md path: `{_agent_md_path}`\n\n"
            f"**Path helpers**:\n"
            f"- Use `workspace.get_current()` to query the live project cwd (`session_cwd` / `workspace_root`)\n"
            f"- Use `workspace.create(path)` / `workspace.migrate(source, target)` for OpenSquad data workspaces"
        )
    else:
        system_vars["AGENT_WORKSPACE"] = "(Agent working directory not configured.)"

    # --- Team collaboration info: stable part (collab card catalog, unchanged during tasks) ---
    collab_cfg = _agent_config.get("collaboration", {}) if _agent_config else {}
    if collab_cfg.get("enabled", False):
        collab_cards_dir = os.path.join(_project_root, "collab_cards") if _project_root else None

        # Scan collab_cards/ to build the catalog table
        catalog_rows = []
        if collab_cards_dir and os.path.isdir(collab_cards_dir):
            for fname in sorted(os.listdir(collab_cards_dir)):
                if not fname.endswith(".md"):
                    continue
                card_name = fname[:-3]
                fpath = os.path.join(collab_cards_dir, fname)
                fm = {}
                try:
                    with open(fpath, encoding="utf-8") as f:
                        raw = f.read()
                    if raw.startswith("---"):
                        end = raw.find("\n---", 3)
                        if end != -1:
                            for line in raw[3:end].strip().splitlines():
                                if ":" in line:
                                    k, _, v = line.partition(":")
                                    fm[k.strip()] = v.strip()
                except Exception:
                    pass
                desc = fm.get("description", "")
                tags = fm.get("tags", "")
                catalog_rows.append(f"| `{card_name}` | {desc} | {tags} |")

        if catalog_rows:
            catalog_table = "| Collab Card | Description | Tags |\n|-------------|-------------|------|\n" + "\n".join(
                catalog_rows
            )
        else:
            catalog_table = "(collab_cards/ directory is empty; no collab cards available)"

        system_vars["TEAM_COLLAB_CARDS"] = (
            "### Available Collaboration Plans (Collab Cards)\n\n"
            f"{catalog_table}\n\n"
            "**Usage (PM-driven)**:\n"
            "1. Based on the current task type, choose an appropriate collab card from the table above\n"
            '2. Call `start_collaboration(card="<name>")` to load the card and start collaboration\n'
            "   - The `suggested_roles` in the card are for reference only; the PM decides which members to invite\n"
            "   - You can omit members initially and invite after deciding via group chat\n"
            '3. Members call `join_collaboration(card="<name>")` upon receiving an invitation\n'
            '4. When done, the PM calls `end_collaboration(card="<name>")` and members call `leave_collaboration(card="<name>")`'
        )

    else:
        system_vars["TEAM_COLLAB_CARDS"] = ""

    # ======== dynamic_vars (dynamic layer, injected into user message prefix) ========

    # --- Runtime state (timestamp changes every turn) ---
    dynamic_vars["RUNTIME_STATE"] = (
        f"Current time: {now}\nInput source: {source}\nWorking state: {current_state}\nWakeup level: {current_wake}"
    )

    # --- Long-term memory (semantic recall based on current query, different each turn) ---
    mm = context.get("memory_manager") or _memory_manager
    recent_messages = context.get("recent_messages", [])

    if mm and query and query.strip():
        try:
            req_length = len(chat_api.req) if chat_api and hasattr(chat_api, "req") else 0
            logger.debug(f"[ContextBase] auto_recall: query='{query[:60]}' req_length={req_length}")
            memory_text = mm.auto_recall(recent_messages, query, req_length=req_length)
            if memory_text and memory_text.strip():
                logger.info(f"[ContextBase] MemoryManager injected {len(memory_text)} chars")
                dynamic_vars["MEMORY_CONTEXT"] = memory_text
            else:
                logger.debug("[ContextBase] auto_recall returned empty")
                dynamic_vars["MEMORY_CONTEXT"] = ""
        except Exception as e:
            logger.warning(f"[ContextBase] MemoryManager auto_recall failed: {e}")
            dynamic_vars["MEMORY_CONTEXT"] = "Long-term memory query failed; use the memory_query tool manually."
    else:
        if not mm:
            logger.debug("[ContextBase] auto_recall skipped: memory_manager is None")
        elif not query or not query.strip():
            logger.debug("[ContextBase] auto_recall skipped: empty query")
        dynamic_vars["MEMORY_CONTEXT"] = ""

    return system_vars, dynamic_vars
