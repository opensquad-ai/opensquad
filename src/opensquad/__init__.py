"""
OpenSquad - Local-first Multi-Agent Collaboration Framework

https://github.com/opensquad-ai/opensquad

Version: single source of truth is pyproject.toml [project].version.
Run ``python scripts/sync_version.py`` after bumping pyproject.toml.
"""

__version__ = "0.4.10"
__author__ = "OpenSquad Contributors"

# Shared runtime context dict - updated by Runner each turn.
_runtime_ctx: dict = {}

try:
    from ._runner import (
        HotReloadManager,
        InputHandler,
        OutputHandler,
        StateMachine,
        ToolExecutor,
        build_summary_payload,
        run_external_summarizer,
    )
    from .events import EventBus, bus
    from .registry import ToolRegistry
    from .runner import AgentRunner

    __all__ = [
        "AgentRunner",
        "EventBus",
        "HotReloadManager",
        "InputHandler",
        "OutputHandler",
        "StateMachine",
        "ToolExecutor",
        "ToolRegistry",
        "__version__",
        "_runtime_ctx",
        # _runner sub-package (Runner refactoring)
        "build_summary_payload",
        "bus",
        "run_external_summarizer",
    ]
except ImportError:
    __all__ = ["__author__", "__version__", "_runtime_ctx"]
