"""
Token Analytics Plugin (New-style Decorator API)

Collects and persists token usage data for historical trend analysis.
Data is collected from two sources:

1. EventBus "token_stats" event (via @on_event decorator):
   - Fires after each LLM call via runner._broadcast_token_stats()
   - Contains: window usage, 4-dimension breakdown, cumulative stats, model
   - Stored in token_snapshots table

2. on_after_tool hook (via @hook.on_after_tool decorator):
   - Fires after each tool invocation
   - Records tool name, estimated token consumption of args + result
   - Stored in tool_usage table

All data is persisted to a shared SQLite database at:
    data/plugins/token_analytics/analytics.db
"""

import logging
import os
from typing import Any

from opensquad.plugin_api import Context, Plugin, hook, on_event, register

from .storage import TokenStorage

logger = logging.getLogger("plugins.token_analytics")


@register(
    name="token_analytics",
    author="OpenSquad",
    description="Collects and persists token usage data with model/tool breakdown for historical trend analysis",
    version="1.0.0",
    plugin_type="hook",
    display_name="Token Analytics",
    config_schema={
        "db_path": {
            "type": "string",
            "default": "data/plugins/token_analytics/analytics.db",
            "description": "SQLite database file path (relative to project root)",
        },
        "buffer_size": {
            "type": "integer",
            "default": 10,
            "description": "Number of records to buffer before flushing to disk",
        },
        "flush_interval_sec": {
            "type": "integer",
            "default": 30,
            "description": "Maximum seconds between flushes",
        },
    },
    contributes={
        "views": [
            {
                "name": "token_dashboard",
                "title": "Token Analytics",
                "icon": "BarChart3",
                "data_endpoint": "/api/plugins/token_analytics/data",
            }
        ]
    },
    tags=["analytics"],
)
class TokenAnalyticsPlugin(Plugin):
    """
    Collects token usage data and persists to SQLite for historical analysis.

    Data sources:
    - EventBus "token_stats": per-LLM-call snapshots (window + cumulative)
    - on_after_tool hook: per-tool-call token estimates
    """

    def __init__(self, context: Context):
        super().__init__(context)
        self._storage: TokenStorage = None

    def on_load(self) -> None:
        config = self.context.config

        # Resolve database path (use workspace root so query.py reads same file)
        db_rel = config.get("db_path", "data/plugins/token_analytics/analytics.db")
        ws_root = os.environ.get("OPENSQUAD_WORKSPACE") or self.context.project_root
        db_path = os.path.join(ws_root, db_rel)

        # Buffer configuration
        buffer_size = config.get("buffer_size", 10)
        flush_interval = config.get("flush_interval_sec", 30)

        # Ensure data directory exists
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        # Initialize storage
        self._storage = TokenStorage(
            db_path=db_path,
            buffer_size=buffer_size,
            flush_interval_sec=flush_interval,
        )

        logger.info(f"[TokenAnalytics] Initialized (agent={self.context.agent_id}, db={db_path})")

    @on_event("token_stats")
    def handle_token_stats(self, event_data: dict[str, Any]) -> None:
        """
        EventBus callback: record a token snapshot after each LLM call.

        event_data format (from runner._broadcast_token_stats):
        {
            "sid": "session_id",
            "agent_id": "pm",
            "data": {
                "used": 12345,
                "max": 128000,
                "model": "gpt-4o",
                "breakdown": {"user": N, "thought": N, "tool": N, "response": N},
                "cumulative": {"total_input_tokens": N, "total_output_tokens": N,
                               "total_tokens": N, "total_requests": N}
            }
        }
        """
        if not self._storage:
            return

        try:
            sid = event_data.get("sid", "")
            agent_id = event_data.get("agent_id", "") or self.context.agent_id
            data = event_data.get("data", {})
            model = data.get("model", "")

            self._storage.record_snapshot(
                agent_id=agent_id,
                session_id=sid,
                model=model,
                token_data=data,
            )
        except Exception as e:
            logger.error(f"[TokenAnalytics] Error recording snapshot: {e}", exc_info=True)

    @hook.on_after_tool
    async def track_tool_usage(self, context: dict[str, Any]) -> dict[str, Any]:
        """
        Hook callback: record tool usage with estimated token consumption.

        context keys:
            tool_name, arguments, result, agent_id, model
        """
        if not self._storage:
            return context

        try:
            tool_name = context.get("tool_name", "")
            args = context.get("arguments", "")
            result = context.get("result", "")
            agent_id = context.get("agent_id", "") or self.context.agent_id
            model = context.get("model", "")

            # Convert to string for token estimation
            args_text = str(args) if args else ""
            result_text = str(result) if result else ""

            self._storage.record_tool_usage(
                agent_id=agent_id,
                session_id="",
                model=model,
                tool_name=tool_name,
                args_text=args_text,
                result_text=result_text,
            )
        except Exception as e:
            logger.error(f"[TokenAnalytics] Error recording tool usage: {e}", exc_info=True)

        return context

    def on_unload(self) -> None:
        if self._storage:
            self._storage.close()
            self._storage = None
