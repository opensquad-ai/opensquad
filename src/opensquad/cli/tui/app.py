"""
OpenSquad TUI — full-screen terminal UI (Textual).

Layout (Claude Code / OpenCode style):
  ┌─ chat log (scroll) ──────────────────────┤
  │  welcome card · messages / tool / cards  │
  ├─ prompt frame ───────────────────────────┤
  │  ❯ input…                                │
  └─ status bar ─────────────────────────────┘
"""

from __future__ import annotations

import os
import re
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any

from opensquad import __version__
from opensquad.cli.api_client import GatewayClient, pick_default_agent, remember_agent
from opensquad.cli.media import (
    PendingMedia,
    attach_from_clipboard,
    attach_from_path,
    chip_label,
    format_pending_chips,
    upload_for_agent,
    upload_for_group,
)
from opensquad.cli.slash_dispatch import dispatch_slash
from opensquad.cli.tui._formatting import FormattingMixin, same_reply, truncated_prefix
from opensquad.cli.tui.decision_picker import (
    PendingDecision,
)
from opensquad.cli.tui.side_stream import SideStreamHub

# Backward-compat names (tests import these from app.py).
_same_reply = same_reply
_is_truncated_prefix = truncated_prefix


def _quiet_tui_loggers() -> None:
    """Keep httpx / httpcore from painting HTTP URLs into the Textual screen.

    httpx logs ``HTTP Request: GET http://127.0.0.1:9555/api/...`` at INFO; when
    written to the same tty as the TUI they flicker under the prompt dock.
    """
    import logging

    for name in (
        "httpx",
        "httpcore",
        "httpcore.connection",
        "httpcore.http11",
        "httpcore.http2",
        "urllib3",
        "asyncio",
        "websockets",
        "websocket",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)


def run_tui(*, gateway: str | None = None, agent: str | None = None, no_start: bool = False) -> None:
    """Entry: launch Textual TUI (agent should already be ready from ``run_code``)."""
    try:
        import importlib.util

        if importlib.util.find_spec("textual") is None:
            raise ImportError("textual not installed")
    except ImportError as e:
        raise SystemExit(f"[tui] textual is required. Install with:\n  pip install 'textual>=8.2.8'\n  ({e})") from e

    _quiet_tui_loggers()

    from opensquad.cli.tui.win_ime_patch import apply_win_ime_patch

    apply_win_ime_patch()

    from opensquad.cli.api_client import GatewayClient

    client = GatewayClient(gateway_url=gateway)
    # Without --agent, leave unset until preflight resolves auto_start_on_boot.
    app = OpenSquadApp(client=client, agent=agent, no_start=no_start)
    app.run()


def _pick_default_agent(client: GatewayClient) -> str | None:
    return pick_default_agent(client)


# ── App ───────────────────────────────────────────────────────────────────


class OpenSquadApp:
    """Factory that subclasses Textual App lazily (keeps import optional)."""

    def __new__(cls, client: GatewayClient, agent: str | None, no_start: bool = False):
        return _build_app_class()(client=client, agent=agent, no_start=no_start)


