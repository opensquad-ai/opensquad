"""
Task Checkpoint — Task state persistence utilities.

All writes use atomic rename (write-to-tmp then rename) to prevent
partial/corrupt reads.
"""

import contextlib
import json
import os
import tempfile
import time
from datetime import datetime
from typing import Any


def _checkpoint_path(agent_dir: str) -> str:
    return os.path.join(agent_dir, "data", "task_checkpoint.json")


# ---------------------------------------------------------------------------
# Atomic write helper
# ---------------------------------------------------------------------------


def _atomic_write_json(path: str, data: dict):
    """Write JSON atomically: write to temp file then rename."""
    dir_name = os.path.dirname(path)
    os.makedirs(dir_name, exist_ok=True)
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".tmp", dir=dir_name)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, default=str)
        # On Windows, os.rename fails if target exists; use os.replace instead
        os.replace(tmp_path, path)
    except Exception:
        # Best-effort cleanup
        with contextlib.suppress(Exception):
            os.unlink(tmp_path)
        raise


# ---------------------------------------------------------------------------
# Task Checkpoint
# ---------------------------------------------------------------------------


def write_checkpoint(
    agent_dir: str,
    *,
    active: bool = True,
    task_id: str = "",
    original_request: str = "",
    plan: str = "",
    progress_summary: str = "",
    last_turn_id: int = 0,
    last_tool_call: dict[str, Any] | None = None,
    next_input: str = "",
    context_snapshot: str = "",
    session_id: str = "",
):
    """
    Write a task checkpoint to disk.

    Called at key points:
    - New user input (task starts)
    - Pre-tool execution
    - Post-tool execution
    - Plan update
    - Task complete / idle (active=False)

    Args:
        agent_dir: Agent root directory
        active: True if task is in-flight; False when cleared
        task_id: Unique task identifier
        original_request: The user's original input that started this task
        plan: Current plan text (from <plan> tag)
        progress_summary: What has been accomplished so far
        last_turn_id: The turn number of the last checkpoint
        last_tool_call: {"name": str, "args": str, "status": "running"|"completed"|"failed", "result_preview": str}
        next_input: The next_input string that would be fed to the next turn
        context_snapshot: Brief summary of conversation context
        session_id: Current session ID
    """
    data = {
        "active": active,
        "ts": time.time(),
        "iso": datetime.now().isoformat(),
        "task_id": task_id,
        "original_request": original_request[:2000] if original_request else "",
        "plan": plan[:3000] if plan else "",
        "progress_summary": progress_summary[:2000] if progress_summary else "",
        "last_turn_id": last_turn_id,
        "last_tool_call": last_tool_call,
        "next_input": next_input[:2000] if next_input else "",
        "context_snapshot": context_snapshot[:1000] if context_snapshot else "",
        "session_id": session_id,
    }
    try:
        _atomic_write_json(_checkpoint_path(agent_dir), data)
    except Exception:
        pass  # Checkpoint is best-effort; never crash the agent


def read_checkpoint(agent_dir: str) -> dict[str, Any] | None:
    """
    Read the task checkpoint. Returns None if file doesn't exist or is corrupt.
    Used on agent startup for auto-resume.
    """
    path = _checkpoint_path(agent_dir)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def clear_checkpoint(agent_dir: str):
    """
    Mark the checkpoint as inactive (task completed/idle).
    Preserves the file for diagnostics but sets active=False.
    """
    existing = read_checkpoint(agent_dir)
    if existing and existing.get("active"):
        existing["active"] = False
        existing["ts"] = time.time()
        existing["iso"] = datetime.now().isoformat()
        existing["cleared_reason"] = "task_completed_or_idle"
        with contextlib.suppress(Exception):
            _atomic_write_json(_checkpoint_path(agent_dir), existing)
    elif not existing:
        # No checkpoint file — write an empty inactive one
        with contextlib.suppress(Exception):
            _atomic_write_json(
                _checkpoint_path(agent_dir),
                {
                    "active": False,
                    "ts": time.time(),
                    "iso": datetime.now().isoformat(),
                },
            )


def build_recovery_prompt(checkpoint: dict[str, Any]) -> str:
    """
    Build a recovery prompt from a checkpoint for auto-resume.
    This prompt is injected into input_hub on startup when a crashed task is detected.

    Returns a formatted string that tells the AI to resume its previous task.
    """
    parts = ["[RECOVERY_MODE] Agent restarted after an unexpected shutdown while a task was in progress."]
    parts.append("Please resume the task from where you left off. Verify the environment state before continuing.")
    parts.append("")

    if checkpoint.get("original_request"):
        parts.append("## Original User Request")
        parts.append(checkpoint["original_request"])
        parts.append("")

    if checkpoint.get("plan"):
        parts.append("## Last Known Plan")
        parts.append(checkpoint["plan"])
        parts.append("")

    if checkpoint.get("progress_summary"):
        parts.append("## Progress Before Crash")
        parts.append(checkpoint["progress_summary"])
        parts.append("")

    last_tool = checkpoint.get("last_tool_call")
    if last_tool:
        status = last_tool.get("status", "unknown")
        name = last_tool.get("name", "unknown")
        parts.append("## Last Tool Call")
        parts.append(f"- Tool: {name}")
        parts.append(f"- Status: {status}")
        if last_tool.get("args"):
            parts.append(f"- Args: {last_tool['args'][:500]}")
        if last_tool.get("result_preview"):
            parts.append(f"- Result preview: {last_tool['result_preview'][:500]}")
        parts.append("")

        if status == "running":
            parts.append("WARNING: The last tool call was still running when the agent crashed.")
            parts.append("Its effects may be partial. Verify the result before proceeding.")
            parts.append("")

    parts.append("## Instructions")
    parts.append("1. First verify the current state (check files, running processes, etc.)")
    parts.append("2. Determine what was already completed vs. what needs to be redone")
    parts.append("3. Continue the task to completion")
    parts.append("4. Do NOT start over from scratch unless the environment state is inconsistent")

    return "\n".join(parts)
