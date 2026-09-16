"""Shared implementation base for the provider API clients.

``ChatAPI`` (OpenAI-compatible), ``ClaudeAPI`` (Anthropic) and ``GoogleAPI``
(Gemini) each used to carry a private copy of the same three things:

* conversation-history persistence and session listing,
* token accounting (tiktoken estimate + per-message LRU cache),
* the context-compression algorithm (retention boundary, tool-pair integrity,
  forced fallback, summariser prompt, auto-compress stats).

Those copies had drifted apart in ways that were actively harmful:

* ``get_cumulative_stats`` returned ``cache_read_tokens`` on two providers and
  ``total_cache_read_tokens`` on the third — the consumers
  (``runner.py``, ``plugins/token_analytics/storage.py``) read the former, so
  Gemini cache statistics were silently dropped.
* ``_count_message_tokens`` on two providers skipped ``reasoning_content``,
  ``role`` and ``tool_call_id`` overhead and used unrelated multimodal
  constants (``image=300`` vs ``image_url=85/1105``).
* ``_prepare_messages`` had opposite degenerate-range branches: one provider
  returned the history **uncompressed** when the summarise range came out
  empty, which is exactly the "compression is a no-op and tokens keep
  climbing" failure the surrounding comments warn about. The same two
  providers also lacked the tool-pair integrity fix, so compression could
  split a ``tool_calls`` / ``tool`` pair and make the provider reject the
  request with a 400.

This module is now the single source of truth for all of it.  A subclass keeps
only what is genuinely provider specific:

* how messages / tools / responses are shaped on the wire,
* ``_summarizer_request`` — how to reach the summariser model.

REQUIRED ATTRS (set by the subclass ``__init__`` before/around
``_init_provider_base()``): ``model``, ``req``, ``encoding``, ``token_max``.

Call ``_init_provider_base()`` from each ``__init__`` to create the shared
state (counters, token caches, compression flags).
"""

from __future__ import annotations

import json
import logging
import os
import time as _time
from collections import OrderedDict
from typing import Any

from .system_config import syscfg

try:
    from tool import logger
except ImportError:  # pragma: no cover - standalone import
    logger = logging.getLogger(__name__)

try:
    from .events import bus
except ImportError:  # pragma: no cover
    bus = None


__all__ = ["ContextOverflowError", "ProviderAPIBase"]


class ContextOverflowError(RuntimeError):
    """The prompt cannot fit the model window even after compression.

    Raised by :meth:`ProviderAPIBase._prepare_messages` when the *irreducible*
    part of the request — the system message plus the tool schemas, neither of
    which any compression path may drop — already exceeds the window. Sending
    such a request is guaranteed to come back 400, so the turn fails fast with
    an actionable message instead.
    """