def _build_app_class():
    from textual import on, work
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Vertical
    from textual.widgets import Footer, Input, Static

    from opensquad.cli.group_bridge import GroupBridge

    # TUI mixins (moved out of app.py to keep this class readable). These
    # import textual transitively, so they load lazily like the rest here.
    from opensquad.cli.tui._decisions import DecisionsMixin
    from opensquad.cli.tui._nav import NavMixin
    from opensquad.cli.tui._sessions import SessionsMixin
    from opensquad.cli.tui._tool_render import ToolRenderMixin
    from opensquad.cli.tui._wait_timers import WaitTimersMixin
    from opensquad.cli.tui.i18n import (
        get_locale,
        load_saved_locale,
        set_locale,
        t,
    )
    from opensquad.cli.tui.selectable_rich_log import SelectableRichLog as RichLog
    from opensquad.cli.tui.slash_suggest import SlashSuggester, slash_completions
    from opensquad.cli.tui.themes import (
        DEFAULT_THEME,
        load_saved_theme,
        register_opensquad_themes,
    )

    class _OpenSquadApp(
        FormattingMixin,
        ToolRenderMixin,
        WaitTimersMixin,
        DecisionsMixin,
        NavMixin,
        SessionsMixin,
        App[None],
    ):
        CSS_PATH = Path(__file__).with_name("styles.tcss")
        TITLE = f"OpenSquad CLI v{__version__}"
        AUTO_FOCUS = "#chat-input"
        ALLOW_SELECT = True
        # Drag-select must not edge-auto-scroll — that fights the highlight and feels like "画面抖动"
        ENABLE_SELECT_AUTO_SCROLL = False
        BINDINGS = [
            Binding("ctrl+c", "cancel_or_clear", "^C×2 Exit", show=False),
            Binding("ctrl+q", "quit", "Quit", show=False),
            Binding("ctrl+l", "clear_log", "Clear", show=False),
            Binding("ctrl+shift+v", "paste_image", "Paste image", show=False),
            Binding("ctrl+e", "cycle_effort", "Effort", show=False, priority=True),
            Binding("ctrl+x", "toggle_live", "Live", show=False, priority=True),
            Binding("ctrl+o", "toggle_detail", "Detail", show=False, priority=True),
            # priority so Tab does NOT move focus away from the input (that made typing die)
            # Idle Tab = Plan/Build; with slash/nav menu open = confirm selection
            Binding("tab", "accept_slash", "Plan/Build", show=False, priority=True),
            Binding("escape", "hide_slash", "Hide menu", show=False),
            Binding("up", "slash_up", "Prev", show=False, priority=True),
            Binding("down", "slash_down", "Next", show=False, priority=True),
            # Multi-select toggle on decision cards (SkipAction when idle → Input gets space)
            Binding("space", "decision_space", show=False, priority=True),
        ]

        def __init__(self, client: GatewayClient, agent: str | None, no_start: bool = False):
            super().__init__()
            self._no_start = bool(no_start)
            self._needs_new_session = False
            self._preflight_done = False
            register_opensquad_themes(self)
            # Apply before first paint when possible
            saved = load_saved_theme()
            if saved in (self.available_themes or {}):
                self.theme = saved
            elif DEFAULT_THEME in (self.available_themes or {}):
                self.theme = DEFAULT_THEME
            self._locale = set_locale(load_saved_locale(), persist=False)
            self.client = client
            self.agent = agent
            self.mode = "solo"
            self.bridge: Any = None
            self.group: GroupBridge | None = None
            self.pending_media: list[PendingMedia] = []
            # Skill chip for next send (Web pendingSkill) — injected as <user_send_skill>
            self.pending_skill: dict[str, str] | None = None
            self.muted = False
            self._agent_paused = False
            self._stream_buf = ""
            self._sending = False
            # Wait animation state
            self._wait_label: str | None = None
            self._wait_gen: int = 0  # invalidate begin_wait vs end_wait races
            self._wait_tick: int = 0
            self._wait_timer = None
            self._SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
            self._slash_items: list[str] = []
            self._slash_helps: list[str] = []
            self._slash_index: int = 0
            self._think_buf_latest: str = ""
            self._think_pending: bool = False
            self._reply_flushed: bool = False
            # Live agent reply streaming (chat-log in-place rewrite)
            self._live_reply: dict[str, Any] | None = None
            self._live_reply_painted: str = ""
            self._live_reply_dirty: bool = False
            self._reply_paint_at: float = 0.0
            # Soft gray sweep across in-progress titles (Thinking / tools / wait)
            self._shimmer_tick: int = 0
            self._shimmer_timer = None
            # Windows IME: Enter may fire before CJK text is committed into Input
            self._submit_gen: int = 0
            self._ctrl_c_at: float = 0.0
            # /session picker (legacy path — also routed via generic nav)
            self._session_pick_active: bool = False
            self._session_pick_items: list[dict[str, Any]] = []
            self._session_pick_index: int = 0
            self._session_current_id: str | None = None
            # Generic nested nav (/model /skill /role /…)
            self._nav_active: bool = False
            self._nav_stack: list[tuple[str, list[Any]]] = []
            self._nav_index: int = 0
            # Plan / Build (OpenCode-style; Tab toggles when no menu)
            self._agent_mode: str = "build"
            # OpenCode-style footer status fields
            self._model_card: str = ""
            self._model_label: str = "—"
            self._model_name: str = ""
            self._model_provider_label: str = ""
            self._reasoning_effort: str = "high"
            self._token_used: int = 0
            self._token_max: int = 0
            # Session / turn token meter (Claude Code–style ↑↓ + elapsed)
            # ↑ = current context window (one upload), NOT cumulative session input
            # ↓ = this-turn output, visually advanced one/few tokens per frame
            self._session_out_tokens: int = 0
            self._turn_started_at: float | None = None
            self._turn_baseline_out: int = 0
            self._turn_out_target: int = 0
            self._turn_out_display: int = 0
            self._last_turn_out: int = 0
            self._last_turn_elapsed: float | None = None
            self._turn_meter_timer = None
            self._meter_paint_at: float = 0.0
            self._meter_last_clock: str = ""
            # Freeze cwd at TUI launch (cmd address) — drives session project path
            self._launch_cwd: str = self._resolve_project_path()
            self._project_path: str = self._launch_cwd
            self._cwd_synced_agent: str | None = None
            # Debounce duplicate compress-clear (summary + history_sync)
            self._compress_clear_at: float = 0.0
            # Message FIFO (solo)
            self._send_queue: deque[tuple[str, list, dict[str, str] | None]] = deque()
            # After Ctrl+C stop: keep queue, do not auto-drain; next user Enter
            # merges held queue + new input into one agent turn.
            self._hold_queue_after_stop: bool = False
            # Live thinking paint throttle
            self._think_paint_at: float = 0.0
            # Side stream (Ctrl+X)
            self._side_hub = SideStreamHub()
            self._live_side_open: bool = False
            self._live_side_key: str | None = None
            self._side_paint_at: float = 0.0
            # API key capture for Connect provider
            self._await_api_key: dict[str, Any] | None = None
            # Model field edit capture (temperature / token_max / …)
            self._await_model_field: dict[str, Any] | None = None
            # /login capture: {"step": "email"|"password", "email": str|None}
            self._await_login: dict[str, Any] | None = None
            self._follow_chat: bool = True
            # Bash-like input history (↑/↓ when no menu open)
            self._input_history: list[str] = []
            self._input_hist_index: int | None = None  # None = editing live draft
            self._input_hist_draft: str = ""
            self._load_input_history()
            # OpenCode-style decision picker (propose_options / mode_switch / group cards)
            self._decision: PendingDecision | None = None
            self._await_custom_answer: bool = False
            self._decision_queue: list[PendingDecision] = []
            # Group @mention autocomplete
            self._mention_items: list[dict[str, str]] = []
            self._mention_index: int = 0
            self._mention_active: bool = False
            self._group_members: list[dict[str, str]] = []
            self._group_oldest_id: str | None = None
            # Dedup first-turn agent reply (stream flush vs final on_line race)
            self._last_agent_reply: str = ""
            # Strip positions of the last committed green agent block — used to
            # in-place upgrade when a longer final arrives after a truncated flush
            # (common when Web + TUI share the same agent and WS fan-out delays
            # the complete message behind an early stream flush).
            self._committed_agent_meta: dict[str, Any] | None = None
            # This turn's user text — drop WS echoes painted as agent output
            self._turn_user_text: str = ""
            # Production defaults: hide boot chatter; fold long thinking/tools
            self._debug_mode: bool = False
            self._detail_expanded: bool = False
            self._welcome_posted: bool = False
            self._DETAIL_TOKEN_LIMIT: int = 100
            self._last_tool_name: str = ""
            self._paint_cache: dict[str, str] = {}
            # Open yellow tool rows keyed by call_id (or name:seq) until result turns green
            self._open_tools: dict[str, dict[str, Any]] = {}
            self._open_tool_seq: int = 0
            self._open_tool_keys: set[str] = set()
            # Sync claim before schedule_ui — blocks duplicate ✓ for same tool
            self._done_tool_keys: set[str] = set()
            self._last_tool_result_key: str = ""
            # Ctrl+O: full tool/thinking bodies + RichLog strip handles for rewrite
            self._tool_detail_pending: dict[str, str] = {}
            self._tool_args_by_key: dict[str, str] = {}
            self._detail_blocks: list[dict[str, Any]] = []
            self._last_flushed_think: str = ""
            self._think_gen: int = 0
            self._placeholder_cache: str = ""
            self._stream_scroll_at: float = 0.0

        def compose(self) -> ComposeResult:
            yield Static(id="header-bar")
            yield RichLog(
                id="chat-log",
                highlight=False,
                markup=True,
                wrap=True,
                auto_scroll=False,
            )
            yield RichLog(
                id="live-side",
                highlight=True,
                markup=True,
                wrap=True,
                auto_scroll=False,
            )
            # Fixed-height live Thinking panel (stream tokens here; finalize into chat-log)
            yield Static(id="live-think")
            with Vertical(id="prompt-dock"):
                yield Static(id="slash-menu")
                yield Static(id="wait-banner")
                # Blue box wraps Input only; status (Build · tokens) sits below
                with Vertical(id="prompt-frame"):
                    yield Input(
                        placeholder=t("placeholder_ask"),
                        id="chat-input",
                        type="text",
                        suggester=SlashSuggester(),
                    )
                yield Static(id="prompt-meta")
                yield Static(id="footer-path")
                yield Static(id="status-bar")
            yield Footer()

        def on_mount(self) -> None:
            # Prevent non-input widgets from stealing keyboard / breaking IME
            for rid in ("#chat-log", "#live-side"):
                try:
                    self.query_one(rid, RichLog).can_focus = False
                except Exception:
                    pass
            for wid in (
                "#header-bar",
                "#slash-menu",
                "#wait-banner",
                "#status-bar",
                "#prompt-meta",
                "#footer-path",
                "#live-think",
            ):
                try:
                    self.query_one(wid).can_focus = False
                except Exception:
                    pass
            try:
                self.query_one(Footer).can_focus = False
            except Exception:
                pass
            self._sync_status_from_agent(self.agent)
            self._refresh_chrome()
            self._hide_slash_menu()
            self._focus_input()
            # Agent is usually already ready (run_code waited). Open WS + new_session.
            # Welcome card is posted after connect so it shows ready (not a stale offline).
            self._preflight_done = True
            if self.client.token and self.agent:
                self.begin_wait(t("wait_boot_ws", name=self.agent))
                self._bootstrap_agent(self.agent, then_new=True)
            else:
                self._post_welcome_card()
                if not self.client.token:
                    self.log_line(t("not_logged_in"), style="system")
                elif not self.agent:
                    self.log_line(t("no_autostart_agent"), style="system")
            self._maybe_hint_windows_terminal()

        def _maybe_hint_windows_terminal(self) -> None:
            """One-shot tip: classic conhost ghosts bordered docks under animation."""
            if sys.platform != "win32":
                return
            if getattr(self, "_wt_hint_done", False):
                return
            self._wt_hint_done = True
            # Windows Terminal / VS Code terminal set these; bare cmd.exe usually does not
            if os.environ.get("WT_SESSION") or os.environ.get("TERM_PROGRAM") == "vscode":
                return
            self.log_line(
                "Tip: Windows Terminal gives a stabler prompt border than classic CMD.",
                style="system",
            )

        def _ensure_new_session(self) -> bool:
            """Send new_session once before first message (deferred from boot)."""
            if not getattr(self, "_needs_new_session", False):
                return True
            if not self.bridge:
                return False
            try:
                self._last_agent_reply = ""
                self._committed_agent_meta = None
                self._session_current_id = None
                self._send_queue.clear()
                self._hold_queue_after_stop = False
                self._stream_buf = ""
                self._reply_flushed = False
                self._session_out_tokens = 0
                self._turn_started_at = None
                self._turn_out_target = 0
                self._turn_out_display = 0
                self._last_turn_out = 0
                self._last_turn_elapsed = None
                self.bridge.send_command("new_session")
                self._needs_new_session = False
                self._log_verbose("New session started")
                return True
            except Exception as e:
                self.log_line(str(e), style="error")
                return False

        def _focus_input(self) -> None:
            try:
                if self._is_selecting():
                    return
                inp = self.query_one("#chat-input", Input)
                if self.focused is not inp:
                    self.set_focus(inp)
            except Exception:
                pass

        def _is_selecting(self) -> bool:
            """True while the user is drag-selecting text (avoid UI churn / scroll fights)."""
            try:
                return bool(getattr(self.screen, "_selecting", False))
            except Exception:
                return False

        def _chat_write(self, content: Any, *, follow: bool | None = None, shrink: bool = True) -> None:
            """Append to chat log; sticky-bottom while agent streams unless user scrolled up."""
            self._chat_write_counted(content, follow=follow, shrink=shrink)

        def _chat_write_counted(self, content: Any, *, follow: bool | None = None, shrink: bool = True) -> int:
            """Append to chat log; return how many RichLog strips were added."""
            log = self.query_one("#chat-log", RichLog)
            before = len(log.lines)
            if self._is_selecting():
                scroll_end = False
            elif follow is True:
                scroll_end = True
                self._follow_chat = True
            elif follow is False:
                scroll_end = False
            else:
                try:
                    at_end = bool(log.is_vertical_scroll_end)
                except Exception:
                    at_end = True
                # Pause sticky scroll when user has scrolled up; resume at bottom
                self._follow_chat = at_end
                scroll_end = at_end
            log.write(content, scroll_end=scroll_end, animate=False, shrink=shrink)
            if scroll_end:
                try:
                    log.scroll_end(animate=False)
                except Exception:
                    pass
            return max(0, len(log.lines) - before)

        def _chat_pop_strips(self, n: int) -> None:
            """Remove the last ``n`` strips from the chat log (in-place rewrite helper)."""
            if n <= 0:
                return
            try:
                from textual.geometry import Size

                log = self.query_one("#chat-log", RichLog)
                n = min(int(n), len(log.lines))
                if n <= 0:
                    return
                del log.lines[-n:]
                log._line_cache.clear()
                log.virtual_size = Size(
                    int(getattr(log, "_widest_line_width", 0) or 0),
                    len(log.lines),
                )
                log.refresh()
            except Exception:
                pass

        def _pin_chat_bottom(self) -> None:
            """Keep tool rows visible when #live-think steals/returns chat height."""
            if self._is_selecting():
                return
            if not getattr(self, "_follow_chat", True):
                return

            def _go() -> None:
                try:
                    self.query_one("#chat-log", RichLog).scroll_end(animate=False)
                except Exception:
                    pass

            _go()
            # Height change from .streaming may apply on next layout pass
            try:
                self.call_after_refresh(_go)
            except Exception:
                pass

        def _log_verbose(self, text: str) -> None:
            """Boot / lifecycle chatter — only when /debug is on."""
            if self._debug_mode:
                self.log_line(text, style="system")

        def _fold_detail_text(self, text: str) -> str:
            """Show first ~100 tokens; append hint unless detail mode (^O)."""
            body = str(text or "")
            if self._detail_expanded or self._approx_tokens(body) <= self._DETAIL_TOKEN_LIMIT:
                return body
            limit = self._DETAIL_TOKEN_LIMIT
            out: list[str] = []
            used = 0
            for ch in body:
                if ord(ch) > 0x2E80:
                    used += 1
                elif ch.isspace():
                    if out and out[-1] != " ":
                        used += 1
                elif ch.isalnum():
                    if not out or out[-1].isspace() or (not out[-1].isalnum() and out[-1] != " "):
                        used += 1
                out.append(ch)
                if used >= limit:
                    break
            trimmed = "".join(out).rstrip()
            if len(trimmed) < len(body.strip()):
                return trimmed + " " + t("detail_fold_hint")
            return body

        def _render_welcome_card(self) -> Any:
            """Kimi-style welcome card with logo — scrolls with chat history."""
            from rich.console import Console, ConsoleOptions, RenderResult
            from rich.segment import Segment
            from rich.style import Style

            surface = self._theme_hex("surface", "#161b22")
            fg = self._theme_hex("foreground", "#e6edf3")
            primary = self._theme_hex("primary", "#58a6ff")
            accent = self._theme_hex("accent", "#7c3aed")
            edge = self._theme_hex("primary-muted", primary)

            if self.mode == "group" and self.group:
                gname = self._escape_markup(self.group.group_name or self.group.group_id)
                meta = f"Group  {gname}  ·  /leave"
            else:
                ready = bool(self.bridge and getattr(self.bridge, "is_open", False))
                state = "ready" if ready else "offline"
                mode = getattr(self, "_agent_mode", "build") or "build"
                mode_plain = t("mode_plan") if mode == "plan" else t("mode_build")
                model = self._escape_markup(getattr(self, "_model_label", None) or "—")
                agent = self._escape_markup(self.agent or "—")
                meta = f"Model  {model}  ·  {agent}  ·  {state}  ·  {mode_plain}"

            raw_cwd = (
                getattr(self, "_launch_cwd", None)
                or getattr(self, "_project_path", None)
                or self._resolve_project_path()
            )
            # Fit path to card width; never collapse to a lone "~"
            path_budget = 72
            try:
                path_budget = max(24, int(self.size.width) - 18)
            except Exception:
                pass
            cwd_disp = self._display_project_path(raw_cwd, max_len=path_budget)
            cwd = self._escape_markup(cwd_disp)
            welcome = self._escape_markup(t("header_welcome"))
            logo_lines = ("┌──┐", "│●●│", "│●●│", "└──┘")
            text_lines = (
                f"[bold {fg}]OpenSquad[/]  [dim]v{__version__}[/]",
                f"[dim]{welcome}[/]",
                f"[dim]{meta}[/]",
                f"[dim]{t('header_project')}[/]  [{primary}]{cwd}[/]",
            )

            class _WelcomeCard:
                def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
                    width = max(24, options.max_width)
                    edge_st = Style.parse(f"{edge} on {surface}")
                    pad_st = Style.parse(f"on {surface}")
                    logo_bg = Style.parse(f"on {accent}")
                    logo_dot = Style.parse(f"bold white on {accent}")
                    inner_w = max(1, width - 2)
                    logo_w = 4
                    gap = 2
                    n_rows = max(len(logo_lines), len(text_lines))

                    def _emit_row(left: str, right_markup: str) -> RenderResult:
                        from rich.text import Text

                        row = Text("", style=pad_st)
                        for ch in left.ljust(logo_w)[:logo_w]:
                            if ch == "●":
                                row.append(ch, style=logo_dot)
                            elif ch in "┌┐└┘─│":
                                row.append(ch, style=logo_bg)
                            else:
                                row.append(ch, style=pad_st)
                        row.append(" " * gap, style=pad_st)
                        if right_markup:
                            row.append_text(Text.from_markup(right_markup))
                        pad = max(0, inner_w - len(row.plain))
                        yield from row
                        if pad:
                            yield Segment(" " * pad, pad_st)
                        yield Segment("│", edge_st)
                        yield Segment.line()

                    yield Segment(("─" * inner_w) + "┐", edge_st)
                    yield Segment.line()
                    for i in range(n_rows):
                        left = logo_lines[i] if i < len(logo_lines) else "    "
                        right = text_lines[i] if i < len(text_lines) else ""
                        yield from _emit_row(left, right)
                    yield Segment(("─" * inner_w) + "┘", edge_st)
                    yield Segment.line()

            return _WelcomeCard()

        def _post_welcome_card(self) -> None:
            if getattr(self, "_welcome_posted", False):
                return
            self._welcome_posted = True

            def _write() -> None:
                self._chat_write(self._render_welcome_card(), follow=True)
                self._chat_write("", follow=True)

            try:
                _write()
            except Exception:
                pass

        def _render_user_block(self, text: str) -> Any:
            """User message card: 1-cell blue accent flush + top/bottom/right outline."""
            from rich.console import Console, ConsoleOptions, RenderResult
            from rich.segment import Segment
            from rich.style import Style

            surface = self._theme_hex("surface", "#161b22")
            fg = self._theme_hex("foreground", "#e6edf3")
            accent = self._theme_hex("primary", "#58a6ff")
            edge = self._theme_hex("primary-muted", accent)
            body = str(text) if text is not None else ""

            class _UserCard:
                def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
                    width = max(4, options.max_width)
                    # Left accent flush to surface; keep top/bottom/right frame (no left │ gap)
                    accent_st = Style.parse(f"on {accent}")
                    edge_st = Style.parse(f"{edge} on {surface}")
                    body_st = Style.parse(f"bold {fg} on {surface}")
                    pad_st = Style.parse(f"on {surface}")
                    inner_w = max(1, width - 2)  # accent + right │

                    def _row(inner: str, *, pad_row: bool = False) -> RenderResult:
                        text_st = pad_st if pad_row else body_st
                        content = inner if len(inner) <= inner_w else inner[:inner_w]
                        if len(content) < inner_w:
                            content = content + (" " * (inner_w - len(content)))
                        yield Segment(" ", accent_st)
                        yield Segment(content, text_st)
                        yield Segment("│", edge_st)
                        yield Segment.line()

                    yield Segment(" ", accent_st)
                    yield Segment(("─" * inner_w) + "┐", edge_st)
                    yield Segment.line()
                    for line in body.splitlines() or [""]:
                        yield from _row(f" {line}")
                    yield Segment(" ", accent_st)
                    yield Segment(("─" * inner_w) + "┘", edge_st)
                    yield Segment.line()

            return _UserCard()

        def _thinking_markup(self, text: str, *, live: bool = False) -> str:
            """Thinking: muted tan label (+ soft shimmer while live) + gray body.

            live=True → shimmer title + trailing window; else fold unless ^O.
            """
            muted = self._theme_hex("text-muted", "#8b949e")
            label = t("thinking_label")
            if live:
                raw = (text or "").strip()
                # Keep last ~900 chars so the fixed panel scrolls content, not layout
                if len(raw) > 900:
                    raw = "…" + raw[-899:]
                body = raw
                label_mk = self._shimmer_markup(label, base=self._thinking_label_hex())
            else:
                body = self._fold_detail_text(text)
                tan = self._thinking_label_hex()
                label_mk = f"[{tan}]{label}[/]"
            safe = self._escape_markup(body)
            return f"{label_mk} [dim {muted}]{safe}[/]"

        def on_key(self, event) -> None:
            """Recover from lost focus: refocus input and apply the printable key.

            (Keys are otherwise dropped when focused is None — felt like 'cannot type'.)
            """
            try:
                focused = self.focused
                if focused is not None and getattr(focused, "id", None) == "chat-input":
                    return
                if event.key in {
                    "tab",
                    "shift+tab",
                    "up",
                    "down",
                    "escape",
                    "enter",
                    "ctrl+c",
                    "ctrl+q",
                    "ctrl+l",
                }:
                    self._focus_input()
                    return
                self._focus_input()
                ch = getattr(event, "character", None)
                if not ch or not ch.isprintable():
                    return
                inp = self.query_one("#chat-input", Input)
                pos = inp.cursor_position
                inp.value = inp.value[:pos] + ch + inp.value[pos:]
                inp.cursor_position = pos + len(ch)
                event.prevent_default()
                event.stop()
            except Exception:
                pass

        # ── wait animation ────────────────────────────────────────────

        def _static_set(self, widget_id: str, markup: str) -> None:
            """Update a Static only when content changes (avoids Windows CMD line spam)."""
            key = widget_id.lstrip("#")
            prev = self._paint_cache.get(key)
            if prev == markup:
                return
            self._paint_cache[key] = markup
            try:
                self.query_one(widget_id, Static).update(markup)
            except Exception:
                self._paint_cache.pop(key, None)

        # ── chrome ────────────────────────────────────────────────────

        @staticmethod
        def _resolve_project_path() -> str:
            """Current project directory (CLI process cwd), OpenCode-style footer."""
            return os.path.abspath(os.getcwd())

        def _agent_dir_name(self, name: str | None = None) -> str:
            """Resolve admin API dir_name (working-directory PUT keys on folder name)."""
            key = (name or self.agent or "").strip()
            if not key:
                return ""
            info = self._lookup_agent(key)
            if info:
                return str(info.get("dir_name") or info.get("agent_id") or key)
            return key

        def _sync_agent_cwd_from_launch(self, name: str | None = None) -> bool:
            """Bind agent session cwd to the directory where `opensquad code` was started.

            Writes ``.session_cwd`` via Gateway admin API so tools/session paths
            follow the cmd launch address (not a leftover Web UI folder).
            """
            if not self.client.token:
                return False
            dir_name = self._agent_dir_name(name)
            if not dir_name:
                return False
            cwd = (getattr(self, "_launch_cwd", None) or self._resolve_project_path()).strip()
            if not cwd or not os.path.isdir(cwd):
                return False
            # Skip repeat PUT for same agent+path in this TUI process
            mark = f"{dir_name}\0{cwd}"
            if getattr(self, "_cwd_synced_agent", None) == mark:
                self._project_path = cwd
                return True
            try:
                self.client.admin_put(
                    f"agents/{dir_name}/working-directory",
                    {"path": cwd},
                )
            except Exception as e:
                self.log_line(f"working directory sync failed: {e}", style="system")
                return False
            self._cwd_synced_agent = mark
            self._project_path = cwd
            short = self._short_path(cwd)
            self._log_verbose(f"Working directory → {short}")
            return True

        @staticmethod
        def _pretty_model_label(card: str = "", model: str = "") -> str:
            """Human label for a model — never show raw prov-* card slugs."""
            raw = (model or "").strip()
            if not raw:
                raw = (card or "").strip()
            if not raw:
                return "—"
            # Hide internal provider-card ids (prov-opencode → useless "Prov Opencode")
            if raw.lower().startswith("prov-"):
                return "—"
            # Prefer human title already spaced
            if " " in raw and not raw.startswith(("http://", "https://")):
                return raw
            # deepseek-v4-flash → DeepSeek V4 Flash
            parts = raw.replace("_", "-").split("-")
            out: list[str] = []
            for part in parts:
                if not part:
                    continue
                low = part.lower()
                if low.startswith("v") and len(low) > 1 and low[1:].isdigit():
                    out.append(low.upper())
                elif low.isdigit():
                    out.append(low)
                elif low in ("gpt", "o1", "o3", "o4"):
                    out.append(low.upper() if low.startswith("o") else "GPT")
                else:
                    out.append(part[:1].upper() + part[1:])
            return " ".join(out) or "—"

        def _context_usage_label(self) -> str:
            used = int(getattr(self, "_token_used", 0) or 0)
            mx = int(getattr(self, "_token_max", 0) or 0)
            if mx > 0:
                pct = min(100, max(0, round(100.0 * used / mx)))
                return f"{self._fmt_tokens(used)} ({pct}%)"
            if used > 0:
                return f"{self._fmt_tokens(used)}"
            return "— (—%)"

        def _opencode_status_markup(self) -> str:
            """OpenCode-style meta under the input box (outside the blue border).

            Left: Build · model · provider · effort; right: ↑↓ tokens + context %.
            Build/Plan muted grey (no blue/orange highlight).
            """
            if self.mode == "group" and self.group:
                gname = self.group.group_name or self.group.group_id
                return f" {gname} · group · /leave"
            mode = getattr(self, "_agent_mode", "build") or "build"
            fg = self._theme_hex("foreground", "#e6edf3")
            muted = self._theme_hex("text-muted", "#8b949e")
            if mode == "plan":
                mode_mk = f"[{muted}]Plan[/]"
            else:
                mode_mk = f"[{muted}]Build[/]"

            model = self._escape_markup(getattr(self, "_model_label", None) or "—")
            provider = self._escape_markup(getattr(self, "_model_provider_label", None) or "").strip()
            effort = self._escape_markup((getattr(self, "_reasoning_effort", None) or "high").lower())
            qn = len(getattr(self, "_send_queue", ()) or ())
            held = bool(getattr(self, "_hold_queue_after_stop", False))
            if held and qn:
                qbit = f" · held:{qn}"
            elif held:
                qbit = " · held"
            elif qn:
                qbit = f" · Q:{qn}"
            else:
                qbit = ""
            live = ""
            hub = getattr(self, "_side_hub", None)
            if hub and any(s.active for s in hub.streams.values()):
                live = " · [dim]^X live[/]"
            media = ""
            if self.pending_media:
                media = f" · {self._escape_markup(format_pending_chips(self.pending_media))}"
            skill = ""
            if getattr(self, "pending_skill", None):
                d = self._escape_markup(str(self.pending_skill.get("dir") or ""))
                if d:
                    skill = f" · [{muted}]/{d}[/]"
            prov_bit = f" · [{muted}]{provider}[/]" if provider else ""
            left = f" {mode_mk} · [{muted}]{model}[/]{prov_bit} · [{muted}]{effort}[/]{qbit}{live}{skill}{media}"
            meter = self._turn_meter_plain()
            ctx = self._context_usage_label()
            if meter:
                right = f"[{muted}]{self._escape_markup(meter)}[/]  [{fg}]{self._escape_markup(ctx)}[/] "
            else:
                right = f"[{fg}]{self._escape_markup(ctx)}[/] "
            # Pad so tokens sit on the right without a second widget
            try:
                meta = self.query_one("#prompt-meta", Static)
                width = int(meta.size.width or 0)
            except Exception:
                width = 0
            if width > 8:
                import re

                plain_left = re.sub(r"\[/?[^\]]*\]", "", left)
                plain_right = re.sub(r"\[/?[^\]]*\]", "", right)
                gap = max(1, width - len(plain_left) - len(plain_right))
                return left + (" " * gap) + right
            return f"{left}  {right}"

        def _footer_path_markup(self) -> str:
            """Footer path row (2d78e00): cwd + theme + shortcut hints."""
            path = self._escape_markup(
                self._short_path(getattr(self, "_project_path", None) or self._resolve_project_path())
            )
            theme_name = self._escape_markup(str(getattr(self, "theme", "") or ""))
            return f" {path}  [dim]· theme {theme_name} · Tab mode · ^E effort · /help[/]"

        def _paint_bottom_status(self) -> None:
            """No-op (#bottom-status removed in 2d78e00 layout). Cache for tests."""
            mode = getattr(self, "_agent_mode", "build") or "build"
            self._paint_cache["bottom-status"] = "Plan" if mode == "plan" else "Build"

        def _paint_prompt_meta_only(self, *, force: bool = False) -> None:
            """Refresh #prompt-meta without touching header/placeholder (2d78e00)."""
            if self._is_selecting() and not force:
                return
            markup = self._opencode_status_markup()
            if not force and markup == self._paint_cache.get("prompt-meta"):
                return
            self._static_set("#prompt-meta", markup)

        def _sync_status_from_agent(self, name: str | None = None) -> None:
            """Pull model / tokens / effort / path from admin agents list + local config."""
            # Prefer frozen launch cwd (cmd address); fall back to live getcwd
            self._project_path = getattr(self, "_launch_cwd", None) or self._resolve_project_path()
            agent_name = name or self.agent
            if not agent_name:
                return
            info = self._lookup_agent(agent_name) or {}
            card = str(info.get("model_card") or "").strip()
            if card:
                self._model_card = card
                try:
                    full = (self.client.admin_get(f"model-cards/{card}") or {}).get("card") or {}
                except Exception:
                    full = {}
                if isinstance(full, dict) and full:
                    title = str(full.get("title") or "").strip()
                    mn = str(full.get("model_name") or "").strip()
                    self._model_name = mn
                    self._model_label = title or self._pretty_model_label("", mn) or "—"
                    self._model_provider_label = str(full.get("provider") or "").strip()
                else:
                    self._model_label = self._pretty_model_label(card)
            ts = info.get("token_stats")
            if isinstance(ts, dict):
                try:
                    self._token_used = int(ts.get("used") or 0)
                    self._token_max = int(ts.get("max") or 0)
                except (TypeError, ValueError):
                    pass
                m = str(ts.get("model") or "").strip()
                if m:
                    self._model_name = m
                    if not self._model_label or self._model_label == "—":
                        self._model_label = self._pretty_model_label("", m)
                session = ts.get("session") if isinstance(ts.get("session"), dict) else {}
                try:
                    if session:
                        sout = session.get("output_tokens", session.get("total_output_tokens"))
                        if sout is not None:
                            self._session_out_tokens = int(sout or 0)
                except (TypeError, ValueError):
                    pass
            # reasoning_effort from workspace agent config.json when available
            try:
                from opensquad._syscfg import workspace_agents_dir

                cfg_path = os.path.join(workspace_agents_dir(agent_name), "config.json")
                if os.path.isfile(cfg_path):
                    import json

                    with open(cfg_path, encoding="utf-8") as f:
                        cfg = json.load(f)
                    model_cfg = (cfg.get("config") or cfg).get("model") or cfg.get("model") or {}
                    if isinstance(model_cfg, dict):
                        effort = str(model_cfg.get("reasoning_effort") or "").strip().lower()
                        if effort in ("low", "medium", "high"):
                            self._reasoning_effort = effort
                        if not card:
                            c2 = str(model_cfg.get("_card") or "").strip()
                            if c2:
                                self._model_card = c2
                                self._model_label = self._pretty_model_label(c2)
            except Exception:
                pass

        def _header_bar_markup(self) -> str:
            """Top dock: OpenSquad · agent · ready/offline · gateway."""
            muted = self._theme_hex("text-muted", "#8b949e")
            fg = self._theme_hex("foreground", "#e6edf3")
            if self.mode == "group" and self.group:
                gname = self._escape_markup(self.group.group_name or self.group.group_id)
                return f"  [{fg}]OpenSquad[/]  ·  [b]{gname}[/b]  ·  [{muted}]group[/]"
            ready = bool(self.bridge and getattr(self.bridge, "is_open", False))
            state = "ready" if ready else "offline"
            agent = self._escape_markup(self.agent or "—")
            gw = self._escape_markup(getattr(self.client, "gateway_url", "") or "")
            return f"  [{fg}]OpenSquad[/]  ·  [b]{agent}[/b]  ·  [{muted}]{state}[/]  ·  [{muted}]{gw}[/]"

        def _dock_busy(self) -> bool:
            """True while a turn/send is in flight (freeze Win Input chrome)."""
            return bool(getattr(self, "_sending", False) or getattr(self, "_turn_started_at", None) is not None)

        def _refresh_chrome(self) -> None:
            # Win CMD: during a turn, never touch Input.placeholder / footer — both
            # sit next to the bordered prompt and re-assigning them ghosts the dock.
            freeze_input = sys.platform == "win32" and self._dock_busy()
            if self._wait_label:
                self._paint_wait()
                self._paint_prompt_meta_only()
                self._static_set("#header-bar", self._header_bar_markup())
                if not freeze_input:
                    try:
                        fpath = self.query_one("#footer-path", Static)
                        fpath.update(self._footer_path_markup())
                    except Exception:
                        pass
                return
            try:
                inp = self.query_one("#chat-input", Input)
            except Exception:
                return

            if self.mode == "group" and self.group:
                gname = self.group.group_name or self.group.group_id
                ph = t("placeholder_group", gname=gname)
            elif getattr(self, "_await_api_key", None):
                ph = t("placeholder_api_key")
            elif getattr(self, "_await_model_field", None):
                fld = (self._await_model_field or {}).get("field") or "value"
                ph = t("placeholder_field", fld=fld)
            elif getattr(self, "_await_login", None):
                step = (self._await_login or {}).get("step") or "email"
                ph = t("placeholder_login_password") if step == "password" else t("placeholder_login_email")
            elif getattr(self, "_await_custom_answer", False):
                ph = t("placeholder_custom")
            else:
                ph = t("placeholder_agent", agent=self.agent or "agent")
            # Never re-assign placeholder unless text changed (Win CMD ghosts bordered dock)
            if not freeze_input and ph != getattr(self, "_placeholder_cache", None):
                self._placeholder_cache = ph
                inp.placeholder = ph
            self._static_set("#header-bar", self._header_bar_markup())
            self._paint_prompt_meta_only()
            if not freeze_input:
                try:
                    fpath = self.query_one("#footer-path", Static)
                    fpath.update(self._footer_path_markup())
                except Exception:
                    pass

        def log_line(self, text: str, style: str = "") -> None:
            """Thread-safe append to chat log (OpenCode-style blocks)."""
            raw = str(text) if text is not None else ""
            body = raw.strip()

            # Live stream already owns an open reply block — upgrade it in place
            if body and style == "agent" and getattr(self, "_live_reply", None):
                if getattr(self, "_think_pending", False):
                    self._flush_thinking_to_log()
                self._finalize_live_reply(raw)
                return

            # Chokepoint: never print the same agent reply twice (stream vs final race).
            # Second copy often arrives with style="" (no · agent label) — still drop it.
            # Longer final after a truncated flush → in-place upgrade (not a 2nd block).
            if body and style in ("agent", ""):
                if self._is_user_echo(body):
                    return
                last = (getattr(self, "_last_agent_reply", None) or "").strip()
                if last and _same_reply(body, last):
                    self._reply_flushed = True
                    return
                if last and _is_truncated_prefix(body, last):
                    # Incoming body is shorter than what we already showed — ignore
                    self._reply_flushed = True
                    return
                if last and _is_truncated_prefix(last, body):
                    # Already painted an incomplete reply; replace it with the full final
                    self._upgrade_committed_agent_reply(body)
                    return
                # Claim synchronously before call_from_thread so a racing write is dropped
                if style == "agent" or (style == "" and body and not body.startswith(("  ⚙", "  ✓", "  ·", "[", "/"))):
                    self._last_agent_reply = body
                    self._reply_flushed = True

            # Assistant/tool lines may arrive while a thought is still open — flush first
            if style in ("", "agent", "tool", "error") and getattr(self, "_think_pending", False):
                if style != "thought":
                    self._flush_thinking_to_log()

            safe = self._escape_markup(raw)

            def _write() -> None:
                w = self._chat_write
                if style == "user":
                    w("", follow=True)
                    w(self._render_user_block(raw), follow=True)
                    w("", follow=True)
                elif style == "thought":
                    w(self._thinking_markup(raw))
                    w("")
                elif style == "agent":
                    try:
                        meta = self._chat_write_markups(self._final_reply_items(raw), follow=True)
                        self._committed_agent_meta = meta
                    except Exception:
                        w("")
                        self._write_agent_body(raw, w, lamp="done")
                        w(self._agent_footer_markup())
                        w("")
                        self._committed_agent_meta = None
                elif style == "error":
                    w(f"{self._signal_lamp('error')}[bold red]{safe}[/]", follow=True)
                elif style == "system":
                    w(f"[dim]{safe}[/]")
                elif style == "tool":
                    self._write_tool_line(raw)
                else:
                    if safe.startswith(("  ⚙", "  ✓", "  ·", "[")) or raw.lstrip().startswith(("⚙", "✓")):
                        self._write_tool_line(raw)
                    else:
                        # Plain agent-like reply may still contain markdown tables
                        if self._looks_like_agent_prose(raw):
                            try:
                                meta = self._chat_write_markups(self._final_reply_items(raw), follow=True)
                                self._committed_agent_meta = meta
                            except Exception:
                                self._write_agent_body(raw, w, lamp="done")
                                w("")
                                self._committed_agent_meta = None
                        else:
                            w(f"{self._signal_lamp('done')}[#e6edf3]{safe}[/]")
                            w("")

            try:
                self._schedule_ui(_write)
            except Exception:
                try:
                    _write()
                except Exception:
                    pass

        def _write_agent_body(self, text: str, write, *, lamp: str = "done") -> None:
            """Write agent reply with left traffic light; render GFM tables as grids."""
            fg = self._theme_hex("foreground", "#e6edf3")
            border = self._theme_hex("text-muted", "#8b949e")
            header = self._theme_hex("accent", "#d2a8ff")
            prefix = self._signal_lamp(lamp)
            indent = "  "  # align continuation under text after ●␣

            def _with_lamp(first: bool, line_markup: str) -> str:
                return f"{prefix}{line_markup}" if first else f"{indent}{line_markup}"

            try:
                from opensquad.cli.tui.md_table import has_markdown_table, iter_text_and_tables

                if not has_markdown_table(text):
                    lines = (text or "").splitlines() or [""]
                    for i, ln in enumerate(lines):
                        write(_with_lamp(i == 0, f"[{fg}]{self._escape_markup(ln)}[/]"))
                    return
                # Use chat width so exported grid matches the viewport
                try:
                    width = max(40, int(self.query_one("#chat-log", RichLog).size.width) - 4)
                except Exception:
                    width = 100
                first = True
                for kind, part in iter_text_and_tables(
                    text,
                    border=border,
                    header_style=f"bold {header}",
                    cell_style=fg,
                    width=width,
                ):
                    if kind == "table":
                        # Text.from_ansi with baked-in box chars; don't shrink/reflow
                        if first:
                            write(prefix.rstrip(), follow=True)
                            first = False
                        write(part, follow=True, shrink=False)
                    elif part == "":
                        write("")
                    else:
                        for i, ln in enumerate(str(part).splitlines() or [""]):
                            write(_with_lamp(first and i == 0, f"[{fg}]{self._escape_markup(ln)}[/]"))
                            first = False
            except Exception:
                write(_with_lamp(True, f"[{fg}]{self._escape_markup(text)}[/]"))

        def log_file_diff(self, lines: list) -> None:
            """Append OpenCode-style file-edit markup (no purple escape path)."""
            if not lines:
                return
            if getattr(self, "_think_pending", False):
                self._flush_thinking_to_log()

            def _write() -> None:
                w = self._chat_write
                for line in lines:
                    w(str(line), follow=True)

            try:
                self.call_from_thread(_write)
            except Exception:
                try:
                    _write()
                except Exception:
                    pass

        def log_plan(self, lines: list) -> None:
            """Append OpenCode-style # Todos plan block."""
            if not lines:
                return
            if getattr(self, "_think_pending", False):
                self._flush_thinking_to_log()

            def _write() -> None:
                w = self._chat_write
                w("", follow=True)
                for line in lines:
                    w(str(line), follow=True)
                w("", follow=True)

            try:
                self.call_from_thread(_write)
            except Exception:
                try:
                    _write()
                except Exception:
                    pass

        def log_plan_payload(self, payload) -> None:
            """Parse plan WS payload and render with current theme colors."""
            try:
                from opensquad.cli.tui.plan_block import (
                    format_opencode_todos_markup,
                    parse_plan_content,
                )

                if isinstance(payload, dict):
                    content = payload.get("text") or payload.get("content") or payload.get("plan")
                    if content is None:
                        content = payload.get("steps")
                else:
                    content = payload
                steps = parse_plan_content(content)
                if not steps:
                    return
                lines = format_opencode_todos_markup(
                    steps,
                    fg=self._theme_hex("foreground", "#c9d1d9"),
                    muted=self._theme_hex("text-muted", "#8b949e"),
                    green=self._theme_hex("success", "#3fb950"),
                    cyan=self._theme_hex("primary", "#58a6ff"),
                    red=self._theme_hex("error", "#f85149"),
                )
                self.log_plan(lines)
            except Exception:
                pass

        def _flush_thinking_to_log(self) -> None:
            """Finalize live Thinking → one transcript row; clear the live panel."""
            buf = (getattr(self, "_think_buf_latest", None) or "").strip()
            self._think_pending = False
            self._think_buf_latest = ""
            self._think_gen = int(getattr(self, "_think_gen", 0) or 0) + 1
            # Keep paragraph breaks; collapse only runs of spaces within a line
            body = ""
            if buf:
                body = "\n".join(" ".join(line.split()) for line in buf.splitlines() if line.strip())
                if not body:
                    body = " ".join(buf.split())
            # Drop identical re-flush (tool_call + thinking_end both finalize)
            if body and body == getattr(self, "_last_flushed_think", ""):

                def _hide_only() -> None:
                    self._hide_live_think()

                try:
                    self._schedule_ui(_hide_only)
                except Exception:
                    try:
                        _hide_only()
                    except Exception:
                        pass
                return
            if body:
                self._last_flushed_think = body

            def _write() -> None:
                self._hide_live_think()
                if not body:
                    return
                start = 0
                try:
                    start = len(self.query_one("#chat-log", RichLog).lines)
                except Exception:
                    start = 0
                n = self._chat_write_counted(self._thinking_markup(body, live=False), follow=True)
                n2 = self._chat_write_counted("", follow=True)
                self._detail_blocks.append(
                    {
                        "kind": "thinking",
                        "detail": body,
                        "start": start,
                        "strips": n,  # blank spacer not part of rewrite target
                    }
                )
                if len(self._detail_blocks) > 200:
                    self._detail_blocks = self._detail_blocks[-150:]
                _ = n2

            try:
                self._schedule_ui(_write)
            except Exception:
                try:
                    _write()
                except Exception:
                    pass

        def _hide_live_think(self) -> None:
            try:
                w = self.query_one("#live-think", Static)
                w.update("")
                w.remove_class("streaming")
                self._paint_cache.pop("live-think", None)
            except Exception:
                pass
            # Chat regains height — re-pin so prior tool rows stay in view
            self._pin_chat_bottom()
            if not self._shimmer_active():
                self._stop_shimmer_timer()

        def _paint_live_think(self) -> None:
            """Stream Thinking tokens into the fixed-height #live-think panel."""
            if not getattr(self, "_think_pending", False):
                return
            gen = int(getattr(self, "_think_gen", 0) or 0)

            def _do() -> None:
                try:
                    if gen != int(getattr(self, "_think_gen", 0) or 0):
                        return
                    if not getattr(self, "_think_pending", False):
                        return
                    buf = (getattr(self, "_think_buf_latest", None) or "").strip()
                    if not buf:
                        return
                    markup = self._thinking_markup(buf, live=True)
                    if self._paint_cache.get("live-think") == markup:
                        return
                    self._paint_cache["live-think"] = markup
                    w = self.query_one("#live-think", Static)
                    grew = "streaming" not in w.classes
                    if grew:
                        w.add_class("streaming")
                    w.update(markup)
                    # #live-think.streaming steals 6 rows from chat — without re-pin,
                    # already-painted tool rows scroll out of the shrink viewport.
                    if grew:
                        self._pin_chat_bottom()
                    self._ensure_shimmer_timer()
                except Exception:
                    pass

            try:
                self._schedule_ui(_do)
            except Exception:
                try:
                    _do()
                except Exception:
                    pass

        def _chat_write_markups(
            self,
            items: list[tuple[Any, dict[str, Any]]],
            *,
            follow: bool | None = True,
        ) -> dict[str, Any]:
            """Append several chat strips; return start/strips meta."""
            log = self.query_one("#chat-log", RichLog)
            start = len(log.lines)
            n = 0
            for i, (content, opts) in enumerate(items):
                is_last = i == len(items) - 1
                n += self._chat_write_counted(
                    content,
                    follow=(follow if is_last else False),
                    shrink=bool(opts.get("shrink", True)),
                )
            return {"start": start, "strips": n}

        def _chat_replace_markups(
            self,
            open_meta: dict[str, Any] | None,
            items: list[tuple[Any, dict[str, Any]]],
            *,
            follow: bool | None = True,
        ) -> dict[str, Any]:
            """Replace an open multi-strip block with new markups."""
            log = self.query_one("#chat-log", RichLog)
            if open_meta:
                start = int(open_meta.get("start", -1))
                strips = int(open_meta.get("strips", 0))
                end = start + strips
                if strips > 0 and start >= 0 and end == len(log.lines):
                    self._chat_pop_strips(strips)
                    meta = self._chat_write_markups(items, follow=follow)
                    self._shift_open_tool_starts(start + 1, int(meta["strips"]) - strips)
                    return {"start": start, "strips": int(meta["strips"])}
                if strips > 0 and 0 <= start < len(log.lines) and end <= len(log.lines):
                    try:
                        from textual.geometry import Size

                        tail = list(log.lines[end:]) if end < len(log.lines) else []
                        del log.lines[start:]
                        log._line_cache.clear()
                        log.virtual_size = Size(
                            int(getattr(log, "_widest_line_width", 0) or 0),
                            len(log.lines),
                        )
                        meta = self._chat_write_markups(items, follow=False)
                        if tail:
                            log.lines.extend(tail)
                            log.virtual_size = Size(
                                int(getattr(log, "_widest_line_width", 0) or 0),
                                len(log.lines),
                            )
                            log.refresh()
                        self._shift_open_tool_starts(end, int(meta["strips"]) - strips)
                        if follow and getattr(self, "_follow_chat", True) and not self._is_selecting():
                            try:
                                log.scroll_end(animate=False)
                            except Exception:
                                pass
                        return {"start": start, "strips": int(meta["strips"])}
                    except Exception:
                        pass
            return self._chat_write_markups(items, follow=follow)

        def _live_reply_items(self, text: str) -> list[tuple[Any, dict[str, Any]]]:
            """Compact streaming reply (yellow lamp, plain lines — no table parse yet)."""
            fg = self._theme_hex("foreground", "#e6edf3")
            lamp = self._signal_lamp("progress")
            # Keep trailing spaces/newlines so the cursor line grows naturally
            raw = text if text is not None else ""
            lines = raw.splitlines() or [""]
            if raw.endswith("\n"):
                lines.append("")
            items: list[tuple[Any, dict[str, Any]]] = []
            for i, ln in enumerate(lines):
                esc = self._escape_markup(ln)
                if i == 0:
                    items.append((f"{lamp}[{fg}]{esc}[/]", {}))
                else:
                    items.append((f"  [{fg}]{esc}[/]", {}))
            return items

        def _final_reply_items(self, text: str) -> list[tuple[Any, dict[str, Any]]]:
            """Final agent block: spacer + body + footer + spacer."""
            items: list[tuple[Any, dict[str, Any]]] = [("", {})]
            body: list[tuple[Any, dict[str, Any]]] = []

            def _w(content: Any, follow: bool = True, shrink: bool = True) -> None:
                body.append((content, {"shrink": shrink}))

            self._write_agent_body(text, _w, lamp="done")
            items.extend(body)
            items.append((self._agent_footer_markup(), {}))
            items.append(("", {}))
            return items

        def _reset_live_reply_state(self) -> None:
            self._live_reply = None
            self._live_reply_painted = ""
            self._live_reply_dirty = False
            self._reply_paint_at = 0.0

        def _paint_live_reply_ui(self) -> None:
            """UI-thread: rewrite the open agent reply from the latest stream buffer."""
            self._live_reply_dirty = False
            if getattr(self, "_reply_flushed", False):
                return
            text = getattr(self, "_stream_buf", None) or ""
            if not text:
                return
            # Throttle full rewrites on Windows CMD (long replies thrash the console)
            now = time.monotonic()
            last = float(getattr(self, "_reply_paint_at", 0.0) or 0.0)
            if text == getattr(self, "_live_reply_painted", None):
                return
            if last and (now - last) < 0.08:
                if not getattr(self, "_live_reply_dirty", False):
                    self._live_reply_dirty = True
                    delay = max(0.01, 0.08 - (now - last))
                    try:
                        self.set_timer(delay, self._paint_live_reply_ui)
                    except Exception:
                        pass
                return
            self._reply_paint_at = now
            self._live_reply_painted = text
            items = self._live_reply_items(text)
            try:
                meta = getattr(self, "_live_reply", None)
                if meta and int(meta.get("strips", 0) or 0) > 0:
                    self._live_reply = self._chat_replace_markups(meta, items, follow=True)
                else:
                    self._live_reply = self._chat_write_markups(items, follow=True)
            except Exception:
                return
            # More tokens arrived while painting — schedule another frame
            latest = getattr(self, "_stream_buf", None) or ""
            if latest != text and not getattr(self, "_reply_flushed", False):
                if not getattr(self, "_live_reply_dirty", False):
                    self._live_reply_dirty = True
                    try:
                        self.set_timer(0.05, self._paint_live_reply_ui)
                    except Exception:
                        self._paint_live_reply_ui()

        def _paint_live_reply(self) -> None:
            """Schedule a coalesced live-reply paint on the UI thread."""
            if getattr(self, "_reply_flushed", False):
                return
            if getattr(self, "_live_reply_dirty", False):
                return
            self._live_reply_dirty = True
            try:
                self._schedule_ui(self._paint_live_reply_ui)
            except Exception:
                try:
                    self._paint_live_reply_ui()
                except Exception:
                    pass

        def _finalize_live_reply(self, text: str) -> None:
            """Turn the streaming yellow reply into the final green block (+ footer)."""
            body = (text or "").strip()
            if not body or self._is_user_echo(body):
                self._stream_buf = ""
                self._reset_live_reply_state()
                return
            last = (getattr(self, "_last_agent_reply", None) or "").strip()
            live = getattr(self, "_live_reply", None)
            if last and _same_reply(body, last) and getattr(self, "_reply_flushed", False) and not live:
                self._stream_buf = ""
                return
            if last and _is_truncated_prefix(body, last) and getattr(self, "_reply_flushed", False) and not live:
                self._stream_buf = ""
                return
            # Already committed a short green block; upgrade it instead of appending
            if last and _is_truncated_prefix(last, body) and not live:
                self._upgrade_committed_agent_reply(body)
                self._stream_buf = ""
                return
            self._last_agent_reply = body
            self._reply_flushed = True
            self._stream_buf = ""
            snap_meta = dict(live) if isinstance(live, dict) else None
            self._live_reply = None
            self._live_reply_dirty = False
            self._live_reply_painted = body

            def _do() -> None:
                items = self._final_reply_items(body)
                try:
                    if snap_meta and int(snap_meta.get("strips", 0) or 0) > 0:
                        self._committed_agent_meta = self._chat_replace_markups(snap_meta, items, follow=True)
                    else:
                        self._committed_agent_meta = self._chat_write_markups(items, follow=True)
                except Exception:
                    # Fallback: plain log write path already claimed — best-effort
                    try:
                        w = self._chat_write
                        w("")
                        self._write_agent_body(body, w, lamp="done")
                        w(self._agent_footer_markup())
                        w("")
                    except Exception:
                        pass
                    self._committed_agent_meta = None

            try:
                self._schedule_ui(_do)
            except Exception:
                try:
                    _do()
                except Exception:
                    pass

        def _upgrade_committed_agent_reply(self, text: str) -> None:
            """Replace the last green agent block with a longer final (no duplicate)."""
            body = (text or "").strip()
            if not body or self._is_user_echo(body):
                return
            self._last_agent_reply = body
            self._reply_flushed = True
            self._stream_buf = ""
            meta = getattr(self, "_committed_agent_meta", None)

            def _do() -> None:
                items = self._final_reply_items(body)
                try:
                    if meta and int(meta.get("strips", 0) or 0) > 0:
                        self._committed_agent_meta = self._chat_replace_markups(meta, items, follow=True)
                    else:
                        # No strip handle — skip second paint rather than duplicate
                        self._committed_agent_meta = meta
                except Exception:
                    self._committed_agent_meta = meta

            try:
                self._schedule_ui(_do)
            except Exception:
                try:
                    _do()
                except Exception:
                    pass

        def _flush_reply_to_log(self) -> None:
            """Commit buffered / live-streamed reply into the transcript once."""
            if self._reply_flushed:
                return
            buf = (self._stream_buf or "").strip()
            if not buf:
                return
            if self._is_user_echo(buf):
                self._stream_buf = ""
                self._reset_live_reply_state()
                return
            if buf == (getattr(self, "_last_agent_reply", None) or ""):
                self._reply_flushed = True
                return
            # Prefer in-place finalize when stream already painted
            if getattr(self, "_live_reply", None) or getattr(self, "_live_reply_painted", ""):
                self._finalize_live_reply(buf)
                return
            # Do NOT set _last_agent_reply here — log_line claims it; pre-setting
            # made log_line's dedup treat this as a duplicate and drop the write.
            self.log_line(buf, style="agent")

        def _nudge_turn_out(self, piece: str) -> None:
            """Accumulate ↓ meter from the first thinking/reply token of the turn."""
            delta = self._approx_out_tokens(piece)
            if delta <= 0:
                return
            self._turn_out_target = int(getattr(self, "_turn_out_target", 0) or 0) + delta
            self._schedule_meter_paint()

        def log_stream(self, chunk: str) -> None:
            """Stream reply tokens into the chat log (coalesced in-place rewrite)."""
            if self._think_pending:
                self._flush_thinking_to_log()
            self._stream_buf += chunk
            # ↓ starts / continues from reply stream tokens
            self._nudge_turn_out(chunk or "")
            # Keep banner short — never mirror stream text (URLs/rest paths flicker at feet)
            if (self._wait_label or "") != t("wait_replying"):
                self.update_wait(t("wait_replying"))
            # Paint growing reply in chat (not wait-banner)
            self._paint_live_reply()

        def log_thinking(self, buf: str) -> None:
            """Stream thought token-by-token into #live-think (fixed height)."""
            incoming = buf or ""
            prev = getattr(self, "_think_buf_latest", "") or ""
            # New thought after a flush — allow the same text to be recorded again.
            # The bridge opens a fresh thought with an empty buffer while we are
            # idle (_think_pending False); reset the dedup key there, otherwise
            # a genuinely new thought whose text repeats the previous one would
            # be silently dropped by _flush_thinking_to_log.
            if not getattr(self, "_think_pending", False):
                self._last_flushed_think = ""
            # Bridge sends full buffer after its own merge; still harden here.
            # A cumulative snapshot is a *strictly longer* prefix of prev →
            # replace; anything else is the next delta chunk and must be
            # appended (a delta char that just happens to equal a prefix of
            # prev is NOT a stale snapshot).
            if not incoming:
                merged = prev
            elif not prev or (incoming.startswith(prev) and len(incoming) > len(prev)):
                merged = incoming
            elif prev.startswith(incoming) and len(incoming) > 1:
                merged = prev  # stale shorter multi-char snapshot
            else:
                merged = prev + incoming
            # ↓ from first thinking token — only count newly appended text
            if len(merged) > len(prev):
                self._nudge_turn_out(merged[len(prev) :])
            elif merged and not prev:
                self._nudge_turn_out(merged)
            self._think_buf_latest = merged
            self._think_pending = True
            if (self._wait_label or "") != t("wait_thinking"):
                self.update_wait(t("wait_thinking"))
            # Paint every chunk; _paint_live_think reads latest buf on the UI thread
            self._paint_live_think()

        def log_thinking_end(self, buf: str) -> None:
            """Thought stream closed — append Thinking once to the transcript."""
            prev = getattr(self, "_think_buf_latest", "") or ""
            if buf:
                incoming = buf
                if not prev or incoming.startswith(prev) or len(incoming) >= len(prev):
                    merged = incoming
                elif prev.startswith(incoming):
                    merged = prev
                else:
                    merged = prev + incoming
                if len(merged) > len(prev):
                    self._nudge_turn_out(merged[len(prev) :])
                self._think_buf_latest = merged
            if self._think_pending or (getattr(self, "_think_buf_latest", "") or "").strip():
                self._think_pending = True
                self._flush_thinking_to_log()
            if (self._wait_label or "") != t("wait_replying"):
                self.update_wait(t("wait_replying"))

        def _is_user_echo(self, text: str) -> bool:
            """True when ``text`` is just this turn's user message (WS multi-device echo)."""
            user = (getattr(self, "_turn_user_text", None) or "").strip()
            body = (text or "").strip()
            if not user or not body:
                return False
            return _same_reply(body, user)

        def on_agent_line(self, text: str) -> None:
            """Bridge on_line: finalize thought, then show assistant/tool line (once)."""
            if self._think_pending:
                self._flush_thinking_to_log()
            t = str(text) if text is not None else ""
            t_norm = t.strip()
            last = (getattr(self, "_last_agent_reply", None) or "").strip()
            streamed = (self._stream_buf or "").strip()

            if t.startswith(("  ⚙", "  ✓")):
                if not self._claim_tool_line(t):
                    return
                self.log_line(t, style="tool")
                return
            if t.startswith("[error]") or t.startswith("[ws]"):
                self.log_line(t, style="error")
                return
            if t.startswith("  ·"):
                self._log_verbose(t)
                return
            if t.startswith("["):
                self.log_line(t, style="system")
                return

            # Prefer final event text (complete) over stream buffer (may miss last
            # debounced WS chunk). Matches Web AIChatPage finalText = text || stream.
            final_text = t_norm or streamed
            if not final_text:
                return

            # Never paint the user's own turn text as an agent reply
            if self._is_user_echo(final_text):
                return

            # Live stream open (or buffer painted) → upgrade yellow block in place
            if getattr(self, "_live_reply", None) or (streamed and not getattr(self, "_reply_flushed", False)):
                self._finalize_live_reply(final_text)
                return

            # Drop stale incomplete stream so finally/flush cannot overwrite with a short copy
            self._stream_buf = ""

            if last and _same_reply(final_text, last):
                self._reply_flushed = True
                return

            # Incomplete green block already committed → upgrade in place (Web+TUI
            # fan-out often delivers a truncated flush before the full final).
            if last and _is_truncated_prefix(last, final_text):
                self._upgrade_committed_agent_reply(final_text)
                return

            # Incomplete stream already committed → still write the complete final
            # (exact-match dedup no longer drops it; log_line allows prefix upgrade).
            self.log_line(final_text, style="agent")

        @on(Input.Submitted, "#chat-input")
        def on_submit(self, event: Input.Submitted) -> None:
            """Submit chat line — IME-safe on Windows.

            On Windows cmd/Windows Terminal, pressing Enter to confirm an IME
            candidate often delivers ``Submitted`` with an *empty* value first;
            the CJK characters arrive a few ms later. Clearing the input
            immediately (old behavior) made 「你好」vanish with nothing sent.

            OpenCode-style fix: never clear on empty Enter; defer briefly so the
            committed text can land, then read ``Input.value`` again.
            """
            # Do NOT clear here — empty Submit must leave room for IME commit
            self._submit_gen += 1
            gen = self._submit_gen
            # Menus: Enter must confirm highlight immediately (no IME defer / empty submit)
            if (
                getattr(self, "_decision", None)
                or getattr(self, "_nav_active", False)
                or getattr(self, "_session_pick_active", False)
                or getattr(self, "_slash_items", None)
                or getattr(self, "_mention_active", False)
            ):
                self._complete_submit(gen)
                return
            hinted = (event.value or "").strip()
            if sys.platform == "win32":
                # Non-empty: short defer (trailing composing char).
                # Empty: longer defer (Enter was likely IME confirm).
                delay = 0.05 if (hinted or self.pending_media or self.pending_skill) else 0.15
                self.set_timer(delay, lambda g=gen: self._complete_submit(g))
            else:
                self._complete_submit(gen)

        def _complete_submit(self, gen: int) -> None:
            """Finish a deferred submit; ignore superseded / duplicate timers."""
            if gen != self._submit_gen:
                return
            # Invalidate further timers for this Enter
            self._submit_gen += 1
            try:
                inp = self.query_one("#chat-input", Input)
            except Exception:
                return

            # Decision picker: Enter confirms (or custom-answer capture / number shortcut)
            if getattr(self, "_await_custom_answer", False) and getattr(self, "_decision", None):
                line = (inp.value or "").rstrip("\n").strip()
                inp.value = ""
                if line:
                    self._submit_custom_decision(line)
                else:
                    self._focus_input()
                return
            if getattr(self, "_decision", None):
                line = (inp.value or "").rstrip("\n").strip()
                inp.value = ""
                if line.isdigit():
                    n = int(line)
                    rows = self._decision.rows
                    if 1 <= n <= len(rows):
                        self._decision.index = n - 1
                        self._decision_confirm()
                        return
                self._decision_confirm()
                return

            # Session / nav picker: Enter confirms highlighted item
            if getattr(self, "_nav_active", False):
                inp.value = ""
                self._nav_confirm()
                return
            if getattr(self, "_session_pick_active", False):
                inp.value = ""
                self._confirm_session_pick()
                return

            # @mention picker: Enter inserts @name
            if getattr(self, "_mention_active", False) and getattr(self, "_mention_items", None):
                self._confirm_mention()
                return

            # Slash command palette: Enter confirms highlight — unless the input
            # already has extra args beyond the choice (/group search 福州).
            if getattr(self, "_slash_items", None):
                idx = max(0, min(self._slash_index, len(self._slash_items) - 1))
                choice = self._slash_items[idx]
                typed = (inp.value or "").rstrip("\n")
                typed_s = typed.strip()
                choice_s = (choice or "").strip()
                # Prefer full typed line when it extends the palette choice with args
                if (
                    typed_s
                    and choice_s
                    and typed_s != choice_s
                    and (typed_s.startswith(choice_s + " ") or typed_s.startswith(choice_s + "\t"))
                ):
                    self._hide_slash_menu()
                    # fall through to normal submit with full typed line
                else:
                    inp.value = ""
                    self._hide_slash_menu()
                    if choice_s:
                        self._push_input_history(choice_s)
                        self._handle_slash(choice_s)
                    else:
                        self._focus_input()
                    return

            line = (inp.value or "").rstrip("\n").strip()
            if not line and not self.pending_media and not self.pending_skill:
                # Truly empty (or IME still open with nothing committed) — keep input
                self._focus_input()
                return

            # Connect-provider: capture API key (do not echo full key)
            if getattr(self, "_await_api_key", None):
                pending = self._await_api_key
                self._await_api_key = None
                inp.value = ""
                self._hide_slash_menu()
                self._hide_nav()
                self._finish_provider_with_key(pending, line)
                return

            # Model parameter edit capture
            if getattr(self, "_await_model_field", None):
                pending = self._await_model_field
                self._await_model_field = None
                inp.value = ""
                self._hide_slash_menu()
                self._finish_model_field_edit(pending, line)
                return

            # /login email or password capture
            if getattr(self, "_await_login", None):
                inp.value = ""
                self._hide_slash_menu()
                self._hide_nav()
                self._on_login_input(line)
                return

            if line:
                self._push_input_history(line)

            inp.value = ""
            self._hide_slash_menu()
            self._hide_session_picker()
            self._hide_nav()

            if line.startswith(("/", "+")):
                self._handle_slash(line)
                return

            skill_snap = dict(self.pending_skill) if self.pending_skill else None
            self.pending_skill = None
            display = self._compose_skill_display(line, skill_snap)
            if not display:
                display = format_pending_chips(self.pending_media) if self.pending_media else ""
            # Group: don't echo solo-style user bubble — WS new_message already shows [sender] …
            if self.mode == "group":
                ws = self._compose_skill_ws(line, skill_snap)
                self._send_plain(ws)
                return
            if display:
                self.log_line(display, style="user")
                self._turn_user_text = line.strip()

            # After Ctrl+C: merge held queue + this input into one turn.
            if getattr(self, "_hold_queue_after_stop", False):
                self._resume_after_stop(line, skill_snap)
                return

            # Solo FIFO: queue while a turn is in flight
            if self._sending:
                snap = list(self.pending_media)
                self.pending_media.clear()
                self._send_queue.append((line, snap, skill_snap))
                self.log_line(f"Queued (#{len(self._send_queue)}) — will send after current reply", style="system")
                self._refresh_chrome()
                self._focus_input()
                return

            self._follow_chat = True
            self._sending = True
            self._send_solo(line, skill=skill_snap)

        def _resume_after_stop(self, line: str, skill_snap: dict[str, str] | None) -> None:
            """Merge held queue + new input and send as a single agent turn."""
            parts: list[str] = []
            media_all: list = []
            skill = skill_snap
            while self._send_queue:
                item = self._send_queue.popleft()
                if len(item) == 3:
                    qline, qsnap, qskill = item
                else:
                    qline, qsnap = item[0], item[1]
                    qskill = None
                if (qline or "").strip():
                    parts.append(qline.strip())
                if qsnap:
                    media_all.extend(list(qsnap))
                if qskill:
                    skill = qskill
            if (line or "").strip():
                parts.append(line.strip())
            if self.pending_media:
                media_all.extend(list(self.pending_media))
                self.pending_media.clear()
            if skill_snap:
                skill = skill_snap

            self._hold_queue_after_stop = False
            if not parts and not media_all:
                self._refresh_chrome()
                self._focus_input()
                return

            merged = "\n\n".join(parts) if parts else "(attachment)"
            n = len(parts)
            self.log_line(
                f"Resuming — sending {n} message(s) as one turn" + (f" (+{len(media_all)} media)" if media_all else ""),
                style="system",
            )
            self.pending_media = list(media_all)

            # Stop turn may still be winding down — park merged payload for drain.
            if self._sending:
                self._send_queue.clear()
                self._send_queue.append((merged, list(media_all), skill))
                self.pending_media.clear()
                self._refresh_chrome()
                self._focus_input()
                return

            self._follow_chat = True
            self._sending = True
            self._refresh_chrome()
            self._send_solo(merged, skill=skill)

        def _handle_slash(self, line: str) -> None:
            import contextlib
            import io
            import shlex

            self.log_line(line, style="user")

            # Interactive nav: handle in TUI directly (never dump static tables into chat)
            raw = line.strip()
            try:
                parts = shlex.split(raw[1:]) if raw[:1] in "/+" else []
            except ValueError:
                parts = raw[1:].split()
            cmd_name = parts[0].lower() if parts else ""
            cmd_args = parts[1:] if parts else []
            nav_aliases = {
                "model": "model",
                "skill": "skill",
                "role": "role",
                "collab": "collab",
                "mcp": "mcp",
                "plugin": "plugin",
                "agentctl": "agent",
                "agents-ctl": "agent",
                "agent": "agent",
                "agents": "agent",
                "group": "group",
                "session": "sessions",
                "sessions": "sessions",
                "theme": "theme",
                "themes": "theme",
                "language": "language",
                "lang": "language",
                "locale": "language",
            }
            # Bare command or explicit list → interactive picker (not static tables)
            if cmd_name in nav_aliases and (not cmd_args or cmd_args[0] in ("list", "ls")):
                kind = nav_aliases[cmd_name]
                if kind == "sessions":
                    self._session_cmd("sessions", [])
                else:
                    self.open_nav(kind)
                self._focus_input()
                return
            # /agent <name> switch
            if cmd_name in ("agent", "agents") and cmd_args and cmd_args[0] not in ("list", "ls"):
                self._switch_agent(cmd_args[0])
                self._focus_input()
                return
            # /clear — wipe RichLog (ANSI clear from dispatch is useless in Textual)
            if cmd_name == "clear":
                self.clear_chat_view(note=t("screen_cleared"))
                return
            # /debug — toggle verbose boot/system logs
            if cmd_name == "debug":
                if cmd_args and cmd_args[0].lower() in ("off", "0", "false"):
                    self._debug_mode = False
                elif cmd_args and cmd_args[0].lower() in ("on", "1", "true"):
                    self._debug_mode = True
                else:
                    self._debug_mode = not self._debug_mode
                self.log_line(t("debug_on") if self._debug_mode else t("debug_off"), style="system")
                self._focus_input()
                return

            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    cont = dispatch_slash(line, self._ctx())
            except Exception as e:
                self.log_line(f"command error: {e}", style="error")
                cont = True
            for out_line in buf.getvalue().splitlines():
                if out_line.strip():
                    self.log_line(out_line, style="system")
            self._refresh_chrome()
            if not cont:
                self.exit()
            else:
                self._focus_input()

        def action_quit(self) -> None:
            self._close_bridges()
            self.exit()

        def action_cancel_or_clear(self) -> None:
            """Ctrl+C: copy / clear / stop; press again quickly to quit (Claude Code style)."""
            import time

            now = time.monotonic()
            if self._ctrl_c_at and (now - self._ctrl_c_at) < 2.0:
                self.action_quit()
                return
            self._ctrl_c_at = now

            # Prefer copy when user selected text with the mouse
            try:
                selected = self.screen.get_selected_text()
            except Exception:
                selected = None
            if selected:
                self.copy_to_clipboard(selected)
                self.notify(t("ctrl_c_copied"), timeout=2)
                return

            inp = self.query_one("#chat-input", Input)
            if inp.value:
                inp.value = ""
                self._hide_slash_menu()
                self.notify(t("ctrl_c_cleared"), timeout=2)
                return

            if self.bridge and (
                self._sending or getattr(self, "_turn_started_at", None) is not None or bool(self._send_queue)
            ):
                nq = len(self._send_queue)
                try:
                    # Agent-wide stop so parallel/tool work also aborts.
                    self.bridge.send_command("stop_task", {"all": True})
                    self.log_line("[system] stop requested (all)", style="system")
                except Exception:
                    pass
                # Keep queue for the next Enter — merge into one turn.
                self._hold_queue_after_stop = True
                if nq:
                    self.notify(
                        t("ctrl_c_stopped_held").format(n=nq),
                        timeout=3,
                    )
                else:
                    self.notify(t("ctrl_c_stopped"), timeout=2)
                self._refresh_chrome()
                return

            self.notify(t("ctrl_c_again"), timeout=2)

        def action_hide_slash(self) -> None:
            from textual.actions import SkipAction

            # Cancel Connect-provider API key capture
            if getattr(self, "_await_api_key", None):
                self._await_api_key = None
                self.log_line("API key capture cancelled", style="system")
                self._refresh_chrome()
                self._focus_input()
                return
            if getattr(self, "_await_model_field", None):
                self._await_model_field = None
                self.log_line("Parameter edit cancelled", style="system")
                self._refresh_chrome()
                self._focus_input()
                return
            if getattr(self, "_await_login", None):
                self._cancel_login()
                return
            # Decision card: Esc = dismiss / deny / ignore
            if getattr(self, "_await_custom_answer", False):
                self._await_custom_answer = False
                self._paint_decision()
                self._refresh_chrome()
                self._focus_input()
                return
            if getattr(self, "_decision", None):
                self._decision_dismiss()
                return
            # Close Ctrl+X live sideview
            if getattr(self, "_live_side_open", False):
                self._close_live_side()
                return
            if self._nav_active:
                self._nav_back_or_close()
                self._focus_input()
                return
            if self._session_pick_active:
                self._hide_session_picker()
                self._focus_input()
                return
            if getattr(self, "_mention_active", False):
                self._hide_mention_menu()
                self._focus_input()
                return
            if not self._slash_items:
                raise SkipAction()
            self._hide_slash_menu()
            self._focus_input()

        def action_accept_slash(self) -> None:
            """Tab: autocomplete highlighted slash/mention text; idle → Plan/Build."""
            from textual.actions import SkipAction

            # Don't steal Tab from Textual Ctrl+P command palette
            if self._command_palette_open():
                raise SkipAction()
            if getattr(self, "_decision", None):
                self._decision_confirm()
                return
            if self._nav_active:
                self._nav_confirm()
                return
            if self._session_pick_active:
                self._confirm_session_pick()
                return
            if getattr(self, "_mention_active", False) and getattr(self, "_mention_items", None):
                self._confirm_mention()
                return

            # Slash palette: Tab only fills the highlighted text (never executes)
            if self._slash_items:
                idx = max(0, min(self._slash_index, len(self._slash_items) - 1))
                choice = (self._slash_items[idx] or "").strip()
                if choice:
                    inp = self.query_one("#chat-input", Input)
                    inp.value = choice + " "
                    inp.cursor_position = len(inp.value)
                    self._hide_slash_menu()
                    self._refresh_slash_menu(inp.value)
                    self._focus_input()
                return

            inp = self.query_one("#chat-input", Input)
            value = inp.value or ""
            # Mid-/ command without open palette: complete first match into input
            if value.startswith(("/", "+")):
                matches = slash_completions(value, limit=1)
                if matches:
                    choice = matches[0][0]
                    inp.value = choice + " "
                    inp.cursor_position = len(inp.value)
                    self._refresh_slash_menu(inp.value)
                    self._focus_input()
                    return

            # No options → OpenCode-style Plan ↔ Build
            self._toggle_agent_mode()

        def _toggle_agent_mode(self) -> None:
            next_mode = "plan" if (getattr(self, "_agent_mode", "build") or "build") == "build" else "build"
            self._apply_agent_mode(next_mode, notify=True, send=True)

        def _apply_agent_mode(self, mode: str, *, notify: bool = False, send: bool = True) -> None:
            mode_n = str(mode or "").strip().lower()
            if mode_n not in ("plan", "build"):
                return
            self._agent_mode = mode_n
            if send and self.bridge and getattr(self.bridge, "is_open", False):
                try:
                    self.bridge.send_command("set_agent_mode", {"mode": mode_n})
                except Exception as e:
                    self.log_line(f"mode switch failed: {e}", style="error")
                    self._refresh_chrome()
                    return
            if notify:
                label = "Plan" if mode_n == "plan" else "Build"
                self.log_line(f"Mode → {label}", style="system")
            self._refresh_chrome()
            if notify or send:
                self._focus_input()

        # ── Decision picker (propose_options / mode_switch / group cards) ──

        def action_slash_up(self) -> None:
            from textual.actions import SkipAction

            # Priority bindings steal arrows from Ctrl+P palette — yield when it is open
            if self._command_palette_open():
                raise SkipAction()
            if (
                getattr(self, "_live_side_open", False)
                and not self._nav_active
                and not self._slash_items
                and not self._decision
                and not getattr(self, "_mention_active", False)
            ):
                keys = self._side_hub.list_keys()
                if len(keys) > 1:
                    cur = self._live_side_key or keys[0]
                    try:
                        i = keys.index(cur)
                    except ValueError:
                        i = 0
                    self._live_side_key = keys[(i - 1) % len(keys)]
                    self._paint_live_side()
                    return
            if self._decision and not self._await_custom_answer:
                self._decision.index = max(0, self._decision.index - 1)
                self._paint_decision()
                return
            if self._nav_active:
                self._nav_index = max(0, self._nav_index - 1)
                self._paint_nav()
                return
            if self._session_pick_active:
                self._session_pick_index = max(0, self._session_pick_index - 1)
                self._paint_session_picker()
                return
            if getattr(self, "_mention_active", False) and self._mention_items:
                self._mention_index = max(0, self._mention_index - 1)
                self._paint_mention_menu()
                return
            if self._slash_items:
                self._slash_index = max(0, self._slash_index - 1)
                self._paint_slash_menu()
                return
            # No menu → bash-style previous input
            if self._history_up():
                return
            raise SkipAction()

        def action_slash_down(self) -> None:
            from textual.actions import SkipAction

            if self._command_palette_open():
                raise SkipAction()
            if (
                getattr(self, "_live_side_open", False)
                and not self._nav_active
                and not self._slash_items
                and not self._decision
                and not getattr(self, "_mention_active", False)
            ):
                keys = self._side_hub.list_keys()
                if len(keys) > 1:
                    cur = self._live_side_key or keys[0]
                    try:
                        i = keys.index(cur)
                    except ValueError:
                        i = 0
                    self._live_side_key = keys[(i + 1) % len(keys)]
                    self._paint_live_side()
                    return
            if self._decision and not self._await_custom_answer:
                rows = self._decision.rows
                self._decision.index = min(len(rows) - 1, self._decision.index + 1)
                self._paint_decision()
                return
            if self._nav_active:
                items = self._nav_current_items()
                self._nav_index = min(len(items) - 1, self._nav_index + 1)
                self._paint_nav()
                return
            if self._session_pick_active:
                self._session_pick_index = min(len(self._session_pick_items) - 1, self._session_pick_index + 1)
                self._paint_session_picker()
                return
            if getattr(self, "_mention_active", False) and getattr(self, "_mention_items", None):
                self._mention_index = min(len(self._mention_items) - 1, self._mention_index + 1)
                self._paint_mention_menu()
                return
            if self._slash_items:
                self._slash_index = min(len(self._slash_items) - 1, self._slash_index + 1)
                self._paint_slash_menu()
                return
            # No menu → bash-style next input / restore draft
            if self._history_down():
                return
            raise SkipAction()

        def _input_history_path(self) -> str:
            return os.path.join(os.path.expanduser("~"), ".opensquad", "tui_input_history")

        def _load_input_history(self) -> None:
            path = self._input_history_path()
            try:
                if os.path.isfile(path):
                    with open(path, encoding="utf-8") as f:
                        lines = [ln.rstrip("\n") for ln in f.readlines()]
                    self._input_history = [ln for ln in lines if ln.strip()][-500:]
            except Exception:
                self._input_history = []

        def _persist_input_history(self) -> None:
            path = self._input_history_path()
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write("\n".join(self._input_history[-500:]))
                    if self._input_history:
                        f.write("\n")
            except Exception:
                pass

        def _push_input_history(self, line: str) -> None:
            """Record a submitted line (bash history semantics)."""
            text = (line or "").rstrip("\n")
            if not text.strip():
                return
            # Never store secrets from capture modes
            if (
                getattr(self, "_await_api_key", None)
                or getattr(self, "_await_model_field", None)
                or getattr(self, "_await_login", None)
            ):
                return
            hist = self._input_history
            if hist and hist[-1] == text:
                self._input_hist_index = None
                self._input_hist_draft = ""
                return
            hist.append(text)
            if len(hist) > 500:
                del hist[:-500]
            self._input_hist_index = None
            self._input_hist_draft = ""
            self._persist_input_history()

        def _history_up(self) -> bool:
            """↑ previous entry. Returns True if handled."""
            hist = getattr(self, "_input_history", None) or []
            if not hist:
                return False
            try:
                inp = self.query_one("#chat-input", Input)
            except Exception:
                return False
            if self._input_hist_index is None:
                self._input_hist_draft = inp.value or ""
                self._input_hist_index = len(hist) - 1
            else:
                self._input_hist_index = max(0, self._input_hist_index - 1)
            inp.value = hist[self._input_hist_index]
            inp.cursor_position = len(inp.value or "")
            return True

        def _history_down(self) -> bool:
            """↓ newer entry / restore draft. Returns True if handled."""
            hist = getattr(self, "_input_history", None) or []
            if self._input_hist_index is None:
                return False
            try:
                inp = self.query_one("#chat-input", Input)
            except Exception:
                return False
            if self._input_hist_index >= len(hist) - 1:
                self._input_hist_index = None
                inp.value = self._input_hist_draft
                inp.cursor_position = len(inp.value or "")
                return True
            self._input_hist_index += 1
            inp.value = hist[self._input_hist_index]
            inp.cursor_position = len(inp.value or "")
            return True

        def _sync_prompt_dock_menu(self) -> None:
            """Grow #prompt-dock only while a menu is visible (avoids Win CMD thrash)."""
            try:
                dock = self.query_one("#prompt-dock")
                menu = self.query_one("#slash-menu", Static)
                open_ = (
                    "visible" in menu.classes
                    or bool(getattr(self, "_slash_visible", False))
                    or bool(getattr(self, "_mention_active", False))
                    or bool(getattr(self, "_decision", None))
                    or bool(getattr(self, "_nav_active", False))
                    or bool(getattr(self, "_session_pick_active", False))
                )
                dock.set_class(open_, "menu-open")
            except Exception:
                pass

        def _hide_slash_menu(self) -> None:
            if not self._slash_items and not getattr(self, "_slash_visible", False):
                return
            self._slash_items = []
            self._slash_helps = []
            self._slash_index = 0
            self._slash_visible = False
            try:
                if not getattr(self, "_mention_active", False) and not getattr(self, "_decision", None):
                    menu = self.query_one("#slash-menu", Static)
                    menu.update("")
                    menu.remove_class("visible")
            except Exception:
                pass
            self._sync_prompt_dock_menu()

        def _active_mention_query(self, value: str) -> str | None:
            """Return filter text after trailing @… if an @-mention is being typed."""
            if self.mode != "group" or not self.group:
                return None
            text = value or ""
            # Match @token at end (CJK names allowed); stop after whitespace
            m = re.search(r"(?:^|[\s])@([\w\u4e00-\u9fff\-]*)$", text)
            if not m:
                return None
            return m.group(1)

        def _ensure_group_members(self, *, force: bool = False) -> list[dict[str, str]]:
            if not force and self._group_members:
                return self._group_members
            if not self.group or not self.group.group_id:
                self._group_members = []
                return []
            try:
                data = self.client.get(f"/api/groups/{self.group.group_id}")
                members = (data or {}).get("members") if isinstance(data, dict) else []
                out: list[dict[str, str]] = []
                if isinstance(members, list):
                    for m in members:
                        if not isinstance(m, dict):
                            continue
                        name = str(m.get("name") or "").strip()
                        mid = str(m.get("id") or "").strip()
                        if not name:
                            continue
                        out.append(
                            {
                                "id": mid,
                                "name": name,
                                "status": str(m.get("status") or ""),
                            }
                        )
                self._group_members = out
                # Keep GroupBridge id→name map in sync for [Name] prefixes
                if self.group is not None:
                    names = {m["id"]: m["name"] for m in out if m.get("id") and m.get("name")}
                    existing = getattr(self.group, "_member_names", None)
                    if isinstance(existing, dict):
                        existing.update(names)
                    else:
                        self.group._member_names = names
            except Exception as e:
                self.log_line(f"[group] members: {e}", style="error")
                self._group_members = []
            return self._group_members

        def _refresh_mention_menu(self, value: str) -> None:
            if getattr(self, "_decision", None) or self._nav_active or self._session_pick_active:
                return
            if value.startswith(("/", "+")):
                self._hide_mention_menu()
                return
            query = self._active_mention_query(value)
            if query is None:
                self._hide_mention_menu()
                return
            members = self._ensure_group_members()
            q = (query or "").lower()
            items: list[dict[str, str]] = []
            for m in members:
                name = m.get("name") or ""
                if not q or q in name.lower() or q in (m.get("id") or "").lower():
                    items.append(m)
            if not items:
                # Still show empty state so user knows @ was recognized
                self._mention_items = []
                self._mention_index = 0
                self._mention_active = True
                self._paint_mention_menu()
                return
            prev = None
            if self._mention_items and 0 <= self._mention_index < len(self._mention_items):
                prev = self._mention_items[self._mention_index].get("name")
            self._mention_items = items[:40]
            self._mention_active = True
            if prev:
                for i, it in enumerate(self._mention_items):
                    if it.get("name") == prev:
                        self._mention_index = i
                        break
                else:
                    self._mention_index = 0
            else:
                self._mention_index = 0
            self._hide_slash_menu()
            self._paint_mention_menu()

        def _paint_mention_menu(self) -> None:
            try:
                menu = self.query_one("#slash-menu", Static)
            except Exception:
                return
            if not self._mention_active:
                return
            hi = self._theme_hex("primary", "#58a6ff")
            fg = self._theme_hex("foreground", "#e6edf3")
            lines: list[str] = [f"[bold {fg}] {t('mention_menu_title')}[/]  [dim]esc[/]"]
            items = self._mention_items
            if not items:
                lines.append("[dim]  (no matching members)[/]")
            else:
                n = len(items)
                idx = max(0, min(self._mention_index, n - 1))
                self._mention_index = idx
                window = 8
                start = max(0, idx - window // 2)
                end = min(n, start + window)
                start = max(0, end - window)
                if start > 0:
                    lines.append(f"[dim]  ↑ {start} more[/]")
                for i in range(start, end):
                    m = items[i]
                    name = str(m.get("name") or "")
                    st = str(m.get("status") or "")
                    row = f" @{name:<20} {st}"
                    if i == idx:
                        lines.append(f"[bold black on {hi}]{self._escape_markup(row)}[/]")
                    else:
                        lines.append(f"[{fg}]{self._escape_markup(row)}[/]")
                if end < n:
                    lines.append(f"[dim]  ↓ {n - end} more[/]")
            lines.append(f"[dim]  {t('hint_mention_menu')}[/]")
            menu.update("\n".join(lines))
            menu.add_class("visible")
            self._sync_prompt_dock_menu()

        def _hide_mention_menu(self) -> None:
            was = self._mention_active or bool(self._mention_items)
            self._mention_active = False
            self._mention_items = []
            self._mention_index = 0
            if not was:
                return
            try:
                if not self._slash_items and not getattr(self, "_decision", None):
                    menu = self.query_one("#slash-menu", Static)
                    menu.update("")
                    menu.remove_class("visible")
            except Exception:
                pass
            self._sync_prompt_dock_menu()

        def _confirm_mention(self) -> None:
            if not self._mention_active or not self._mention_items:
                self._hide_mention_menu()
                return
            idx = max(0, min(self._mention_index, len(self._mention_items) - 1))
            name = str(self._mention_items[idx].get("name") or "").strip()
            if not name:
                self._hide_mention_menu()
                return
            try:
                inp = self.query_one("#chat-input", Input)
            except Exception:
                self._hide_mention_menu()
                return
            value = inp.value or ""
            m = re.search(r"(?:^|[\s])@([\w\u4e00-\u9fff\-]*)$", value)
            if m:
                start = m.start(1) - 1  # include @
                new_val = value[:start] + f"@{name} "
            else:
                new_val = value.rstrip() + f" @{name} "
            inp.value = new_val
            inp.cursor_position = len(new_val)
            self._hide_mention_menu()
            self._focus_input()

        def _paint_slash_menu(self) -> None:
            """OpenCode-style floating command list with highlight bar + scroll window."""
            menu = self.query_one("#slash-menu", Static)
            if not self._slash_items:
                menu.update("")
                menu.remove_class("visible")
                self._slash_visible = False
                return
            n = len(self._slash_items)
            idx = max(0, min(self._slash_index, n - 1))
            self._slash_index = idx
            # Keep a fixed window so ↑↓ can reach every match (menu max-height ~14)
            window = 10
            start = max(0, idx - window // 2)
            end = min(n, start + window)
            start = max(0, end - window)
            lines: list[str] = []
            if start > 0:
                lines.append(f"[dim]  ↑ {start} more[/]")
            for i in range(start, end):
                text = self._slash_items[i]
                help_text = ""
                if i < len(self._slash_helps):
                    help_text = self._slash_helps[i]
                # pad command column
                cmd = text.ljust(18)
                desc = (help_text[:42] + "…") if len(help_text) > 42 else help_text
                row = f" {cmd} {desc}"
                if i == idx:
                    # amber highlight bar (OpenCode-like)
                    lines.append(f"[bold black on #f59e0b]{self._escape_markup(row)}[/]")
                else:
                    lines.append(f"[#c9d1d9]{self._escape_markup(row)}[/]")
            if end < n:
                lines.append(f"[dim]  ↓ {n - end} more[/]")
            lines.append(f"[dim]  {t('hint_slash_menu')}[/]")
            menu.update("\n".join(lines))
            menu.add_class("visible")
            self._slash_visible = True
            self._sync_prompt_dock_menu()

        def _refresh_slash_menu(self, value: str) -> None:
            # Decision overlay owns #slash-menu — never clobber it with command palette
            if getattr(self, "_decision", None):
                return
            if not value.startswith(("/", "+")):
                self._hide_slash_menu()
                return
            # Load all matches — paint window scrolls; do not truncate to first page
            matches = slash_completions(value, limit=64)
            new_items = [m[0] for m in matches]
            new_helps = [m[1] for m in matches]
            if not new_items:
                self._hide_slash_menu()
                return
            if new_items == self._slash_items and new_helps == self._slash_helps:
                return
            prev = (
                self._slash_items[self._slash_index]
                if self._slash_items and 0 <= self._slash_index < len(self._slash_items)
                else None
            )
            self._slash_items = new_items
            self._slash_helps = new_helps
            if prev in new_items:
                self._slash_index = new_items.index(prev)
            else:
                self._slash_index = 0
            self._paint_slash_menu()

        @on(Input.Changed, "#chat-input")
        def on_input_changed(self, event: Input.Changed) -> None:
            val = event.value or ""
            if getattr(self, "_decision", None):
                return
            if self._nav_active or self._session_pick_active:
                if val.startswith(("/", "+")):
                    self._hide_nav()
                    self._hide_session_picker()
                    self._refresh_slash_menu(val)
                return
            if val.startswith(("/", "+")):
                self._hide_mention_menu()
                self._refresh_slash_menu(val)
                return
            self._hide_slash_menu()
            self._refresh_mention_menu(val)

        def action_clear_log(self) -> None:
            self.clear_chat_view(note=t("screen_cleared"))

        def clear_chat_view(self, *, note: str | None = None) -> None:
            """Clear the visible chat transcript (RichLog), keep chrome/status."""

            def _do() -> None:
                try:
                    self.query_one("#chat-log", RichLog).clear()
                except Exception:
                    pass
                self._stream_buf = ""
                self._think_buf_latest = ""
                self._think_pending = False
                self._open_tools = {}
                self._open_tool_keys = set()
                self._done_tool_keys = set()
                self._last_tool_result_key = ""
                self._last_flushed_think = ""
                self._detail_blocks = []
                self._tool_detail_pending = {}
                self._tool_args_by_key = {}
                self._reply_flushed = False
                self._reset_live_reply_state()
                self._stop_shimmer_timer()
                self._stop_turn_meter_timer()
                self._last_agent_reply = ""
                self._committed_agent_meta = None
                self._turn_user_text = ""
                self._sending = False
                try:
                    self._hide_live_think()
                except Exception:
                    pass
                try:
                    self.end_wait()
                except Exception:
                    pass
                msg = (note or t("screen_cleared")).strip()
                if msg:
                    self._chat_write(
                        f"[dim]{self._escape_markup(msg)}[/]",
                        follow=True,
                    )
                    self._chat_write("", follow=True)
                self._focus_input()

            try:
                self.call_from_thread(_do)
            except Exception:
                try:
                    _do()
                except Exception:
                    pass

        def _on_context_compressed(self) -> None:
            """After agent context compression: wipe old transcript for a fresh view."""
            import time

            now = time.monotonic()
            # summary_generated + history_sync(reason=compression) often arrive together
            if self._compress_clear_at and (now - self._compress_clear_at) < 2.0:
                return
            self._compress_clear_at = now
            self.clear_chat_view(note="Context compressed — view cleared. Continue chatting (summary kept in memory).")

        def action_paste_image(self) -> None:
            try:
                media = attach_from_clipboard()
            except Exception as e:
                self.log_line(f"[attach] {e}", style="error")
                return
            if not media:
                self.log_line("[attach] No image on clipboard. Use /image <path> or Ctrl+Shift+V", style="system")
                return
            self.pending_media.append(media)
            self.log_line(f"attached {chip_label(media)}", style="system")
            self._refresh_chrome()

        # ── ctx for slash_dispatch ────────────────────────────────────

        def _ctx(self) -> dict[str, Any]:
            return {
                "client": self.client,
                "agent": self.agent,
                "gateway": self.client.gateway_url,
                "mode": self.mode,
                "group": self.group,
                "shell": self,
                "switch_agent": self._switch_agent,
                "start_agent": self.start_agent,
                "session_cmd": self._session_cmd,
                "open_nav": self.open_nav,
                "refresh_client": self._refresh_client,
                "tui_login": self._start_login,
                "join_group": self.join_group,
                "leave_group": self.leave_group,
                "attach_image": self.attach_image,
                "detach_media": self.detach_media,
                "set_muted": self.set_muted,
                "history": self.show_history,
                "group_members": self.show_group_members,
                "group_search": self.search_group_messages,
                "group_more": self.load_more_group_history,
                "approve": self.approve,
                "reject": self.reject,
                "choose": self.choose,
                "clear_screen": lambda: self.clear_chat_view(note=t("screen_cleared")),
                "apply_theme": self.apply_theme,
                "apply_locale": self.apply_locale,
            }

        # ── send ──────────────────────────────────────────────────────

        @staticmethod
        def _compose_skill_ws(line: str, skill: dict[str, str] | None) -> str:
            """Wire content for agent: optional <user_send_skill> prefix (same as Web)."""
            text = (line or "").strip()
            if not skill:
                return text
            dir_name = str(skill.get("dir") or "").strip()
            if not dir_name:
                return text
            tag = f"<user_send_skill>{dir_name}</user_send_skill>"
            return f"{tag}\n\n{text}" if text else tag

        @staticmethod
        def _compose_skill_display(line: str, skill: dict[str, str] | None) -> str:
            """User-visible line: /skillName + optional text (no XML)."""
            text = (line or "").strip()
            if not skill:
                return text
            dir_name = str(skill.get("dir") or "").strip()
            if not dir_name:
                return text
            return f"/{dir_name} {text}".strip() if text else f"/{dir_name}"

        def _send_plain(self, line: str) -> None:
            if not self.client.token:
                self.log_line("Login first: /login", style="error")
                return
            if self.mode == "group" and self.group and line.isdigit():
                self._group_numeric_reply_work(line)
                return
            if self.mode == "group":
                self._send_group(line)
            else:
                self._send_solo(line)

        @work(thread=True, group="group-action")
        def _group_numeric_reply_work(self, line: str) -> None:
            if not self.group:
                return
            try:
                if self.group.resolve_numeric_reply(line):
                    return
            except Exception as e:
                self.log_line(str(e), style="error")
                return
            # Not a card shortcut — fall through as a normal group message
            self._send_group(line)

        @work(thread=True, group="chat-send")
        def _send_solo(self, line: str, skill: dict[str, str] | None = None) -> None:
            from opensquad.cli.commands.chat_cmd import AgentWsError

            if not self.agent:
                self.log_line("Select an agent: /agent <name> or /start <name>", style="error")
                self._sending = False
                return
            self._stream_buf = ""
            self._reply_flushed = False
            self._reset_live_reply_state()
            self._stop_shimmer_timer()
            self._stop_turn_meter_timer()
            self._last_agent_reply = ""
            self._committed_agent_meta = None
            self._turn_user_text = (line or "").strip() or getattr(self, "_turn_user_text", "")
            self._think_buf_latest = ""
            self._think_pending = False
            self._open_tools = {}
            self._last_tool_result_key = ""
            self._think_gen = int(getattr(self, "_think_gen", 0) or 0) + 1
            try:
                self.call_from_thread(self._hide_live_think)
            except Exception:
                pass
            already = bool(self.bridge and getattr(self.bridge, "is_open", False))
            self._begin_turn_meter()
            self.begin_wait(t("wait_connecting") if not already else t("wait_thinking"))
            try:
                if not already:
                    if not self._ensure_agent_connected(self.agent):
                        self.log_line("Agent not ready — try /start then send again", style="error")
                        return
                if not self.bridge:
                    return
                if not self._ensure_new_session():
                    self.log_line("Session not ready — try /new or /start", style="error")
                    return
                images: list[str] = []
                attachments: list[dict] = []
                chips = []
                for media in list(self.pending_media):
                    try:
                        upload_for_agent(self.client, self.agent, media)
                        chips.append(chip_label(media))
                        if media.kind == "image" and media.uploaded_path:
                            images.append(media.uploaded_path)
                        else:
                            attachments.append(
                                {
                                    "name": media.label,
                                    "url": media.uploaded_url or "",
                                    "size": media.size,
                                    "type": media.kind,
                                }
                            )
                    except Exception as e:
                        self.log_line(f"upload failed {media.label}: {e}", style="error")
                self.pending_media.clear()
                # Prefer explicit skill snap from submit/queue; fall back to pending
                skill_snap = skill if skill is not None else (dict(self.pending_skill) if self.pending_skill else None)
                if skill is None and self.pending_skill:
                    self.pending_skill = None
                ws_content = self._compose_skill_ws(line, skill_snap)
                self.call_from_thread(self._refresh_chrome)
                if chips:
                    self.log_line(f"sending with: {' '.join(chips)}", style="system")
                if skill_snap and skill_snap.get("dir"):
                    self.log_line(f"skill /{skill_snap['dir']}", style="system")
                self.update_wait(t("wait_thinking"))
                try:
                    self.bridge.turn_reset()
                    self.bridge.send_chat(
                        ws_content,
                        images=images or None,
                        attachments=attachments or None,
                    )
                    self.bridge.wait_turn(timeout=600)
                except AgentWsError as e:
                    self.log_line(str(e), style="error")
                except Exception as e:
                    self.log_line(f"send failed: {e}", style="error")
            finally:
                if self._think_pending:
                    self._flush_thinking_to_log()
                if (self._stream_buf or "").strip() and not self._reply_flushed:
                    self._flush_reply_to_log()
                self._sending = False
                self._stream_buf = ""
                self._reply_flushed = False
                self._reset_live_reply_state()
                self._stop_shimmer_timer()
                self._stop_turn_meter_timer()
                # keep _last_agent_reply so a late on_line cannot reprint the same turn
                self._end_turn_meter()
                self.end_wait()
                self.call_from_thread(self._focus_input)
                self.call_from_thread(self._drain_send_queue)

        def _drain_send_queue(self) -> None:
            if self._sending or self.mode == "group":
                return
            # Ctrl+C hold: wait for the next user Enter to merge+resume.
            if getattr(self, "_hold_queue_after_stop", False):
                self._refresh_chrome()
                return
            if not self._send_queue:
                self._refresh_chrome()
                return
            item = self._send_queue.popleft()
            if len(item) == 3:
                line, snap, skill_snap = item
            else:
                line, snap = item[0], item[1]
                skill_snap = None
            self.pending_media = list(snap)
            self.log_line(f"Dequeued — sending next ({len(self._send_queue)} left)", style="system")
            self._follow_chat = True
            self._sending = True
            self._refresh_chrome()
            self._send_solo(line, skill=skill_snap)

        @work(thread=True)
        def _send_group(self, line: str) -> None:
            if not self.group or not self.group.group_id:
                self.log_line("Join a group: /group join <id>", style="error")
                return
            atts = []
            chips = []
            for media in list(self.pending_media):
                try:
                    atts.append(upload_for_group(self.client, media))
                    chips.append(chip_label(media))
                except Exception as e:
                    self.log_line(f"upload failed: {e}", style="error")
            self.pending_media.clear()
            self.call_from_thread(self._refresh_chrome)
            body: dict[str, Any] = {
                "content": line or "(attachment)",
                "group_id": self.group.group_id,
                "type": "TEXT",
            }
            if atts:
                body["attachments"] = atts
                self.log_line(f"sending with: {' '.join(chips)}", style="system")
            try:
                self.client.post(f"/api/groups/{self.group.group_id}/messages", body)
            except Exception as e:
                self.log_line(f"group send failed: {e}", style="error")

        # ── agent / group ─────────────────────────────────────────────

        def start_agent(self, name: str | None = None) -> None:
            """Slash /start — boot process + connect (non-blocking)."""
            target = name or self.agent
            if not target:
                self.log_line("Usage: /start <agent>", style="error")
                return
            self._log_verbose(f"Starting agent '{target}'…")
            self._bootstrap_agent(target, then_new=False)

        @work(thread=True, exclusive=True, group="agent-boot")
        def _bootstrap_agent(self, name: str, then_new: bool = False) -> None:
            # begin_wait may already be set by on_mount; keep a single wait gen
            if not getattr(self, "_wait_label", None):
                self.begin_wait(t("wait_preparing"))
            try:
                ok = self._ensure_agent_connected(name)
                if ok:
                    self._log_verbose(f"Connected to agent '{name}' (solo)")
                    if then_new:
                        self._needs_new_session = True
                        self._ensure_new_session()
                else:
                    self.log_line(
                        f"Agent '{name}' not ready yet. Retry /start or wait and /new",
                        style="error",
                    )
            finally:
                self.end_wait()

                def _after() -> None:
                    self._sync_status_from_agent(name)
                    self._post_welcome_card()
                    self._refresh_chrome()
                    self._focus_input()

                try:
                    self.call_from_thread(_after)
                except Exception:
                    try:
                        _after()
                    except Exception:
                        pass

        def _lookup_agent(self, name: str) -> dict[str, Any] | None:
            try:
                data = self.client.admin_get("agents")
            except Exception:
                return None
            for a in data.get("agents") or []:
                if a.get("dir_name") == name or a.get("agent_id") == name:
                    return a
            return None

        def _agent_is_ready(self, name: str) -> bool:
            info = self._lookup_agent(name)
            return bool(info and info.get("ready"))

        def _start_agent_process(self, name: str, *, force_restart: bool = False) -> None:
            try:
                if force_restart:
                    self.update_wait(t("wait_boot_agent", name=name) + " (restart)")
                    self.client.admin_post(f"agents/{name}/restart")
                else:
                    self.update_wait(t("wait_boot_agent", name=name))
                    self.client.admin_post(f"agents/{name}/start")
            except Exception as e:
                msg = str(e).lower()
                if "already" not in msg and "running" not in msg:
                    self.log_line(f"agent start: {e}", style="system")

        def _wait_agent_ready(self, name: str, timeout: float = 90.0) -> bool:
            import time

            deadline = time.time() + timeout
            started = time.time()
            delay = 0.12
            while time.time() < deadline:
                info = self._lookup_agent(name)
                if info and info.get("ready"):
                    return True
                status = (info or {}).get("process_status") or "?"
                reg = (info or {}).get("registry_status") or "offline"
                elapsed = int(time.time() - started)
                # Compact human progress — avoid raw proc/registry spam in banner
                st = f"{status}/{reg}"
                # Win CMD: at most ~1Hz banner changes (elapsed string)
                if sys.platform != "win32" or elapsed != getattr(self, "_boot_ready_last_s", -1):
                    self._boot_ready_last_s = elapsed
                    self.update_wait(t("wait_boot_ready", name=name, elapsed=elapsed, status=st))
                time.sleep(delay)
                delay = min(delay * 1.25, 0.7)
            return self._agent_is_ready(name)

        def _ensure_agent_connected(self, name: str) -> bool:
            """Start agent if needed, wait ready, open WS. Safe to call from worker thread."""
            if not self.client.token:
                self.log_line("Login required: /login", style="error")
                return False
            self.agent = name
            # Session project path = cmd cwd at TUI launch (before first turn)
            self._sync_agent_cwd_from_launch(name)
            info = self._lookup_agent(name)
            if info and info.get("ready"):
                self.update_wait(t("wait_boot_ws", name=name))
                ok = self._connect_agent_sync(name)
                if ok:
                    remember_agent(name)
                return ok

            # Process alive but not in Gateway registry → restart so it re-registers
            if info and info.get("process_status") == "running" and not info.get("ready"):
                self._log_verbose(f"{name} process running but registry offline — restarting…")
                self._start_agent_process(name, force_restart=True)
            else:
                self._start_agent_process(name, force_restart=False)

            if not self._wait_agent_ready(name):
                return False
            self.update_wait(t("wait_boot_ws", name=name))
            ok = self._connect_agent_sync(name)
            if ok:
                remember_agent(name)
            return ok

        def _connect_agent_sync(self, name: str) -> bool:
            from opensquad.cli.commands.chat_cmd import AgentBridge, AgentWsError

            if self.bridge:
                self.bridge.close()
                self.bridge = None
            self.agent = name
            self._agent_paused = False
            self.bridge = AgentBridge(self.client, name, interactive=True)
            self.bridge.on_line = lambda t: self.on_agent_line(t)
            self.bridge.on_stream = lambda c: self.log_stream(c)
            self.bridge.on_thinking = lambda buf: self.log_thinking(buf)
            self.bridge.on_thinking_end = lambda buf: self.log_thinking_end(buf)
            self.bridge.on_agent_mode = lambda m: self.call_from_thread(
                lambda mode=m: self._apply_agent_mode(mode, notify=False, send=False)
            )
            self.bridge.on_token_stats = lambda d: self.call_from_thread(lambda data=d: self._on_token_stats(data))
            self.bridge.on_model_info = lambda card, model: self.call_from_thread(
                lambda c=card, m=model: self._on_model_info(c, m)
            )
            self.bridge.on_reasoning_effort = lambda e: self.call_from_thread(
                lambda effort=e: self._on_reasoning_effort(effort)
            )
            self.bridge.on_context_compressed = lambda: self.call_from_thread(self._on_context_compressed)
            self.bridge.on_side_chunk = lambda key, kind, title, text, fresh=False: self.call_from_thread(
                lambda k=key, kd=kind, t=title, x=text, f=fresh: self._on_side_chunk(k, kd, t, x, fresh=f)
            )
            self.bridge.on_side_summary = lambda s: self.call_from_thread(
                lambda msg=s: self.log_line(msg, style="tool")
            )
            self.bridge.on_side_done = lambda key: self.call_from_thread(lambda k=key: self._on_side_done(k))
            self.bridge.on_decision = lambda evt, data: self.call_from_thread(
                lambda e=evt, d=data: self._on_bridge_decision(e, d if isinstance(d, dict) else {})
            )
            self.bridge.on_file_diff = lambda lines: self.log_file_diff(lines)
            self.bridge.on_plan = lambda payload: self.log_plan_payload(payload)
            self.bridge.on_tool_detail = lambda name, cid, text: self._on_tool_detail(name, cid, text)
            self._sync_status_from_agent(name)
            try:
                # Longer retries: process may still be binding after ready=true
                self.bridge.connect(retries=12, delay=0.35)
                try:
                    self.bridge.send_command("request_token_stats")
                except Exception:
                    pass
                self.call_from_thread(self._refresh_chrome)
                return True
            except AgentWsError as e:
                self.log_line(str(e), style="error")
                self.bridge = None
                return False
            except Exception as e:
                self.log_line(f"Could not connect: {e}", style="error")
                self.bridge = None
                return False

        def _on_token_stats(self, data: Any) -> None:
            if not isinstance(data, dict):
                return
            try:
                if "used" in data:
                    self._token_used = int(data.get("used") or 0)
                if "max" in data:
                    self._token_max = int(data.get("max") or 0)
            except (TypeError, ValueError):
                pass
            # Session output cumulative — used only to finalize this-turn ↓ delta
            session = data.get("session") if isinstance(data.get("session"), dict) else {}
            try:
                if session:
                    sout = session.get("output_tokens")
                    if sout is None:
                        sout = session.get("total_output_tokens")
                    if sout is not None:
                        self._session_out_tokens = int(sout or 0)
                        if getattr(self, "_turn_started_at", None) is not None:
                            real = max(
                                0,
                                self._session_out_tokens - int(getattr(self, "_turn_baseline_out", 0) or 0),
                            )
                            # Raise target only — display keeps animating toward it
                            if real > int(getattr(self, "_turn_out_target", 0) or 0):
                                self._turn_out_target = real
                                self._ensure_turn_meter_timer()
            except (TypeError, ValueError):
                pass
            m = str(data.get("model") or "").strip()
            model_changed = False
            if m:
                self._model_name = m
                if not self._model_label or self._model_label == "—":
                    self._model_label = self._pretty_model_label("", m)
                    model_changed = True
            # Win CMD: any dock Static.update can ghost the bordered prompt box.
            # Idle: skip. In-turn: only token meter (throttled), never touch Input.
            if getattr(self, "_turn_started_at", None) is not None:
                self._paint_prompt_meta_only()
            elif model_changed:
                self._paint_bottom_status()

        def _on_model_info(self, card: str | None, model: str | None) -> None:
            c = str(card or "").strip()
            m = str(model or "").strip()
            if c:
                self._model_card = c
            if m:
                self._model_name = m
                # Prefer real model id/title over prov-* card slug
                if not self._model_label or self._model_label == "—" or self._model_label.lower().startswith("prov"):
                    self._model_label = self._pretty_model_label("", m)
            self._static_set("#header-bar", self._header_bar_markup())
            self._paint_bottom_status()

        def _on_reasoning_effort(self, effort: str) -> None:
            e = str(effort or "").strip().lower()
            if e in ("low", "medium", "high"):
                self._reasoning_effort = e
                self._paint_bottom_status()

        def _switch_agent(self, name: str) -> None:
            if self.mode == "group":
                self.leave_group()
            self.log_line(f"Switching to agent '{name}'…", style="system")
            self._bootstrap_agent(name, then_new=False)

        def join_group(self, group_ref: str) -> None:
            if not self.client.token:
                self.log_line("Login first: /login", style="error")
                return
            gid, gname = self._resolve_group(group_ref)
            if not gid:
                self.log_line(f"group not found: {group_ref}", style="error")
                return
            if self.bridge:
                self.bridge.set_paused(True)
                self._agent_paused = True
            if self.group:
                self.group.close()
            self.group = GroupBridge(self.client)
            self.group.muted = self.muted
            self.group.on_line = lambda t: self.log_line(t)
            self.group.on_pending_cards = lambda: self._schedule_ui(self._open_group_pending_decision)
            self.group.on_resolved_card = lambda rid: self._schedule_ui(self._clear_decision_by_id, str(rid or ""))
            try:
                self.group.connect(gid, group_name=gname or gid, history_limit=15)
            except Exception as e:
                self.log_line(f"group connect failed: {e}", style="error")
                self.group = None
                return
            self.group.set_active(True)
            self.mode = "group"
            self._group_oldest_id = None
            self._ensure_group_members(force=True)
            # Seed oldest cursor so /group more works after join history
            try:
                seed = self.client.get(
                    f"/api/groups/{gid}/messages",
                    params={"limit": 15},
                )
                if isinstance(seed, list) and seed and isinstance(seed[0], dict) and seed[0].get("id"):
                    self._group_oldest_id = str(seed[0].get("id"))
            except Exception:
                pass
            self.log_line(
                f"Joined {gname} — mode=group  (@mention · /group members|search|more · /leave)",
                style="system",
            )
            self._refresh_chrome()
            self._focus_input()
            # Open picker if history already has pending cards
            try:
                self._open_group_pending_decision()
            except Exception:
                pass

        def leave_group(self) -> None:
            if self.group:
                self.group.set_active(False)
                if self.muted:
                    self.group.close()
                    self.group = None
            self.mode = "solo"
            self._hide_mention_menu()
            self._group_members = []
            self._group_oldest_id = None
            # Don't keep a group options card open after leaving
            try:
                if self._decision and getattr(self._decision, "source", "") == "group":
                    self._decision_queue = [d for d in self._decision_queue if getattr(d, "source", "") != "group"]
                    self._hide_decision()
            except Exception:
                pass
            if self.bridge:
                self.bridge.set_paused(False)
                self._agent_paused = False
            elif self.agent:
                self._bootstrap_agent(self.agent, then_new=False)
            self.log_line(f"Left group → solo ({self.agent or 'no agent'})", style="system")
            self._refresh_chrome()
            self._focus_input()

        def _resolve_group(self, ref: str) -> tuple[str | None, str]:
            try:
                groups = self.client.get("/api/groups")
            except Exception as e:
                self.log_line(str(e), style="error")
                return None, ""
            if not isinstance(groups, list):
                return ref, ref
            for g in groups:
                if g.get("id") == ref or g.get("name") == ref:
                    return g.get("id"), g.get("name") or g.get("id")
            low = ref.lower()
            for g in groups:
                name = str(g.get("name") or "")
                gid = str(g.get("id") or "")
                if low in name.lower() or low in gid.lower() or gid.startswith(ref) or gid.startswith(f"g-{ref}"):
                    return g.get("id"), name or g.get("id")
            # numeric index from /group list
            if ref.isdigit():
                idx = int(ref) - 1
                if 0 <= idx < len(groups):
                    g = groups[idx]
                    return g.get("id"), g.get("name") or g.get("id")
            return (ref if ref.startswith("g-") else None), ref

        # ── Generic nested nav (/model /skill /role /…) ────────────────

        def _set_input_password(self, on: bool) -> None:
            """Toggle password masking on #chat-input (login / secrets)."""
            try:
                inp = self.query_one("#chat-input", Input)
                inp.password = bool(on)
            except Exception:
                pass

        def _start_login(self, email: str | None = None) -> None:
            """Begin TUI login capture (never use stdin input/getpass)."""
            email_s = (email or "").strip()
            self._set_input_password(False)
            if email_s:
                self._await_login = {"step": "password", "email": email_s}
                self._set_input_password(True)
                self.log_line(t("login_ask_password", email=email_s), style="system")
            else:
                self._await_login = {"step": "email", "email": None}
                self.log_line(t("login_ask_email"), style="system")
            self._placeholder_cache = None
            self._refresh_chrome()
            self._focus_input()

        def _cancel_login(self) -> None:
            self._await_login = None
            self._set_input_password(False)
            self.log_line(t("login_cancelled"), style="system")
            self._placeholder_cache = None
            self._refresh_chrome()
            self._focus_input()

        def _on_login_input(self, line: str) -> None:
            pending = getattr(self, "_await_login", None) or {}
            step = pending.get("step") or "email"
            if step == "email":
                email = (line or "").strip()
                if not email:
                    self.log_line(t("login_email_required"), style="error")
                    self._focus_input()
                    return
                self._await_login = {"step": "password", "email": email}
                self._set_input_password(True)
                self.log_line(t("login_ask_password", email=email), style="system")
                self._placeholder_cache = None
                self._refresh_chrome()
                self._focus_input()
                return
            if step == "name":
                name = (line or "").strip()
                email = str(pending.get("email") or "").strip()
                password = str(pending.get("password") or "")
                self._await_login = None
                self._set_input_password(False)
                self._placeholder_cache = None
                if not email or not name:
                    self.log_line(t("login_cancelled"), style="system")
                    self._refresh_chrome()
                    self._focus_input()
                    return
                self.begin_wait(t("login_working"))
                self._do_login(email, password, name=name)
                return
            email = str(pending.get("email") or "").strip()
            password = line if line is not None else ""
            self._await_login = None
            self._set_input_password(False)
            self._placeholder_cache = None
            if not email or not password:
                self.log_line(t("login_cancelled"), style="system")
                self._refresh_chrome()
                self._focus_input()
                return
            self.begin_wait(t("login_working"))
            self._decide_login_flow(email, password)

        @work(thread=True, group="login")
        def _decide_login_flow(self, email: str, password: str) -> None:
            """Check registration status off-thread; collect a display name when
            the first web account does not exist yet, otherwise log in directly."""
            try:
                status = self.client.registration_status()
                needs_register = bool(status.get("registration_required"))
            except Exception:
                needs_register = False

            if not needs_register:
                self._do_login(email, password)
                return

            def _ask_name() -> None:
                self.end_wait()
                self._await_login = {"step": "name", "email": email, "password": password}
                self.log_line(t("login_ask_name", email=email), style="system")
                self._refresh_chrome()
                self._focus_input()

            try:
                self.call_from_thread(_ask_name)
            except Exception:
                _ask_name()

        @work(thread=True, group="login")
        def _do_login(self, email: str, password: str, name: str | None = None) -> None:
            try:
                lang = str(getattr(self, "_locale", None) or get_locale() or "zh")
                if name:
                    data = self.client.register(name, email, password, language=lang)
                else:
                    data = self.client.login(email, password, language=lang)
                user = (data or {}).get("user") or {}
                name = str(user.get("name") or email)
                mail = str(user.get("email") or email)

                def _ok() -> None:
                    self.end_wait()
                    self.log_line(t("login_ok", name=name, email=mail), style="system")
                    self._refresh_client()
                    self._refresh_chrome()
                    self._focus_input()

                try:
                    self.call_from_thread(_ok)
                except Exception:
                    _ok()
            except Exception as e:
                err = str(e)

                def _fail() -> None:
                    self.end_wait()
                    self.log_line(t("login_failed", err=err), style="error")
                    self._refresh_chrome()
                    self._focus_input()

                try:
                    self.call_from_thread(_fail)
                except Exception:
                    _fail()

        def _refresh_client(self) -> None:
            self.client = GatewayClient(gateway_url=self.client.gateway_url)
            if self.agent and self.client.token and self.mode == "solo":
                self._bootstrap_agent(self.agent, then_new=False)

        def attach_image(self, path: str | None = None) -> None:
            try:
                media = attach_from_path(path) if path else attach_from_clipboard()
                if not media:
                    self.log_line("No image — /image <path> or Ctrl+Shift+V", style="system")
                    return
                self.pending_media.append(media)
                self.log_line(f"queued {chip_label(media)}", style="system")
                self._refresh_chrome()
            except Exception as e:
                self.log_line(str(e), style="error")

        def detach_media(self) -> None:
            n = len(self.pending_media)
            had_skill = bool(self.pending_skill)
            self.pending_media.clear()
            self.pending_skill = None
            bits = []
            if n:
                bits.append(f"{n} attachment(s)")
            if had_skill:
                bits.append("skill chip")
            self.log_line(f"cleared {' · '.join(bits) if bits else 'pending'}", style="system")
            self._refresh_chrome()

        def set_muted(self, muted: bool) -> None:
            self.muted = muted
            if self.group:
                self.group.set_muted(muted)
            self.log_line(f"background alerts {'muted' if muted else 'on'}", style="system")

        def _group_member_names(self) -> dict[str, str]:
            """id→name map for group message prefixes."""
            names: dict[str, str] = {}
            if self.group and getattr(self.group, "_member_names", None):
                names.update(self.group._member_names)
            for m in getattr(self, "_group_members", None) or []:
                mid = str(m.get("id") or "").strip()
                name = str(m.get("name") or "").strip()
                if mid and name:
                    names[mid] = name
            return names

        def _format_group_message_lines(self, m: dict) -> list[str]:
            from opensquad.cli.group_render import format_message_lines

            names = self._group_member_names()
            if self.group and hasattr(self.group, "enrich_message"):
                m = self.group.enrich_message(m)
            return format_message_lines(m, shell_style=True, member_names=names)

        def show_history(self, n: int = 20) -> None:
            if self.mode == "group" and self.group and self.group.group_id:
                try:
                    if hasattr(self.group, "refresh_member_names"):
                        self.group.refresh_member_names()
                    msgs = self.client.get(
                        f"/api/groups/{self.group.group_id}/messages",
                        params={"limit": max(1, min(int(n or 20), 100))},
                    )
                    if isinstance(msgs, list):
                        if msgs and isinstance(msgs[0], dict) and msgs[0].get("id"):
                            self._group_oldest_id = str(msgs[0].get("id"))
                        self.log_line(f"[group] last {len(msgs)} messages:", style="system")
                        for m in msgs:
                            if isinstance(m, dict):
                                for line in self._format_group_message_lines(m):
                                    self.log_line(line.lstrip("\n") or line)
                        if msgs:
                            self.log_line(
                                "[dim]/group more for older · /group search <kw> to find[/]",
                                style="system",
                            )
                except Exception as e:
                    self.log_line(str(e), style="error")
                return
            self._session_cmd("sessions")

        def show_group_members(self) -> None:
            if self.mode != "group" or not self.group or not self.group.group_id:
                self.log_line("Join a group first: /group join <id>", style="error")
                return
            members = self._ensure_group_members(force=True)
            gname = self.group.group_name or self.group.group_id
            self.log_line(f"[group] members · {gname} ({len(members)})", style="system")
            if not members:
                self.log_line("  (no members)", style="system")
                return
            for i, m in enumerate(members, 1):
                name = m.get("name") or "?"
                mid = m.get("id") or ""
                st = m.get("status") or ""
                detail = f"  [{i}] @{name}"
                if st:
                    detail += f"  · {st}"
                if mid:
                    detail += f"  · {mid}"
                self.log_line(detail, style="system")
            self.log_line("[dim]Type @ in the input to mention a member[/]", style="system")

        def search_group_messages(self, query: str, limit: int = 30) -> None:
            if self.mode != "group" or not self.group or not self.group.group_id:
                self.log_line("Join a group first: /group join <id>", style="error")
                return
            q = (query or "").strip()
            if not q:
                self.log_line("usage: /group search <keyword>", style="error")
                return
            try:
                if hasattr(self.group, "refresh_member_names"):
                    self.group.refresh_member_names()
                msgs = self.client.get(
                    f"/api/groups/{self.group.group_id}/search",
                    params={"q": q, "limit": max(1, min(int(limit or 30), 100))},
                )
            except Exception as e:
                self.log_line(f"[group] search failed: {e}", style="error")
                return
            if not isinstance(msgs, list):
                self.log_line("[group] search: unexpected response", style="error")
                return
            self.log_line(f"[group] search “{q}” → {len(msgs)} hit(s)", style="system")
            if not msgs:
                return
            for m in msgs:
                if isinstance(m, dict):
                    for line in self._format_group_message_lines(m):
                        self.log_line(line.lstrip("\n") or line)

        def load_more_group_history(self, n: int = 20) -> None:
            """Load older messages (before the oldest currently shown)."""
            if self.mode != "group" or not self.group or not self.group.group_id:
                self.log_line("Join a group first: /group join <id>", style="error")
                return
            params: dict[str, Any] = {"limit": max(1, min(int(n or 20), 100))}
            before = getattr(self, "_group_oldest_id", None)
            if before:
                params["before"] = before
            try:
                if hasattr(self.group, "refresh_member_names"):
                    self.group.refresh_member_names()
                msgs = self.client.get(
                    f"/api/groups/{self.group.group_id}/messages",
                    params=params,
                )
            except Exception as e:
                self.log_line(f"[group] more failed: {e}", style="error")
                return
            if not isinstance(msgs, list) or not msgs:
                self.log_line("[group] no older messages", style="system")
                return
            if isinstance(msgs[0], dict) and msgs[0].get("id"):
                self._group_oldest_id = str(msgs[0].get("id"))
            self.log_line(f"[group] +{len(msgs)} older message(s):", style="system")
            for m in msgs:
                if isinstance(m, dict):
                    for line in self._format_group_message_lines(m):
                        self.log_line(line.lstrip("\n") or line)

        def approve(self, approval_id: str | None = None, note: str = "") -> None:
            if not self.group:
                self.log_line("Join a group first", style="error")
                return
            self._approve_work(approval_id, note, reject=False)

        def reject(self, approval_id: str | None = None, note: str = "") -> None:
            if not self.group:
                self.log_line("Join a group first", style="error")
                return
            self._approve_work(approval_id, note, reject=True)

        @work(thread=True, group="group-action")
        def _approve_work(self, approval_id: str | None = None, note: str = "", reject: bool = False) -> None:
            if not self.group:
                self.log_line("Join a group first", style="error")
                return
            try:
                self.group.resolve_approval(approval_id, reject=reject, note=note or "")
                self.log_line(
                    f"{'rejected' if reject else 'approved'} {approval_id or '(latest)'}",
                    style="system",
                )
            except Exception as e:
                self.log_line(str(e), style="error")

        def choose(self, proposal_id: str | None, value: str) -> None:
            if not self.group:
                self.log_line("Join a group first", style="error")
                return
            self._choose_work(proposal_id, value)

        @work(thread=True, group="group-action")
        def _choose_work(self, proposal_id: str | None, value: str) -> None:
            if not self.group:
                self.log_line("Join a group first", style="error")
                return
            try:
                if value.isdigit():
                    pr = self.group.find_proposal(proposal_id)
                    n = int(value)
                    if pr and 1 <= n <= len(pr.options):
                        value = pr.options[n - 1][1]
                self.group.resolve_choose(proposal_id, value)
                self.log_line(f"chose {value}", style="system")
            except Exception as e:
                self.log_line(str(e), style="error")

        def _close_bridges(self) -> None:
            if self.bridge:
                self.bridge.close()
                self.bridge = None
            if self.group:
                self.group.close()
                self.group = None

        def on_unmount(self) -> None:
            self._close_bridges()

    return _OpenSquadApp
