"""
Canonical message model for LLM turns.

Single conversion boundary between raw LLM responses and internal turn data,
mirroring the "convert only at the LLM boundary" design:

- ``ToolCall`` / ``ToolResult`` -- canonical tool-call representation
  (the codebase historically passes ``list[tuple[str, dict]]`` around; these
  dataclasses give the same data a name and a stable shape).
- ``AssistantTurn`` -- parsed content of one assistant response (tags, options,
  visible text, tool calls).
- ``parse_tool_calls()`` -- the boundary the turn loop uses instead of inline
  format juggling: native-FC data wins, then XML, then the parser's other
  formats (JSON / DSML / Minimax / attr-based).

The tag-extraction half of ``parse_assistant_turn`` is the canonical API;
wiring it into the turn loop's inline tag block is a follow-up (the turn loop
currently still extracts tags itself -- see _runner/_turn_loop.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from opensquad._runner._tag_utils import compose_user_visible_message
from opensquad.parser import ResponseParser

__all__ = [
    "AssistantTurn",
    "ToolCall",
    "ToolResult",
    "parse_assistant_turn",
    "parse_tool_calls",
]


@dataclass(frozen=True)
class ToolCall:
    """Canonical tool call: name + parsed args dict."""

    name: str
    args: dict[str, Any]

    @classmethod
    def from_tuple(cls, pair: tuple[str, dict[str, Any]]) -> ToolCall:
        name, args = pair
        return cls(name, args)

    def to_tuple(self) -> tuple[str, dict[str, Any]]:
        return (self.name, self.args)


@dataclass(frozen=True)
class ToolResult:
    """Canonical tool result (matches the persisted event shape)."""

    id: str
    name: str
    args: dict[str, Any]
    result: Any

    def to_event(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "args": self.args, "result": self.result}


@dataclass
class AssistantTurn:
    """One assistant response, parsed into its canonical parts."""

    text: str
    thought: str = ""
    plan: str = ""
    state: str | None = None
    wake: str | None = None
    sleep_seconds: str | None = None
    sys_cmd: str | None = None
    task_start: str | None = None
    title: str | None = None
    options: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    visible_text: str = ""
    visible_tag: str | None = None

    @property
    def has_tools(self) -> bool:
        return bool(self.tool_calls)


def parse_tool_calls(
    full_response: str,
    tool_data_from_api: list[tuple[str, dict[str, Any]]] | None = None,
) -> list[ToolCall]:
    """Canonical tool-call extraction.

    ``tool_data_from_api`` (native function-calling data) wins when present;
    otherwise the response is parsed with the strategy's parser (XML first,
    then JSON / DSML / Minimax / attr formats via ``ResponseParser``).
    """
    if tool_data_from_api:
        return [ToolCall.from_tuple(pair) for pair in tool_data_from_api]
    parsed = ResponseParser.parse_tool_calls(full_response)
    return [ToolCall(name, args) for name, args in parsed]


def parse_assistant_turn(
    full_response: str, tool_data_from_api: list[tuple[str, dict[str, Any]]] | None = None
) -> AssistantTurn:
    """Parse a full assistant response into a canonical ``AssistantTurn``.

    Combines tag extraction (thought / plan / state / wake / sleep / to_system /
    task_start / title / options), user-visible text composition, and tool-call
    extraction through ``parse_tool_calls``.
    """
    text = str(full_response or "")
    turn = AssistantTurn(text=text)
    if not text.strip():
        return turn

    turn.thought = ResponseParser.extract_tag(text, "thought") or ResponseParser.extract_tag(text, "think") or ""
    turn.plan = ResponseParser.extract_tag(text, "plan") or ""

    # Option buttons
    import re

    option_matches = re.findall(r"<option>(.*?)</option>", text, re.DOTALL)
    turn.options = [o.strip() for o in option_matches if o.strip()]

    # State / control tags (single-occurrence)
    for attr, tag in (
        ("state", "state"),
        ("wake", "wake"),
        ("sleep_seconds", "sleep"),
        ("sys_cmd", "to_system"),
        ("task_start", "task_start"),
        ("title", "title"),
    ):
        found = ResponseParser.extract_tag(text, tag)
        if found is not None:
            setattr(turn, attr, found.strip() if isinstance(found, str) else found)

    visible_text, visible_tag = compose_user_visible_message(text)
    turn.visible_text = visible_text
    turn.visible_tag = visible_tag

    turn.tool_calls = parse_tool_calls(text, tool_data_from_api)
    return turn
