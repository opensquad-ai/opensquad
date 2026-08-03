"""Extract method groups from cli/tui/app.py into mixin modules.

Usage: python scripts/split_tui_mixins.py <group-name>
Groups: formatting, tool_render, wait_timers, decisions, nav, sessions
"""

from __future__ import annotations

import ast
import itertools
import sys
import textwrap
from pathlib import Path

APP = Path("src/opensquad/cli/tui/app.py")
TUI = Path("src/opensquad/cli/tui")

GROUPS: dict[str, tuple[str, list[str]]] = {
    "formatting": (
        "FormattingMixin",
        [
            "_approx_tokens",
            "_thinking_label_hex",
            "_tool_line_failed",
            "_escape_markup",
            "_fmt_duration",
            "_fmt_tokens_smooth",
            "_short_path",
            "_display_project_path",
            "_fmt_tokens",
            "_theme_hex",
            "_looks_like_agent_prose",
            "_approx_out_tokens",
        ],
    ),
    "tool_render": (
        "ToolRenderMixin",
        [
            "_parse_tool_line",
            "_tool_markup",
            "_tool_markup_parts",
            "_agent_footer_markup",
            "_signal_lamp",
            "_shimmer_markup",
            "_tool_dedupe_key",
            "_claim_tool_line",
            "_pop_open_tool",
            "_write_tool_line",
            "_on_tool_detail",
            "_take_tool_detail",
            "_detail_block_markup",
            "_rewrite_detail_blocks",
            "_shift_open_tool_starts",
            "_chat_replace_open",
        ],
    ),
    "wait_timers": (
        "WaitTimersMixin",
        [
            "_schedule_ui",
            "begin_wait",
            "update_wait",
            "end_wait",
            "_sanitize_wait_label",
            "_tick_wait",
            "_paint_wait",
            "_shimmer_active",
            "_ensure_shimmer_timer",
            "_stop_shimmer_timer",
            "_repaint_open_tools_shimmer",
            "_tick_shimmer",
            "_schedule_meter_paint",
            "_ensure_turn_meter_timer",
            "_stop_turn_meter_timer",
            "_tick_turn_meter",
            "_advance_out_display",
            "_turn_meter_plain",
            "_begin_turn_meter",
            "_end_turn_meter",
        ],
    ),
    "decisions": (
        "DecisionsMixin",
        [
            "_on_bridge_decision",
            "_enqueue_decision",
            "_open_decision",
            "_paint_decision",
            "_hide_decision",
            "_clear_decision_by_id",
            "_decision_confirm",
            "_decision_toggle_multi",
            "action_decision_space",
            "_decision_dismiss",
            "_submit_custom_decision",
            "_resolve_decision_choose",
            "_resolve_group_choose_work",
            "_resolve_decision_ignore",
            "_group_choose_action_work",
            "_resolve_mode_switch",
            "_resolve_group_approval",
            "_resolve_group_approval_work",
            "_open_group_pending_decision",
            "_command_palette_open",
        ],
    ),
    "nav": (
        "NavMixin",
        [
            "open_nav",
            "_open_theme_nav",
            "_open_language_nav",
            "apply_theme",
            "apply_locale",
            "action_cycle_effort",
            "action_toggle_detail",
            "action_toggle_live",
            "_on_side_chunk",
            "_on_side_done",
            "_open_live_side",
            "_close_live_side",
            "_paint_live_side",
            "_nav_connect_providers",
            "_nav_provider_ask_key",
            "_finish_provider_with_key",
            "_reload_model_nav",
            "_current_model_name_hint",
            "_save_provider_card",
            "_nav_provider_use_model",
            "_nav_provider_show",
            "_nav_provider_edit_field",
            "_nav_card_edit_field",
            "_finish_model_field_edit",
            "_apply_provider_model_field",
            "_apply_card_field",
            "_nav_provider_toggle_field",
            "_nav_card_toggle_field",
            "_load_nav_kind",
            "_push_nav",
            "_nav_current_items",
            "_paint_nav",
            "_hide_nav",
            "_nav_back_or_close",
            "_nav_confirm",
            "_run_nav_action",
            "_nav_model_use",
            "_nav_model_assign_pick",
            "_nav_model_assign_to",
            "_nav_show_json",
            "_nav_skill_compose",
            "_nav_skill_show",
            "_nav_delete",
            "_nav_role_assign",
            "_nav_role_show",
            "_nav_text",
            "_nav_collab_board",
            "_nav_mcp_toggle",
            "_nav_mcp_show",
            "_nav_plugin_toggle",
        ],
    ),
    "sessions": (
        "SessionsMixin",
        [
            "_session_cmd",
            "_agent_session_key",
            "_fetch_and_show_sessions",
            "_show_sessions_as_nav",
            "_show_session_picker",
            "_paint_session_picker",
            "_hide_session_picker",
            "_confirm_session_pick",
            "_switch_session_ref",
            "_resolve_and_switch",
            "_switch_session",
            "_do_switch_session",
            "_render_switched_session",
        ],
    ),
}

