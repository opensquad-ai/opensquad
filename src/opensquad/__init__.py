"""
OpenSquad - Local-first Multi-Agent Collaboration Framework

https://github.com/opensquad-ai/opensquad

Version: single source of truth is pyproject.toml [project].version.
Run ``python scripts/sync_version.py`` after bumping pyproject.toml.
"""

from __future__ import annotations

from typing import Any

__version__ = "0.8.1"
__author__ = "OpenSquad Contributors"

# Shared runtime context dict - updated by Runner each turn.
_runtime_ctx: dict = {}

# Heavy symbols (AgentRunner, ToolRegistry, …) are loaded on first attribute
# access so `import opensquad` / `from opensquad import __version__` stay cheap.
_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "AgentRunner": (".runner", "AgentRunner"),
    "EventBus": (".events", "EventBus"),
    "bus": (".events", "bus"),
    "ToolRegistry": (".registry", "ToolRegistry"),
    "HotReloadManager": ("._runner", "HotReloadManager"),
    "InputHandler": ("._runner", "InputHandler"),
    "OutputHandler": ("._runner", "OutputHandler"),
    "StateMachine": ("._runner", "StateMachine"),
    "ToolExecutor": ("._runner", "ToolExecutor"),
    "build_summary_payload": ("._runner", "build_summary_payload"),
    "run_external_summarizer": ("._runner", "run_external_summarizer"),
}

__all__ = [
    "AgentRunner",
    "EventBus",
    "HotReloadManager",
    "InputHandler",
    "OutputHandler",
    "StateMachine",
    "ToolExecutor",
    "ToolRegistry",
    "__author__",
    "__version__",
    "_runtime_ctx",
    "build_summary_payload",
    "bus",
    "run_external_summarizer",
]


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr = target
    try:
        from importlib import import_module

        module = import_module(module_name, __name__)
        value = getattr(module, attr)
    except ImportError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
