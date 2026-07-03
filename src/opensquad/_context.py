"""
AgentContext — dependency container for agent runtime.

Eliminates the global singleton pattern used by seven modules
(EventBus, InputHub, MessageQueue, SleepController, AIStateManager,
EventPipeline, MessageRouter) by providing a single dataclass-based
container created once at boot and injected into all consumers.

Migration phases:
  Phase 1a — Create this file + add get_instance() getters to each module
  Phase 1b — Boot code creates AgentContext and calls set_current_context()
  Phase 1c — AgentRunner and sub-modules accept AgentContext in constructor
  Phase 2  — All inline imports replaced by context lookups
  Phase 3  — Global singletons removed
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from opensquad.chat_api import ChatAPI
    from opensquad.claude_api import ClaudeAPI
    from opensquad.event_pipeline import EventPipeline
    from opensquad.events import EventBus
    from opensquad.google_api import GoogleAPI
    from opensquad.input_hub import InputHub
    from opensquad.message_queue import MessageQueue
    from opensquad.message_router import MessageRouter
    from opensquad.registry import ToolRegistry
    from opensquad.session_manager import SessionManager
    from opensquad.sleep_controller import SleepController
    from opensquad.state_manager import AIStateManager

    # Python 3.11+ union syntax used below (safe with from __future__ import annotations)
    ChatAPIType = ChatAPI | ClaudeAPI | GoogleAPI
    MemoryManager = Any
    PluginManager = Any

logger = logging.getLogger(__name__)


@dataclass
class AgentContext:
    """Container for all agent-level dependencies (ApplicationContext).

    ApplicationContext 模式的核心：将分散在模块级的全局单例（EventBus、
    InputHub、MessageQueue、SessionManager 等）集中管理，通过显式依赖
    注入传递给 AgentRunner 和各子系统。

    Usage::

        # 生产环境：boot 时创建并填充
        ctx = AgentContext(
            event_bus=bus,
            input_hub=input_hub,
            chat_api=chat_api,
            ...
        )
        set_current_context(ctx)

        # 测试环境：注入 mock
        ctx = AgentContext(
            event_bus=MagicMock(),
            input_hub=MagicMock(),
            ...
        )
        runner = AgentRunner(chat_api=ctx.chat_api, agent_context=ctx)

    Migration phase: Phase 1c-1d (see module docstring).

    Fields prefixed with underscores indicate internal state not intended
    for direct access.
    """

    # Runtime services — instantiated by boot()
    event_bus: EventBus | None = None
    """Application-wide event bus (``EventBus``)."""
    input_hub: InputHub | None = None
    """Input dispatch hub — receives user/group messages."""
    message_queue: MessageQueue | None = None
    """Async message queue for group/DM messages (``asyncio.Queue`` wrapper)."""
    sleep_controller: SleepController | None = None
    """Agent sleep/wake cycle controller."""
    state_manager: AIStateManager | None = None
    """AI state machine (idle/working/sleeping)."""
    event_pipeline: EventPipeline | None = None
    """External event pipeline for role=tool message injection."""
    message_router: MessageRouter | None = None
    """Routes incoming messages between agents and group chats."""
    session_manager: SessionManager | None = None
    """Conversation session persistence manager."""

    # Core domain objects — created by boot(), consumed by Runner
    chat_api: ChatAPIType | None = None
    """LLM API client (``ChatAPI`` | ``ClaudeAPI`` | ``GoogleAPI``)."""
    tool_registry: ToolRegistry | None = None
    """Tool discovery and dispatch center."""
    memory_manager: Any | None = None
    """Long-term memory manager (optional, lazily initialized)."""
    plugin_manager: Any | None = None
    """Plugin system manager (optional, set post-boot)."""

    # Metadata — set by boot()
    agent_id: str = ""
    """Unique agent identifier from config.json."""
    agent_name: str = ""
    """Human-readable agent name."""
    config_path: str = ""
    """Absolute path to the agent's ``config.json``."""
    agent_dir: str = ""
    """Absolute path to the agent's directory."""
    workspace_dir: str = ""
    """Active workspace root path."""

    @property
    def is_complete(self) -> bool:
        """Return True if all required runtime services are present.

        Checks the seven mandatory services needed for AgentRunner to
        function. ``chat_api`` and ``tool_registry`` are considered
        optional at construction time because they may be set after
        the plugin system initialises.
        """
        return all(
            [
                self.event_bus is not None,
                self.input_hub is not None,
                self.message_queue is not None,
                self.state_manager is not None,
                self.session_manager is not None,
                self.chat_api is not None,
                self.tool_registry is not None,
            ]
        )

    @classmethod
    def from_boot(cls, **overrides: Any) -> AgentContext:
        """Factory that creates an AgentContext pre-populated with global
        module-level singletons as defaults.

        Callers only need to supply overrides for fields that should differ
        from the global singletons (e.g. ``chat_api``, ``tool_registry``,
        ``agent_id``)::

            ctx = AgentContext.from_boot(agent_id="my-agent", chat_api=my_api)

        Returns:
            AgentContext with module-level defaults merged with overrides.
        """
        from opensquad.event_pipeline import event_pipeline
        from opensquad.events import bus
        from opensquad.input_hub import input_hub
        from opensquad.message_queue import message_queue
        from opensquad.message_router import message_router
        from opensquad.session_manager import session_manager
        from opensquad.sleep_controller import sleep_controller
        from opensquad.state_manager import state_manager

        defaults = {
            "event_bus": bus,
            "input_hub": input_hub,
            "message_queue": message_queue,
            "sleep_controller": sleep_controller,
            "state_manager": state_manager,
            "event_pipeline": event_pipeline,
            "message_router": message_router,
            "session_manager": session_manager,
        }
        defaults.update(overrides)
        return cls(**defaults)


# ------------------------------------------------------------------
# Thread-safe current-context holder (ContextVar)
# Phase 1b sets this via set_current_context().
# ------------------------------------------------------------------

_context_var: ContextVar[AgentContext | None] = ContextVar(
    "agent_context",
    default=None,
)


def set_current_context(ctx: AgentContext) -> None:
    """Set the AgentContext for the current async context (task/thread).

    Args:
        ctx: The AgentContext to associate with the current executing
             asyncio task (``ContextVar`` scoped).
    """
    _context_var.set(ctx)


def get_current_context() -> AgentContext | None:
    """Return the AgentContext for the current execution context, or None
    if no context has been set.

    Safe to call from any async context — returns ``None`` if
    ``set_current_context()`` has not been called.
    """
    return _context_var.get()


def require_context() -> AgentContext:
    """Return the current AgentContext or raise ``RuntimeError``.

    Useful in boot code or deep call chains where ``None`` would be
    a programming error rather than a runtime condition.

    Raises:
        RuntimeError: If no AgentContext has been set via ``set_current_context()``.
    """
    ctx = _context_var.get()
    if ctx is None:
        raise RuntimeError("No AgentContext available. Ensure set_current_context() was called during boot.")
    return ctx
