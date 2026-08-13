"""
Tool Call Strategy Pattern

This module implements different strategies for LLM tool calling:
- XMLToolCallStrategy: Original XML-based format (for compatibility)
- NativeToolCallStrategy: Native Function Calling API (OpenAI/Claude/Gemini)

The strategy pattern allows seamless switching between formats based on model capabilities.
"""

import functools
import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class ToolCallStrategy(ABC):
    """Abstract base class for tool call strategies"""

    def __init__(self, tool_registry):
        self.tool_registry = tool_registry

    @abstractmethod
    def prepare_llm_call(self, system_prompt: str) -> dict[str, Any]:
        """
        Prepare LLM call parameters

        Args:
            system_prompt: Base system prompt template

        Returns:
            Dictionary with:
            - system_prompt: Final system prompt (str)
            - tools: Tool definitions (List[Dict] or None)
            - tool_choice: Tool choice mode (str, optional)
        """
        pass

    @abstractmethod
    def parse_response(self, response: Any) -> list[tuple[str, dict[str, Any]]] | None:
        """
        Parse LLM response to extract tool calls

        Args:
            response: LLM response (format depends on strategy)

        Returns:
            List of (tool_name, args_dict) tuples if tool calls found, else None.
            Returns list with one element for single tool call, or multiple
            elements for parallel tool calls.
        """
        pass

    @abstractmethod
    def get_strategy_name(self) -> str:
        """Return strategy name for logging"""
        pass


class XMLToolCallStrategy(ToolCallStrategy):
    """
    XML-based tool call strategy (original implementation)

    This strategy generates tool descriptions as text and injects them into
    the system prompt. LLM outputs tool calls in XML format like:

    <tool_call>
      <func>tool_name</func>
      <param1>value1</param1>
    </tool_call>
    """

    def prepare_llm_call(self, system_prompt: str) -> dict[str, Any]:
        """
        Inject tool descriptions into system prompt

        Returns:
            - system_prompt: Prompt with {{TOOL_DESCRIPTIONS}} replaced
            - tools: None (XML mode doesn't use API-level tools)
        """
        # Generate text-based tool descriptions
        tool_desc = self.tool_registry.generate_tool_descriptions()

        # Replace placeholder in system prompt
        final_prompt = system_prompt.replace("{{TOOL_DESCRIPTIONS}}", tool_desc)

        return {"system_prompt": final_prompt, "tools": None, "tool_choice": None}

    def parse_response(self, response_text) -> list[tuple[str, dict[str, Any]]] | None:
        """
        Parse XML format tool call from text response.
        Supports parallel tool calls — scans for multiple XML blocks.

        Args:
            response_text: Full text output from LLM (str).
                During streaming, ChatCompletionChunk objects are passed here -- they are
                silently ignored (return None). Actual XML parsing happens in runner.py
                after the full response is assembled.

        Returns:
            List of (tool_name, args_dict) tuples, or None if no tool calls.
        """
        if not isinstance(response_text, str):
            return None
        from opensquad.parser import ResponseParser

        results = ResponseParser.parse_tool_calls(response_text)
        return results if results else None

    def get_strategy_name(self) -> str:
        return "XML"


