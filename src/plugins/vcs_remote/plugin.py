import logging
from typing import Any

from opensquad.plugin_api import Context, Plugin, hook, on_event, register

logger = logging.getLogger("plugins.vcs_remote")


@register(
    name="vcs_remote",
    author="OpenSquad",
    description="Remote VCS tools using GitHub CLI (gh). Handles Issues and PRs.",
    version="1.1.0",
    plugin_type="tool",
    display_name="VCS Remote",
    tags=["github", "vcs", "remote", "issue", "pr"],
    contributes={
        "views": [{"name": "audit", "title": "VCS Audit", "icon": "History", "data_endpoint": "/api/ai-web/audit/logs"}]
    },
)
class VCSRemotePlugin(Plugin):
    def __init__(self, context: Context):
        super().__init__(context)
        from opensquad.utils.audit_vcs import AuditLogManager

        self.audit_mgr = AuditLogManager(self.context.project_root)

    def on_load(self) -> None:
        logger.info("[VCSRemotePlugin] Initialized.")

    @hook.on_after_tool
    async def track_vcs_footprint(self, ctx: dict[str, Any]):
        """Capture footprints of Git and VCS operations."""
        t_name = ctx.get("tool_name", "")
        # Only track tools in 'git' or 'vcs' namespaces
        if not (t_name.startswith("git.") or t_name.startswith("vcs.")):
            return ctx

        self.audit_mgr.log_footprint(
            agent_id=self.context.agent_id or "system",
            action=t_name,
            arguments=ctx.get("arguments", {}),
            output=str(ctx.get("result", "")),
            status="success" if not str(ctx.get("result", "")).startswith("Error") else "error",
        )
        return ctx

    def get_tool_modules(self) -> list[dict[str, Any]]:
        from . import vcs_tools

        # Wrap tool functions to add event publishing logic
        def wrap_with_event(func, event_name):
            def wrapper(*args, **kwargs):
                res = func(*args, **kwargs)
                if isinstance(res, str) and not res.startswith("Error"):
                    # Publish precise collaboration events
                    self.context.event_bus.emit(
                        "vcs_activity",
                        {
                            "action": event_name,
                            "agent_id": self.context.agent_id,
                            "result": res,
                            "payload": {"args": args, "kwargs": kwargs},
                        },
                    )
                return res

            wrapper.__name__ = func.__name__
            wrapper.__doc__ = func.__doc__
            return wrapper

        # Dynamically wrap key collaboration functions
        wrapped_module = type("vcs_tools_wrapped", (), {})()
        for attr in dir(vcs_tools):
            val = getattr(vcs_tools, attr)
            if callable(val) and not attr.startswith("_"):
                if attr in ["issue_create", "issue_comment", "pr_create", "pr_merge"]:
                    setattr(wrapped_module, attr, wrap_with_event(val, attr))
                else:
                    setattr(wrapped_module, attr, val)

        return [
            {
                "name": "vcs",
                "module": wrapped_module,
                "level": "core",
                "auto_register": True,
            }
        ]

    @on_event("vcs_activity")
    async def handle_vcs_event(self, data: dict[str, Any]):
        """Handle VCS events and push notifications based on context."""
        action = data.get("action")
        origin_agent = data.get("agent_id")

        # Avoid handling events emitted by self (unless required)
        if origin_agent == self.context.agent_id:
            return

        # Get the current Agent's identity and task context to decide whether to respond
        # Example: if this is a PR creation event and I am the Reviewer, notify me in group chat
        logger.info(f"[VCSRemote] Agent {self.context.agent_id} detected VCS activity: {action} by {origin_agent}")

        # More complex routing logic can be added here, e.g., checking the Issue/PR assignee
        # If the current Agent is the assignee, send a DM or group @ mention via the im tool
