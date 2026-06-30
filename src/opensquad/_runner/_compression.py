"""
Compression module -- summary payload building + external summarizer LLM call.

Extracted from runner.py to reduce its size.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

from opensquad.system_config import syscfg
from opensquad.tool import logger

__all__ = ["build_summary_payload", "run_external_summarizer"]


def build_summary_payload(
    previous_summary: str,
    messages: list[dict[str, Any]],
    events: list[dict[str, Any]],
    keep_last: int | None = None,
) -> str:
    """Build the text payload sent to the summarizer LLM."""
    lines: list[str] = []

    if previous_summary and previous_summary.strip():
        lines.append("[Previous Context Summary]")
        lines.append(previous_summary.strip())
    else:
        lines.append("[Previous Context Summary]\n(none)")

    lines.append("\n[Conversation Messages to Compress]")
    msgs_to_compress = messages[:-keep_last] if keep_last is not None and len(messages) > keep_last else messages

    for msg in msgs_to_compress:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if not content:
            continue
        short = str(content).strip()
        if len(short) > 800:
            short = short[:800] + "..."
        lines.append(f"- {role}: {short}")

    lines.append("\n[Workflow Events to Compress]")
    for evt in events:
        etype = evt.get("type", "")
        data = evt.get("data", evt.get("content", ""))
        text = json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else str(data or "")
        text = text.strip()
        if not text:
            continue
        if len(text) > 800:
            text = text[:800] + "..."
        lines.append(f"- {etype}: {text}")

    lines.append("\n[Compression Rules]")
    lines.append(
        "Use the exact summary template with these sections: "
        "Current Task, Original Goal, Completed, Current State, "
        "Key Parameters, Unresolved Issues."
    )
    lines.append(
        "Current Task MUST describe what the agent is working on RIGHT NOW -- the most recent user request in detail."
    )
    lines.append("Original Goal is the very first user request in this session, in one sentence.")
    lines.append(
        "Current State is the most important section -- include open files, current directory, last tool executed."
    )
    lines.append("Preserve all file paths, IDs, ports, version numbers, config values, and error messages verbatim.")
    lines.append(
        "You MUST consider full workflow context: thought, plan, tool_call, tool_result, and info/status events."
    )
    lines.append("Completed must include Done/In progress/Todo sub-bullets with specific file paths.")
    if keep_last is not None:
        lines.append(
            f"Keep only the last {keep_last} messages in live chat history; "
            "everything above must be summarized into CONTEXT_SUMMARY."
        )
    else:
        lines.append(
            "Compress ALL messages and events into CONTEXT_SUMMARY. "
            "Keep only the newest 10% of content as live context."
        )
    return "\n".join(lines)


async def run_external_summarizer(
    summary_payload: str,
    base_url: str,
    api_key: str,
    model: str,
    on_chunk: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    """Call an external LLM to generate a context summary."""
    model = syscfg.get("summarizer", "model") or model or "gpt-4o-mini"

    system_prompt = (
        "You are a summarizer agent. Return ONLY the summary in the "
        "specified template. Do not add commentary or extra sections."
    )

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=60)
        logger.info(
            "[Compression] Calling external summarizer: model=%s, payload_len=%d",
            model,
            len(summary_payload),
        )

        if on_chunk is None:
            response = await client.chat.completions.create(
                model=model,
                temperature=0.2,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": summary_payload},
                ],
            )
            content = response.choices[0].message.content or ""
            return content.strip()

        stream = await client.chat.completions.create(
            model=model,
            temperature=0.2,
            stream=True,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": summary_payload},
            ],
        )

        parts: list[str] = []
        async for chunk in stream:
            try:
                delta = chunk.choices[0].delta.content or ""
            except Exception:
                delta = ""
            if not delta:
                continue
            parts.append(delta)
            with contextlib.suppress(Exception):
                await on_chunk(delta)

        result = "".join(parts).strip()
        if not result:
            logger.warning(
                "[Compression] External summarizer returned empty result (model=%s, payload_len=%d)",
                model,
                len(summary_payload),
            )
        return result

    except Exception as e:
        logger.error(f"[Compression] External summarizer failed: {e}")
        return ""
