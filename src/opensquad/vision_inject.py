"""Vision-tool image injection, shared by every event-pipeline drain site.

The vision plugin (`plugins/vision/vision.py`) verifies the paths exist, writes
them to `img_path.txt`, and pushes a `vision_tool` / `inject_images` event.
Whichever code drains that event has to move the paths into the turn's image
list — otherwise the model only ever receives the literal formatted text

    [vision_tool @ 11:03:07] [Image injection requested: ['C:\\shot.png']]

and keeps calling sleep tools waiting for pixels that were dropped on the floor.
Only the parallel loop's per-tool drain used to handle it; the wait loop, the
serial mid-loop drain and the pre-chat dedupe drain all swallowed it.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Iterable

logger = logging.getLogger(__name__)

VISION_SOURCE = "vision_tool"
VISION_ACTION = "inject_images"
PATH_FILE = "img_path.txt"


def collect_paths(events: Iterable[Any]) -> list[str]:
    """Image paths requested by vision events, in arrival order, deduplicated."""
    out: list[str] = []
    for evt in events or []:
        if getattr(evt, "source", None) != VISION_SOURCE:
            continue
        meta = getattr(evt, "metadata", None) or {}
        if meta.get("action") != VISION_ACTION:
            continue
        for p in meta.get("image_paths") or []:
            if isinstance(p, str) and p and p not in out:
                out.append(p)
    return out


def add_paths_to_turn(runner: Any, paths: Iterable[str]) -> list[str]:
    """Append paths this turn has not queued yet; return what was added."""
    current = runner._current_images
    already = set(current or [])
    new_paths = [p for p in (paths or []) if p and p not in already]
    if new_paths:
        if current is None:
            runner._current_images = list(new_paths)
        else:
            current.extend(new_paths)
    return new_paths


def _path_file(runner: Any) -> str:
    agent_dir = getattr(runner, "_agent_dir", "") or ""
    return os.path.join(agent_dir, PATH_FILE) if agent_dir else PATH_FILE


def clear_path_file(runner: Any) -> None:
    """Forget a request that has been consumed in memory.

    The serial loop reads `img_path.txt` at turn start. Leaving the paths there
    after an in-memory injection would attach the same images a second time on
    the next turn, so a consumed request must clear the file.
    """
    try:
        with open(_path_file(runner), "w", encoding="utf-8") as f:
            f.write("")
    except Exception:
        logger.debug("[VISION] failed to clear %s", PATH_FILE, exc_info=True)


def write_path_file(runner: Any, paths: list[str]) -> None:
    """Legacy net: hand the paths to the serial loop's turn-start reader."""
    try:
        with open(_path_file(runner), "w", encoding="utf-8") as f:
            f.write(str(list(paths)))
    except Exception:
        logger.debug("[VISION] failed to write %s", PATH_FILE, exc_info=True)


def apply_vision_injection(runner: Any, events: Iterable[Any]) -> list[str]:
    """Queue image paths carried by drained vision events. Call before formatting.

    Returns the paths newly added to the turn. Non-vision events are ignored, so
    this is cheap to call from every drain site.
    """
    paths = collect_paths(events)
    if not paths:
        return []
    try:
        added = add_paths_to_turn(runner, paths)
    except Exception:
        logger.warning(
            "[VISION] in-memory injection failed; leaving %d path(s) in %s for the next turn",
            len(paths),
            PATH_FILE,
            exc_info=True,
        )
        write_path_file(runner, paths)
        return []
    clear_path_file(runner)
    if added:
        logger.info("[VISION] injected %d image(s) from vision_tool event: %s", len(added), added)
    else:
        logger.info("[VISION] %d image(s) already queued; event consumed without duplicating", len(paths))
    return added
