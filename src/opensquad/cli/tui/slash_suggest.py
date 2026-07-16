"""Textual slash/plus command suggester + match helpers for TUI autocomplete."""

from __future__ import annotations

from textual.suggester import Suggester

from opensquad.cli.slash_commands import match_commands, resolve_command


def slash_completions(value: str, *, limit: int = 64) -> list[tuple[str, str]]:
    """
    Return [(completion_text, help), ...] for the current input value.
    completion_text includes the leading / or +.
    """
    if not value.startswith(("/", "+")):
        return []
    prefix = value[0]
    body = value[1:]
    parts = body.split()
    at_first = (not body.endswith(" ")) and (len(parts) <= 1)

    out: list[tuple[str, str]] = []
    if at_first:
        token = parts[0] if parts else ""
        for cmd in match_commands(token)[:limit]:
            out.append((f"{prefix}{cmd.name}", cmd.help))
        return out

    if not parts:
        return []
    cmd = resolve_command(parts[0])
    if not cmd or not cmd.subcommands:
        return []

    # /group search <keyword> — once past the subcommand, hide the palette
    # so Enter submits the full line (not just "/group search").
    if body.endswith(" ") and len(parts) == 1:
        sub_token = ""
    elif len(parts) == 2 and not body.endswith(" "):
        sub_token = parts[1]
    else:
        return []

    for sub in cmd.subcommands:
        if sub.startswith(sub_token.lower()) or (sub_token and sub_token.lower() in sub):
            out.append((f"{prefix}{cmd.name} {sub}", cmd.help))
            if len(out) >= limit:
                break
    return out


class SlashSuggester(Suggester):
    """Ghost-text suggestion; Tab accepts (Textual Input default)."""

    def __init__(self) -> None:
        super().__init__(use_cache=False, case_sensitive=True)

    async def get_suggestion(self, value: str) -> str | None:
        matches = slash_completions(value, limit=1)
        if not matches:
            return None
        suggestion = matches[0][0]
        # Only suggest when it extends current value (Tab-complete friendly)
        if suggestion.startswith(value) and suggestion != value:
            return suggestion
        # Fuzzy match that isn't a prefix — still offer full replacement via Tab
        if value.startswith(("/", "+")) and len(value) >= 2:
            return suggestion
        return None