HEADERS: dict[str, str] = {
    "formatting": '''"""Formatting helpers for the OpenSquad TUI (extracted from app.py)."""

from __future__ import annotations

from typing import Any


''',
    "tool_render": '''"""Tool-line rendering for the OpenSquad TUI (extracted from app.py)."""

from __future__ import annotations

import re
from typing import Any

from opensquad.cli.tui._formatting import same_reply, truncated_prefix


''',
    "wait_timers": '''"""Wait status, shimmer and turn-meter timers (extracted from app.py)."""

from __future__ import annotations

import time
from typing import Any


''',
    "decisions": '''"""Bridge decision / approval card flow (extracted from app.py)."""

from __future__ import annotations

from typing import Any

from textual import work
from textual.widgets import Static

from opensquad.cli.tui._formatting import FormattingMixin
from opensquad.cli.tui.decision_picker import (
    PendingDecision,
    from_group_approval,
    from_mode_switch,
    from_propose_options,
    render_decision_markup,
)


''',
    "nav": '''"""Navigation menu / provider / model / skill flows (extracted from app.py)."""

from __future__ import annotations

from typing import Any

from textual import work
from textual.widgets import Static

from opensquad.cli.tui._formatting import FormattingMixin
from opensquad.cli.tui.i18n import get_locale, load_saved_locale, normalize_locale, set_locale, t
from opensquad.cli.tui.themes import (
    DEFAULT_THEME,
    list_theme_names,
    load_saved_theme,
    register_opensquad_themes,
    save_theme,
)


''',
    "sessions": '''"""Session list / switch flow (extracted from app.py)."""

from __future__ import annotations

from typing import Any

from textual import work

from opensquad.cli.tui._formatting import FormattingMixin


''',
}


def main(group: str) -> None:
    mixin_name, method_names = GROUPS[group]
    src = APP.read_text(encoding="utf-8")
    tree = ast.parse(src)
    cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "_OpenSquadApp")
    by_name = {}
    for stmt in cls.body:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            by_name[stmt.name] = stmt
    missing = [m for m in method_names if m not in by_name]
    if missing:
        print("MISSING methods:", missing)
        sys.exit(1)

    lines = src.splitlines()
    segments = []
    for name in method_names:
        node = by_name[name]
        start = node.decorator_list[0].lineno if node.decorator_list else node.lineno
        end = node.end_lineno
        segments.append((start, end, name))
    segments.sort()

    # Ensure no overlap and contiguous dedent is possible
    for (s1, e1, n1), (s2, e2, n2) in itertools.pairwise(segments):
        assert s2 > e1 + 1, f"overlap between {n1} and {n2}"

    # extract source, strip common 12-space indent (class body inside function)
    parts = []
    for s, e, name in segments:
        block = "\n".join(lines[s - 1 : e])
        parts.append(block)
    joined = "\n\n".join(parts)
    dedented = textwrap.dedent(joined)
    # re-indent for a module-level class body (4 spaces)
    reindented = "\n".join(("    " + ln) if ln.strip() else ln for ln in dedented.splitlines())

    header = HEADERS[group]
    out = f'{header}class {mixin_name}:\n    """Mixin methods moved from cli/tui/app.py (see app.py for the app class)."""\n\n{reindented}\n'
    out_path = TUI / f"_{group}.py"
    out_path.write_text(out, encoding="utf-8", newline="\n")

    # rewrite app.py: remove moved method nodes
    remove_spans = [(s, e) for s, e, _ in segments]
    keep = []
    prev_end = 0
    for s, e in remove_spans:
        keep.append((prev_end, s - 1))
        prev_end = e
    keep.append((prev_end, len(lines)))
    new_lines = []
    for a, b in keep:
        new_lines.extend(lines[a:b])
    new_src = "\n".join(new_lines)
    APP.write_text(new_src, encoding="utf-8", newline="\n")
    print(f"group={group} mixin={mixin_name} methods={len(method_names)} -> {out_path}")
    print(f"app.py: {len(lines)} lines -> {len(new_lines)} lines")


if __name__ == "__main__":
    main(sys.argv[1])
