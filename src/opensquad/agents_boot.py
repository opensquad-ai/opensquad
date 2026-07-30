"""
agents/boot.py - Generic Agent Launcher (config-driven)

Usage:
    python agents/boot.py --agent-dir agents/ultimate/
    python agents/boot.py --agent-dir agents/coder/

Three customizable parts of an Agent:
    1. context.py  -- Lifecycle hooks (init initialization + before_input dynamic injection)
    2. config.json -- Model config (api_key, model, base_url) + connection config
    3. tools config -- Tool imports and registration (config.json tools field + custom registration in init)

Each boot.py process = one independent Agent (independent global state).
"""

import argparse
import asyncio
import importlib
import importlib.util
import json
import logging
import os
import sys
import time
import warnings
from typing import Any

# Suppress noisy warnings globally
warnings.filterwarnings("ignore", category=ResourceWarning)
warnings.filterwarnings("ignore", message="The executor did not finishing joining")
if sys.platform == "win32":
    # Silence asyncio logs which often report "closed pipe" errors on exit
    logging.getLogger("asyncio").setLevel(logging.CRITICAL)

# -- Global anyio patch (Python 3.12+) --
# DISABLED: the patch in anyio_patches.py breaks anyio.connect_tcp() used by
# httpx for ALL LLM API calls (CancelledError leaks out of connect_tcp's
# move_on_after scope). The patch was meant to fix a leak of
# _num_cancels_requested on timeout, but it caused a much more severe bug:
# every chat() call fails immediately with CancelledError, the runner task
# is interrupted and restarted with initial_query=None, silently dropping
# every user message. The runner now has its own CancelledError safety net
# at the chat() call site (runner.py) — that handles the timeout-leak case
# without breaking normal network calls.
# See: anyio_patches.py for the original rationale (kept for reference).
# try:
#     from opensquad.anyio_patches import apply as _apply_anyio_patches
#
#     _apply_anyio_patches()
# except Exception:
#     pass  # Not fatal if patch fails — runner has its own safety net

# Project root (one level up from this file)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# -- Workspace initialization (must come before any syscfg-related code) --
# Load the user's last selected workspace and set it in syscfg so that all
# subsequent paths (including api_process cwd) point to the correct workspace
try:
    from opensquad.system_config import syscfg as _syscfg_early
    from opensquad.workspace_utils import load_last_workspace as _load_last_ws

    # Prefer explicit env from the launcher (authoritative for this process).
    _ws_env = os.environ.get("OPENSQUAD_WORKSPACE", "").strip() or os.environ.get("OPENSQUAD_USER_DATA", "").strip()
    if _ws_env and os.path.isdir(_ws_env):
        _syscfg_early.set_workspace(_ws_env)
        print(f"[Boot] Workspace (from env): {_ws_env}")
    else:
        _last_ws = _load_last_ws()
        if _last_ws and os.path.exists(_last_ws):
            _syscfg_early.set_workspace(_last_ws)
            print(f"[Boot] Workspace: {_last_ws}")
        else:
            print(f"[Boot] Workspace: {_syscfg_early.get_workspace()} (default)")
except Exception as _ws_err:
    print(f"[Boot] Warning: failed to load last workspace: {_ws_err}")

import contextlib

from opensquad import ToolRegistry, bus
from opensquad._context import AgentContext, set_current_context
from opensquad.agent_boot_phases import AgentBootPhases
from opensquad.event_pipeline import event_pipeline
from opensquad.input_hub import input_hub
from opensquad.log_setup import setup_logging as _setup_logging
from opensquad.message_queue import message_queue
from opensquad.message_router import message_router
from opensquad.sleep_controller import sleep_controller
from opensquad.system_config import syscfg
from opensquad.xml_parser import StreamingTagParser

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Model card hot-reload helpers (used by Runner for dynamic model switching)
# ---------------------------------------------------------------------------


def resolve_provider(model_cfg: dict) -> str:
    """Normalise the API protocol string from a model config dict.

    Applies the same smart auto-detection rules used at boot time:
    if api_protocol is generic `openai` / `openai_compat` but the model name
    contains `claude` or `gemini`, auto-switch the protocol to claude/google.
    """
    provider = model_cfg.get("api_protocol", "openai")
    model_name = model_cfg.get("model_name", "").lower()
    if provider in ("openai", "openai_compat") and ("claude" in model_name or "anthropic" in model_name):
        return "claude"
    if provider in ("openai", "openai_compat") and "gemini" in model_name:
        return "google"
    return provider


