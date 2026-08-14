"""
OpenSquad Plugin API (Decorator-based)

Provides a developer-friendly, AstrBot-inspired API for building plugins:

    from opensquad.plugin_api import register, tool, hook, on_event, Plugin, Context

    @register("my_plugin", "Author", "A cool plugin", "1.0.0")
    class MyPlugin(Plugin):
        def __init__(self, context: Context):
            super().__init__(context)

        @hook.on_after_tool
        async def track(self, ctx): ...

        @tool(name="my_ns", description="Do stuff", level="core")
        def do_stuff(self, query: str): ...

        @on_event("token_stats")
        def handle_stats(self, data): ...

Lifecycle:
    1. PluginManager discovers plugin directories
    2. Imports plugin.py, finds class with __plugin_meta__
    3. Builds Context, instantiates Plugin(context)
    4. Calls plugin.on_load()
    5. Scans @tool methods -> ToolModuleWrapper -> ToolRegistry
    6. Scans @hook methods -> hook chain
    7. Scans @on_event methods -> EventBus.subscribe()
    8. Auto-generates plugin.json for Launcher static reads
    9. Proxy-pattern plugins import a tool module via proxy_tool_module()
       (import failure is PluginToolAttachError, not a silent empty list)
"""

import functools
import inspect
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Context: runtime information injected into every plugin
# ---------------------------------------------------------------------------


class Context:
    """
    Runtime context passed to every plugin at instantiation.

    Attributes:
        agent_id:     ID of the agent this plugin is loaded for
        project_root: absolute path to the project root directory
        event_bus:    EventBus singleton (opensquad.events.bus)
        config:       plugin's own config values (from config_schema defaults
                      merged with any user overrides)
        data_dir:     absolute path to data/plugins/{plugin_name}/
        plugin_dir:   absolute path to plugins/{plugin_name}/
    """

    __slots__ = (
        "agent_id",
        "config",
        "data_dir",
        "event_bus",
        "plugin_dir",
        "project_root",
    )

    def __init__(
        self,
        agent_id: str = "",
        project_root: str = ".",
        event_bus: Any = None,
        config: dict[str, Any] | None = None,
        data_dir: str = "",
        plugin_dir: str = "",
    ):
        self.agent_id = agent_id
        self.project_root = project_root
        self.event_bus = event_bus
        self.config = config or {}
        self.data_dir = data_dir
        self.plugin_dir = plugin_dir

    def __repr__(self):
        return f"Context(agent_id={self.agent_id!r}, data_dir={self.data_dir!r})"


# ---------------------------------------------------------------------------
# Plugin: base class for all new-style plugins
# ---------------------------------------------------------------------------


class Plugin:
    """
    Base class for all new-style OpenSquad plugins.

    Subclasses should:
    - Be decorated with @register(...)
    - Accept a Context in __init__
    - Optionally override on_load() and on_unload()
    - Use @tool, @hook.on_xxx, @on_event decorators on methods
    """

    def __init__(self, context: Context):
        self.context = context
        # Populated by PluginManager after __init__:
        self.name: str = ""
        self.version: str = ""
        self.plugin_type: str = ""

    def on_load(self) -> None:
        """Called after plugin is instantiated and metadata is set up."""
        pass

    def on_unload(self) -> None:
        """Called during shutdown."""
        pass

    @property
    def is_loaded(self) -> bool:
        return bool(self.name)


# ---------------------------------------------------------------------------
# @register decorator
# ---------------------------------------------------------------------------


