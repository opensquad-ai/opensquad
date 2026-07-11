"""Reasoning effort (thinking depth) helpers — Cursor-style low/medium/high.

Maps the UI enum onto provider-specific API parameters:
  - DeepSeek V4: thinking.enabled + reasoning_effort high|max
  - OpenAI o-series / native: reasoning_effort low|medium|high
  - Claude: thinking.budget_tokens
"""

from __future__ import annotations

from typing import Any

VALID_EFFORTS = ("low", "medium", "high")
DEFAULT_EFFORT = "high"

_CLAUDE_BUDGET = {
    "low": 2048,
    "medium": 8000,
    "high": 16000,
}


def normalize_effort(value: str | None) -> str:
    v = (value or DEFAULT_EFFORT).strip().lower()
    return v if v in VALID_EFFORTS else DEFAULT_EFFORT


def effort_to_claude_budget(effort: str | None) -> int:
    return _CLAUDE_BUDGET[normalize_effort(effort)]


def is_deepseek_style(*, model: str = "", base_url: str = "") -> bool:
    hay = f"{model} {base_url}".lower()
    return "deepseek" in hay


def map_openai_compat_effort(
    effort: str | None,
    *,
    model: str = "",
    base_url: str = "",
) -> str:
    """Return the ``reasoning_effort`` value to send on OpenAI-compatible APIs."""
    e = normalize_effort(effort)
    if is_deepseek_style(model=model, base_url=base_url):
        # DeepSeek: low/medium → high; high → max
        return "max" if e == "high" else "high"
    return e


def apply_openai_compat_thinking_params(
    request_params: dict[str, Any],
    *,
    is_think: bool,
    effort: str | None,
    model: str = "",
    base_url: str = "",
) -> None:
    """Mutate ``request_params`` for Chat Completions when thinking is enabled."""
    if not is_think:
        return
    mapped = map_openai_compat_effort(effort, model=model, base_url=base_url)
    request_params["reasoning_effort"] = mapped
    if is_deepseek_style(model=model, base_url=base_url):
        # OpenAI SDK rejects unknown top-level keys; DeepSeek needs extra_body.
        extra = dict(request_params.get("extra_body") or {})
        extra["thinking"] = {"type": "enabled"}
        request_params["extra_body"] = extra
