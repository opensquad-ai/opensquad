"""ContextBuilder — Extracted from AgentRunner (P1-1).

Responsible for building the system prompt and dynamic context prefix
for each conversation turn. This was previously part of AgentRunner._setup_prompt(),
which was ~200 lines and made the runner class too large.

The builder is stateless (per-turn): all mutable state is passed in and returned.
"""

import asyncio
import logging
import os
from typing import Any

from opensquad.context_base import inject_standard
from opensquad.skill_loader import build_skills_prompt, get_loaded_skills

logger = logging.getLogger(__name__)


# ── Module-level helpers (moved from runner.py) ──


def build_context_prefix(dynamic_parts: dict) -> str:
    """
    Assemble the dynamic variable dict into a system context block prepended to the user message.
    Empty values are skipped automatically to avoid extra blank blocks.
    Fixed order: RUNTIME_STATE -> TASK_STATE -> MEMORY_CONTEXT -> other custom keys
    """
    _ORDER = [
        ("RUNTIME_STATE", "Runtime State"),
        ("TASK_STATE", "Task Plan"),
        ("MEMORY_CONTEXT", "Long-term Memory (Recalled This Round)"),
    ]
    _known_keys = {k for k, _ in _ORDER}

    sections = []
    for key, label in _ORDER:
        val = dynamic_parts.get(key, "")
        if val and str(val).strip():
            sections.append(f"### {label}\n\n{val}")

    # Append unknown custom keys (from before_input hook)
    for key, val in dynamic_parts.items():
        if key not in _known_keys and val and str(val).strip():
            sections.append(f"### {key}\n\n{val}")

    if not sections:
        return ""

    body = "\n\n---\n\n".join(sections)
    return f"[System Context - Updated Each Round]\n\n{body}\n\n[/System Context]\n\n"


def build_dynamic_mcp_state(mcp_adapter) -> str:
    """Build dynamic MCP state text reflecting currently available MCP servers and tools."""
    try:
        servers = mcp_adapter.list_servers()
        if not servers:
            return ""

        lines = ["### Active MCP Services", ""]
        for server_name, info in servers.items():
            connected = info.get("connected", False)
            tool_count = info.get("tool_count", 0)
            tools = info.get("tools", [])
            status_mark = "[OK]" if connected else "[FAIL]"
            lines.append(f"**{status_mark} {server_name}**")
            lines.append(f"- Status: {'Connected' if connected else 'Disconnected'}")
            lines.append(f"- Available tools: {tool_count}")
            if tools and len(tools) <= 10:
                lines.append(f"- Tool list: {', '.join(tools)}")
            elif tools:
                lines.append(f"- Tool list: {', '.join(tools[:5])}... ({tool_count} total)")
            lines.append("")
        lines.append("---")
        lines.append("[Tip] To add a new MCP service, call `mcp_query.add_server`")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"[ContextBuilder] Error building MCP state: {e}")
        return ""


# ── ContextBuilder class ──