class NativeToolCallStrategy(ToolCallStrategy):
    """
    Native Function Calling strategy (OpenAI/Claude/Gemini compatible)

    This strategy generates OpenAI Tools JSON Schema and passes it via
    the API's `tools` parameter. The API returns structured tool calls.
    """

    # Emit live args for these tools so the UI can stream file content.
    _STREAM_ARG_HINTS = (
        "write_file",
        "edit_file",
        "replace_in_file",
        "create_file",
        "write_to_file",
    )

    def __init__(self, tool_registry, tool_filter: str = "all"):
        """
        Initialize Native FC strategy

        Args:
            tool_registry: ToolRegistry instance
            tool_filter: tool filter strategy ("all" | "baseline" | "high" | List[str])
                - "all": all tools (default)
                - "baseline": high-frequency tools only (files, system, search, memory, MCP)
                - "high": high + medium frequency tools
                - List[str]: custom namespace list
        """
        super().__init__(tool_registry)
        self._tool_calls_buffer = []  # Buffer for streaming mode
        self._tool_filter = tool_filter  # tool filter strategy
        self._delta_callback = None  # optional: callable(dict) for live UI
        self._last_delta_emit_at: dict[int, float] = {}
        self._delta_min_interval_s = 0.05  # ~20 Hz throttle

    def set_delta_callback(self, callback) -> None:
        """Register/clear a callback for incremental tool_call argument updates."""
        self._delta_callback = callback
        if callback is None:
            self._last_delta_emit_at.clear()

    def fork(self) -> "NativeToolCallStrategy":
        """Independent per-turn copy.

        The agent keeps one shared strategy for schema generation; concurrent
        parallel turns must NOT share the streaming state (single
        ``_delta_callback`` slot + shared ``_tool_calls_buffer`` would interleave
        two sessions' tool_call chunks — tools from session B executed under
        session A's sid). Fork a fresh instance per chat() call so streaming
        tool-call accumulation and delta emission are isolated per session.
        """
        return NativeToolCallStrategy(self.tool_registry, tool_filter=self._tool_filter)

    @classmethod
    def _should_stream_args(cls, tool_name: str) -> bool:
        n = (tool_name or "").lower().replace(".", "__")
        return any(h in n for h in cls._STREAM_ARG_HINTS)

    def _emit_args_delta(self, index: int, *, force: bool = False) -> None:
        """Push throttled partial tool args to the gateway (file write/edit only)."""
        if not self._delta_callback:
            return
        if index < 0 or index >= len(self._tool_calls_buffer):
            return
        tc = self._tool_calls_buffer[index]
        name = tc.get("function", {}).get("name") or ""
        if not name or not self._should_stream_args(name):
            return
        import time

        now = time.monotonic()
        last = self._last_delta_emit_at.get(index, 0.0)
        if not force and (now - last) < self._delta_min_interval_s:
            return
        self._last_delta_emit_at[index] = now
        args_so_far = tc.get("function", {}).get("arguments") or ""
        call_id = tc.get("id") or f"partial_tc_{index}"
        try:
            self._delta_callback(
                {
                    "id": call_id,
                    "index": index,
                    "name": name,
                    "arguments": args_so_far,
                    "partial": True,
                    "force": bool(force),
                }
            )
        except Exception as e:
            logger.debug("[NativeFC] delta callback failed: %s", e)

    _NATIVE_FC_NOTICE = (
        "\n## Native Function Calling\n"
        "This session uses Native Function Calling via the API `tools` parameter. "
        "DO NOT emit XML format tool calls; use the structured tools parameter instead.\n\n"
    )

    def _strip_markdown_section(self, text: str, needle: str) -> str:
        """Drop a ##/### section whose heading contains *needle* (until next heading)."""
        pattern = re.compile(
            rf"(?ms)^[ \t]*(#{{2,3}})[ \t]+[^\n]*{re.escape(needle)}[^\n]*\n.*?(?=^[ \t]*#{{2,3}}[ \t]|\Z)"
        )
        return pattern.sub("", text)

    def _remove_tool_format_section(self, prompt: str) -> str:
        """Strip leftover XML tool-format prose so Native FC is not double-instructed."""
        text = prompt
        text = self._strip_markdown_section(text, "Tool Call Format")
        text = self._strip_markdown_section(text, "Built-in Tools")
        text = self._strip_markdown_section(text, "Available Tools")
        text = text.replace("{{TOOL_DESCRIPTIONS}}", "")
        if "Native Function Calling" not in text:
            text = self._NATIVE_FC_NOTICE + text
        return text

    def prepare_llm_call(self, system_prompt: str) -> dict[str, Any]:
        """
        Generate OpenAI Tools schema and return call parameters.

        Returns:
            - system_prompt: XML tool-format leftover stripped; Native FC notice added
            - tools: List of tool definitions in OpenAI format
            - tool_choice: "auto" (let model decide)
        """
        tools = self.tool_registry.generate_openai_tools(tool_filter=self._tool_filter)
        system_prompt = self._remove_tool_format_section(system_prompt)
        return {"system_prompt": system_prompt, "tools": tools, "tool_choice": "auto"}

    def parse_response(self, api_response) -> list[tuple[str, dict[str, Any]]] | None:
        """
        Parse native Function Calling response.
        Supports parallel tool calls — returns list of all tool calls.

        Args:
            api_response: API response chunk (streaming mode)

        Returns:
            List of (tool_name, args_dict) tuples, or None if no tool calls.
        """
        # Handle streaming response chunks
        if hasattr(api_response, "choices") and api_response.choices:
            delta = api_response.choices[0].delta

            # Check if this chunk contains tool_calls
            if hasattr(delta, "tool_calls") and delta.tool_calls:
                for tc in delta.tool_calls:
                    # Buffer the tool call data
                    if tc.index >= len(self._tool_calls_buffer):
                        self._tool_calls_buffer.append(
                            {
                                "id": getattr(tc, "id", None),
                                "type": getattr(tc, "type", "function"),
                                "function": {"name": "", "arguments": ""},
                            }
                        )

                    buf = self._tool_calls_buffer[tc.index]
                    # Keep first non-empty id from the stream
                    if getattr(tc, "id", None) and not buf.get("id"):
                        buf["id"] = tc.id

                    name_just_set = False
                    args_grew = False
                    # Accumulate function name
                    if hasattr(tc, "function") and tc.function:
                        if hasattr(tc.function, "name") and tc.function.name:
                            if not buf["function"]["name"]:
                                name_just_set = True
                            buf["function"]["name"] = tc.function.name

                        # Accumulate arguments (streamed incrementally)
                        if hasattr(tc.function, "arguments") and tc.function.arguments:
                            buf["function"]["arguments"] += tc.function.arguments
                            args_grew = True

                    if name_just_set or args_grew:
                        self._emit_args_delta(tc.index, force=name_just_set)

            # Check if stream finished (finish_reason present)
            finish_reason = getattr(api_response.choices[0], "finish_reason", None)
            if finish_reason == "tool_calls" or finish_reason == "stop":
                # Stream finished, parse ALL buffered tool calls
                if self._tool_calls_buffer:
                    # Final flush so UI has the complete args before runner tool_call
                    for i in range(len(self._tool_calls_buffer)):
                        self._emit_args_delta(i, force=True)
                    results = []
                    for tc in self._tool_calls_buffer:
                        tool_name = tc["function"]["name"]
                        args_json = tc["function"]["arguments"]
                        try:
                            args_dict = json.loads(args_json)
                        except json.JSONDecodeError as e:
                            logger.error(f"Failed to parse tool call arguments: {e}")
                            logger.debug(f"Raw arguments: {args_json}")
                            continue
                        logger.info(f"Native FC parsed tool call: {tool_name}")
                        results.append((tool_name, args_dict))
                    self._tool_calls_buffer = []  # Clear buffer
                    self._last_delta_emit_at.clear()
                    return results if results else None
        else:
            # Empty or missing choices -- some proxy APIs return chunks with
            # choices=[] but still have a finish_reason at the chunk level.
            # Check if stream is finished and parse buffered tool calls.
            finish_reason = getattr(api_response, "finish_reason", None)
            if (finish_reason == "tool_calls" or finish_reason == "stop") and self._tool_calls_buffer:
                for i in range(len(self._tool_calls_buffer)):
                    self._emit_args_delta(i, force=True)
                results = []
                for tc in self._tool_calls_buffer:
                    tool_name = tc["function"]["name"]
                    args_json = tc["function"]["arguments"]
                    try:
                        args_dict = json.loads(args_json)
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to parse tool call arguments: {e}")
                        logger.debug(f"Raw arguments: {args_json}")
                        continue
                    logger.info(f"Native FC parsed tool call (from empty-choices chunk): {tool_name}")
                    results.append((tool_name, args_dict))
                self._tool_calls_buffer = []  # Clear buffer
                self._last_delta_emit_at.clear()
                return results if results else None
            # Also try to extract from choices[0] if it exists (even if list appears falsy)
            if hasattr(api_response, "choices") and api_response.choices is not None and len(api_response.choices) > 0:
                finish_reason = getattr(api_response.choices[0], "finish_reason", None)
                if finish_reason == "tool_calls" or finish_reason == "stop":
                    if self._tool_calls_buffer:
                        for i in range(len(self._tool_calls_buffer)):
                            self._emit_args_delta(i, force=True)
                        results = []
                        for tc in self._tool_calls_buffer:
                            tool_name = tc["function"]["name"]
                            args_json = tc["function"]["arguments"]
                            try:
                                args_dict = json.loads(args_json)
                            except json.JSONDecodeError as e:
                                logger.error(f"Failed to parse tool call arguments: {e}")
                                logger.debug(f"Raw arguments: {args_json}")
                                continue
                            logger.info(f"Native FC parsed tool call (from choices[0]): {tool_name}")
                            results.append((tool_name, args_dict))
                        self._tool_calls_buffer = []
                        self._last_delta_emit_at.clear()
                        return results if results else None

        return None

    def _parse_buffered_tool_calls(self) -> tuple[str, dict[str, Any]] | None:
        """Parse the buffered tool calls (after streaming completes)"""
        if not self._tool_calls_buffer:
            return None

        # Take the first tool call (OpenSquad executes one tool at a time)
        tc = self._tool_calls_buffer[0]
        tool_name = tc["function"]["name"]
        args_json = tc["function"]["arguments"]

        # Parse arguments JSON
        try:
            args_dict = json.loads(args_json)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse tool call arguments: {e}")
            logger.debug(f"Raw arguments: {args_json}")
            return None

        logger.info(f"Native FC parsed tool call: {tool_name}")
        return tool_name, args_dict

    def get_strategy_name(self) -> str:
        return "Native-FC"