def register(
    name: str,
    author: str = "",
    description: str = "",
    version: str = "1.0.0",
    plugin_type: str = "tool",
    display_name: str = "",
    config_schema: dict[str, Any] | None = None,
    config_section: str = "",
    dependencies: dict[str, list[str]] | None = None,
    contributes: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    node_scope: str = "all",
):
    """
    Class decorator: declare plugin metadata.

    Usage:
        @register("websearch", "OpenSquad", "Web search tools", "1.0.0",
                  plugin_type="tool")
        class WebSearchPlugin(Plugin):
            ...

    The metadata is stored on cls.__plugin_meta__ and used by PluginManager
    to auto-generate plugin.json and manage the plugin lifecycle.

    Args:
        name:          unique plugin identifier (e.g. "websearch", "token_analytics")
        author:        plugin author name
        description:   human-readable description
        version:       semantic version string
        plugin_type:   "platform" | "tool" | "hook" — classification label only.
                       Runtime behavior is determined by what the plugin actually
                       registers (@tool methods, @hook methods, or a `service`
                       field in plugin.json), NOT by this label. The only runtime
                       branch is `platform` vs non-platform (platform plugins
                       bridge their config to system_config.json, see
                       launcher_main.py). The `hook` type is a documentation hint
                       meaning "no @tool methods, only hooks/events/side-effects"
                       (e.g. token_analytics, task_watch, long_memory). A `hook`
                       plugin that also exposes @tool methods works fine — the
                       label is advisory and does not gate any code path.
        display_name:  short display name (defaults to auto-generated from name)
        config_schema: JSON-Schema-like dict for plugin configuration.
                       Each key maps to a field descriptor dict with:
                         type        (str)  "string" | "integer" | "number" | "boolean"
                         default     (any)  Default value
                         description (str)  Help text shown in the UI
                         secret      (bool) If True, rendered as a masked password input
                         enum        (list) Restricts value to one of these choices
        dependencies:  {"pip": ["requests", ...]}
        contributes:   frontend contribution points, e.g.
                       {"views": [{"name": "...", "title": "...", "icon": "...", "data_endpoint": "..."}]}
        node_scope:    "all" | "single".  Advisory hint for multi-node deployments.
                       "all"    -- plugin is meaningful on every node (default).
                       "single" -- plugin should run on exactly one node (e.g. an
                                  IMAP listener); generated plugin.json will have
                                  enabled=False so only the chosen node is activated.
    """

    def decorator(cls):
        cls.__plugin_meta__ = {
            "name": name,
            "author": author,
            "display_name": display_name,
            "description": description,
            "version": version,
            "type": plugin_type,
            "config_schema": config_schema or {},
            "config_section": config_section,
            "dependencies": dependencies or {"pip": []},
            "contributes": contributes or {},
            "tags": tags or [],
            "node_scope": node_scope,
        }
        return cls

    return decorator


# Re-export so `from opensquad.plugin_api import proxy_tool_module` works in source.
try:
    from plugins.proxy_tools import PluginToolAttachError, proxy_tool_module
except ImportError:  # pragma: no cover - plugins package always present in-repo
    PluginToolAttachError = RuntimeError  # type: ignore[misc, assignment]

    def proxy_tool_module(*_a, **_k):  # type: ignore[no-untyped-def]
        raise ImportError("plugins.proxy_tools is required")


# ---------------------------------------------------------------------------
# @tool decorator
# ---------------------------------------------------------------------------


def tool(
    name: str = "",
    description: str = "",
    level: str = "extended",
    auto_register: bool = False,
    requires_agent_id: bool = False,
):
    """
    Method decorator: mark a method as a registerable agent tool.

    Usage:
        class MyPlugin(Plugin):
            @tool(name="websearch", description="Search the web", level="core")
            def search(self, queries: list, max_results: int = 30):
                ...

    Multiple methods can share the same `name` -- they will be grouped
    under a single tool namespace (like functions in a module).

    Args:
        name:             tool namespace name (e.g. "websearch")
                          if empty, defaults to the method name
        description:      tool-level description
        level:            "core" (detailed docs) or "extended" (summary only)
        auto_register:    if True, auto-registered to all agents
        requires_agent_id: if True, agent_id will be available via self.context
    """

    def decorator(method):
        method.__tool_meta__ = {
            "name": name or method.__name__,
            "description": description,
            "level": level,
            "auto_register": auto_register,
            "requires_agent_id": requires_agent_id,
        }
        return method

    return decorator


# ---------------------------------------------------------------------------
# @hook decorators
# ---------------------------------------------------------------------------