def create_chat_api_from_config(model_cfg: dict, system_prompt: str, stream_parser=None):
    """Factory: create the correct ChatAPI/ClaudeAPI/GoogleAPI from *model_cfg*.

    Used by Runner's hot-reload path when the provider class changes (e.g.
    switching from an OpenAI-compatible card to a Claude card).  The returned
    instance has an empty conversation history -- the caller is responsible for
    transferring `req` / state from the old instance.
    """
    provider = resolve_provider(model_cfg)
    is_image = model_cfg.get("is_image", False)
    is_video = model_cfg.get("is_video", False)
    if stream_parser is None:
        stream_parser = StreamingTagParser({})

    if provider in ("claude", "anthropic"):
        from opensquad.claude_api import ClaudeAPI

        return ClaudeAPI(
            api_key=model_cfg.get("api_key", ""),
            base_url=model_cfg.get("base_url", ""),
            model=model_cfg.get("model_name", ""),
            prompt=system_prompt,
            stream_parser=stream_parser,
            token_max=model_cfg.get("token_max", 100000),
            temperature=model_cfg.get("temperature", 0.3),
            is_img_model=is_image,
            is_audio_model=model_cfg.get("is_audio_model", False),
            is_video_model=is_video,
            use_file_api=model_cfg.get("use_file_api", False),
            file_api_size_threshold=model_cfg.get("file_api_size_threshold", 4 * 1024 * 1024),
            max_video_frames=min(model_cfg.get("max_video_frames", 8), 20),
            top_k=model_cfg.get("top_k", 0),
            is_think=model_cfg.get("is_think", False),
            thinking_budget_tokens=model_cfg.get("thinking_budget_tokens", 10000),
            reasoning_effort=model_cfg.get("reasoning_effort", "high"),
        )
    elif provider in ("google", "gemini"):
        from opensquad.google_api import GoogleAPI

        return GoogleAPI(
            api_key=model_cfg.get("api_key", ""),
            base_url=model_cfg.get("base_url", ""),
            model=model_cfg.get("model_name", ""),
            prompt=system_prompt,
            stream_parser=stream_parser,
            token_max=model_cfg.get("token_max", 1000000),
            temperature=model_cfg.get("temperature", 0.3),
            is_img_model=is_image,
            is_audio_model=model_cfg.get("is_audio_model", False),
            is_video_model=is_video,
            use_file_api=model_cfg.get("use_file_api", False),
            file_api_size_threshold=model_cfg.get("file_api_size_threshold", 4 * 1024 * 1024),
            is_image_output=model_cfg.get("is_image_output", False),
            top_k=model_cfg.get("top_k", 0),
        )
    else:
        from opensquad.chat_api import ChatAPI

        return ChatAPI(
            api_key=model_cfg.get("api_key", ""),
            base_url=model_cfg.get("base_url", ""),
            model=model_cfg.get("model_name", ""),
            prompt=system_prompt,
            stream_parser=stream_parser,
            token_max=model_cfg.get("token_max", 100000),
            temperature=model_cfg.get("temperature", 0.3),
            is_img_model=is_image,
            is_audio_model=model_cfg.get("is_audio_model", False),
            is_video_model=is_video,
            use_file_api=model_cfg.get("use_file_api", False),
            file_api_size_threshold=model_cfg.get("file_api_size_threshold", 4 * 1024 * 1024),
            is_audio_output=model_cfg.get("is_audio_output", False),
            audio_output_voice=model_cfg.get("audio_output_voice", "alloy"),
            frequency_penalty=model_cfg.get("frequency_penalty", 0.0),
            presence_penalty=model_cfg.get("presence_penalty", 0.0),
            enable_repetition_check=model_cfg.get("enable_repetition_check", False),
            is_think=model_cfg.get("is_think", False),
            reasoning_effort=model_cfg.get("reasoning_effort", "high"),
            is_image_output=model_cfg.get("is_image_output", False),
            image_size=model_cfg.get("image_size"),
            image_steps=model_cfg.get("image_steps"),
            image_cfg_scale=model_cfg.get("image_cfg_scale"),
        )


# ---------------------------------------------------------------------------
# Agent template auto-copy mechanism
# ---------------------------------------------------------------------------
def ensure_agent_in_workspace(agent_name: str) -> str:
    """
    Ensure the agent exists in the workspace. If not, automatically copy the template
    from the installation directory.

    Args:
        agent_name: Agent name (e.g. "ultimate")

    Returns:
        Full path to the agent in the workspace

    Raises:
        FileNotFoundError: If the agent also does not exist in the installation directory
    """
    import shutil

    # Workspace agent path
    workspace_agent_dir = syscfg.workspace_agents_dir(agent_name)

    # If it already exists, return directly
    if os.path.exists(workspace_agent_dir):
        logger.info(f"[Boot] Agent '{agent_name}' found in workspace: {workspace_agent_dir}")
        return workspace_agent_dir

    # Not found, try to copy from the installation directory
    _builtin_root = syscfg.get_builtin_root()
    if _builtin_root is None:
        _builtin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    builtin_agent_dir = os.path.join(_builtin_root, "agents", agent_name)

    if not os.path.exists(builtin_agent_dir):
        raise FileNotFoundError(
            f"Agent '{agent_name}' not found in workspace or installation directory.\n"
            f"  Workspace: {workspace_agent_dir}\n"
            f"  Builtin: {builtin_agent_dir}"
        )

    logger.info(f"[Boot] Agent '{agent_name}' not found in workspace. Copying from installation directory...")
    logger.info(f"  Source: {builtin_agent_dir}")
    logger.info(f"  Target: {workspace_agent_dir}")

    try:
        # Ensure parent directory exists
        os.makedirs(os.path.dirname(workspace_agent_dir), exist_ok=True)

        # Copy the entire agent directory
        shutil.copytree(builtin_agent_dir, workspace_agent_dir)

        logger.info(f"[Boot] Agent '{agent_name}' successfully copied to workspace")
        return workspace_agent_dir

    except Exception as e:
        logger.error(f"[Boot] Failed to copy agent '{agent_name}' to workspace: {e}")
        raise


