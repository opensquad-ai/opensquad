"""Ask the agent to finish what it was doing when it (or the machine) went away.

The two halves already existed but were never joined: ``task_checkpoint.write_checkpoint()``
had **no caller anywhere**, and the one line that meant to clear a checkpoint imported
``opensquad.checkpoint`` — a module that does not exist — inside ``try/except: pass``, so it
no-oped silently. Nothing was ever recorded, so nothing could ever be resumed, while the
docstrings in ``task_checkpoint`` described the resume as if it happened.

What is added here is the boot end of the chain:

* the runner records what it is doing (turn start, every tool result) and clears the record
  once no session is running any more — see ``runner._record_task_checkpoint`` and the empty
  ``_emit_busy_sessions`` call;
* on boot this module turns that record into the ``[RECOVERY_MODE]`` prompt
  ``task_checkpoint.build_recovery_prompt`` already knew how to write, and pushes it into
  the agent's own input hub — the "the system tells the agent to continue" step, delivered
  as a normal message so the user can see it happened.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable

from opensquad.task_checkpoint import build_recovery_prompt, clear_checkpoint, read_checkpoint

logger = logging.getLogger(__name__)

#: A checkpoint older than this is not "the task you were doing" any more.
MAX_RECOVERY_AGE_S = 7 * 24 * 3600

#: The source tag the recovery message carries (visible in logs / session provenance).
RECOVERY_SOURCE = "task_recovery"


def recovery_disabled() -> bool:
    """``OPENSQUAD_NO_TASK_RECOVERY=1`` opts out — used by test suites and clean starts."""
    return str(os.environ.get("OPENSQUAD_NO_TASK_RECOVERY") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def pending_checkpoint(agent_dir: str, *, now: float | None = None) -> dict[str, Any] | None:
    """The checkpoint worth resuming: active, and not so old that it is only noise."""
    if not agent_dir:
        return None
    checkpoint = read_checkpoint(agent_dir)
    if not checkpoint or not checkpoint.get("active"):
        return None
    age = float(now if now is not None else time.time()) - float(checkpoint.get("ts") or 0)
    if age > MAX_RECOVERY_AGE_S:
        logger.info("[Recovery] checkpoint is %.1f days old — leaving it alone", age / 86400.0)
        return None
    return checkpoint


def recovery_message(checkpoint: dict[str, Any]) -> str:
    """The prompt injected at boot (kept separate so tests can assert on it)."""
    return build_recovery_prompt(checkpoint)


def recover_pending_task(agent_dir: str, *, push: Callable[..., Any], now: float | None = None) -> bool:
    """Ask the agent to carry on with an unfinished task. True when a recovery went out.

    Idempotent by construction: the checkpoint is deactivated *before* the push, so a
    restart loop (or a second boot path) cannot inject the same recovery twice.
    """
    if recovery_disabled():
        return False
    checkpoint = pending_checkpoint(agent_dir, now=now)
    if not checkpoint:
        return False
    clear_checkpoint(agent_dir)
    message = recovery_message(checkpoint)
    try:
        push(message, source=RECOVERY_SOURCE, session_id=str(checkpoint.get("session_id") or ""))
    except Exception as exc:  # noqa: BLE001 - a boot convenience must never break the boot
        logger.error("[Recovery] could not hand the recovery prompt to the agent: %s", exc)
        return False
    logger.info(
        "[Recovery] recovery prompt injected (session=%s, last tool=%s, checkpoint age=%.0fs)",
        checkpoint.get("session_id") or "-",
        (checkpoint.get("last_tool_call") or {}).get("name") or "-",
        time.time() - float(checkpoint.get("ts") or 0),
    )
    return True