class _HookDecorators:
    """
    Namespace for hook decorators.

    All decorators support an optional `priority` keyword argument (default 0).
    Higher priority handlers run first within the same hook chain.

    Two call styles are supported:

        @hook.on_after_tool                  # priority=0 (default)
        @hook.on_after_tool(priority=100)    # explicit priority

    Stacking multiple hooks on one method is also supported:

        @hook.on_before_tool
        @hook.on_after_tool(priority=50)
        async def my_handler(self, ctx): ...
    """

    # All valid hook names
    HOOK_NAMES = frozenset(
        {
            # -- Input / LLM pipeline --
            "on_message_received",  # message received, before any processing
            "on_before_llm",  # just before LLM API call
            "on_after_llm",  # just after LLM API response
            # -- Tool execution --
            "on_before_tool",  # before a tool is called (supports skip)
            "on_after_tool",  # after a tool call (success or error)
            "on_tool_error",  # after a tool call that returned Error:...
            # -- Output --
            "on_before_send",  # before reply is persisted + emitted (supports stop/rewrite)
            "on_after_send",  # after reply has been sent
            # -- Prompt --
            "on_before_prompt",  # before system prompt is finalized each turn (supports rewrite)
            # -- Task lifecycle --
            "on_task_start",  # when a new task begins (state -> working)
            "on_task_complete",  # when a task ends (task_complete / task_failed)
            # -- State machine --
            "on_state_change",  # when agent state transitions (idle/working/sleeping)
        }
    )

    @staticmethod
    def on_message_received(method=None, *, priority: int = 0):
        """Hook: message received, before any processing.

        Context: message, channel, sender_name, chat_name, source_chat_id, input_source.
        Set context['__stop__'] = True to drop the message entirely.
        """

        def decorator(m):
            if not hasattr(m, "__hook_meta__"):
                m.__hook_meta__ = []
            m.__hook_meta__.append({"hook_name": "on_message_received", "priority": priority})
            return m

        if method is not None:
            return decorator(method)
        return decorator

    @staticmethod
    def on_before_llm(method=None, *, priority: int = 0):
        """Hook: just before LLM API call.

        Context: messages (list), model (str), agent_id.
        Set context['__stop__'] = True to skip the LLM call entirely.
        """

        def decorator(m):
            if not hasattr(m, "__hook_meta__"):
                m.__hook_meta__ = []
            m.__hook_meta__.append({"hook_name": "on_before_llm", "priority": priority})
            return m

        if method is not None:
            return decorator(method)
        return decorator

    @staticmethod
    def on_after_llm(method=None, *, priority: int = 0):
        """Hook: just after LLM API response.

        Context: response (str), agent_id.
        Modify context['response'] to rewrite the raw LLM output.
        """

        def decorator(m):
            if not hasattr(m, "__hook_meta__"):
                m.__hook_meta__ = []
            m.__hook_meta__.append({"hook_name": "on_after_llm", "priority": priority})
            return m

        if method is not None:
            return decorator(method)
        return decorator

    @staticmethod
    def on_before_tool(method=None, *, priority: int = 0):
        """Hook: before a tool is called.

        Context: tool_name, arguments, agent_id.
        Set context['skip'] = True to skip execution (provide context['result'] as substitute).
        """

        def decorator(m):
            if not hasattr(m, "__hook_meta__"):
                m.__hook_meta__ = []
            m.__hook_meta__.append({"hook_name": "on_before_tool", "priority": priority})
            return m

        if method is not None:
            return decorator(method)
        return decorator

    @staticmethod
    def on_after_tool(method=None, *, priority: int = 0):
        """Hook: after a tool call (success or error).

        Context: tool_name, arguments, result, agent_id, model.
        Modify context['result'] to rewrite the tool result seen by the LLM.
        """

        def decorator(m):
            if not hasattr(m, "__hook_meta__"):
                m.__hook_meta__ = []
            m.__hook_meta__.append({"hook_name": "on_after_tool", "priority": priority})
            return m

        if method is not None:
            return decorator(method)
        return decorator

    @staticmethod
    def on_tool_error(method=None, *, priority: int = 0):
        """Hook: after a tool call that returned an error (result starts with 'Error:').

        Fired AFTER on_after_tool, only when result is still an error string.
        Context: tool_name, arguments, error (str), agent_id.
        Modify context['error'] to override the error message sent to the LLM
        (e.g. inject a retry instruction or substitute a fallback result).
        """

        def decorator(m):
            if not hasattr(m, "__hook_meta__"):
                m.__hook_meta__ = []
            m.__hook_meta__.append({"hook_name": "on_tool_error", "priority": priority})
            return m

        if method is not None:
            return decorator(method)
        return decorator

    @staticmethod
    def on_before_send(method=None, *, priority: int = 0):
        """Hook: just before the assistant reply is persisted and emitted.

        Context: message (str), agent_id.
        Modify context['message'] to rewrite the reply.
        Set context['__stop__'] = True to cancel sending entirely.
        """

        def decorator(m):
            if not hasattr(m, "__hook_meta__"):
                m.__hook_meta__ = []
            m.__hook_meta__.append({"hook_name": "on_before_send", "priority": priority})
            return m

        if method is not None:
            return decorator(method)
        return decorator

    @staticmethod
    def on_after_send(method=None, *, priority: int = 0):
        """Hook: after the assistant reply has been persisted and emitted.

        Context: message (str), agent_id.
        Read-only in practice; use for logging, analytics, side-effects.
        """

        def decorator(m):
            if not hasattr(m, "__hook_meta__"):
                m.__hook_meta__ = []
            m.__hook_meta__.append({"hook_name": "on_after_send", "priority": priority})
            return m

        if method is not None:
            return decorator(method)
        return decorator

    @staticmethod
    def on_before_prompt(method=None, *, priority: int = 0):
        """Hook: before the system prompt is finalized for each LLM turn.

        Context: prompt (str), agent_id.
        Modify context['prompt'] to inject or rewrite the system prompt dynamically.
        Useful for: real-time data injection, SLA countdowns, per-turn context.
        """

        def decorator(m):
            if not hasattr(m, "__hook_meta__"):
                m.__hook_meta__ = []
            m.__hook_meta__.append({"hook_name": "on_before_prompt", "priority": priority})
            return m

        if method is not None:
            return decorator(method)
        return decorator

    @staticmethod
    def on_task_start(method=None, *, priority: int = 0):
        """Hook: when a new task begins (agent state transitions to 'working').

        Context: task_id, requirement (str), source (str), agent_id.
        Use for: external notifications, SLA timer start, task chain triggers.
        """

        def decorator(m):
            if not hasattr(m, "__hook_meta__"):
                m.__hook_meta__ = []
            m.__hook_meta__.append({"hook_name": "on_task_start", "priority": priority})
            return m

        if method is not None:
            return decorator(method)
        return decorator

    @staticmethod
    def on_task_complete(method=None, *, priority: int = 0):
        """Hook: when a task ends (task_complete or task_failed system command).

        Context: task_id, completion_status ('completed'|'failed'), tools_used (list),
                 turns (int), agent_id.
        Use for: completion notifications, audit logging, chaining follow-up tasks.
        """

        def decorator(m):
            if not hasattr(m, "__hook_meta__"):
                m.__hook_meta__ = []
            m.__hook_meta__.append({"hook_name": "on_task_complete", "priority": priority})
            return m

        if method is not None:
            return decorator(method)
        return decorator

    @staticmethod
    def on_state_change(method=None, *, priority: int = 0):
        """Hook: when the agent state machine transitions.

        Context: old_state (str), new_state (str), agent_id.
        States: 'idle', 'working', 'sleeping'.
        Use for: stuck-detection alerts, progress dashboards, resource management.

        NOTE: this hook fires as an asyncio task (outside the state lock),
        so handlers must not assume the state hasn't changed again by the time
        they run. Do NOT call state_manager.get_state() from within this hook.
        """

        def decorator(m):
            if not hasattr(m, "__hook_meta__"):
                m.__hook_meta__ = []
            m.__hook_meta__.append({"hook_name": "on_state_change", "priority": priority})
            return m

        if method is not None:
            return decorator(method)
        return decorator