# ---------------------------------------------------------------------------
# Tool module mapping: config.json tools string -> actual module path
# ---------------------------------------------------------------------------
TOOL_MODULES = {
    # --- Core framework tools (remain built-in for now) ---
    "system": "opensquad.tools.system",
    "filesystem": "opensquad.tools.filesystem",
    "im": "opensquad.tools.im",
    "agent_setup": "opensquad.tools.agent_setup",
    "long_memory": "opensquad.tools.long_memory",
    "collaboration": "opensquad.tools.collaboration",
    "delegate_task": "opensquad.tools.delegate",
    "workspace": "opensquad.tools.workspace",
    "task_watch": "opensquad.tools.task_watch",
    "agent_mode": "opensquad.tools.agent_mode_tools",
    "choice_tools": "opensquad.tools.choice_tools",
    "goal": "opensquad.tools.goal_tools",
    # --- Plugin-owned tools: resolved via PluginManager, not direct import here ---
    # websearch        -> plugins/websearch/
    # vision           -> plugins/vision/
    # sequential_think -> plugins/sequential_think/
    # media            -> plugins/media/
    # whisper_transcribe -> plugins/whisper/
    # mcp_query        -> plugins/mcp_query/
    # feishu_send      -> plugins/feishu/          (Phase 1)
    # telegram_send    -> plugins/telegram/        (Phase 1)
}

# Core-level tools get detailed docs in prompts; extended-level get summary only
CORE_TOOLS = {
    "system",
    "filesystem",
    "im",
    "long_memory",
    "collaboration",
    "agent_mode",
    "choice_tools",
    "goal",
}

# Mandatory tools: automatically injected into every agent regardless of config.json tools list.
# These are the built-in core tools shown in the UI as "系统内置".
MANDATORY_TOOLS = {
    "system",
    "filesystem",
    "agent_setup",
    "im",
    "collaboration",
    "delegate_task",
    "workspace",
    "task_watch",
    "agent_mode",
    "choice_tools",
    "goal",
}