class ToolCallStrategySelector:
    """
    Strategy selector that chooses the appropriate tool call strategy
    based on model capabilities and user configuration
    """

    @staticmethod
    def select(config: dict, tool_registry) -> ToolCallStrategy:
        """
        Select tool call strategy based on configuration

        Args:
            config: Agent configuration dict
            tool_registry: ToolRegistry instance

        Returns:
            Appropriate ToolCallStrategy instance

        Configuration:
            Add to agent config.json:
            {
              "model": {
                "tool_call_mode": "auto" | "native" | "xml",
                "tool_filter": "all" | "baseline" | "high" | ["namespace1", "namespace2"]
              }
            }

        Modes:
            - "auto": Auto-detect based on model capabilities (default)
            - "native": Force native Function Calling (fallback to XML if unsupported)
            - "xml": Force XML format (for compatibility)

        Tool Filters (Native FC only):
            - "all": All tools (default for backward compat, 124+ tools)
            - "baseline": High-frequency tools only (59 tools, ~65% reduction)
            - "high": High + medium frequency tools (DEFAULT, ~77 tools, ~45% reduction)
            - List[str]: Custom namespace list
        NOTE: since 0.8.5 the default is "high" (was "all") — set tool_filter
        explicitly in config.json to override (e.g. "all" to restore).
        """
        model_config = config.get("model", {})
        mode = model_config.get("tool_call_mode", "native")
        # P0-1 perf: "all" (159 tools ≈ 16.7K tokens) is now opt-in. Every
        # agent that does not explicitly configure tool_filter gets the curated
        # "high" set (framework core + common plugins, ≈8K tokens) — a ~45%
        # cut in per-request tool schema tokens, which directly reduces TTFT
        # and cost. Override with "all" (or a namespace list) per agent.
        tool_filter = model_config.get("tool_filter", "high")  # get filter config
        # api_protocol: API 协议类型 (openai / openai_compat / claude / google)
        provider = model_config.get("api_protocol", "")
        model_name = model_config.get("model_name", "")

        # Check if model supports Function Calling
        supports_fc = ToolCallStrategySelector._supports_function_calling(provider, model_name)

        # Strategy selection logic
        if mode == "native":
            if not supports_fc:
                logger.warning(f"Model {model_name} may not support Function Calling. Falling back to XML mode.")
                return XMLToolCallStrategy(tool_registry)

            # Check if we have implemented Native FC for this provider
            is_implemented = ToolCallStrategySelector._is_native_fc_implemented(provider)
            if not is_implemented:
                logger.warning(
                    f"Native FC not yet implemented for {provider} (provider). "
                    f"The API will accept tools parameter but ignore it. "
                    f"Tools will be unavailable. "
                    f"Recommend using 'tool_call_mode: auto' or 'xml' instead."
                )

            logger.info(f"Using Native Function Calling for {model_name} (filter: {tool_filter})")
            return NativeToolCallStrategy(tool_registry, tool_filter=tool_filter)

        elif mode == "xml":
            logger.info(f"Using XML format (forced by config) for {model_name}")
            return XMLToolCallStrategy(tool_registry)

        elif mode == "auto":
            # Auto mode: Use Native FC only if BOTH:
            # 1. Model supports it (API-level)
            # 2. We have implemented it (code-level)
            is_implemented = ToolCallStrategySelector._is_native_fc_implemented(provider)

            if supports_fc and is_implemented:
                logger.info(f"Auto-selected Native Function Calling for {model_name} (filter: {tool_filter})")
                return NativeToolCallStrategy(tool_registry, tool_filter=tool_filter)
            elif supports_fc and not is_implemented:
                logger.info(f"Auto-selected XML format for {model_name}: Native FC not yet implemented for {provider}")
                return XMLToolCallStrategy(tool_registry)
            else:
                logger.info(f"Auto-selected XML format for {model_name}: model does not support Native FC")
                return XMLToolCallStrategy(tool_registry)

        else:
            logger.warning(f"Unknown tool_call_mode: {mode}. Falling back to XML.")
            return XMLToolCallStrategy(tool_registry)

    @staticmethod
    @functools.lru_cache(maxsize=8)
    def _is_native_fc_implemented(provider: str) -> bool:
        """
        Check if our system has implemented Native FC for the given provider

        Returns:
            True if Native FC is fully implemented, False otherwise

        Implementation status (v1.2):
            - [x] OpenAI (ChatAPI): Fully implemented
            - [x] Claude (ClaudeAPI): Fully implemented (v1.2)
            - [x] Google (GoogleAPI): Fully implemented (v1.2)
        """
        # Normalize provider name
        provider_map = {
            "openai_compat": "openai",
            "openai": "openai",
            "claude": "claude",
            "anthropic": "claude",
            "google": "google",
            "gemini": "google",
        }

        normalized_provider = provider_map.get(provider.lower(), "openai")

        # OpenAI, Claude, Google are all implemented now
        implemented_providers = {"openai", "claude", "google"}
        return normalized_provider in implemented_providers

    @staticmethod
    @functools.lru_cache(maxsize=64)
    def _supports_function_calling(provider: str, model_name: str) -> bool:
        """
        Detect if model supports native Function Calling at API level

        Uses ModelCapabilityRegistry for comprehensive model support detection.

        Note: This only checks if the MODEL supports FC, not whether our
        implementation supports it. Use _is_native_fc_implemented() to check
        if our system has implemented Native FC for a given provider.
        """
        from .model_capabilities import ModelCapabilityRegistry

        # Normalize provider name
        provider_map = {
            "openai_compat": "openai",
            "openai": "openai",
            "claude": "claude",
            "anthropic": "claude",
            "google": "google",
            "gemini": "google",
        }

        normalized_provider = provider_map.get(provider.lower(), "openai")

        # Use model capability registry to detect support
        return ModelCapabilityRegistry.supports_function_calling(model_name, normalized_provider)