hook = _HookDecorators()
"""
Hook decorator namespace. Usage:

    @hook.on_after_tool
    async def my_hook(self, context):
        ...
"""


# ---------------------------------------------------------------------------
# @on_event decorator
# ---------------------------------------------------------------------------


def on_event(event_type: str):
    """
    Method decorator: auto-subscribe to an EventBus event.

    Usage:
        class MyPlugin(Plugin):
            @on_event("token_stats")
            def handle_stats(self, event_data: dict):
                ...

    PluginManager will call context.event_bus.subscribe(event_type, bound_method)
    automatically during plugin loading.

    Args:
        event_type: EventBus event name (e.g. "token_stats")
    """

    def decorator(method):
        if not hasattr(method, "__event_meta__"):
            method.__event_meta__ = []
        method.__event_meta__.append({"event_type": event_type})
        return method

    return decorator


# ---------------------------------------------------------------------------
# ToolModuleWrapper: bridge between @tool methods and ToolRegistry
# ---------------------------------------------------------------------------


class ToolModuleWrapper:
    """
    Wraps a plugin instance's @tool-decorated methods into a ToolRegistry-
    compatible interface.

    ToolRegistry uses inspect.getmembers(module, inspect.isfunction) to
    discover tool functions, then getattr(module, func_name) to call them.

    This wrapper exposes bound methods as regular functions so that
    ToolRegistry can discover and call them transparently.

    Usage (internal, by PluginManager):
        wrapper = ToolModuleWrapper(plugin_instance, namespace="websearch")
        registry.register(wrapper, namespace="websearch", level="core")
    """

    def __init__(self, plugin_instance: Plugin, namespace: str):
        self._plugin = plugin_instance
        self._namespace = namespace
        self._functions: dict[str, Callable] = {}

    def add_method(self, method_name: str, bound_method: Callable, doc: str = ""):
        """
        Add a bound method as a plain function attribute.

        The function is exposed with proper signature (without 'self') so
        that ToolRegistry's inspect-based discovery works correctly.
        Preserves *args / **kwargs from the original signature so that
        variadic tool methods are callable with positional args too.
        """

        # Create a wrapper function that strips 'self' from the signature
        # but transparently forwards both positional and keyword args.
        @functools.wraps(bound_method)
        def wrapper_func(*args, **kwargs):
            return bound_method(*args, **kwargs)

        # Rebuild signature without 'self', keeping VAR_POSITIONAL / VAR_KEYWORD
        orig_sig = inspect.signature(bound_method)
        params = [p for p in orig_sig.parameters.values() if p.name != "self"]
        wrapper_func.__signature__ = orig_sig.replace(parameters=params)

        if doc:
            wrapper_func.__doc__ = doc

        # Set as attribute so getattr(wrapper, method_name) works
        self._functions[method_name] = wrapper_func
        setattr(self, method_name, wrapper_func)

    def __repr__(self):
        funcs = list(self._functions.keys())
        return f"ToolModuleWrapper(ns={self._namespace!r}, funcs={funcs})"