class ProviderAPIBase:
    """Algorithmic core shared by every provider API client."""

    # ────────────────────────── shared state ──────────────────────────

    def _init_provider_base(self) -> None:
        """Create the state every provider shares.

        Idempotent; safe to call before ``self.req`` exists as long as the
        caller does not invoke compression yet.
        """
        self._sid_provider = None  # Injected by Runner; session_id for this turn
        self._user_id_provider = None  # Injected by Runner; user_id for this turn
        self._latest_summary = ""  # Compressed-context summary ({{CONTEXT_SUMMARY}})
        self._auto_compressed = False  # Did auto-compression run in the last chat()?
        self._auto_compress_stats: dict[str, Any] = {}
        # CRITICAL: must be handed back to DeepSeek V4 on the next turn when tools are involved
        self._prev_reasoning_content = ""
        self._last_tools: list[dict] | None = None

        # ── Incremental token counter ──
        # Avoids re-encoding the whole message list on every compression check.
        # Incremented by add_* and invalidated by compression / pop / hot-reload.
        self._cached_token_count: int | None = None
        self._cached_tools_token_count: int = 0

        # ── Per-message token cache (identity+shape keyed LRU) ──
        self._msg_token_cache: OrderedDict = OrderedDict()
        self._msg_token_cache_max_size = 5000

        # ── Cumulative consumption statistics ──
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_requests = 0
        self.total_cache_read_tokens = 0
        self.total_cache_creation_tokens = 0

    def _provider_label(self) -> str:
        """Log/`status` prefix, e.g. ``[ClaudeAPI]``."""
        return f"[{type(self).__name__}]"

    # ────────────────────────── provider hooks ──────────────────────────

    @staticmethod
    def _is_tool_result_msg(msg: dict) -> bool:
        """True when *msg* carries a tool result rather than user prose.

        Covers OpenAI (``role: "tool"``), Anthropic (``type: "tool_result"``)
        and Gemini (``type: "functionResponse"``) shapes, because a session can
        be switched between providers and keep its history.
        """
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "tool":
            return True
        if role == "user" and isinstance(content, list):
            return any(
                isinstance(item, dict) and item.get("type") in ("tool_result", "functionResponse") for item in content
            )
        return False

    def _first_real_user_idx(self) -> int:
        """Index of the first genuine user message (``-1`` when there is none)."""
        for i in range(1, len(self.req)):
            if self.req[i].get("role") == "user" and not self._is_tool_result_msg(self.req[i]):
                return i
        return -1

    def _summarizer_model(self) -> str:
        """Model used for compression summaries (falls back to the main model)."""
        return syscfg.get("summarizer", "model") or self.model

    def _summarizer_request(self, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
        """Send the summary prompt to the provider and return the bare summary.

        Subclasses only implement this; prompt assembly and error handling are
        shared in :meth:`_generate_summary`.
        """
        raise NotImplementedError

    # ────────────────────────── event emission ──────────────────────────

    def _emit_with_sid(self, etype, data):
        """Emit an event tagged with the current session/turn, if known."""
        sid = self._sid_provider() if self._sid_provider else None
        wrapper: dict = {"data": data}
        if sid:
            wrapper["sid"] = sid
        try:
            from opensquad.session_parallel import get_turn_local
            from opensquad.turn_trace import make_trace_id

            tl = get_turn_local()
            if tl is not None:
                wrapper["turn_id"] = int(tl.turn or 0)
                wrapper["round_id"] = int(tl.round or 0)
                wrapper["trace_id"] = make_trace_id("", sid or tl.sid, tl.round, tl.turn)
        except Exception:
            pass
        if bus is not None:
            bus.emit(etype, wrapper)

    # ────────────────────────── history persistence ──────────────────────────

    def _ensure_history_dir(self):
        """Lazy resolve history_dir from workspace (workspace may not be set at __init__ time)."""
        if self.history_dir is None:
            self.history_dir = syscfg.workspace_data_dir("ai_his_talk")
            os.makedirs(self.history_dir, exist_ok=True)

    def _initialize_history(self, topic: str | None):
        if not topic:
            return
        self._ensure_history_dir()
        self.history_file = os.path.join(self.history_dir, f"{topic}.json")
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, encoding="utf-8") as f:
                    self.req = json.load(f)
            except Exception as e:
                logger.error(f"{self._provider_label()} Failed to load history: {e}")

    def save_history(self):
        if self.history_file:
            try:
                with open(self.history_file, "w", encoding="utf-8") as f:
                    json.dump(self.req, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.error(f"{self._provider_label()} Failed to save history: {e}")

    def list_sessions(self) -> list[str]:
        """List all historical session names."""
        self._ensure_history_dir()
        if not os.path.exists(self.history_dir):
            return []
        files = os.listdir(self.history_dir)
        return [f[:-5] for f in files if f.endswith(".json")]

    def get_cumulative_stats(self) -> dict:
        """Cumulative token consumption for this client.

        Key names are part of the contract with ``runner.py`` and the
        ``token_analytics`` plugin — keep them exactly as below.
        """
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
            "total_requests": self.total_requests,
            "cache_read_tokens": self.total_cache_read_tokens,
            "cache_creation_tokens": self.total_cache_creation_tokens,
        }

    # ────────────────────────── token accounting ──────────────────────────

    def _count_tools_tokens(self, tools: list[dict] | None) -> int:
        """Token overhead of the tool definitions themselves."""
        if not tools:
            return 0
        if not self.encoding:
            # Mirror _count_message_tokens: tiktoken can be unavailable, and
            # this method is called directly (not only through the
            # try/except in _count_tokens) by get_current_token_count() and
            # _irreducible_prompt_tokens(), where a bare AttributeError would
            # take the whole turn down.
            return len(str(tools)) // 4
        num_tokens = 0
        for tool in tools:
            num_tokens += 6
            fn = tool.get("function", {}) if isinstance(tool, dict) else getattr(tool, "function", {})
            if fn.get("name"):
                num_tokens += len(self.encoding.encode(fn["name"]))
            if fn.get("description"):
                num_tokens += len(self.encoding.encode(fn["description"]))
            if fn.get("parameters"):
                num_tokens += len(self.encoding.encode(json.dumps(fn["parameters"], ensure_ascii=False)))
        return num_tokens

    def _count_message_tokens(self, message: dict) -> int:
        """Tokens for a single message, with an identity+shape LRU cache."""
        if not self.encoding:
            return len(str(message)) // 4

        from opensquad.token_breakdown import message_token_cache_key

        try:
            msg_key = message_token_cache_key(message)
            if msg_key in self._msg_token_cache:
                self._msg_token_cache.move_to_end(msg_key)  # LRU touch
                return self._msg_token_cache[msg_key]
        except (TypeError, AttributeError, ValueError):
            msg_key = None

        num_tokens = 4
        role = message.get("role", "")
        if role:
            num_tokens += len(self.encoding.encode(role))
        if message.get("name"):
            num_tokens += len(self.encoding.encode(message["name"])) + 1
        if message.get("tool_call_id"):
            num_tokens += len(self.encoding.encode(message["tool_call_id"])) + 1
        if role == "tool":
            num_tokens += 2
        for key, value in message.items():
            if key == "content":
                if isinstance(value, str):
                    num_tokens += len(self.encoding.encode(value))
                elif value is None:
                    num_tokens += 1
                elif isinstance(value, list):
                    from opensquad.token_breakdown import count_multimodal_content_tokens

                    for item in value:
                        if not isinstance(item, dict):
                            continue
                        if item.get("type") == "text":
                            num_tokens += len(self.encoding.encode(item["text"]))
                        elif item.get("type") == "image_url":
                            detail = item.get("image_url", {}).get("detail", "auto")
                            num_tokens += 1105 if detail == "high" else 85
                        elif item.get("type") in ("audio_url", "video_url"):
                            num_tokens += 120
                    # Anthropic tool_result / Gemini functionResponse / tool_use
                    num_tokens += count_multimodal_content_tokens(value, self.encoding)
            elif key == "reasoning_content" and isinstance(value, str):
                # Thinking text is uploaded with the message and counts toward
                # the provider's input tokens; skipping it undercounts ~2.5%.
                num_tokens += len(self.encoding.encode(value))
            elif key == "tool_calls" and isinstance(value, list):
                from opensquad.token_breakdown import tool_fn_text

                for tc in value:
                    num_tokens += 8
                    tc_id = tc.get("id", "") if isinstance(tc, dict) else ""
                    if tc_id:
                        num_tokens += len(self.encoding.encode(tc_id))
                    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                    text = tool_fn_text(fn if fn else tc)
                    if text:
                        num_tokens += len(self.encoding.encode(text))

        if msg_key is not None:
            self._msg_token_cache[msg_key] = num_tokens
            while len(self._msg_token_cache) > self._msg_token_cache_max_size:
                self._msg_token_cache.popitem(last=False)  # LRU eviction
        return num_tokens

    def _count_tokens(self, messages: list[dict], tools: list[dict] | None = None) -> int:
        """Estimate the token cost of a message list (+ optional tool schemas)."""
        if not self.encoding:
            return len(str(messages)) // 4
        try:
            num_tokens = sum(self._count_message_tokens(m) for m in messages)
            num_tokens += self._count_tools_tokens(tools)
        except Exception as e:
            logger.warning(f"{self._provider_label()} Token count error: {e}")
            return len(str(messages)) // 4
        num_tokens += 3
        return num_tokens

    def get_current_token_count(self, tools: list[dict] | None = None) -> int:
        """Current token count, using the incremental cache when possible."""
        if tools is not None and tools is not self._last_tools:
            self._cached_tools_token_count = self._count_tools_tokens(tools)
            self._last_tools = tools

        if self._cached_token_count is not None:
            return self._cached_token_count + self._cached_tools_token_count + 3

        total = self._count_tokens(self.req, tools)
        self._cached_token_count = total - self._cached_tools_token_count - 3
        return total

    def invalidate_token_cache(self):
        """Drop the incremental counter after an out-of-band ``self.req`` change."""
        self._cached_token_count = None

    # ────────────────────────── compression helpers ──────────────────────────

    def _tail_msgs_for_rounds(self, n_rounds: int) -> list[dict]:
        """The tail messages covering the most recent *n_rounds* user turns.

        Tool results are deliberately excluded: they are large payloads that
        should be summarised, not carried verbatim.
        """
        msgs = self.req[1:]  # Skip system msg
        user_turn_count = 0
        for i in range(len(msgs) - 1, -1, -1):
            msg = msgs[i]
            if (msg.get("role") or "") == "user" and not self._is_tool_result_msg(msg):
                user_turn_count += 1
                if user_turn_count >= n_rounds:
                    return [m for m in msgs[i:] if not self._is_tool_result_msg(m)]
        return msgs  # fallback: return all non-system messages

    def _build_conv_text(self, messages: list[dict], budget_chars: int) -> str:
        """Render a message list as plain text under an overall char budget."""
        items = []
        for m in messages:
            role = m.get("role", "unknown")
            content = m.get("content", "")
            if isinstance(content, list):
                text_parts = []
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    t = item.get("type", "")
                    if t == "text":
                        text_parts.append(item["text"])
                    elif t == "tool_use":
                        inp = json.dumps(item.get("input", {}), ensure_ascii=False)[:300]
                        text_parts.append(f"[tool_use: {item.get('name', '?')}({inp})]")
                    elif t == "tool_result":
                        for c in item.get("content") or []:
                            if isinstance(c, dict) and c.get("type") == "text":
                                text_parts.append(c["text"])
                content = "\n".join(text_parts)
            else:
                content = str(content)
            items.append((role, content))

        total_chars = sum(len(c) for _, c in items)
        if total_chars <= budget_chars:
            return "\n".join(f"{role}: {content}" for role, content in items)

        base_alloc = max(200, budget_chars // max(len(items), 1))
        parts = []
        for role, content in items:
            if len(content) <= base_alloc:
                parts.append(f"{role}: {content}")
            else:
                parts.append(f"{role}: {content[:base_alloc]}...[truncated]")
        return "\n".join(parts)

    # ────────────────────────── summarisation ──────────────────────────

    @staticmethod
    def _summary_system_prompt() -> str:
        return (
            "You are a summarizer agent. Return ONLY the summary in the specified template. "
            "Do not add commentary or extra sections."
        )

    @staticmethod
    def _summary_user_prompt(conv_text: str) -> str:
        return (
            "You are compressing conversation history for an AI Agent that is currently executing a task.\n"
            "The compression result will replace this history; the Agent must be able to seamlessly continue working based on your summary.\n\n"
            "[Hard rules - the following must be preserved verbatim, never rewritten or omitted]\n"
            "- All file paths and directory names\n"
            "- All IDs, ports, version numbers, and configuration values\n"
            "- The original text of all error messages\n"
            "- Requirements, constraints, or preferences explicitly specified by the user\n"
            "- The most recent user request (what the agent is currently working on)\n\n"
            "[Output format - be specific, include exact values, avoid vague summaries]\n\n"
            "## Current Task\n"
            "(What the agent is working on RIGHT NOW — the most recent user request in detail)\n\n"
            "## Original Goal\n"
            "(The very first user request in this session, in one sentence)\n\n"
            "## Completed\n"
            "(Operations successfully executed and confirmed, with key output values and file paths)\n\n"
            "## Current State\n"
            "(What state the system/files/code is in right now — this is the most important section. "
            "Include open files, current working directory, last tool executed, etc.)\n\n"
            "## Key Parameters\n"
            "(Exact values that will definitely be needed going forward: paths, configs, API addresses, port numbers, etc.)\n\n"
            "## Unresolved Issues\n"
            "(Explicitly existing blockers, errors, or incomplete steps; omit this section if none)\n\n"
            "---\n"
            f"Conversation history to compress:\n{conv_text}"
        )

    def _generate_summary(self, messages: list[dict]) -> str:
        """Summarise *messages* with the provider's summariser model.

        Prompt assembly, the prompt-size trace and every failure path are
        shared; only the transport lives in :meth:`_summarizer_request`.
        """
        budget = syscfg.ctx_conv_text_budget_chars()
        max_tokens = syscfg.ctx_summary_max_tokens()
        conv_text = self._build_conv_text(messages, budget)

        system_prompt = self._summary_system_prompt()
        user_prompt = self._summary_user_prompt(conv_text)

        try:
            from .chat_api import _get_tiktoken

            full_prompt_text = system_prompt + "\n" + user_prompt
            estimated_prompt_tokens = len(_get_tiktoken().encode(full_prompt_text))
            logger.info(
                "[CompressTrace] summary prompt: model=%s, chars=%d, estimated_tokens=%d, max_tokens=%d",
                self._summarizer_model(),
                len(full_prompt_text),
                estimated_prompt_tokens,
                max_tokens,
            )
        except Exception:
            pass  # Tokenizer may fail; continue anyway

        try:
            return self._summarizer_request(system_prompt, user_prompt, max_tokens)
        except Exception as e:
            logger.error(f"{self._provider_label()} Summary generation failed: {e}")
            return "Summary generation failed. Please rely on the First User Query."

    # ────────────────────────── context compression ──────────────────────────

    def _irreducible_prompt_tokens(self) -> int:
        """Tokens that survive *every* compression path.

        ``_prepare_messages`` rebuilds the request as
        ``[system_msg, first_user, *recent]`` in all branches, so the system
        message and the tool schemas are a hard lower bound on the size of any
        request this provider will ever send. Nothing can summarise them away.
        """
        if not self.req:
            return 0
        total = 0
        for m in self.req:
            if isinstance(m, dict) and m.get("role") == "system":
                total += self._count_message_tokens(m)
        total += self._count_tools_tokens(self._last_tools)
        return total

    def _prepare_messages(self) -> list[dict]:
        """Return the message list to send, compacting the context if needed.

        The single algorithm used by every provider:

        1. Trigger at ``token_max * trigger_threshold`` (default 0.75), plus a
           x3-scaled hard guard at 85% of the window to absorb the systematic
           undercount of the local tiktoken estimate.
        2. Retention boundary = newest messages within
           ``ctx_keep_recent_fraction`` of the current tokens, then widened —
           never past ``ctx_recent_hard_cap_frac`` — to cover the last
           ``ctx_keep_recent_rounds`` user turns and the two most recent user
           anchors.
        3. Never split a ``tool_calls`` / ``tool`` pair.
        4. If that still leaves an empty summarise range, force a
           token-budget-only split so the summariser actually runs; a
           degenerate conversation is compacted without a summary rather than
           returned uncompressed.
        """
        _t0 = _time.monotonic()

        # Reset auto-compression flags for this call
        self._auto_compressed = False
        self._auto_compress_stats = {}

        # 0. Irreducible-overflow preflight. The system message and the tool
        # schemas are never dropped by any branch below, so they are a floor on
        # the request size. If that floor alone overflows the window, no amount
        # of compacting can save the turn. Fail loudly and actionably here
        # rather than shipping a request that must 400 - and note the old
        # `len(self.req) < 5` early return made this case worse, because a
        # brand-new session (3 messages) returned the oversized prompt whole.
        irreducible = self._irreducible_prompt_tokens()
        overflow_limit = int(self.token_max * syscfg.ctx_overflow_guard_frac())
        if irreducible > overflow_limit:
            detail = (
                f"System prompt alone needs {irreducible:,} tokens "
                f"(limit {overflow_limit:,} of {self.token_max:,}). Compression cannot fix this: "
                f"the system message and the tool schemas are never summarised. Usual cause is an "
                f"oversized agent.md (permanent memory) or CONTEXT_SUMMARY - shrink it and retry."
            )
            logger.error("[CompressTrace] UNRECOVERABLE PROMPT OVERFLOW: %s", detail)
            self._emit_with_sid("status", "System prompt alone exceeds the model window; aborting.")
            raise ContextOverflowError(detail)

        # 1. Count current tokens (uses the incremental cache when available)
        current_tokens = self.get_current_token_count(self._last_tools)
        threshold = syscfg.ctx_trigger_threshold()
        threshold_tokens = int(self.token_max * threshold)

        # Hard guard: if the *scaled* estimate would pass a high watermark,
        # force compression so the request never hits a provider 400.
        scaled_estimate = current_tokens * 3
        hard_watermark = int(self.token_max * 0.85)
        if scaled_estimate > hard_watermark and current_tokens <= threshold_tokens:
            logger.warning(
                "[CompressTrace] HARD GUARD triggered: local estimate %d tokens, scaled x3 = %d > 85%% of max %d. "
                "Forcing compression to avoid 400.",
                current_tokens,
                scaled_estimate,
                self.token_max,
            )
            self._emit_with_sid("status", "Context near limit, compacting (hard guard)...")

        if current_tokens <= threshold_tokens and scaled_estimate <= hard_watermark:
            logger.info("[CompressTrace] below threshold, no compression needed")
            return self.req

        logger.warning(
            "[CompressTrace] context compression TRIGGERED (%.1f%% of max)",
            current_tokens / max(self.token_max, 1) * 100,
        )
        self._emit_with_sid("status", "Context limit reached, compacting...")

        if len(self.req) < 5:
            # Too few messages to compress meaningfully — keep all
            logger.info("[CompressTrace] too few messages (%d), skipping compression", len(self.req))
            return self.req

        system_msg = self.req[0]
        first_user_idx = self._first_real_user_idx()
        first_user_msg = self.req[first_user_idx] if first_user_idx != -1 else None

        logger.info(
            "[CompressTrace] scan: total_msgs=%d, first_user_idx=%d",
            len(self.req),
            first_user_idx,
        )

        msg_tokens = [(i, self._count_message_tokens(m)) for i, m in enumerate(self.req)]

        # 2a. Token-budget retention
        keep_frac = syscfg.ctx_keep_recent_fraction()
        keep_token_budget = int(current_tokens * keep_frac)
        recent_hard_cap = int(current_tokens * syscfg.ctx_recent_hard_cap_frac())

        recent_start = len(self.req)  # default: no recent portion
        recent_token_sum = 0
        for idx, tok in reversed(msg_tokens):
            if recent_token_sum + tok <= keep_token_budget:
                recent_token_sum += tok
                recent_start = idx
            else:
                break

        if first_user_msg is not None:
            recent_start = max(recent_start, first_user_idx + 1)

        # 2b. Rounds floor: never retain less than the last N user turns, as
        # long as that still fits the hard cap. In a long autonomous tool run
        # the 2nd-to-last user turn sits near the very start of the
        # conversation; protecting it there would swallow the entire context
        # and leave the summarise range empty, so such a pullback is refused
        # and the message is summarised like everything else.
        rounds_tail = self._tail_msgs_for_rounds(syscfg.ctx_keep_recent_rounds())
        rounds_start = recent_start
        if rounds_tail:
            for i, m in enumerate(self.req):
                if m is rounds_tail[0]:
                    rounds_start = i
                    break
        if rounds_start < recent_start:
            candidate_tokens = sum(t for _, t in msg_tokens[rounds_start:])
            if candidate_tokens <= recent_hard_cap:
                logger.warning(
                    "[CompressTrace] extending recent_start to %d for last %d user rounds "
                    "(was %d, candidate_tokens=%d <= cap=%d)",
                    rounds_start,
                    syscfg.ctx_keep_recent_rounds(),
                    recent_start,
                    candidate_tokens,
                    recent_hard_cap,
                )
                recent_start = rounds_start
            else:
                logger.warning(
                    "[CompressTrace] NOT extending to %d for user rounds: would add %d tokens, "
                    "exceeding recent hard cap %d (will be summarized)",
                    rounds_start,
                    candidate_tokens,
                    recent_hard_cap,
                )

        # 2c. User-anchor pullback, bounded by the same hard cap
        user_indices = [i for i in range(len(self.req)) if self.req[i].get("role") == "user"]
        for anchor in (
            user_indices[-2] if len(user_indices) >= 2 else None,
            user_indices[-1] if user_indices else None,
        ):
            if anchor is None or anchor >= recent_start:
                continue
            candidate_tokens = sum(t for _, t in msg_tokens[anchor:])
            if candidate_tokens <= recent_hard_cap:
                logger.warning(
                    "[CompressTrace] extending recent_start to include user at "
                    "idx=%d (was recent_start=%d, candidate_tokens=%d <= cap=%d)",
                    anchor,
                    recent_start,
                    candidate_tokens,
                    recent_hard_cap,
                )
                recent_start = min(recent_start, anchor)
            else:
                logger.warning(
                    "[CompressTrace] NOT extending to user at idx=%d: would add "
                    "%d tokens, exceeding recent hard cap %d (will be summarized)",
                    anchor,
                    candidate_tokens,
                    recent_hard_cap,
                )

        # 3. Never start the retained section on an orphan tool message: the
        # provider rejects `role:"tool"` without its preceding `tool_calls`.
        while recent_start > 0 and recent_start < len(self.req) and self.req[recent_start].get("role") == "tool":
            recent_start -= 1
            logger.warning(
                "[CompressTrace] tool message at recent_start, extending to include "
                "preceding assistant (new recent_start=%d, role=%s)",
                recent_start,
                self.req[recent_start].get("role"),
            )
        for offset in range(min(3, len(self.req) - recent_start)):
            idx = recent_start + offset
            if self.req[idx].get("role") == "tool":
                needed = idx - 1
                while needed >= 0 and self.req[needed].get("role") != "assistant":
                    needed -= 1
                if needed >= 0 and self.req[needed].get("tool_calls"):
                    recent_start = min(recent_start, needed)
                    logger.warning(
                        "[CompressTrace] orphan tool at idx=%d, extending recent_start to %d (assistant with tool_calls)",
                        idx,
                        recent_start,
                    )
                    break

        recent_msgs = self.req[recent_start:]
        end_scan = recent_start
        start_scan = (first_user_idx + 1) if first_user_msg else 1

        logger.info(
            "[CompressTrace] retention: keep_frac=%.2f, keep_budget=%d tokens, "
            "recent_start=%d, recent_msgs=%d, recent_tokens=%d, summarize_range=[%d, %d) msgs=%d",
            keep_frac,
            keep_token_budget,
            recent_start,
            len(recent_msgs),
            recent_token_sum,
            start_scan,
            end_scan,
            end_scan - start_scan,
        )

        if start_scan >= end_scan:
            # Anchor/rounds pullback swallowed the whole compression range.
            # NEVER return uncompressed context here — that defeats the point
            # of triggering compression and leaves tokens pinned above the
            # limit. Force a token-budget-only split so the summariser runs.
            logger.warning(
                "[CompressTrace] compression range empty (start=%d end=%d), "
                "forcing token-budget-only retention (dropping user anchors)",
                start_scan,
                end_scan,
            )
            recent_start = len(self.req)
            acc = 0
            for idx, tok in reversed(msg_tokens):
                if acc + tok <= keep_token_budget:
                    acc += tok
                    recent_start = idx
                else:
                    break
            # Leave at least one message for the summarizer.
            min_scan = min((first_user_idx + 2) if first_user_idx != -1 else 2, len(self.req))
            if recent_start < min_scan:
                recent_start = min_scan
            while recent_start > 0 and recent_start < len(self.req) and self.req[recent_start].get("role") == "tool":
                recent_start -= 1
            recent_msgs = self.req[recent_start:]
            end_scan = recent_start
            start_scan = (first_user_idx + 1) if first_user_msg else 1
            logger.warning(
                "[CompressTrace] forced recent_start=%d, summarize_range=[%d, %d) msgs=%d",
                recent_start,
                start_scan,
                end_scan,
                end_scan - start_scan,
            )
            if start_scan >= end_scan:
                # Degenerate conversation — nothing to summarise, but still
                # drop the (empty) middle rather than returning it whole.
                partial = [system_msg]
                if first_user_msg:
                    partial.append(first_user_msg)
                partial.extend(recent_msgs)
                new_count = self._count_tokens(partial, self._last_tools)
                logger.info(
                    "[CompressTrace] skip summary: msgs=%d, tokens_before=%d, tokens_after=%d",
                    len(partial),
                    current_tokens,
                    new_count,
                )
                self.req = partial
                self.invalidate_token_cache()
                self._auto_compressed = True
                self._auto_compress_stats = {
                    "tokens_before": current_tokens,
                    "tokens_after": new_count,
                    "messages_before": len(partial),
                    "messages_after": len(partial),
                    "dropped_count": 0,
                    "summarize_range": [start_scan, end_scan],
                    "recent_start": recent_start,
                    "recent_tokens": recent_token_sum,
                    "keep_frac": keep_frac,
                    "previous_summary": (getattr(self, "_latest_summary", "") or "").strip(),
                    "summary_empty": True,
                }
                return self.req

        # 4. Generate summary
        dropped_count = end_scan - start_scan
        msgs_to_summarize = self.req[start_scan:end_scan]
        summarize_tokens = sum(t for _, t in msg_tokens[start_scan:end_scan])

        logger.info(
            "[CompressTrace] calling summarizer: %d messages, %d tokens, build_wait=%.2fs",
            dropped_count,
            summarize_tokens,
            _time.monotonic() - _t0,
        )
        summary_content = self._generate_summary(msgs_to_summarize)
        logger.info(
            "[CompressTrace] summarizer returned: summary_len=%d chars, elapsed=%.2fs",
            len(summary_content),
            _time.monotonic() - _t0,
        )

        # Capture the prior summary BEFORE overwriting — the runner needs it
        # for compress_current_session(previous_summary=...).
        previous_summary_snapshot = (getattr(self, "_latest_summary", "") or "").strip()
        self._latest_summary = f"[Context summary | Compressed {dropped_count} messages]\n{summary_content}"

        compacted_req = [system_msg]
        if first_user_msg:
            compacted_req.append(first_user_msg)
        compacted_req.extend(recent_msgs)

        new_token_count = self._count_tokens(compacted_req, self._last_tools)
        logger.info(
            "[CompressTrace] compression result: msgs: %d -> %d, tokens: %d -> %d, saved=%d (%.1f%%)",
            len(self.req),
            len(compacted_req),
            current_tokens,
            new_token_count,
            current_tokens - new_token_count,
            (current_tokens - new_token_count) / max(current_tokens, 1) * 100,
        )

        # Preserve reasoning_content from the pre-compression history: the
        # holder message may be dropped, and the inject logic must be able to
        # re-attach it to every assistant message afterwards.
        _last_reasoning = None
        for m in reversed(self.req):  # original, not yet overwritten
            if m.get("role") == "assistant" and m.get("reasoning_content"):
                _last_reasoning = m.get("reasoning_content")
                break

        self.req = compacted_req
        self.invalidate_token_cache()  # Compression changed the message list

        if _last_reasoning:
            self._prev_reasoning_content = _last_reasoning
            logger.info(
                "[CompressTrace] Preserved _prev_reasoning_content after auto-compression, len=%d",
                len(_last_reasoning),
            )

        # Fingerprint of the first kept recent message so the runner can align
        # the disk archive cut with this recent_start boundary.
        first_kept_role = ""
        first_kept_content = ""
        for m in recent_msgs:
            role = m.get("role") or ""
            if role not in ("user", "assistant"):
                continue
            content = m.get("content", "")
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(str(item.get("text") or ""))
                content = "\n".join(parts)
            content = str(content or "").strip()
            if not content:
                continue
            first_kept_role = role
            first_kept_content = content[:240]
            break

        self._auto_compressed = True
        self._auto_compress_stats = {
            "tokens_before": current_tokens,
            "tokens_after": new_token_count,
            "messages_before": len(self.req) + dropped_count,
            "messages_after": len(self.req),
            "dropped_count": dropped_count,
            "summarize_range": [start_scan, end_scan],
            "recent_start": recent_start,
            "recent_tokens": recent_token_sum,
            "keep_frac": keep_frac,
            "previous_summary": previous_summary_snapshot,
            "first_kept_role": first_kept_role,
            "first_kept_content": first_kept_content,
        }
        logger.info(
            "[CompressTrace] auto-compression COMPLETE (total_elapsed=%.2fs): %d -> %d tokens, %d messages retained",
            _time.monotonic() - _t0,
            current_tokens,
            new_token_count,
            len(self.req),
        )
        return self.req