class ContextBuilder:
    """Builds system prompt and dynamic context for each turn.

    Stateless: create a new instance per turn, or reuse with fresh `build()` calls.
    """

    # Anti-repetition reminder appended to every system prompt
    _REPETITION_REMINDER = (
        "\n\n[STRICT OPERATIONAL RULES]\n"
        "1. ANTI-REPETITION:\n"
        "   - Do NOT repeat your previous sentences or conversational filler.\n"
        "   - If you find yourself outputting the same phrase as before, STOP immediately and proceed with a tool call or a new perspective.\n"
        "   - When in a task-oriented loop, focus strictly on tool output and next actions. "
        "Do NOT output repetitive 'I will now execute...' statements if you already said them.\n"
        "2. COMMAND SELECTION:\n"
        "   - For commands that complete quickly (e.g., git, pip install, python scripts < 2min), use `system.run_session_job` (persistent shell session).\n"
        "   - For LONG-RUNNING services, SERVERS, or BACKGROUND tasks (e.g., npm run dev, starting an API, large builds), "
        "YOU MUST USE `system.start_job` with non-blocking mode and poll by check_job."
    )

    def __init__(
        self,
        chat_api,
        tool_call_strategy,
        task_manager,
        plugin_manager=None,
        hooks: dict | None = None,
        memory_manager=None,
        config_path: str = "",
    ):
        self.chat_api = chat_api
        self.tool_call_strategy = tool_call_strategy
        self.task_manager = task_manager
        self.plugin_manager = plugin_manager
        self.hooks = hooks or {}
        self.memory_manager = memory_manager
        self.config_path = config_path

        # Prompt snapshot / cache state (moved from AgentRunner)
        self._has_prompt_snapshot = False
        self._mcp_in_system_prompt: str = ""
        self._last_base_system_prompt: str = ""

    async def build(
        self,
        last_user_input: str,
        current_input_source: str,
        current_turn: int,
        current_round: int,
    ) -> tuple[str, str, dict[str, Any], bool]:
        """Build the system prompt and dynamic context for this turn.

        Returns:
            system_prompt: The final system prompt string
            dynamic_prefix: The dynamic context prefix to prepend to user message
            llm_params: Dict with "tools" and "tool_choice" for the LLM call
            is_changed: Whether the system prompt changed vs previous turn
        """
        # Start from template
        base = self.chat_api.get_template()
        dynamic_parts: dict[str, Any] = {}

        # Layer 1: Engine built-in injection (tool call strategy)
        llm_params = self.tool_call_strategy.prepare_llm_call(base)
        final = llm_params["system_prompt"]

        # Skills injection (uses json_cache to avoid re-reading unchanged config)
        _prompt_preload_cfg = None
        if self.config_path and os.path.isfile(self.config_path):
            try:
                from opensquad.json_cache import load_json_cached

                _agent_cfg = load_json_cached(self.config_path)
                _prompt_preload_cfg = _agent_cfg.get("prompt_preload") if _agent_cfg else None
            except Exception:
                pass
        skills_prompt = build_skills_prompt(get_loaded_skills(), _prompt_preload_cfg)
        final = final.replace("{{SKILLS_INSTRUCTIONS}}", skills_prompt)
        final = final.replace("{{TASK_STATE}}", "")

        # MCP two-stage injection
        try:
            from opensquad.tools.mcp_adapter import get_mcp_adapter

            mcp_adapter = get_mcp_adapter()
            mcp_state = build_dynamic_mcp_state(mcp_adapter) if mcp_adapter else ""
        except Exception as e:
            logger.debug(f"[ContextBuilder] Failed to get MCP state: {e}")
            mcp_state = ""

        if "{{MCP_CURRENT_STATE}}" in final:
            final = final.replace("{{MCP_CURRENT_STATE}}", "")
        base_final = final

        base_changed = base_final != self._last_base_system_prompt
        if base_changed:
            self._last_base_system_prompt = base_final
            if mcp_state:
                final = base_final + f"\n\n## MCP Service Status\n\n{mcp_state}"
            self._mcp_in_system_prompt = mcp_state
        else:
            if mcp_state != self._mcp_in_system_prompt:
                dynamic_parts["MCP_CURRENT_STATE"] = mcp_state
            else:
                if self._mcp_in_system_prompt:
                    final = base_final + f"\n\n## MCP Service Status\n\n{self._mcp_in_system_prompt}"

        # Layer 2: Standard injection (parallelize independent state queries)
        from opensquad import state_manager as _state_module

        current_state, current_wake = await asyncio.gather(
            _state_module.state_manager.get_state(),
            _state_module.state_manager.get_wake_mode(),
        )

        context = {
            "query": last_user_input,
            "source": current_input_source,
            "chat_api": self.chat_api,
            "tool_registry": getattr(self, "_tool_registry", None),
            "task_manager": self.task_manager,
            "memory_manager": self.memory_manager,
            "recent_messages": self.chat_api.req[-4:] if self.chat_api.req else [],
            "current_state": current_state,
            "current_wake": current_wake,
        }

        try:
            system_vars, standard_dynamic_vars = inject_standard(context)
            for key, value in system_vars.items():
                placeholder = "{{" + key + "}}"
                if placeholder in final:
                    final = final.replace(placeholder, str(value))
                else:
                    final += f"\n\n## {key}\n{value}"
            dynamic_parts.update(standard_dynamic_vars)
        except Exception as e:
            logger.error(f"[ContextBuilder] Standard context injection error: {e}")

        # Layer 3: Custom hook (before_input)
        before_input = self.hooks.get("before_input")
        if before_input:
            try:
                if asyncio.iscoroutinefunction(before_input):
                    custom_vars = await before_input(context)
                else:
                    custom_vars = before_input(context)
                if isinstance(custom_vars, dict):
                    for key, value in custom_vars.items():
                        placeholder = "{{" + key + "}}"
                        if placeholder in final:
                            final = final.replace(placeholder, str(value))
                        else:
                            dynamic_parts[key] = value
            except Exception as e:
                logger.error(f"[ContextBuilder] before_input hook error: {e}")

        # Plugin hook: on_before_prompt
        if self.plugin_manager:
            try:
                _hook_ctx = await self.plugin_manager.run_hook(
                    "on_before_prompt",
                    {
                        "prompt": final,
                        "agent_id": getattr(self, "_agent_id", ""),
                    },
                )
                final = _hook_ctx.get("prompt", final)
            except Exception as e:
                logger.warning(f"[ContextBuilder] on_before_prompt hook error: {e}")

        # Anti-repetition reminder
        if not final.endswith(self._REPETITION_REMINDER):
            final += self._REPETITION_REMINDER

        # Change detection
        prev_prompt = self.chat_api.get_system_prompt()
        is_changed = final != prev_prompt
        if is_changed:
            self.chat_api.update_system_prompt(final)

        # Build dynamic prefix
        dynamic_prefix = build_context_prefix(dynamic_parts)

        return (
            final,
            dynamic_prefix,
            {
                "tools": llm_params.get("tools"),
                "tool_choice": llm_params.get("tool_choice", "auto"),
            },
            is_changed,
        )

    def mark_snapshot_emitted(self):
        """Mark that the prompt snapshot has been emitted."""
        self._has_prompt_snapshot = True

    @property
    def has_prompt_snapshot(self) -> bool:
        return self._has_prompt_snapshot
