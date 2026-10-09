"""Mod-contributed slash commands (M1 P7).

A mod's command has to look like any other command to the CLI *and* be refused
locally so the text can fall through to the agent, which is the only place that
can run it. These lock both halves.
"""

from __future__ import annotations

import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))

from opensquad.cli import slash_commands  # noqa: E402
from opensquad.cli.slash_dispatch import dispatch_slash  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_registry():
    slash_commands.clear_runtime_commands()
    yield
    slash_commands.clear_runtime_commands()


def test_a_mod_command_joins_lookup_help_and_completion():
    builtin_count = len(slash_commands.all_commands())
    slash_commands.register_runtime_command(
        "replay", help="step through the last turn's edits", source="replay-theater"
    )

    assert len(slash_commands.all_commands()) == builtin_count + 1
    assert slash_commands.resolve_command("/replay") is not None
    assert "replay" in slash_commands.all_names()
    assert slash_commands.command_source("/replay") == "replay-theater"
    assert slash_commands.command_source("/help") == "builtin"
    # It shows up where a user looks for it.
    assert "replay" in slash_commands.format_help()
    assert any("replay" in line for line in slash_commands.suggest_lines("rep"))


def test_a_mod_may_not_shadow_a_builtin():
    slash_commands.register_runtime_command("help", help="mine now", source="evil")
    assert slash_commands.resolve_command("/help").help != "mine now"
    assert slash_commands.command_source("/help") == "builtin"
    # ...and the registered entry is not listed either.
    assert [c.name for c in slash_commands.all_commands()].count("help") == 1


def test_withdrawing_a_mod_withdraws_only_its_commands():
    slash_commands.register_runtime_command("a", source="mod-a")
    slash_commands.register_runtime_command("b", source="mod-b")
    slash_commands.clear_runtime_commands("mod-a")
    assert slash_commands.resolve_command("/a") is None
    assert slash_commands.resolve_command("/b") is not None


def test_a_bad_command_name_is_refused():
    assert slash_commands.register_runtime_command("", source="m") is None
    assert slash_commands.register_runtime_command("two words", source="m") is None
    assert slash_commands.register_runtime_command("///", source="m") is None


def test_dispatch_lets_a_mod_command_fall_through_to_the_agent():
    """Returning False is the contract: the CLI sends the text on as a message,
    where the agent's interception runs it. Handling it locally would print
    "Unknown command" and the mod would never see it."""
    slash_commands.register_runtime_command("demo", help="fixture", source="deny-demo")
    assert dispatch_slash("/demo", {}) is False


def test_dispatch_still_handles_builtins_and_reports_unknowns(capsys):
    assert dispatch_slash("/help", {}) is True
    assert dispatch_slash("/definitely-not-a-command", {}) is True
    captured = capsys.readouterr()
    assert "Unknown command" in captured.out
