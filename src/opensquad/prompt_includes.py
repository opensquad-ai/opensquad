"""Expand ``{{include:relative/path.md}}`` directives in prompt templates.

Include paths are resolved relative to the prompt root directory (the directory
that contains the entry template, typically ``src/prompts/``). Nested includes
are supported; cycles and path escape are rejected.
"""

from __future__ import annotations

import functools
import logging
import os
import re

logger = logging.getLogger(__name__)


def default_prompts_root() -> str:
    """Return ``src/prompts`` (or the frozen equivalent next to this package)."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.abspath(os.path.join(here, "..", "prompts"))
    if os.path.isdir(candidate):
        return candidate
    alt = os.path.abspath(os.path.join(here, "prompts"))
    return alt if os.path.isdir(alt) else candidate


@functools.lru_cache(maxsize=32)
def load_prompt_part(rel: str) -> str:
    """Load ``src/prompts/{rel}`` (cached). ``rel`` is relative to the prompts root."""
    rel = (rel or "").strip().replace("\\", "/")
    if not rel or rel.startswith("/") or re.match(r"^[A-Za-z]:", rel) or ".." in rel.split("/"):
        raise ValueError(f"Invalid prompt part path: {rel!r}")
    root = default_prompts_root()
    path = os.path.abspath(os.path.join(root, rel))
    try:
        common = os.path.commonpath([root, path])
    except ValueError as exc:
        raise ValueError(f"Prompt part escapes prompts root: {rel!r}") from exc
    if common != os.path.abspath(root):
        raise ValueError(f"Prompt part escapes prompts root: {rel!r}")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Prompt part not found: {rel} ({path})")
    with open(path, encoding="utf-8") as fh:
        return fh.read().strip()


def is_scheduled_task_turn(text: str) -> bool:
    """True when this user turn was fired by the scheduled-task engine."""
    t = (text or "").lstrip()
    return t.startswith("[Scheduled Task") or t.lower().startswith("[scheduled task")


_INCLUDE_RE = re.compile(r"\{\{\s*include:([^}]+?)\s*\}\}(\n?)")


def expand_includes(text: str, prompt_root: str, *, _stack: tuple[str, ...] = ()) -> str:
    """Recursively expand ``{{include:...}}`` directives in *text*.

    Args:
        text: Template text that may contain include directives.
        prompt_root: Absolute path to the prompts directory (include base).
        _stack: Internal cycle-detection stack of absolute included paths.

    Returns:
        Fully expanded text with no remaining include directives.

    Notes:
        If an include directive is followed by a newline and the included file
        already ends with a newline, the template's trailing newline is dropped
        so one-include-per-line entries do not inject blank lines.
    """
    prompt_root = os.path.abspath(prompt_root)

    def _replace(match: re.Match[str]) -> str:
        rel = match.group(1).strip().replace("\\", "/")
        trailing_nl = match.group(2)
        if not rel or rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
            raise ValueError(f"Invalid include path: {rel!r}")
        target = os.path.abspath(os.path.normpath(os.path.join(prompt_root, rel)))
        try:
            common = os.path.commonpath([prompt_root, target])
        except ValueError as exc:
            raise ValueError(f"Include path escapes prompt root: {rel!r}") from exc
        if common != prompt_root:
            raise ValueError(f"Include path escapes prompt root: {rel!r}")
        if target in _stack:
            chain = " -> ".join(_stack + (target,))
            raise ValueError(f"Circular include detected: {chain}")
        if not os.path.isfile(target):
            raise FileNotFoundError(f"Included prompt file not found: {rel} ({target})")
        with open(target, encoding="utf-8") as fh:
            nested = fh.read()
        expanded = expand_includes(nested, prompt_root, _stack=_stack + (target,))
        if trailing_nl and expanded.endswith("\n"):
            return expanded
        return expanded + trailing_nl

    prev = None
    current = text
    for _ in range(32):
        prev = current
        current = _INCLUDE_RE.sub(_replace, current)
        if current == prev:
            return current
    raise RuntimeError("include expansion did not converge")


def read_prompt_with_includes(path: str, prompt_root: str | None = None) -> str:
    """Read a prompt file and expand includes relative to *prompt_root*.

    If *prompt_root* is omitted, the parent directory of *path* is used.
    """
    path = os.path.abspath(path)
    root = os.path.abspath(prompt_root) if prompt_root else os.path.dirname(path)
    with open(path, encoding="utf-8") as fh:
        return expand_includes(fh.read(), root)