# ---------------------------------------------------------------------------
# Utility: extract metadata from a plugin class
# ---------------------------------------------------------------------------


def get_plugin_meta(cls) -> dict[str, Any] | None:
    """Get __plugin_meta__ from a class, or None if not decorated."""
    return getattr(cls, "__plugin_meta__", None)


def get_tool_methods(instance) -> list[dict[str, Any]]:
    """
    Scan a plugin instance for @tool-decorated methods.

    Returns list of:
        {"method_name": str, "bound_method": callable, "meta": dict}
    """
    results = []
    for attr_name in dir(instance):
        if attr_name.startswith("_"):
            continue
        try:
            attr = getattr(instance, attr_name)
        except Exception:
            continue
        if callable(attr) and hasattr(attr, "__tool_meta__"):
            results.append(
                {
                    "method_name": attr_name,
                    "bound_method": attr,
                    "meta": attr.__tool_meta__,
                }
            )
    return results


def get_hook_methods(instance) -> dict[str, list[Callable]]:
    """
    Scan a plugin instance for @hook-decorated methods.

    Returns dict of: {hook_name: [bound_method, ...]}
    """
    hooks: dict[str, list[Callable]] = {}
    for attr_name in dir(instance):
        if attr_name.startswith("_"):
            continue
        try:
            attr = getattr(instance, attr_name)
        except Exception:
            continue
        if callable(attr) and hasattr(attr, "__hook_meta__"):
            for entry in attr.__hook_meta__:
                hook_name = entry["hook_name"]
                if hook_name not in hooks:
                    hooks[hook_name] = []
                hooks[hook_name].append(attr)
    return hooks


