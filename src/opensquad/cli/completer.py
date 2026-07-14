"""prompt_toolkit completer: /sk → /skill, nested /skill li → /skill list."""

from __future__ import annotations

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

from opensquad.cli.slash_commands import COMMANDS, match_commands, resolve_command


class SlashCompleter(Completer):
    """Complete when the line starts with / or +."""

    def get_completions(self, document: Document, complete_event: CompleteEvent):
        text = document.text_before_cursor
        if not text.startswith(("/", "+")):
            return

        prefix_char = text[0]
        body = text[1:]
        parts = body.split()
        # Cursor still on first token: "/sk" or "/skill"
        at_first = " " not in body or (body.endswith(" ") is False and len(parts) <= 1)

        if at_first and not body.endswith(" "):
            token = parts[0] if parts else ""
            for cmd in match_commands(token):
                # Replace from after / or +
                yield Completion(
                    cmd.name,
                    start_position=-len(token),
                    display=f"{prefix_char}{cmd.name}",
                    display_meta=cmd.help,
                )
            return

        # Nested: "/skill li" → list
        if not parts:
            return
        cmd = resolve_command(parts[0])
        if not cmd or not cmd.subcommands:
            return

        # Completing subcommand
        if body.endswith(" ") and len(parts) == 1:
            sub_token = ""
        elif len(parts) >= 2 and not body.endswith(" "):
            sub_token = parts[1]
        else:
            # deeper args — no static completion yet
            if len(parts) > 2 or (len(parts) == 2 and body.endswith(" ")):
                return
            sub_token = ""

        for sub in cmd.subcommands:
            if sub.startswith(sub_token.lower()):
                yield Completion(
                    sub,
                    start_position=-len(sub_token),
                    display=f"{prefix_char}{cmd.name} {sub}",
                    display_meta=cmd.help,
                )


def build_completer() -> SlashCompleter:
    # Touch COMMANDS so registry is loaded
    _ = COMMANDS
    return SlashCompleter()
