"""Extract AgentRunner._handle_turn_result into _runner/_turn_loop.py::TurnLoop.

Line-based extraction (preserves comments); AST-based `self` -> `self.runner`
transform applied at exact source offsets (replaced back-to-front so earlier
offsets stay valid). runner.py gets a thin delegation wrapper.
"""

from __future__ import annotations

import ast
from pathlib import Path

RUNNER = Path("src/opensquad/runner.py")
OUT = Path("src/opensquad/_runner/_turn_loop.py")

MODULE_HEADER = '''"""
Turn-loop module -- per-turn result handling for AgentRunner.

Extracted from runner.py.  Follows the StateMachine pattern: ``TurnLoop``
holds no persistent state of its own; all state lives on the AgentRunner
instance passed to the constructor.  This makes the turn logic testable with
a minimal fake runner (see tests/test_turn_loop.py).
"""

from __future__ import annotations

import hashlib
import json
import os
import os as _os
import re
import time
from datetime import datetime
from typing import Any

from opensquad import bus
from opensquad.input_hub import input_hub
from opensquad.log_setup import get_tool_call_debug_logger
from opensquad.parser import ResponseParser
from opensquad.sleep_controller import sleep_controller
from opensquad.task_logger import task_logger
from opensquad.task_supervisor import task_supervisor
from opensquad.tool import logger
from opensquad.tools.system import reset_tool_call_context, set_tool_call_context

# Helpers that still live on runner.py; imported lazily at call time so the
# module can be imported independently of runner state.
from opensquad.runner import _get_session_manager, _get_state_manager

from opensquad._runner._tag_utils import compose_user_visible_message

__all__ = ["TurnLoop"]


class TurnLoop:
    """Per-turn execution logic. Instantiate with the owning AgentRunner."""

    def __init__(self, runner: Any) -> None:
        self.runner = runner


'''


def main() -> None:
    src = RUNNER.read_text(encoding="utf-8")
    tree = ast.parse(src)
    cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "AgentRunner")
    fn = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_handle_turn_result")

    lines = src.splitlines()
    start = fn.decorator_list[0].lineno if fn.decorator_list else fn.lineno
    end = fn.end_lineno

    # Collect exact (lineno, col) of every `self` Name in the function (incl. nested).
    positions = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Name) and n.id == "self":
            positions.append((n.lineno, n.col_offset))
    positions.sort(reverse=True)  # back-to-front so earlier offsets stay valid

    method_lines = lines[start - 1 : end]
    method_text = "\n".join(method_lines)
    # Replace back-to-front: each offset is (line_idx_in_file, col).
    # NOTE: ast col_offset counts UTF-8 BYTES when the file contains non-ASCII;
    # convert to character offset per line before slicing.
    for lineno, col in positions:
        local = lineno - start  # map file lineno (1-based) -> index within method_text
        line = method_lines[local]
        line_bytes = line.encode("utf-8")
        # byte col -> char col (walk bytes until we've consumed `col` bytes)
        char_col = len(line_bytes[:col].decode("utf-8", errors="replace"))
        assert line[char_col : char_col + 4] == "self", (lineno, col, line[char_col : char_col + 8])
        line = line[:char_col] + "self.runner" + line[char_col + 4 :]
        method_lines[local] = line
    method_text = "\n".join(method_lines)

    # The method sits at 4-space indent in runner.py; keep the same indent in TurnLoop.
    # Rename to handle_turn_result (drop the private underscore; still prefixed for clarity).
    method_text = method_text.replace("async def _handle_turn_result(", "async def handle_turn_result(", 1)
    out = MODULE_HEADER + method_text + "\n"
    OUT.write_text(out, encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({len(method_text.splitlines())} method lines)")

    # Rewrite runner.py: replace method with delegation wrapper.
    wrapper = (
        "    async def _handle_turn_result(\n"
        "        self,\n"
        "        full_response: str,\n"
        "        tool_data_from_api=None,\n"
        "        output_media=None,\n"
        "        finish_reason: str | None = None,\n"
        "        stream_error: bool = False,\n"
        "    ) -> tuple[bool, str, bool]:\n"
        '        """Turn result handling -- implemented in _runner/_turn_loop.py::TurnLoop."""\n'
        "        from ._runner._turn_loop import TurnLoop\n"
        "\n"
        "        return await TurnLoop(self).handle_turn_result(\n"
        "            full_response,\n"
        "            tool_data_from_api,\n"
        "            output_media,\n"
        "            finish_reason,\n"
        "            stream_error,\n"
        "        )\n"
    )
    new_lines = lines[: start - 1] + wrapper.splitlines() + lines[end:]
    RUNNER.write_text("\n".join(new_lines), encoding="utf-8", newline="\n")
    print(f"runner.py: {len(lines)} lines -> {len(new_lines)} lines")


if __name__ == "__main__":
    main()
