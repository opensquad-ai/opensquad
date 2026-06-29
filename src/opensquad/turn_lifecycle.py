# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable
import time as _time
from opensquad.structured_log import perf_event


@dataclass
class PromptSetupResult:
    system_prompt: str
    dynamic_prefix: str
    tools: Any
    tool_choice: str
    changed: bool
    diff: list[str]
    should_emit_prompt: bool
    prompt_payload: dict[str, Any]


class TurnLifecycle:
    """Owns per-turn lifecycle side effects extracted from `AgentRunner`."""

    def __init__(self, runner: Any):
        self.runner = runner

    def before_task(self, query: str) -> tuple[str, str]:
        task_id, cleaned = self.runner._extract_task_id(query)
        if task_id:
            query = cleaned
            self.runner.chat_api.load_his = task_id

        if self.runner._memory_manager:
            self.runner._memory_manager.advance_turn()

        return query, self.runner.chat_api.load_his or "continuous"

    async def build_prompt_setup(self) -> PromptSetupResult:
        t0 = _time.perf_counter()
        final, dynamic_prefix, llm_params, is_changed = await self.runner._context_builder.build(
            last_user_input=self.runner._last_user_input,
            current_input_source=self.runner._current_input_source,
            current_turn=self.runner._current_turn,
            current_round=self.runner._current_round,
        )
        diff_lines = self.runner._build_prompt_diff(final, is_changed)
        should_emit_prompt = (not self.runner._context_builder.has_prompt_snapshot) or is_changed
        prompt_payload = {
            "system_prompt": final,
            "dynamic_prefix": dynamic_prefix or "",
            "changed": is_changed,
            "diff": diff_lines,
        }
        perf_event(
            "runner", "prompt_setup_done",
            agent_id=getattr(self.runner, "_agent_id", ""),
            elapsed_ms=int((_time.perf_counter() - t0) * 1000),
            turn=self.runner._current_turn,
            changed=is_changed,
        )
        return PromptSetupResult(
            system_prompt=final,
            dynamic_prefix=dynamic_prefix,
            tools=llm_params.get("tools"),
            tool_choice=llm_params.get("tool_choice", "auto"),
            changed=is_changed,
            diff=diff_lines,
            should_emit_prompt=should_emit_prompt,
            prompt_payload=prompt_payload,
        )

    async def apply_prompt_setup(self, emit: Callable[[str, Any], Awaitable[None]]) -> PromptSetupResult:
        result = await self.build_prompt_setup()
        self.runner._current_tools = result.tools
        self.runner._current_tool_choice = result.tool_choice
        self.runner._dynamic_context_prefix = result.dynamic_prefix

        if result.should_emit_prompt:
            await emit("prompt_update", result.prompt_payload)
            self.runner._session_manager.add_event(
                "prompt_update",
                result.prompt_payload,
                turn_id=self.runner._current_turn,
                round_id=self.runner._current_round,
            )
            self.runner._context_builder.mark_snapshot_emitted()
        return result
