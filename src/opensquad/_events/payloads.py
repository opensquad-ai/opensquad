# -*- coding: utf-8 -*-
"""
Typed payload schemas for EventBus events.

Each dataclass corresponds to an event type string emitted by
``EventBus.emit_typed()``.  Using typed payloads instead of raw dicts
catches payload structure errors at the call site.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional


@dataclass
class ToolCallEvent:
    """Payload for ``tool_call`` events."""
    id: str
    name: str
    args: str


@dataclass
class TokenStatsEvent:
    """Payload for ``token_stats`` events."""
    agent_id: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0


@dataclass
class StateChangeEvent:
    """Payload for ``state_change`` events."""
    old_state: str
    new_state: str
    agent_id: str


@dataclass
class AgentReadyEvent:
    """Payload for ``agent_ready`` events."""
    agent_id: str


@dataclass
class StatusEvent:
    """Payload for ``status`` updates."""
    status: str
    agent_id: str = ""


@dataclass
class InfoEvent:
    """Payload for ``info`` messages."""
    text: str
    event: str = ""
    agent_id: str = ""


@dataclass
class SessionEvent:
    """Payload for ``current_session`` / ``session_list`` events."""
    id: str = ""
    title: str = ""
    sessions: Optional[List[dict]] = None


@dataclass
class HistorySyncEvent:
    """Payload for ``history_sync`` events."""
    messages: List[dict] = None
    events: List[dict] = None
    session_id: str = ""
    is_working_session: bool = True


@dataclass
class UserMessageEvent:
    """Payload for ``user_msg`` events."""
    content: str
    sid: str = ""
    channel: str = ""


@dataclass
class TurnStartEvent:
    """Payload for ``turn_start`` events."""
    turn: int = 0
    started_ms: int = 0


@dataclass
class TurnElapsedEvent:
    """Payload for ``turn_elapsed`` events."""
    started_ms: int = 0
    ended_ms: int = 0


# Registry: event_type_name → payload dataclass
EVENT_PAYLOADS: dict[str, type] = {
    "tool_call": ToolCallEvent,
    "token_stats": TokenStatsEvent,
    "state_change": StateChangeEvent,
    "agent_ready": AgentReadyEvent,
    "status": StatusEvent,
    "info": InfoEvent,
    "current_session": SessionEvent,
    "session_list": SessionEvent,
    "history_sync": HistorySyncEvent,
    "user_msg": UserMessageEvent,
    "turn_start": TurnStartEvent,
    "turn_elapsed": TurnElapsedEvent,
}