def get_event_methods(instance) -> list[dict[str, Any]]:
    """
    Scan a plugin instance for @on_event-decorated methods.

    Returns list of: {"event_type": str, "bound_method": callable}
    """
    results = []
    for attr_name in dir(instance):
        if attr_name.startswith("_"):
            continue
        try:
            attr = getattr(instance, attr_name)
        except Exception:
            continue
        if callable(attr) and hasattr(attr, "__event_meta__"):
            for entry in attr.__event_meta__:
                results.append(
                    {
                        "event_type": entry["event_type"],
                        "bound_method": attr,
                    }
                )
    return results


def generate_plugin_json(cls, instance=None) -> dict[str, Any]:
    """
    Generate a plugin.json-compatible dict from @register + @tool + @hook metadata.

    Used by PluginManager to auto-create/update plugin.json for Launcher.

    Args:
        cls:      the plugin class (with __plugin_meta__)
        instance: optional instance (for scanning bound methods);
                  if None, scans unbound methods on the class

    Returns:
        dict suitable for writing as plugin.json
    """
    meta = cls.__plugin_meta__
    scan_target = instance if instance is not None else cls

    # Collect tools
    tools = []
    seen_namespaces = set()
    for attr_name in dir(scan_target):
        if attr_name.startswith("_"):
            continue
        try:
            attr = getattr(scan_target, attr_name)
        except Exception:
            continue
        if callable(attr) and hasattr(attr, "__tool_meta__"):
            tmeta = attr.__tool_meta__
            ns = tmeta["name"]
            if ns not in seen_namespaces:
                seen_namespaces.add(ns)
                tools.append(
                    {
                        "name": ns,
                        "module": "plugin_api",
                        "level": tmeta.get("level", "extended"),
                        "auto_register": tmeta.get("auto_register", False),
                        "requires_agent_id": tmeta.get("requires_agent_id", False),
                    }
                )

    # Collect hooks
    hook_names = []
    for attr_name in dir(scan_target):
        if attr_name.startswith("_"):
            continue
        try:
            attr = getattr(scan_target, attr_name)
        except Exception:
            continue
        if callable(attr) and hasattr(attr, "__hook_meta__"):
            for entry in attr.__hook_meta__:
                hname = entry["hook_name"]
                if hname not in hook_names:
                    hook_names.append(hname)

    # Build config section
    config_schema = meta.get("config_schema", {})
    config_section = {
        "schema": config_schema,
    }
    # Platform plugins with config_section bridge to system_config.json
    if meta.get("config_section"):
        config_section["section"] = meta["config_section"]

    return {
        "name": meta["name"],
        "display_name": meta.get("display_name", meta["name"].replace("_", " ").title()),
        "version": meta["version"],
        "type": meta["type"],
        "enabled": meta.get("node_scope", "all") != "single",
        "node_scope": meta.get("node_scope", "all"),
        "description": meta.get("description", ""),
        "author": meta.get("author", ""),
        "tags": meta.get("tags", []),
        "tools": tools,
        "hooks": hook_names,
        "config": config_section,
        "config_schema": config_schema,
        "contributes": meta.get("contributes", {}),
        "dependencies": meta.get("dependencies", {"pip": []}),
    }
