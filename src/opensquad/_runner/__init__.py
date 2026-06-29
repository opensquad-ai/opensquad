# -*- coding: utf-8 -*-
"""
_opensquad_runner — Runner refactoring sub-package.

Structure:
    __init__.py      — Re-exports everything from runner.py for backward compat
    _compression.py  — Summary payload building + external summarizer LLM call
    _state_machine.py — Wake / sleep / idle state transitions
    _output_handler.py — Stream parsing, event emission, token stats
    _input_handler.py — Internal command routing (__STOP__, __NEW_SESSION__, etc.)
    _tool_executor.py — Tool call dispatch, result handling, retry logic
    _hot_reload.py    — Config and plugin hot-reload logic
    _turn_loop.py     — Per-turn execution (assembles all above)
"""

from ._compression import build_summary_payload, run_external_summarizer
from ._state_machine import StateMachine
from ._output_handler import OutputHandler
from ._input_handler import InputHandler
from ._tool_executor import ToolExecutor
from ._hot_reload import HotReloadManager

__all__ = [
    "build_summary_payload",
    "run_external_summarizer",
    "StateMachine",
    "OutputHandler",
    "InputHandler",
    "ToolExecutor",
    "HotReloadManager",
]