BOOT_PHASES = AgentBootPhases(
    tool_modules=TOOL_MODULES,
    mandatory_tools=MANDATORY_TOOLS,
    core_tools=CORE_TOOLS,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
# Apply rotating file handler via unified log_setup
_setup_logging(logging.getLogger(), "agent_run.log")
# Suppress httpx verbose HTTP request logging (noisy proxy calls)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


# ===================================================================
# Part 1: context.py -- Lifecycle hooks
# ===================================================================


def load_context_module(agent_dir: str):
    """
    Load the context.py module from the agent directory (optional).

    context.py can define two functions:
        init(agent_context)           -- Called once at startup for custom initialization
        before_input(agent_context)   -- Called before each LLM call; returns a dict of dynamic variables

    Returns the loaded module, or None if it does not exist.
    """
    context_path = os.path.join(agent_dir, "context.py")
    if not os.path.exists(context_path):
        return None

    try:
        spec = importlib.util.spec_from_file_location("agent_context_module", context_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception as e:
        logging.error(f"[Boot] Failed to load context.py: {e}")
        return None


# ===================================================================
# Part 2: config.json -- Config loading and prompt building
# ===================================================================


def load_config(agent_dir: str) -> dict:
    """Load config.json from the agent directory (supports BOM) and validate schema."""
    config_path = os.path.join(agent_dir, "config.json")
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config not found: {config_path}")
    with open(config_path, encoding="utf-8-sig") as f:
        raw = json.load(f)

    from opensquad.agent_config_schema import apply_config_defaults

    apply_config_defaults(raw)

    # Config Validation: validate config.json schema at boot time
    from opensquad.config_schema import ConfigValidationError, validate_agent_config

    try:
        validated = validate_agent_config(raw)
    except ConfigValidationError:
        raise

    # Inherit api_key from model card when config has _card reference but empty api_key.
    # This ensures users who configure their key in the model card (via UI or file edit)
    # don't need to also manually set it in every agent's config.json.
    model = validated.get("model", {})
    card_name = model.get("_card", "")
    api_key = model.get("api_key", "")
    if card_name and not api_key:
        try:
            from opensquad.model_switch import resolve_card

            card = resolve_card(card_name)
            card_api_key = card.get("api_key", "").strip()
            if card_api_key:
                validated["model"]["api_key"] = card_api_key
                logger.info(
                    "[Boot] Inherited api_key from model card '%s' for agent '%s'",
                    card_name,
                    validated.get("agent_name", "?"),
                )
        except Exception as exc:
            logger.debug(
                "[Boot] Could not resolve model card '%s' to inherit api_key: %s",
                card_name,
                exc,
            )

    return validated


def _resolve_tool_format(config: dict) -> str:
    """
    Return the tool call format based on config.model.tool_call_mode and model capabilities.

    Returns:
        "fc"  -- Use Native Function Calling (corresponds to *_fc.md template)
        "xml" -- Use XML tool calls (corresponds to *_xml.md template)
    """
    model_cfg = config.get("model", {})
    mode = model_cfg.get("tool_call_mode", "auto")
    if mode == "native":
        return "fc"
    elif mode == "xml":
        return "xml"
    else:  # auto -- query model capabilities database
        from opensquad.model_capabilities import get_model_capability

        provider = model_cfg.get("api_protocol", "openai_compat")
        model_name = model_cfg.get("model_name", "")
        cap = get_model_capability(model_name, provider)
        # Default to function calling (fc) when model is not in DB — most modern models support it
        if not cap:
            return "fc"
        return "fc" if cap.supports_function_calling else "xml"


def build_system_prompt(config: dict, agent_dir: str) -> str:
    """
    Build the system prompt:
    1. Read base.md (general engine rules)
    2. Read role.md (role card)
    3. Inject role.md content into the {{EXPERT_ROLE_CARD}} placeholder
    4. Collaboration protocol is injected per-turn via {{TEAM_COLLAB_CARDS}}
       in context_base.py (not here).
    """
    prompt_cfg = config.get("prompt", {})
    model_cfg = config.get("model", {})
    is_think = model_cfg.get("is_think", False)

    _builtin_root = syscfg.get_builtin_root()
    if _builtin_root is None:
        _builtin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    elif os.path.basename(_builtin_root).lower() == "opensquad":
        _builtin_root = os.path.dirname(_builtin_root)

    prompt_root = os.path.join(_builtin_root, "src", "opensquad", "prompts")
    legacy_prompt_root = os.path.join(_builtin_root, "src", "prompts")
    older_legacy_prompt_root = os.path.join(_builtin_root, "prompts")
    frozen_prompt_root = os.path.join(_builtin_root, "opensquad", "prompts")
    if not os.path.isdir(prompt_root):
        if os.path.isdir(frozen_prompt_root):
            prompt_root = frozen_prompt_root
        elif os.path.isdir(legacy_prompt_root):
            prompt_root = legacy_prompt_root
        elif os.path.isdir(older_legacy_prompt_root):
            prompt_root = older_legacy_prompt_root
    # If prompt.base is explicitly specified, use it; otherwise auto-select one of four templates
    # based on is_think + tool_call_mode
    base_rel = prompt_cfg.get("base")
    if base_rel:
        base_path = os.path.join(_builtin_root, base_rel)
    else:
        tool_fmt = _resolve_tool_format(config)  # "fc" or "xml"
        prefix = "base" if is_think else "thought"  # native thinking model vs. requires explicit <thought>
        base_path = os.path.join(prompt_root, f"{prefix}_{tool_fmt}.md")

    logger.info(f"[Boot] is_think={is_think}, base prompt: {base_path}")
    if not os.path.exists(base_path):
        raise FileNotFoundError(f"Base prompt not found: {base_path}")
    from opensquad.prompt_includes import read_prompt_with_includes

    # Includes resolve relative to the directory containing the entry template
    # (src/prompts/ or the bundled prompts/ root).
    base_prompt = read_prompt_with_includes(base_path, os.path.dirname(base_path))

    # role.md path (relative to agent directory)
    role_file = prompt_cfg.get("role", "role.md")
    role_path = os.path.join(agent_dir, role_file)
    role_content = ""
    if os.path.exists(role_path):
        with open(role_path, encoding="utf-8") as f:
            role_content = f.read()

    # Inject role card
    system_prompt = base_prompt.replace("{{EXPERT_ROLE_CARD}}", role_content)

    # Collaboration protocol is injected per-turn via {{TEAM_COLLAB_CARDS}} by
    # context_base.py; nothing to do here at boot time.

    # Inject MCP usage guide (only when mcp.enabled = true)
    mcp_cfg = config.get("mcp", {})
    if mcp_cfg.get("enabled", True):
        mcp_guide = _build_mcp_guide()
        system_prompt = system_prompt.replace("{{MCP_GUIDE}}", mcp_guide)
        # {{MCP_CURRENT_STATE}} is kept as a placeholder; runner.py replaces it each turn with the live state.
        # Do NOT replace it at boot time, otherwise the placeholder disappears and runner cannot update it.
        logger.info("[Boot] MCP guide injected")
    else:
        system_prompt = system_prompt.replace("{{MCP_GUIDE}}", "(MCP service is not enabled.)")
        system_prompt = system_prompt.replace("{{MCP_CURRENT_STATE}}", "(MCP service is not enabled.)")

    # Realtime / mouthpiece voice: rules once in system prompt; user turns only carry <realtime_voice>.
    voice_cfg = config.get("voice") or {}
    if isinstance(voice_cfg, dict) and (
        any(voice_cfg.get(k) for k in ("asr_card", "tts_card", "realtime_card"))
        or (
            voice_cfg.get("base_url")
            and voice_cfg.get("api_key")
            and any(voice_cfg.get(k) for k in ("asr_model", "tts_model", "realtime_model"))
        )
    ):
        system_prompt = system_prompt.rstrip() + "\n\n" + _VOICE_REALTIME_PROMPT_SECTION
        logger.info("[Boot] Realtime voice prompt section injected")

    return system_prompt


_VOICE_REALTIME_PROMPT_SECTION = """## Realtime voice mode

When a user message is wrapped in `<realtime_voice id="...">...</realtime_voice>` (or `<realtime_voice>...</realtime_voice>`), it is a live voice utterance from the Agent Web voice UI. Your reply will be spoken aloud by TTS.

Rules for these turns:
1. Answer normally: use tools if needed, reply in the user's language, keep it speakable and reasonably concise.
2. Plain speech only: no emoji, emoticons, markdown, or decorative symbols (no *, **, #, backticks, _, ~, |, decorative brackets, ★, •, →, etc.). TTS would read them aloud. Use plain sentences and commas/periods only.
3. If you deliberately choose not to speak this turn, reply with ONLY `[VOICE_NO_REPLY]` and nothing else.
4. Do not mention the realtime_voice tag, VoiceAsk, or mouthpiece in a normal answer.
"""


def _build_mcp_guide() -> str:
    """Build the MCP usage guide (generic version, without hard-coding specific server lists)"""
    return """## MCP (Model Context Protocol) Service Usage Guide

### What is MCP?
MCP is a plugin system that extends your capabilities, allowing you to call external tools (e.g. browser, filesystem, database, etc.).
The currently enabled MCP services and their tool details are shown in the "Current MCP Service Status" section below (updated in real time).

### Tool Naming Convention
- Format: `mcp__{server_name}__{tool_name}`
- Example: `mcp__filesystem__read_file`, `mcp__windows-cli__execute_command`

### How to Call MCP Tools

**Direct call (recommended)** -- when you know the full tool name:
```
<tool_call name="mcp__{server}__{tool}">
  <arguments>{"param": "value"}</arguments>
</tool_call>
```

**Query available tools** -- when you are unsure:
- `mcp_query.list_servers()` -- List all MCP server statuses
- `mcp_query.get_all_tools()` -- View all available tool details

**Dynamically add new services** -- when you need new capabilities:
- `mcp_query.add_server(server_name, command, args, timeout)` -- Install immediately, no restart needed

### Notes
1. All Agents share a unified MCP configuration managed centrally in `data/mcp_config.json`
2. You can enable/disable services through the MCP Manager in the admin panel

---
"""


# ===================================================================
# Part 3: Tool registration
# ===================================================================


def register_builtin_tools_sync(config: dict, registry: ToolRegistry, agent_dir: str) -> None:
    """
    Synchronous version: register only built-in tool modules from TOOL_MODULES.
    Plugin-owned tools are intentionally excluded here and loaded by plugin_manager.
    Safe to call from a synchronous context (e.g. do_plugin_reload).
    registry.register is idempotent -- already-registered tools are silently overwritten
    with the same module reference, so calling this during hot-reload is safe.
    """
    BOOT_PHASES.register_builtin_tools(config, registry, agent_dir)


async def register_tools(config: dict, registry: ToolRegistry, agent_dir: str) -> ToolRegistry:
    """
    Dynamically import and register tool modules based on the tools list in config.json.
    Two registration methods are supported:
        1. config.json tools field -- declarative (string list mapped to built-in modules)
        2. context.py init()       -- programmatic (call registry.register directly in init)

    MCP initialization is intentionally deferred to a background task in main() so
    Gateway WS registration and the Runner can start without waiting on slow MCP
    servers (e.g. Playwright npx bootstrap).
    """
    BOOT_PHASES.register_builtin_tools(config, registry, agent_dir)
    return registry


async def _initialize_mcp_background(
    config: dict,
    registry: ToolRegistry,
    agent_dir: str,
    agent_logger: logging.Logger,
    runner: Any | None = None,
) -> None:
    """Load MCP tools in the background; hot-reload runner plugin tools when done."""
    try:
        await BOOT_PHASES.initialize_runtime_infrastructure(config, registry, agent_dir)
        agent_logger.info("[Boot] MCP runtime ready (background)")
        if runner is not None and getattr(runner, "_plugin_manager", None):
            pm = runner._plugin_manager
            pm.reload_plugins(
                registry=runner.tool_registry,
                agent_id=runner._agent_id,
                agent_tool_names=runner._agent_tool_names,
            )
            pm.register_tools_to_agent(
                registry=runner.tool_registry,
                agent_id=runner._agent_id,
                agent_tool_names=runner._agent_tool_names,
                agent_tool_levels=runner._agent_tool_levels,
            )
        # Log final tool inventory after MCP + plugin hot-reload completes.
        # This is the definitive list — if a namespace is missing here, the
        # agent will not see that tool at runtime.
        registry.log_inventory(agent_logger)
    except Exception as exc:
        agent_logger.warning(f"[Boot] MCP background init failed: {exc}")


# ===================================================================
# Connections and routing
# ===================================================================


async def setup_response_router(logger: logging.Logger):
    """
    Response router -- listens to Runner output and prints it to the console.
    Does not auto-forward to group chat (AI must explicitly call im.send_message).
    """

    def on_ai_response(data):
        try:
            content = ""
            content = data.get("data", "") or data.get("content", "") if isinstance(data, dict) else str(data)
            if not content:
                return
            print(f"\n[AI Reply]: {content[:100]}...")
        except Exception as e:
            logger.error(f"[Router] Error: {e}")

    bus.subscribe("to_user", on_ai_response)
    bus.subscribe("to_user_final", on_ai_response)
    bus.subscribe("to_user_end_task", on_ai_response)


# ===================================================================
# Main entry point
# ===================================================================


async def main(agent_dir: str, override_port: int | None = None):
    """
    Main entry point: three-part initialization flow
    1. Load config.json (Part 2)
    2. Register tools (Part 3)
    3. Load context.py and call init (Part 1)
    4. Pass the before_input hook to AgentRunner
    """
    # If the path is relative and contains no path separator, it may be an agent name
    # e.g.: --agent-dir ultimate  or  --agent-dir agents/ultimate
    if not os.path.isabs(agent_dir):
        # Try to extract the agent name
        parts = agent_dir.replace("\\", "/").split("/")
        if "agents" in parts:
            # Path format: agents/ultimate
            agent_name = parts[-1]
        else:
            # Direct name: ultimate
            agent_name = parts[-1] if len(parts) == 1 else parts[-1]

        # Ensure the agent exists in the workspace (auto-copy if not)
        try:
            agent_dir = ensure_agent_in_workspace(agent_name)
        except FileNotFoundError:
            # If auto-copy fails, fall back to the provided path
            logger.warning(f"[Boot] Could not auto-copy agent, using provided path: {agent_dir}")
            agent_dir = os.path.abspath(agent_dir)
    else:
        agent_dir = os.path.abspath(agent_dir)

    # 0. Configure independent log file for each agent
    # Log path: agents/{agent_name}/data/logs/agent.log
    agent_name = os.path.basename(agent_dir)
    agent_log_dir = os.path.join(agent_dir, "data", "logs")
    os.makedirs(agent_log_dir, exist_ok=True)

    # Reconfigure root logger to write to the agent-specific log directory
    _setup_logging(
        logging.getLogger(),
        "agent.log",  # Every agent uses agent.log
        force=True,
        log_dir=agent_log_dir,  # Specify the agent-specific directory
    )

    boot_main_t0 = time.perf_counter()
    config = load_config(agent_dir)

    agent_id = config.get("agent_id", "unknown")
    agent_name = config.get("agent_name", "Unknown")
    agent_logger = logging.getLogger(f"Agent[{agent_id}]")
    agent_logger.info(f"[BootPerf] boot_main_start=0ms agent_id={agent_id}")

    # Expose config to plugins/tools (ASR/TTS Translate card resolution, etc.)
    try:
        from opensquad import agent_runtime_context as _arc
        from plugins.step_voice import step_voice_tools as _sv_tools

        _arc.set_context(config=config, agent_id_value=agent_id, agent_dir_value=agent_dir)
        _sv_tools.set_agent_config(config)
    except Exception as e:
        agent_logger.debug("[Boot] agent_runtime_context / asr_tts inject skipped: %s", e)

    # Auto-enable ASR/TTS Translate plugin when voice is configured (cards or inline)
    voice_cfg = config.get("voice") or {}
    if isinstance(voice_cfg, dict) and (
        any(voice_cfg.get(k) for k in ("asr_card", "tts_card", "realtime_card"))
        or (
            voice_cfg.get("base_url")
            and voice_cfg.get("api_key")
            and any(voice_cfg.get(k) for k in ("asr_model", "tts_model", "realtime_model"))
        )
    ):
        tools_list = list(config.get("tools") or [])
        changed = False
        if "asr_tts" not in tools_list:
            tools_list.append("asr_tts")
            changed = True
        # Migrate legacy plugin id → generic name
        if "step_voice" in tools_list:
            tools_list = [t for t in tools_list if t != "step_voice"]
            if "asr_tts" not in tools_list:
                tools_list.append("asr_tts")
            changed = True
        if changed:
            config["tools"] = tools_list
            agent_logger.info("[Boot] Auto-enabled asr_tts plugin (voice config present)")

    # ── Phase 1b: Create AgentContext ──
    agent_ctx = AgentContext(
        event_bus=bus,
        input_hub=input_hub,
        message_queue=message_queue,
        state_manager=None,
        session_manager=None,
        sleep_controller=sleep_controller,
        event_pipeline=event_pipeline,
        message_router=message_router,
        agent_id=agent_id,
        agent_name=agent_name,
        config_path=os.path.join(agent_dir, "config.json"),
        agent_dir=agent_dir,
    )
    set_current_context(agent_ctx)

    # Allow overriding port config via command-line argument
    if override_port:
        if "web_server" not in config:
            config["web_server"] = {"enabled": True}
        config["web_server"]["port"] = override_port
        agent_logger.info(f"[Boot] Port overridden by command line: {override_port}")

    # Gateway default config: auto-enable and use default URL if not configured
    gw = config.get("gateway")
    if not isinstance(gw, dict):
        config["gateway"] = {"enabled": True, "url": syscfg.gateway_register_url()}
    else:
        if not gw.get("url"):
            gw["url"] = syscfg.gateway_register_url()
        if "enabled" not in gw:
            gw["enabled"] = True

    agent_logger.info(f"Booting agent: {agent_name} ({agent_id})")

    # 0. Bind the event loop
    bus.set_loop(asyncio.get_running_loop())

    # 0.0.1 Register event loop with task_supervisor (for background monitor)
    from opensquad.task_supervisor import task_supervisor

    task_supervisor.set_event_loop(asyncio.get_running_loop())

    # 0.0.2 Start lightweight health-check HTTP server (P0-2: Launcher probes this for hang detection)
    from opensquad.health_server import start_health_server

    _health_port = start_health_server()
    agent_logger.info(f"[Boot] Health server on port {_health_port}")

    # P0-2: Runtime infrastructure init (InputHub + isolated session/state runtime)
    runtime_artifacts = await BOOT_PHASES.initialize_agent_runtime(
        config=config,
        agent_dir=agent_dir,
        input_hub=input_hub,
        agent_logger=agent_logger,
    )
    data_dir = runtime_artifacts.data_dir

    # P0-2: Write health port to runtime registry so Launcher can discover it quickly
    try:
        import json as _json

        _runtime_dir = syscfg.workspace_metadata_dir("runtime")
        os.makedirs(_runtime_dir, exist_ok=True)
        _registry_file = os.path.join(_runtime_dir, f"agent_{agent_id}.json")
        if os.path.isfile(_registry_file):
            with open(_registry_file, encoding="utf-8") as _f:
                _reg = _json.load(_f)
        else:
            _reg = {}
        _reg["health_port"] = _health_port
        with open(_registry_file, "w", encoding="utf-8") as _f:
            _json.dump(_reg, _f, ensure_ascii=False, indent=2)
        agent_logger.info(f"[Boot] Health port {_health_port} registered")
    except Exception as _e:
        agent_logger.warning(f"[Boot] Failed to register health port: {_e}")

    # 1. Build system prompt (config.json + role.md)
    system_prompt = build_system_prompt(config, agent_dir)

    # 2. Initialize ChatAPI (LLM)
    chat_runtime = BOOT_PHASES.initialize_chat_runtime(
        config=config,
        system_prompt=system_prompt,
        history_dir=runtime_artifacts.history_dir,
        agent_logger=agent_logger,
    )
    chat_api = chat_runtime.chat_api
    agent_ctx.chat_api = chat_api
    provider = chat_runtime.provider
    model_cfg = chat_runtime.model_cfg

    # 3. Start connections ASAP (Web/Bridge/Gateway) to minimize time-to-chat-ready.
    # Heavy tool/plugin/MCP initialization continues in background after chat entrypoint is up.
    await BOOT_PHASES.setup_connections(config, agent_logger, data_dir)
    agent_logger.info(
        f"[BootPerf] phase_ready_chat={int((time.perf_counter() - boot_main_t0) * 1000)}ms agent_id={agent_id}"
    )

    # 4. Register tools (config.json tools field + MCP)
    tool_registry = ToolRegistry()
    agent_ctx.tool_registry = tool_registry
    await register_tools(config, tool_registry, agent_dir)

    # 3.05 Inject runtime config for the built-in `delegate_task` tool.
    BOOT_PHASES.initialize_delegate_tool(
        provider=provider,
        model_cfg=model_cfg,
        system_prompt=system_prompt,
        tool_registry=tool_registry,
        agent_logger=agent_logger,
    )

    # 3.05 ═══ Start Runner EARLY (before slow plugins) so urgent commands are never lost ═══
    # The GatewayAdapter has already connected by now and messages are arriving via
    # input_hub.  The Runner's main loop is the only consumer of input_hub — if it hasn't
    # started yet, commands like __NEW_SESSION__ pile up in the urgent queue and get lost
    # on agent restart.  We create the Runner with plugin_manager=None and later inject
    # the real one after plugins finish loading (hot-reload will pick up new tools).
    early_runner_artifacts = BOOT_PHASES.start_early_runner(
        chat_api=chat_api,
        tool_registry=tool_registry,
        agent_id=agent_id,
        config=config,
        agent_dir=agent_dir,
        vision_config=chat_runtime.vision_config,
        boot_main_t0=boot_main_t0,
        agent_logger=agent_logger,
        agent_context=agent_ctx,
        session_manager=runtime_artifacts.session_manager,
        state_manager=runtime_artifacts.state_manager,
    )
    _early_runner = early_runner_artifacts.runner
    _runner_task = early_runner_artifacts.runner_task

    # Register model-switch ASAP — early runner already consumes WS commands.
    # Waiting until after plugins/MCP used to leave `_runner is None` when boot
    # was cancelled mid-way (UI: switch snaps back to default after ~1s).
    try:
        from opensquad.model_switch import init as _model_switch_init

        _model_switch_init(_early_runner, os.path.join(agent_dir, "config.json"))
        agent_logger.warning(
            "[Boot] model_switch coordinator ready (early) config=%s",
            os.path.join(agent_dir, "config.json"),
        )
    except Exception as _e:
        agent_logger.warning(f"[Boot] model_switch early init failed: {_e}")

    asyncio.create_task(
        _initialize_mcp_background(
            config,
            tool_registry,
            agent_dir,
            agent_logger,
            runner=_early_runner,
        )
    )

    plugin_runtime = BOOT_PHASES.initialize_plugin_runtime(
        config=config,
        agent_dir=agent_dir,
        project_root=syscfg.get_workspace(),
        data_dir=data_dir,
        tool_registry=tool_registry,
        early_runner=_early_runner,
        agent_name=agent_name,
        agent_logger=agent_logger,
        boot_main_t0=boot_main_t0,
    )
    skills = plugin_runtime.skills
    memory_manager_instance = plugin_runtime.memory_manager

    context_runtime = await BOOT_PHASES.initialize_context_runtime(
        config=config,
        agent_dir=agent_dir,
        agent_id=agent_id,
        agent_name=agent_name,
        chat_api=chat_api,
        tool_registry=tool_registry,
        input_hub=input_hub,
        project_root=syscfg.get_workspace(),
        memory_manager=memory_manager_instance,
        load_context_module=load_context_module,
        agent_logger=agent_logger,
    )
    context_module = context_runtime.context_module
    hooks = context_runtime.hooks

    # 5. Start response router
    await setup_response_router(agent_logger)

    # 6. CLI listener
    async def cli_loop():
        loop = asyncio.get_event_loop()
        while True:
            try:
                text = await loop.run_in_executor(None, input, "")
                if text and text.strip():
                    input_hub.push(text, source="cli")
            except (EOFError, KeyboardInterrupt, asyncio.CancelledError):
                break

    asyncio.create_task(cli_loop())

    # 8. Startup info
    print("\n" + "=" * 50)
    print(f"Agent Started: {agent_name} ({agent_id})")
    print(f"  Type: {config.get('agent_type', 'unknown')}")
    if config.get("web_server", {}).get("enabled"):
        web_port = config["web_server"].get("port", syscfg.port("agent_web_server"))
        print(f"  Web: http://localhost:{web_port}")
    if config.get("gateway", {}).get("enabled"):
        print(f"  Gateway ID: {agent_id}")
    if config.get("group_chat", {}).get("enabled"):
        print("  Group Chat: Enabled")
    if context_module:
        print(f"  Hooks: {', '.join(hooks.keys()) or 'init only'}")
    if skills:
        print(f"  Skills: {', '.join(s.display_name for s in skills)}")
    print("=" * 50 + "\n")

    # 9. Agent is now fully initialized — update the early Runner with remaining fields
    BOOT_PHASES.finalize_runner_runtime(
        early_runner=_early_runner,
        hooks=hooks,
        memory_manager=memory_manager_instance,
        boot_main_t0=boot_main_t0,
        agent_id=agent_id,
        agent_logger=agent_logger,
    )

    # model_switch.init already ran right after start_early_runner; refresh the
    # runner/config handle here in case boot mutated paths (idempotent).
    try:
        from opensquad.model_switch import init as _model_switch_init

        _model_switch_init(_early_runner, os.path.join(agent_dir, "config.json"))
    except Exception as _e:
        agent_logger.warning(f"[Boot] model_switch coordinator re-init failed: {_e}")

    await BOOT_PHASES.await_runner_shutdown(
        early_runner=_early_runner,
        runner_task=_runner_task,
        session_manager=runtime_artifacts.session_manager,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Boot an AI agent from config")
    parser.add_argument("--agent-dir", required=True, help="Path to agent directory containing config.json")
    parser.add_argument("--port", type=int, help="Override web server port")
    args = parser.parse_args()

    with contextlib.suppress(KeyboardInterrupt, SystemExit):
        asyncio.run(main(args.agent_dir, override_port=args.port))
