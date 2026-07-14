"""
OpenSquad TUI — full-screen terminal UI (Textual).

Layout (Claude Code / OpenCode style):
  ┌─ header ─────────────────────────────────┐
  │ OpenSquad CLI · agent · mode             │
  ├─ chat log (scroll) ──────────────────────┤
  │  messages / tool / cards                 │
  ├─ prompt frame ───────────────────────────┤
  │  ❯ input…                                │
  └─ status bar ─────────────────────────────┘
"""

from __future__ import annotations

import os
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any

from opensquad import __version__
from opensquad.cli.api_client import GatewayClient, load_credentials, pick_default_agent, remember_agent
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
from opensquad.cli.tui.redact import redact_secrets
from opensquad.cli.tui.side_stream import SideStreamHub


def run_tui(*, gateway: str | None = None, agent: str | None = None) -> None:
    """Entry: launch the Textual app (blocks until quit)."""
    try:
        import importlib.util

        if importlib.util.find_spec("textual") is None:
            raise ImportError("textual not installed")
    except ImportError as e:
        raise SystemExit(f"[tui] textual is required. Install with:\n  pip install 'textual>=8.2.8'\n  ({e})") from e

    # Windows: allow IME-committed CJK (Space/Enter confirm) into Input
    from opensquad.cli.tui.win_ime_patch import apply_win_ime_patch

    apply_win_ime_patch()

    client = GatewayClient(gateway_url=gateway)
    if not agent and client.token:
        agent = pick_default_agent(client)
    app = OpenSquadApp(client=client, agent=agent)
    app.run()


def _pick_default_agent(client: GatewayClient) -> str | None:
    return pick_default_agent(client)


# ── App ───────────────────────────────────────────────────────────────────


class OpenSquadApp:
    """Factory that subclasses Textual App lazily (keeps import optional)."""

    def __new__(cls, client: GatewayClient, agent: str | None):
        return _build_app_class()(client=client, agent=agent)


def _build_app_class():
    from textual import on, work
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Vertical
    from textual.widgets import Footer, Input, RichLog, Static

    from opensquad.cli.commands.chat_cmd import AgentBridge, AgentWsError
    from opensquad.cli.group_bridge import GroupBridge
    from opensquad.cli.tui.slash_suggest import SlashSuggester, slash_completions
    from opensquad.cli.tui.themes import (
        DEFAULT_THEME,
        list_theme_names,
        load_saved_theme,
        register_opensquad_themes,
        save_theme,
    )

    class _OpenSquadApp(App[None]):
        CSS_PATH = Path(__file__).with_name("styles.tcss")
        TITLE = f"OpenSquad CLI v{__version__}"
        AUTO_FOCUS = "#chat-input"
        ALLOW_SELECT = True
        # Drag-select must not edge-auto-scroll — that fights the highlight and feels like "画面抖动"
        ENABLE_SELECT_AUTO_SCROLL = False
        BINDINGS = [
            Binding("ctrl+c", "cancel_or_clear", "^C×2 Exit", show=True),
            Binding("ctrl+q", "quit", "Quit", show=True),
            Binding("ctrl+l", "clear_log", "Clear", show=False),
            Binding("ctrl+shift+v", "paste_image", "Paste image", show=False),
            Binding("ctrl+e", "cycle_effort", "Effort", show=True, priority=True),
            Binding("ctrl+x", "toggle_live", "Live", show=True, priority=True),
            # priority so Tab does NOT move focus away from the input (that made typing die)
            # Idle Tab = Plan/Build; with slash/nav menu open = confirm selection
            Binding("tab", "accept_slash", "Plan/Build", show=True, priority=True),
            Binding("escape", "hide_slash", "Hide menu", show=False),
            Binding("up", "slash_up", "Prev", show=False, priority=True),
            Binding("down", "slash_down", "Next", show=False, priority=True),
        ]

        def __init__(self, client: GatewayClient, agent: str | None):
            super().__init__()
            register_opensquad_themes(self)
            # Apply before first paint when possible
            saved = load_saved_theme()
            if saved in (self.available_themes or {}):
                self.theme = saved
            elif DEFAULT_THEME in (self.available_themes or {}):
                self.theme = DEFAULT_THEME
            self.client = client
            self.agent = agent
            self.mode = "solo"
            self.bridge: AgentBridge | None = None
            self.group: GroupBridge | None = None
            self.pending_media: list[PendingMedia] = []
            self.muted = False
            self._agent_paused = False
            self._stream_buf = ""
            self._sending = False
            # Wait animation state
            self._wait_label: str | None = None
            self._wait_tick: int = 0
            self._wait_timer = None
            self._SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
            self._slash_items: list[str] = []
            self._slash_helps: list[str] = []
            self._slash_index: int = 0
            self._think_buf_latest: str = ""
            self._think_pending: bool = False
            self._reply_flushed: bool = False
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
            self._reasoning_effort: str = "high"
            self._token_used: int = 0
            self._token_max: int = 0
            self._project_path: str = self._resolve_project_path()
            # Debounce duplicate compress-clear (summary + history_sync)
            self._compress_clear_at: float = 0.0
            # Message FIFO (solo)
            self._send_queue: deque[tuple[str, list]] = deque()
            # Live thinking paint throttle
            self._think_paint_at: float = 0.0
            # Side stream (Ctrl+X)
            self._side_hub = SideStreamHub()
            self._live_side_open: bool = False
            self._live_side_key: str | None = None
            # API key capture for Connect provider
            self._await_api_key: dict[str, Any] | None = None
            self._follow_chat: bool = True

        def compose(self) -> ComposeResult:
            yield Static(id="header-bar")
            yield RichLog(
                id="chat-log",
                highlight=True,
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
            yield Static(id="live-think")
            with Vertical(id="prompt-dock"):
                yield Static(id="slash-menu")
                yield Static(id="wait-banner")
                with Vertical(id="prompt-frame"):
                    yield Input(
                        placeholder='Ask anything…  "/" · Tab mode · ^E effort · ^X live',
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
                    self.query_one(wid, Static).can_focus = False
                except Exception:
                    pass
            try:
                self.query_one(Footer).can_focus = False
            except Exception:
                pass
            self._sync_status_from_agent(self.agent)
            self._refresh_chrome()
            creds = load_credentials()
            self._chat_write(f"[bold]OpenSquad[/]  [dim]v{__version__}[/]", follow=True)
            if creds.get("email"):
                self._chat_write(f"[dim]{creds.get('email')} · {self.client.gateway_url}[/]", follow=True)
            else:
                self._chat_write("[yellow]Not logged in — /login[/]", follow=True)
            self._chat_write(
                "[dim]Tab Plan/Build · ^E effort · ^X live · /theme · /model Connect[/]\n",
                follow=True,
            )
            self._hide_slash_menu()
            self._focus_input()
            if self.client.token and self.agent:
                self.log_line(
                    f"Booting agent '{self.agent}' in background…",
                    style="system",
                )
                self._bootstrap_agent(self.agent, then_new=False)
            elif not self.agent:
                self.log_line("No agent selected. /agent list then /start <name>", style="system")

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

        def _chat_write(self, content: str, *, follow: bool | None = None) -> None:
            """Append to chat log; sticky-bottom while agent streams unless user scrolled up."""
            log = self.query_one("#chat-log", RichLog)
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
            log.write(content, scroll_end=scroll_end, animate=False)
            if scroll_end:
                try:
                    log.scroll_end(animate=False)
                except Exception:
                    pass

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

        def _escape_markup(self, text: str) -> str:
            return text.replace("[", "\\[")

        # ── wait animation ────────────────────────────────────────────

        def begin_wait(self, label: str) -> None:
            """Show spinner for any long operation (boot / connect / reply)."""

            def _start() -> None:
                self._wait_label = label
                self._wait_tick = 0
                banner = self.query_one("#wait-banner", Static)
                banner.add_class("visible")
                status = self.query_one("#status-bar", Static)
                status.add_class("waiting")
                if self._wait_timer is None:
                    # Slow spin: 12.5fps redraws fight mouse selection highlight
                    self._wait_timer = self.set_interval(0.2, self._tick_wait)
                self._paint_wait()

            try:
                self.call_from_thread(_start)
            except Exception:
                try:
                    _start()
                except Exception:
                    pass

        def update_wait(self, label: str) -> None:
            def _upd() -> None:
                self._wait_label = label
                self._paint_wait()

            try:
                self.call_from_thread(_upd)
            except Exception:
                try:
                    _upd()
                except Exception:
                    pass

        def end_wait(self) -> None:
            def _stop() -> None:
                self._wait_label = None
                if self._wait_timer is not None:
                    self._wait_timer.stop()
                    self._wait_timer = None
                try:
                    banner = self.query_one("#wait-banner", Static)
                    banner.update("")
                    banner.remove_class("visible")
                    self.query_one("#status-bar", Static).remove_class("waiting")
                except Exception:
                    pass
                self._refresh_chrome()

            try:
                self.call_from_thread(_stop)
            except Exception:
                try:
                    _stop()
                except Exception:
                    pass

        def _tick_wait(self) -> None:
            if not self._wait_label or self._is_selecting():
                return
            self._wait_tick += 1
            self._paint_wait()

        def _paint_wait(self) -> None:
            if not self._wait_label or self._is_selecting():
                return
            spin = self._SPIN[self._wait_tick % len(self._SPIN)]
            text = f" {spin}  {self._wait_label}"
            try:
                self.query_one("#wait-banner", Static).update(f"[bold yellow]{text}[/]")
                self.query_one("#status-bar", Static).update(text)
            except Exception:
                pass

        # ── chrome ────────────────────────────────────────────────────

        @staticmethod
        def _resolve_project_path() -> str:
            """Current project directory (CLI process cwd), OpenCode-style footer."""
            return os.path.abspath(os.getcwd())

        @staticmethod
        def _short_path(path: str, max_len: int = 48) -> str:
            p = (path or "").strip() or "—"
            home = str(Path.home())
            if p.startswith(home):
                p = "~" + p[len(home) :]
            if len(p) <= max_len:
                return p
            keep = max_len - 1
            left = max(8, keep // 2)
            right = keep - left
            return p[:left] + "…" + p[-right:]

        @staticmethod
        def _fmt_tokens(n: int) -> str:
            n = max(0, int(n or 0))
            if n >= 1_000_000:
                return f"{n / 1_000_000:.1f}M"
            if n >= 1000:
                return f"{n / 1000:.1f}K"
            return str(n)

        @staticmethod
        def _pretty_model_label(card: str = "", model: str = "") -> str:
            raw = (card or model or "").strip()
            if not raw:
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

        def _theme_hex(self, key: str, fallback: str) -> str:
            """Resolve a Textual theme CSS variable to a hex color for Rich markup."""
            try:
                val = (self.get_css_variables() or {}).get(key) or fallback
                s = str(val).strip()
                if s.startswith("#") and len(s) >= 4:
                    return s.split()[0]
            except Exception:
                pass
            return fallback

        def _opencode_status_markup(self) -> str:
            """Line inside prompt-frame under input: mode · model · effort · tokens · Q."""
            mode = getattr(self, "_agent_mode", "build") or "build"
            primary = self._theme_hex("primary", "#58a6ff")
            warning = self._theme_hex("warning", "#d29922")
            fg = self._theme_hex("foreground", "#e6edf3")
            if mode == "plan":
                mode_mk = f"[bold {warning}]Plan[/]"
            else:
                mode_mk = f"[bold {primary}]Build[/]"

            model = self._escape_markup(getattr(self, "_model_label", None) or "—")
            effort = self._escape_markup((getattr(self, "_reasoning_effort", None) or "high").lower())
            tok = self._escape_markup(self._context_usage_label())
            qn = len(getattr(self, "_send_queue", ()) or ())
            qbit = f" · Q:{qn}" if qn else ""
            live = ""
            hub = getattr(self, "_side_hub", None)
            if hub and any(s.active for s in hub.streams.values()):
                live = " · [dim]^X live[/]"
            media = ""
            if self.pending_media:
                media = f" · {self._escape_markup(format_pending_chips(self.pending_media))}"
            return f" {mode_mk} · [{fg}]{model}[/] · [bold {warning}]{effort}[/] · [{fg}]{tok}[/]{qbit}{live}{media}"

        def _footer_path_markup(self) -> str:
            path = self._escape_markup(
                self._short_path(getattr(self, "_project_path", None) or self._resolve_project_path())
            )
            theme_name = self._escape_markup(str(getattr(self, "theme", "") or ""))
            return f" {path}  [dim]· theme {theme_name} · Tab mode · ^E effort · /help[/]"

        def _sync_status_from_agent(self, name: str | None = None) -> None:
            """Pull model / tokens / effort / path from admin agents list + local config."""
            self._project_path = self._resolve_project_path()
            agent_name = name or self.agent
            if not agent_name:
                return
            info = self._lookup_agent(agent_name) or {}
            card = str(info.get("model_card") or "").strip()
            if card:
                self._model_card = card
                self._model_label = self._pretty_model_label(card)
            ts = info.get("token_stats")
            if isinstance(ts, dict):
                try:
                    self._token_used = int(ts.get("used") or 0)
                    self._token_max = int(ts.get("max") or 0)
                except (TypeError, ValueError):
                    pass
                m = str(ts.get("model") or "").strip()
                if m and (not self._model_label or self._model_label == "—"):
                    self._model_label = self._pretty_model_label(card, m)
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

        def _refresh_chrome(self) -> None:
            if self._wait_label:
                self._paint_wait()
                return
            header = self.query_one("#header-bar", Static)
            inp = self.query_one("#chat-input", Input)
            try:
                meta = self.query_one("#prompt-meta", Static)
            except Exception:
                meta = None
            try:
                fpath = self.query_one("#footer-path", Static)
            except Exception:
                fpath = None

            if self.mode == "group" and self.group:
                gname = self.group.group_name or self.group.group_id
                header.update(f"  OpenSquad  ·  [b]{gname}[/b]  ·  group")
                inp.placeholder = f"Message {gname}…  Enter send · /leave · /approve"
                if meta:
                    meta.update(f" {gname} · group · /leave")
                if fpath:
                    fpath.update(self._footer_path_markup())
            else:
                ready = bool(self.bridge and getattr(self.bridge, "is_open", False))
                state = "ready" if ready else "offline"
                mode = getattr(self, "_agent_mode", "build") or "build"
                mode_plain = "Plan" if mode == "plan" else "Build"
                header.update(f"  OpenSquad  ·  [b]{self.agent or '—'}[/b]  ·  {state}  ·  {self.client.gateway_url}")
                if getattr(self, "_await_api_key", None):
                    inp.placeholder = "Paste API key…  Enter save · Esc cancel"
                else:
                    inp.placeholder = f"Message {self.agent or 'agent'}…  Tab {mode_plain} · ^E effort · ^X live"
                if meta:
                    meta.update(self._opencode_status_markup())
                if fpath:
                    fpath.update(self._footer_path_markup())

        def log_line(self, text: str, style: str = "") -> None:
            """Thread-safe append to chat log (OpenCode-style blocks)."""
            # Assistant/tool lines may arrive while a thought is still open — flush first
            if style in ("", "agent", "tool", "error") and getattr(self, "_think_pending", False):
                if style != "thought":
                    self._flush_thinking_to_log()

            safe = self._escape_markup(str(text) if text is not None else "")

            def _write() -> None:
                w = self._chat_write
                if style == "user":
                    # New user turn: always reveal the latest exchange
                    w("", follow=True)
                    w(f"[on #21262d][bold #79c0ff]  {safe}  [/][/]", follow=True)
                    w("", follow=True)
                elif style == "thought":
                    w(f"[italic #8b949e]Thinking: {safe}[/]")
                    w("")
                elif style == "agent":
                    w("")
                    w(f"[#e6edf3]{safe}[/]")
                    w(f"[dim]· {self.agent or 'agent'}[/]")
                    w("")
                elif style == "error":
                    w(f"[bold red]{safe}[/]", follow=True)
                elif style == "system":
                    w(f"[dim]{safe}[/]")
                elif style == "tool":
                    w(f"[#d2a8ff]{safe}[/]")
                else:
                    if safe.startswith(("  ⚙", "  ✓", "  ·", "[")):
                        w(f"[dim]{safe}[/]")
                    else:
                        w(f"[#e6edf3]{safe}[/]")
                        w("")

            try:
                self.call_from_thread(_write)
            except Exception:
                try:
                    _write()
                except Exception:
                    pass

        def _flush_thinking_to_log(self) -> None:
            """Persist thinking into the transcript (conversation order)."""
            buf = (getattr(self, "_think_buf_latest", None) or "").strip()
            self._think_pending = False
            self._think_buf_latest = ""
            self._hide_live_think()
            if not buf:
                return
            one_line = " ".join(buf.split())
            safe = self._escape_markup(one_line)

            def _write() -> None:
                muted = self._theme_hex("text-muted", "#8b949e")
                self._chat_write(f"[italic {muted}]Thinking: {safe}[/]", follow=True)
                self._chat_write("", follow=True)

            try:
                self.call_from_thread(_write)
            except Exception:
                try:
                    _write()
                except Exception:
                    pass

        def _hide_live_think(self) -> None:
            try:
                w = self.query_one("#live-think", Static)
                w.update("")
                w.remove_class("visible")
            except Exception:
                pass

        def _paint_live_think(self) -> None:
            buf = (getattr(self, "_think_buf_latest", None) or "").strip()
            if not buf:
                return
            # Show last ~1200 chars so the widget stays readable
            show = buf if len(buf) <= 1200 else ("…" + buf[-1199:])
            safe = self._escape_markup(show)
            muted = self._theme_hex("text-muted", "#8b949e")

            def _do() -> None:
                try:
                    w = self.query_one("#live-think", Static)
                    w.update(f"[italic {muted}]Thinking: {safe}[/]")
                    w.add_class("visible")
                    if getattr(self, "_follow_chat", True) and not self._is_selecting():
                        try:
                            self.query_one("#chat-log", RichLog).scroll_end(animate=False)
                        except Exception:
                            pass
                except Exception:
                    pass

            try:
                self.call_from_thread(_do)
            except Exception:
                try:
                    _do()
                except Exception:
                    pass

        def _flush_reply_to_log(self) -> None:
            """Write buffered streamed reply into the transcript once."""
            if self._reply_flushed:
                return
            buf = (self._stream_buf or "").strip()
            if not buf:
                return
            self._reply_flushed = True
            self.log_line(buf, style="agent")

        def log_stream(self, chunk: str) -> None:
            """Stream tokens: keep thinking in history, preview reply in status."""
            if self._think_pending:
                self._flush_thinking_to_log()
            self._stream_buf += chunk
            preview = self._stream_buf.replace("\n", " ").strip()
            if len(preview) > 42:
                preview = "…" + preview[-42:]
            self.update_wait(f"Replying… {preview}" if preview else "Replying…")
            # Sticky-scroll chat while reply accumulates (final flush still once)
            if getattr(self, "_follow_chat", True) and not self._is_selecting():
                try:
                    self.call_from_thread(lambda: self.query_one("#chat-log", RichLog).scroll_end(animate=False))
                except Exception:
                    pass

        def log_thinking(self, buf: str) -> None:
            """Accumulate thought into #live-think (conversation area), not banner spam."""
            self._think_buf_latest = buf or ""
            self._think_pending = True
            self.update_wait("Thinking…")
            now = time.monotonic()
            if now - getattr(self, "_think_paint_at", 0.0) < 0.1:
                return
            self._think_paint_at = now
            self._paint_live_think()

        def log_thinking_end(self, buf: str) -> None:
            """Thought stream closed — write Thinking into transcript before the reply."""
            if buf:
                self._think_buf_latest = buf
            if self._think_pending or (buf or "").strip():
                self._think_pending = True
                self._flush_thinking_to_log()
            self.update_wait("Replying…")

        def on_agent_line(self, text: str) -> None:
            """Bridge on_line: finalize thought, then show assistant/tool line."""
            if self._think_pending:
                self._flush_thinking_to_log()
            t = str(text) if text is not None else ""
            # If we already streamed the same reply, skip duplicate final message
            streamed = (self._stream_buf or "").strip()
            if streamed and t.strip() == streamed:
                self._flush_reply_to_log()
                return
            if t.startswith(("  ⚙", "  ✓")):
                self.log_line(t, style="tool")
            elif t.startswith("[error]") or t.startswith("[ws]"):
                self.log_line(t, style="error")
            elif t.startswith("[") or t.startswith("  ·"):
                self.log_line(t, style="system")
            else:
                if streamed and not self._reply_flushed:
                    self._flush_reply_to_log()
                elif t.strip():
                    self.log_line(t, style="agent")

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
                getattr(self, "_nav_active", False)
                or getattr(self, "_session_pick_active", False)
                or getattr(self, "_slash_items", None)
            ):
                self._complete_submit(gen)
                return
            hinted = (event.value or "").strip()
            if sys.platform == "win32":
                # Non-empty: short defer (trailing composing char).
                # Empty: longer defer (Enter was likely IME confirm).
                delay = 0.05 if (hinted or self.pending_media) else 0.15
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

            # Session / nav picker: Enter confirms highlighted item
            if getattr(self, "_nav_active", False):
                inp.value = ""
                self._nav_confirm()
                return
            if getattr(self, "_session_pick_active", False):
                inp.value = ""
                self._confirm_session_pick()
                return

            # Slash command palette: Enter confirms highlighted command (not raw/empty input)
            if getattr(self, "_slash_items", None):
                idx = max(0, min(self._slash_index, len(self._slash_items) - 1))
                choice = self._slash_items[idx]
                inp.value = ""
                self._hide_slash_menu()
                if choice:
                    self._handle_slash(choice)
                else:
                    self._focus_input()
                return

            line = (inp.value or "").rstrip("\n").strip()
            if not line and not self.pending_media:
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

            inp.value = ""
            self._hide_slash_menu()
            self._hide_session_picker()
            self._hide_nav()

            if line.startswith(("/", "+")):
                self._handle_slash(line)
                return

            self.log_line(line or format_pending_chips(self.pending_media), style="user")
            if self.mode == "group":
                self._send_plain(line)
                return

            # Solo FIFO: queue while a turn is in flight
            if self._sending:
                snap = list(self.pending_media)
                self.pending_media.clear()
                self._send_queue.append((line, snap))
                self.log_line(f"Queued (#{len(self._send_queue)}) — will send after current reply", style="system")
                self._refresh_chrome()
                self._focus_input()
                return

            self._follow_chat = True
            self._sending = True
            self._send_solo(line)

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
                self.clear_chat_view(note="Screen cleared")
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
                self.notify("已复制 · 再按 Ctrl+C 退出", timeout=2)
                return

            inp = self.query_one("#chat-input", Input)
            if inp.value:
                inp.value = ""
                self._hide_slash_menu()
                self.notify("已清空 · 再按 Ctrl+C 退出", timeout=2)
                return

            if self.bridge and self._sending:
                try:
                    self.bridge.send_command("stop_task")
                    self.log_line("[system] stop requested", style="system")
                except Exception:
                    pass
                self.notify("已请求停止 · 再按 Ctrl+C 退出", timeout=2)
                return

            self.notify("再按一次 Ctrl+C 退出", timeout=2)

        def action_hide_slash(self) -> None:
            from textual.actions import SkipAction

            # Cancel Connect-provider API key capture
            if getattr(self, "_await_api_key", None):
                self._await_api_key = None
                self.log_line("API key capture cancelled", style="system")
                self._refresh_chrome()
                self._focus_input()
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
            if not self._slash_items:
                raise SkipAction()
            self._hide_slash_menu()
            self._focus_input()

        def action_accept_slash(self) -> None:
            """Tab: confirm menu selection, or toggle Plan/Build when idle."""
            if self._nav_active:
                self._nav_confirm()
                return
            if self._session_pick_active:
                self._confirm_session_pick()
                return

            # Slash palette: Tab also confirms highlighted command (same as Enter)
            if self._slash_items:
                idx = max(0, min(self._slash_index, len(self._slash_items) - 1))
                choice = self._slash_items[idx]
                inp = self.query_one("#chat-input", Input)
                inp.value = ""
                self._hide_slash_menu()
                if choice:
                    self._handle_slash(choice)
                return

            inp = self.query_one("#chat-input", Input)
            value = inp.value or ""
            # Mid-/ command without open palette: complete first match
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

        def action_slash_up(self) -> None:
            from textual.actions import SkipAction

            if getattr(self, "_live_side_open", False) and not self._nav_active and not self._slash_items:
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
            if self._nav_active:
                self._nav_index = max(0, self._nav_index - 1)
                self._paint_nav()
                return
            if self._session_pick_active:
                self._session_pick_index = max(0, self._session_pick_index - 1)
                self._paint_session_picker()
                return
            if not self._slash_items:
                raise SkipAction()
            self._slash_index = max(0, self._slash_index - 1)
            self._paint_slash_menu()

        def action_slash_down(self) -> None:
            from textual.actions import SkipAction

            if getattr(self, "_live_side_open", False) and not self._nav_active and not self._slash_items:
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
            if self._nav_active:
                items = self._nav_current_items()
                self._nav_index = min(len(items) - 1, self._nav_index + 1)
                self._paint_nav()
                return
            if self._session_pick_active:
                self._session_pick_index = min(len(self._session_pick_items) - 1, self._session_pick_index + 1)
                self._paint_session_picker()
                return
            if not self._slash_items:
                raise SkipAction()
            self._slash_index = min(len(self._slash_items) - 1, self._slash_index + 1)
            self._paint_slash_menu()

        def _hide_slash_menu(self) -> None:
            if not self._slash_items and not getattr(self, "_slash_visible", False):
                return
            self._slash_items = []
            self._slash_helps = []
            self._slash_index = 0
            self._slash_visible = False
            try:
                menu = self.query_one("#slash-menu", Static)
                menu.update("")
                menu.remove_class("visible")
            except Exception:
                pass

        def _paint_slash_menu(self) -> None:
            """OpenCode-style floating command list with highlight bar."""
            menu = self.query_one("#slash-menu", Static)
            if not self._slash_items:
                menu.update("")
                menu.remove_class("visible")
                self._slash_visible = False
                return
            lines: list[str] = []
            for i, text in enumerate(self._slash_items):
                help_text = ""
                if i < len(self._slash_helps):
                    help_text = self._slash_helps[i]
                # pad command column
                cmd = text.ljust(18)
                desc = (help_text[:42] + "…") if len(help_text) > 42 else help_text
                row = f" {cmd} {desc}"
                if i == self._slash_index:
                    # amber highlight bar (OpenCode-like)
                    lines.append(f"[bold black on #f59e0b]{self._escape_markup(row)}[/]")
                else:
                    lines.append(f"[#c9d1d9]{self._escape_markup(row)}[/]")
            lines.append("[dim]  ↑↓ 选择 · Enter/Tab 确认 · Esc 关闭[/]")
            menu.update("\n".join(lines))
            menu.add_class("visible")
            self._slash_visible = True

        def _refresh_slash_menu(self, value: str) -> None:
            if not value.startswith(("/", "+")):
                self._hide_slash_menu()
                return
            matches = slash_completions(value, limit=10)
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
            if self._nav_active or self._session_pick_active:
                if val.startswith(("/", "+")):
                    self._hide_nav()
                    self._hide_session_picker()
                    self._refresh_slash_menu(val)
                return
            self._refresh_slash_menu(val)

        def action_clear_log(self) -> None:
            self.clear_chat_view(note="Screen cleared")

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
                self._reply_flushed = False
                self._sending = False
                try:
                    self.end_wait()
                except Exception:
                    pass
                msg = (note or "Screen cleared").strip()
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
                "join_group": self.join_group,
                "leave_group": self.leave_group,
                "attach_image": self.attach_image,
                "detach_media": self.detach_media,
                "set_muted": self.set_muted,
                "history": self.show_history,
                "approve": self.approve,
                "reject": self.reject,
                "choose": self.choose,
                "clear_screen": lambda: self.clear_chat_view(note="Screen cleared"),
                "apply_theme": self.apply_theme,
            }

        # ── send ──────────────────────────────────────────────────────

        def _send_plain(self, line: str) -> None:
            if not self.client.token:
                self.log_line("Login first: /login", style="error")
                return
            if self.mode == "group" and self.group and line.isdigit():
                try:
                    if self.group.resolve_numeric_reply(line):
                        return
                except Exception as e:
                    self.log_line(str(e), style="error")
                    return
            if self.mode == "group":
                self._send_group(line)
            else:
                self._send_solo(line)

        @work(thread=True, group="chat-send")
        def _send_solo(self, line: str) -> None:
            if not self.agent:
                self.log_line("Select an agent: /agent <name> or /start <name>", style="error")
                self._sending = False
                return
            self._stream_buf = ""
            self._reply_flushed = False
            self._think_buf_latest = ""
            self._think_pending = False
            self.begin_wait(f"Connecting {self.agent}…")
            try:
                if not self.bridge or not getattr(self.bridge, "is_open", False):
                    if not self._ensure_agent_connected(self.agent):
                        self.log_line("Agent not ready — try /start then send again", style="error")
                        return
                if not self.bridge:
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
                self.call_from_thread(self._refresh_chrome)
                if chips:
                    self.log_line(f"sending with: {' '.join(chips)}", style="system")
                self.update_wait("Thinking…")
                try:
                    self.bridge.turn_reset()
                    self.bridge.send_chat(line, images=images or None, attachments=attachments or None)
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
                self.end_wait()
                self.call_from_thread(self._focus_input)
                self.call_from_thread(self._drain_send_queue)

        def _drain_send_queue(self) -> None:
            if self._sending or self.mode == "group":
                return
            if not self._send_queue:
                self._refresh_chrome()
                return
            line, snap = self._send_queue.popleft()
            self.pending_media = list(snap)
            self.log_line(f"Dequeued — sending next ({len(self._send_queue)} left)", style="system")
            self._follow_chat = True
            self._sending = True
            self._refresh_chrome()
            self._send_solo(line)

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
            self.log_line(f"Starting agent '{target}'…", style="system")
            self._bootstrap_agent(target, then_new=False)

        @work(thread=True, exclusive=True, group="agent-boot")
        def _bootstrap_agent(self, name: str, then_new: bool = False) -> None:
            self.begin_wait(f"Starting agent {name}…")
            try:
                ok = self._ensure_agent_connected(name)
                if ok:
                    self.log_line(f"Connected to agent '{name}' (solo)", style="system")
                    if then_new and self.bridge:
                        self.update_wait("Opening new session…")
                        try:
                            self.bridge.send_command("new_session")
                            self.log_line("new session requested", style="system")
                        except Exception as e:
                            self.log_line(str(e), style="error")
                else:
                    self.log_line(
                        f"Agent '{name}' not ready yet. Retry /start or wait and /new",
                        style="error",
                    )
            finally:
                self.end_wait()
                self.call_from_thread(self._refresh_chrome)
                self.call_from_thread(self._focus_input)

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
                    self.update_wait(f"Restarting {name} (re-register)…")
                    self.client.admin_post(f"agents/{name}/restart")
                else:
                    self.client.admin_post(f"agents/{name}/start")
            except Exception as e:
                msg = str(e).lower()
                if "already" not in msg and "running" not in msg:
                    self.log_line(f"agent start: {e}", style="system")

        def _wait_agent_ready(self, name: str, timeout: float = 90.0) -> bool:
            import time

            deadline = time.time() + timeout
            while time.time() < deadline:
                info = self._lookup_agent(name)
                if info and info.get("ready"):
                    return True
                status = (info or {}).get("process_status") or "?"
                reg = (info or {}).get("registry_status") or "offline"
                self.update_wait(f"Waiting {name} ready (proc={status} registry={reg})…")
                time.sleep(0.8)
            return self._agent_is_ready(name)

        def _ensure_agent_connected(self, name: str) -> bool:
            """Start agent if needed, wait ready, open WS. Safe to call from worker thread."""
            if not self.client.token:
                self.log_line("Login required: /login", style="error")
                return False
            self.agent = name
            info = self._lookup_agent(name)
            if info and info.get("ready"):
                self.update_wait(f"Connecting WebSocket to {name}…")
                ok = self._connect_agent_sync(name)
                if ok:
                    remember_agent(name)
                return ok

            # Process alive but not in Gateway registry → restart so it re-registers
            if info and info.get("process_status") == "running" and not info.get("ready"):
                self.log_line(
                    f"{name} process running but registry offline — restarting…",
                    style="system",
                )
                self._start_agent_process(name, force_restart=True)
            else:
                self.update_wait(f"Starting process {name}…")
                self._start_agent_process(name, force_restart=False)

            if not self._wait_agent_ready(name):
                return False
            self.update_wait(f"Connecting WebSocket to {name}…")
            ok = self._connect_agent_sync(name)
            if ok:
                remember_agent(name)
            return ok

        def _connect_agent_sync(self, name: str) -> bool:
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
            self.bridge.on_side_chunk = lambda key, kind, title, text: self.call_from_thread(
                lambda k=key, kd=kind, t=title, x=text: self._on_side_chunk(k, kd, t, x)
            )
            self.bridge.on_side_summary = lambda s: self.call_from_thread(
                lambda msg=s: self.log_line(msg, style="tool")
            )
            self.bridge.on_side_done = lambda key: self.call_from_thread(lambda k=key: self._on_side_done(k))
            self._sync_status_from_agent(name)
            try:
                # Longer retries: process may still be binding after ready=true
                self.bridge.connect(retries=12, delay=0.8)
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
            m = str(data.get("model") or "").strip()
            if m and (not self._model_label or self._model_label == "—"):
                self._model_label = self._pretty_model_label(self._model_card, m)
            self._refresh_chrome()

        def _on_model_info(self, card: str | None, model: str | None) -> None:
            c = str(card or "").strip()
            m = str(model or "").strip()
            if c:
                self._model_card = c
            if c or m:
                self._model_label = self._pretty_model_label(c, m)
            self._refresh_chrome()

        def _on_reasoning_effort(self, effort: str) -> None:
            e = str(effort or "").strip().lower()
            if e in ("low", "medium", "high"):
                self._reasoning_effort = e
                self._refresh_chrome()

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
            try:
                self.group.connect(gid, group_name=gname or gid, history_limit=15)
            except Exception as e:
                self.log_line(f"group connect failed: {e}", style="error")
                self.group = None
                return
            self.group.set_active(True)
            self.mode = "group"
            self.log_line(f"Joined {gname} — mode=group (/leave to exit)", style="system")
            self._refresh_chrome()
            self._focus_input()

        def leave_group(self) -> None:
            if self.group:
                self.group.set_active(False)
                if self.muted:
                    self.group.close()
                    self.group = None
            self.mode = "solo"
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

        def open_nav(self, kind: str) -> None:
            """Entry from slash_dispatch: open interactive list for a resource kind."""
            kind = (kind or "").strip().lower()
            if kind in ("agents", "agentctl"):
                kind = "agent"
            if kind in ("theme", "themes"):
                self._open_theme_nav()
                return
            if kind == "session" or kind == "sessions":
                self._session_cmd("sessions")
                return
            self.begin_wait(f"Loading {kind}…")
            self._load_nav_kind(kind)

        def _open_theme_nav(self) -> None:
            from opensquad.cli.tui.nav_menus import build_theme_menu

            title, items = build_theme_menu(self, current=str(self.theme or ""))
            # Jump highlight to current theme
            cur = str(self.theme or "")
            idx = 0
            for i, it in enumerate(items):
                if getattr(it, "id", None) == cur:
                    idx = i
                    break
            self._push_nav(title, items, replace=True)
            self._nav_index = idx
            self._paint_nav()

        def apply_theme(self, name: str) -> None:
            """Apply a Textual theme by name and persist preference."""
            name = (name or "").strip()
            if not name:
                self._open_theme_nav()
                return
            available = self.available_themes or {}
            # fuzzy: case-insensitive + prefix
            key = name
            if key not in available:
                low = name.lower()
                matches = [n for n in list_theme_names(self) if n.lower() == low or n.lower().startswith(low)]
                if len(matches) == 1:
                    key = matches[0]
                elif matches:
                    self.log_line(
                        f"Ambiguous theme '{name}': " + ", ".join(matches[:8]),
                        style="system",
                    )
                    self._open_theme_nav()
                    return
                else:
                    self.log_line(
                        f"Unknown theme '{name}'. Try /theme",
                        style="error",
                    )
                    self._open_theme_nav()
                    return
            try:
                self.theme = key
                save_theme(key)
                self._refresh_chrome()
                if self._nav_active:
                    self._hide_nav()
                self.log_line(f"Theme → {key}", style="system")
                self._focus_input()
            except Exception as e:
                self.log_line(f"theme failed: {e}", style="error")

        def action_cycle_effort(self) -> None:
            order = ("low", "medium", "high")
            cur = (getattr(self, "_reasoning_effort", None) or "high").lower()
            try:
                idx = order.index(cur)
            except ValueError:
                idx = 2
            nxt = order[(idx + 1) % len(order)]
            self._reasoning_effort = nxt
            self._refresh_chrome()
            if self.bridge and getattr(self.bridge, "is_open", False):
                try:
                    self.bridge.send_command("set_reasoning_effort", {"effort": nxt})
                except Exception as e:
                    self.log_line(f"effort switch failed: {e}", style="error")
                    return
            self.log_line(f"Reasoning effort → {nxt}", style="system")
            self._focus_input()

        def action_toggle_live(self) -> None:
            if getattr(self, "_live_side_open", False):
                self._close_live_side()
            else:
                self._open_live_side()

        def _on_side_chunk(self, key: str, kind: str, title: str, text: str) -> None:
            self._side_hub.append(key, text, kind=kind, title=title)
            self._live_side_key = key
            self._refresh_chrome()
            if getattr(self, "_live_side_open", False):
                self._paint_live_side()

        def _on_side_done(self, key: str) -> None:
            self._side_hub.mark_done(key)
            self._refresh_chrome()

        def _open_live_side(self) -> None:
            stream = self._side_hub.get(self._live_side_key) or self._side_hub.get()
            if stream is None:
                keys = self._side_hub.list_keys()
                if not keys:
                    self.log_line("No live sub-agent/shell stream yet", style="system")
                    return
                self._live_side_key = keys[0]
                stream = self._side_hub.get(self._live_side_key)
            self._live_side_open = True
            try:
                chat = self.query_one("#chat-log", RichLog)
                side = self.query_one("#live-side", RichLog)
                chat.add_class("hidden")
                side.add_class("visible")
            except Exception:
                pass
            self._paint_live_side()
            self.log_line("[dim]Live view — Ctrl+X or Esc to return[/]", style="system")
            self._focus_input()

        def _close_live_side(self) -> None:
            self._live_side_open = False
            try:
                chat = self.query_one("#chat-log", RichLog)
                side = self.query_one("#live-side", RichLog)
                side.remove_class("visible")
                chat.remove_class("hidden")
            except Exception:
                pass
            self._focus_input()

        def _paint_live_side(self) -> None:
            stream = self._side_hub.get(self._live_side_key) or self._side_hub.get()
            if stream is None:
                return
            try:
                side = self.query_one("#live-side", RichLog)
                side.clear()
                side.write(
                    f"[bold]Live · {self._escape_markup(stream.kind)} · "
                    f"{self._escape_markup(stream.title)}[/]  [dim]Ctrl+X back[/]",
                    scroll_end=True,
                    animate=False,
                )
                side.write(stream.dump() or "[dim](waiting for output…)[/]", scroll_end=True, animate=False)
                side.scroll_end(animate=False)
            except Exception:
                pass

        @work(thread=True, group="nav-action")
        def _nav_connect_providers(self) -> None:
            from opensquad.cli.tui.nav_menus import build_provider_menu

            self.begin_wait("Loading providers…")
            try:
                try:
                    self.client.post("/api/ai-web/model-presets/refresh", json_body={})
                except Exception:
                    pass
                title, items = build_provider_menu(self.client)
                self.call_from_thread(lambda: self._push_nav(title, items, replace=False))
            except Exception as e:
                self.log_line(f"[model] presets: {e}", style="error")
            finally:
                self.end_wait()

        def _nav_provider_ask_key(self, provider: dict) -> None:
            if not isinstance(provider, dict) or not provider.get("id"):
                self.log_line("Invalid provider", style="error")
                return
            self._hide_nav()
            self._await_api_key = {"provider": provider}
            self.log_line(
                f"Paste API key for {provider.get('label') or provider.get('id')} then Enter",
                style="system",
            )
            self._refresh_chrome()
            self._focus_input()

        def _finish_provider_with_key(self, pending: dict, api_key: str) -> None:
            provider = pending.get("provider") or {}
            key = (api_key or "").strip()
            if not key:
                self.log_line("API key empty — cancelled", style="system")
                self._refresh_chrome()
                self._focus_input()
                return
            self.begin_wait("Saving model card…")
            self._save_provider_card(provider, key)

        @work(thread=True, group="nav-action")
        def _save_provider_card(self, provider: dict, api_key: str) -> None:
            from opensquad.cli.tui.nav_menus import build_provider_model_menu

            try:
                models = list(provider.get("models") or [])
                model = models[0] if models else {}
                pid = str(provider.get("id") or "provider")
                mn = str(model.get("model_name") or "default")
                slug = f"{pid}-{mn}".replace("/", "-").replace(" ", "-").lower()
                slug = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in slug)[:64]
                card = {
                    "name": slug,
                    "title": str(model.get("title") or mn),
                    "provider": str(provider.get("provider") or provider.get("label") or pid),
                    "base_url": str(provider.get("base_url") or ""),
                    "api_protocol": str(provider.get("api_protocol") or "openai_compat"),
                    "api_key": api_key,
                    "model_name": mn,
                    "token_max": int(model.get("token_max") or 128000),
                    "temperature": model.get("temperature", 0),
                    "is_think": bool(model.get("is_think")),
                    "is_image": bool(model.get("is_image")),
                    "is_audio": bool(model.get("is_audio") or model.get("is_audio_output")),
                }
                self.client.admin_put(f"model-cards/{slug}", card)
                masked = api_key[:4] + "…" if len(api_key) > 4 else "***"
                self.log_line(f"Saved card '{slug}' (key {masked})", style="system")
                if self.agent:
                    body = dict(card)
                    body["card_name"] = slug
                    self.client.admin_put(f"agents/{self.agent}/model-card", body)
                    if self.bridge and getattr(self.bridge, "is_open", False):
                        try:
                            self.bridge.send_command("switch_model", {"card": slug})
                        except Exception:
                            pass
                    self._model_card = slug
                    self._model_label = self._pretty_model_label(slug, str(card.get("title") or ""))
                title, items = build_provider_model_menu(provider, card_name=slug)
                self.call_from_thread(lambda: self._push_nav(title, items, replace=True))
                self.call_from_thread(self._refresh_chrome)
            except Exception as e:
                self.log_line(f"[model] save failed: {e}", style="error")
            finally:
                self.end_wait()
                self.call_from_thread(self._focus_input)

        @work(thread=True, group="nav-action")
        def _nav_provider_use_model(self, provider: dict, model: dict, card_name: str) -> None:
            try:
                pid = str(provider.get("id") or "provider")
                mn = str(model.get("model_name") or "")
                slug = (card_name or f"{pid}-{mn}").replace("/", "-")[:64]
                # Load existing card to keep api_key
                existing = {}
                try:
                    existing = (self.client.admin_get(f"model-cards/{slug}") or {}).get("card") or {}
                except Exception:
                    existing = {}
                card = dict(existing) if isinstance(existing, dict) else {}
                card.update(
                    {
                        "name": slug,
                        "title": str(model.get("title") or mn),
                        "provider": str(provider.get("provider") or provider.get("label") or pid),
                        "base_url": str(provider.get("base_url") or card.get("base_url") or ""),
                        "api_protocol": str(
                            provider.get("api_protocol") or card.get("api_protocol") or "openai_compat"
                        ),
                        "model_name": mn,
                        "token_max": int(model.get("token_max") or card.get("token_max") or 128000),
                        "temperature": model.get("temperature", card.get("temperature", 0)),
                        "is_think": bool(model.get("is_think")),
                        "is_image": bool(model.get("is_image")),
                    }
                )
                if not card.get("api_key"):
                    self.log_line("Card missing api_key — Connect provider again", style="error")
                    return
                self.client.admin_put(f"model-cards/{slug}", card)
                if self.agent:
                    body = dict(card)
                    body["card_name"] = slug
                    self.client.admin_put(f"agents/{self.agent}/model-card", body)
                    if self.bridge and getattr(self.bridge, "is_open", False):
                        self.bridge.send_command("switch_model", {"card": slug})
                self._model_card = slug
                self._model_label = self._pretty_model_label(slug, str(card.get("title") or mn))
                self.log_line(f"Model → {card.get('title') or mn}", style="system")
                self.call_from_thread(self._hide_nav)
                self.call_from_thread(self._refresh_chrome)
            except Exception as e:
                self.log_line(f"[model] {e}", style="error")

        @work(thread=True, group="nav-load")
        def _load_nav_kind(self, kind: str) -> None:
            from opensquad.cli.tui.nav_menus import (
                build_agent_menu,
                build_collab_menu,
                build_group_menu,
                build_mcp_menu,
                build_model_menu,
                build_plugin_menu,
                build_role_menu,
                build_skill_menu,
            )

            try:
                if kind == "model":
                    title, items = build_model_menu(self.client, self.agent)
                elif kind == "skill":
                    title, items = build_skill_menu(self.client)
                elif kind == "role":
                    title, items = build_role_menu(self.client, self.agent)
                elif kind == "collab":
                    title, items = build_collab_menu(self.client)
                elif kind == "mcp":
                    title, items = build_mcp_menu(self.client)
                elif kind == "plugin":
                    title, items = build_plugin_menu(self.client)
                elif kind == "agent":
                    title, items = build_agent_menu(self.client, self.agent)
                elif kind == "group":
                    title, items = build_group_menu(self.client)
                else:
                    self.log_line(f"Unknown nav kind: {kind}", style="error")
                    return
                self.call_from_thread(lambda: self._push_nav(title, items, replace=True))
            except Exception as e:
                self.log_line(f"[{kind}] {e}", style="error")
            finally:
                self.end_wait()

        def _push_nav(self, title: str, items: list, *, replace: bool = False) -> None:
            self._hide_slash_menu()
            self._hide_session_picker()
            if replace or not self._nav_stack:
                self._nav_stack = [(title, list(items))]
            else:
                self._nav_stack.append((title, list(items)))
            self._nav_index = 0
            self._nav_active = True
            self._paint_nav()
            self.log_line(
                f"{title} — ↑↓ 选择 · Enter 进入/执行 · Esc 返回",
                style="system",
            )
            self._focus_input()

        def _nav_current_items(self) -> list:
            if not self._nav_stack:
                return []
            return self._nav_stack[-1][1]

        def _paint_nav(self) -> None:
            menu = self.query_one("#slash-menu", Static)
            if not self._nav_active or not self._nav_stack:
                menu.update("")
                menu.remove_class("visible")
                return
            title, items = self._nav_stack[-1]
            if not items:
                menu.update(f"[dim]  {self._escape_markup(title)} (empty)[/]")
                menu.add_class("visible")
                return
            idx = max(0, min(self._nav_index, len(items) - 1))
            self._nav_index = idx
            window = 12
            start = max(0, idx - window // 2)
            end = min(len(items), start + window)
            start = max(0, end - window)
            fg = self._theme_hex("foreground", "#e6edf3")
            hi = self._theme_hex("primary", "#58a6ff")
            lines: list[str] = [
                f"[bold {fg}] {self._escape_markup(title)}[/]  [dim]esc[/]"
                + (f"  [dim]depth {len(self._nav_stack)}[/]" if len(self._nav_stack) > 1 else "")
            ]
            for i in range(start, end):
                it = items[i]
                mark = getattr(it, "mark", " ") or " "
                label = str(getattr(it, "label", "") or "")
                detail = str(getattr(it, "detail", "") or "")
                if len(label) > 28:
                    label = label[:27] + "…"
                if len(detail) > 36:
                    detail = detail[:35] + "…"
                hint = " ›" if getattr(it, "children", None) else ""
                row = f" {mark} {label:<28} {detail}{hint}"
                if i == idx:
                    lines.append(f"[bold black on {hi}]{self._escape_markup(row)}[/]")
                else:
                    lines.append(f"[{fg}]{self._escape_markup(row)}[/]")
            lines.append("[dim]  ↑↓ 选择 · Enter/Tab 确认 · Esc 返回/关闭[/]")
            menu.update("\n".join(lines))
            menu.add_class("visible")

        def _hide_nav(self) -> None:
            self._nav_active = False
            self._nav_stack = []
            self._nav_index = 0
            try:
                menu = self.query_one("#slash-menu", Static)
                if not self._slash_items and not self._session_pick_active:
                    menu.update("")
                    menu.remove_class("visible")
            except Exception:
                pass

        def _nav_back_or_close(self) -> None:
            if len(self._nav_stack) > 1:
                self._nav_stack.pop()
                self._nav_index = 0
                self._paint_nav()
            else:
                self._hide_nav()

        def _nav_confirm(self) -> None:
            items = self._nav_current_items()
            if not items:
                return
            idx = max(0, min(self._nav_index, len(items) - 1))
            item = items[idx]
            children = getattr(item, "children", None)
            if children:
                self._push_nav(str(item.label), list(children), replace=False)
                return
            action = getattr(item, "action", None)
            data = dict(getattr(item, "data", None) or {})
            if action in (None, "nav.noop"):
                return
            if action == "nav.back":
                self._nav_back_or_close()
                return
            self._run_nav_action(str(action), data)

        def _run_nav_action(self, action: str, data: dict[str, Any]) -> None:
            """Execute a leaf nav action (may open another submenu)."""
            try:
                if action == "model.use":
                    self._nav_model_use(str(data.get("name") or ""))
                elif action == "model.assign_pick":
                    self._nav_model_assign_pick(str(data.get("name") or ""))
                elif action == "model.assign_to":
                    self._nav_model_assign_to(str(data.get("name") or ""), str(data.get("agent") or ""))
                elif action == "model.show":
                    self._nav_show_json(f"model-cards/{data.get('name')}", "model")
                elif action == "model.connect_providers":
                    self._nav_connect_providers()
                elif action == "model.provider_pick":
                    self._nav_provider_ask_key(data.get("provider") or {})
                elif action == "model.provider_use_model":
                    self._nav_provider_use_model(
                        data.get("provider") or {},
                        data.get("model") or {},
                        str(data.get("card_name") or ""),
                    )
                elif action == "theme.apply":
                    self.apply_theme(str(data.get("name") or ""))
                elif action == "skill.show":
                    self._nav_skill_show(str(data.get("name") or ""))
                elif action == "skill.rm":
                    self._nav_delete(f"skills/{data.get('name')}", f"skill {data.get('name')}")
                elif action == "role.assign":
                    self._nav_role_assign(str(data.get("name") or ""))
                elif action == "role.show":
                    self._nav_role_show(str(data.get("name") or ""))
                elif action == "collab.show":
                    self._nav_text(f"collab-cards/{data.get('name')}", "collab")
                elif action == "collab.board":
                    self._nav_collab_board()
                elif action == "mcp.toggle":
                    self._nav_mcp_toggle(str(data.get("name") or ""), bool(data.get("enable")))
                elif action == "mcp.show":
                    self._nav_mcp_show(str(data.get("name") or ""))
                elif action == "plugin.toggle":
                    self._nav_plugin_toggle(str(data.get("id") or ""), bool(data.get("enable")))
                elif action == "plugin.status":
                    self._nav_show_json(f"plugins/{data.get('id')}", "plugin")
                elif action == "agent.switch":
                    self._hide_nav()
                    self._switch_agent(str(data.get("name") or ""))
                elif action == "agent.start":
                    self._hide_nav()
                    self.start_agent(str(data.get("name") or ""))
                elif action == "group.join":
                    self._hide_nav()
                    self.join_group(str(data.get("ref") or ""))
                elif action == "session.switch":
                    self._hide_nav()
                    self._switch_session(str(data.get("id") or ""), str(data.get("title") or ""))
                else:
                    self.log_line(f"Unhandled action: {action}", style="error")
            except Exception as e:
                self.log_line(str(e), style="error")

        @work(thread=True, group="nav-action")
        def _nav_model_use(self, name: str) -> None:
            if not name:
                return
            if not self.agent:
                self.log_line("Select an agent first: /agent <name>", style="error")
                return
            self.begin_wait(f"Using model {name}…")
            try:
                data = self.client.admin_get(f"model-cards/{name}")
                card = data.get("card") or data
                body = dict(card) if isinstance(card, dict) else {}
                body["card_name"] = name
                self.client.admin_put(f"agents/{self.agent}/model-card", body)
                if self.bridge and getattr(self.bridge, "is_open", False):
                    try:
                        self.bridge.send_command("switch_model", {"card": name})
                    except Exception as e:
                        self.log_line(f"runtime switch: {e}", style="system")
                self._model_card = name
                # Prefer card title from API when present
                title = ""
                if isinstance(card, dict):
                    title = str(card.get("title") or card.get("display_name") or "").strip()
                self._model_label = title or self._pretty_model_label(name)
                self.log_line(f"Model '{name}' → agent '{self.agent}'", style="system")
                self.call_from_thread(self._hide_nav)
                self.call_from_thread(self._refresh_chrome)
            except Exception as e:
                self.log_line(f"[model] {e}", style="error")
            finally:
                self.end_wait()
                self.call_from_thread(self._focus_input)

        @work(thread=True, group="nav-action")
        def _nav_model_assign_pick(self, name: str) -> None:
            from opensquad.cli.tui.nav_menus import build_agent_pick_menu

            try:
                title, items = build_agent_pick_menu(
                    self.client,
                    action="model.assign_to",
                    payload={"name": name},
                    title=f"Assign '{name}' → agent",
                )
                self.call_from_thread(lambda: self._push_nav(title, items, replace=False))
            except Exception as e:
                self.log_line(f"[model] {e}", style="error")

        @work(thread=True, group="nav-action")
        def _nav_model_assign_to(self, name: str, agent: str) -> None:
            if not name or not agent:
                return
            self.begin_wait(f"Assign {name} → {agent}…")
            try:
                data = self.client.admin_get(f"model-cards/{name}")
                card = data.get("card") or data
                body = dict(card) if isinstance(card, dict) else {}
                body["card_name"] = name
                self.client.admin_put(f"agents/{agent}/model-card", body)
                self.log_line(f"Assigned model '{name}' → '{agent}'", style="system")
                self.call_from_thread(self._hide_nav)
            except Exception as e:
                self.log_line(f"[model] {e}", style="error")
            finally:
                self.end_wait()

        @work(thread=True, group="nav-action")
        def _nav_show_json(self, path: str, tag: str) -> None:
            try:
                data = self.client.admin_get(path)
                import json

                safe = redact_secrets(data)
                text = json.dumps(safe, ensure_ascii=False, indent=2)
                if len(text) > 2000:
                    text = text[:2000] + "\n…"
                self.log_line(f"[{tag}] {path}", style="system")
                for line in text.splitlines():
                    self.log_line(line, style="system")
            except Exception as e:
                self.log_line(f"[{tag}] {e}", style="error")

        @work(thread=True, group="nav-action")
        def _nav_skill_show(self, name: str) -> None:
            try:
                data = self.client.admin_get(f"skills/{name}/source")
                md = data.get("skill_md") or ""
                self.log_line(f"[skill] {name}", style="system")
                for line in (md or str(data)).splitlines()[:80]:
                    self.log_line(line, style="system")
            except Exception as e:
                self.log_line(f"[skill] {e}", style="error")

        @work(thread=True, group="nav-action")
        def _nav_delete(self, path: str, label: str) -> None:
            try:
                self.client.admin_delete(path)
                self.log_line(f"Deleted {label}", style="system")
                self.call_from_thread(self._hide_nav)
            except Exception as e:
                self.log_line(str(e), style="error")

        @work(thread=True, group="nav-action")
        def _nav_role_assign(self, name: str) -> None:
            if not self.agent:
                self.log_line("Select an agent first", style="error")
                return
            try:
                card = self.client.admin_get(f"role-cards/{name}")
                content = card.get("content") or ""
                self.client.admin_put(
                    f"agents/{self.agent}/role-prompt",
                    {"content": content, "card_name": name},
                )
                self.log_line(f"Role '{name}' → '{self.agent}'", style="system")
                self.call_from_thread(self._hide_nav)
            except Exception as e:
                self.log_line(f"[role] {e}", style="error")

        @work(thread=True, group="nav-action")
        def _nav_role_show(self, name: str) -> None:
            try:
                data = self.client.admin_get(f"role-cards/{name}")
                content = data.get("content") or ""
                self.log_line(f"[role] {name}", style="system")
                for line in content.splitlines()[:80]:
                    self.log_line(line, style="system")
            except Exception as e:
                self.log_line(f"[role] {e}", style="error")

        @work(thread=True, group="nav-action")
        def _nav_text(self, path: str, tag: str) -> None:
            try:
                data = self.client.admin_get(path)
                content = data.get("content") or ""
                self.log_line(f"[{tag}] {path}", style="system")
                for line in str(content).splitlines()[:80]:
                    self.log_line(line, style="system")
            except Exception as e:
                self.log_line(f"[{tag}] {e}", style="error")

        @work(thread=True, group="nav-action")
        def _nav_collab_board(self) -> None:
            try:
                data = self.client.ai_web_get("collab-board/tasks")
                tasks = data.get("tasks") or []
                self.log_line(f"[collab] board tasks ({len(tasks)})", style="system")
                for t in tasks[:30]:
                    tid = t.get("task_id") or ""
                    name = t.get("task_name") or ""
                    st = t.get("status") or ""
                    self.log_line(f"  {tid}  {name}  [{st}]", style="system")
            except Exception as e:
                self.log_line(f"[collab] {e}", style="error")

        @work(thread=True, group="nav-action")
        def _nav_mcp_toggle(self, name: str, enable: bool) -> None:
            from opensquad.cli.commands.mcp_cmd import _toggle

            try:
                _toggle(self.client, name, enable)
                self.log_line(
                    f"MCP '{name}' {'enabled' if enable else 'disabled'}",
                    style="system",
                )
                self.call_from_thread(lambda: self.open_nav("mcp"))
            except Exception as e:
                self.log_line(f"[mcp] {e}", style="error")

        @work(thread=True, group="nav-action")
        def _nav_mcp_show(self, name: str) -> None:
            import json

            try:
                from opensquad.cli.commands.mcp_cmd import _get_servers

                servers = _get_servers(self.client)
                cfg = servers.get(name) or {}
                text = json.dumps(cfg, ensure_ascii=False, indent=2)
                self.log_line(f"[mcp] {name}", style="system")
                for line in text.splitlines()[:60]:
                    self.log_line(line, style="system")
            except Exception as e:
                self.log_line(f"[mcp] {e}", style="error")

        @work(thread=True, group="nav-action")
        def _nav_plugin_toggle(self, pid: str, enable: bool) -> None:
            try:
                action = "enable" if enable else "disable"
                self.client.admin_put(f"plugins/{pid}/{action}", {})
                self.log_line(f"Plugin '{pid}' {action}d", style="system")
                self.call_from_thread(lambda: self.open_nav("plugin"))
            except Exception as e:
                self.log_line(f"[plugin] {e}", style="error")

        def _session_cmd(self, name: str, args: list | None = None) -> None:
            args = list(args or [])
            if name in ("sessions", "session"):
                if not self.agent:
                    self.log_line("No agent — /agent <name> first", style="error")
                    return
                if args:
                    self._switch_session_ref(args[0])
                    return
                self.begin_wait("Loading sessions…")
                self._fetch_and_show_sessions()
                return

            if name == "new":
                if not self.agent:
                    self.log_line("Select an agent first: /agent <name>", style="error")
                    return
                self.log_line("Preparing session (start agent if needed)…", style="system")
                self._bootstrap_agent(self.agent, then_new=True)
                return

            if not self.bridge or not getattr(self.bridge, "is_open", False):
                self.log_line("Agent offline — /start then retry", style="error")
                return
            try:
                if name == "stop":
                    nq = len(self._send_queue)
                    self._send_queue.clear()
                    self.bridge.send_command("stop_task")
                    self.bridge.turn_done()
                    self._sending = False
                    self.log_line(
                        "stop requested" + (f" · cleared {nq} queued" if nq else ""),
                        style="system",
                    )
                    self._refresh_chrome()
                elif name == "compress":
                    self.begin_wait("Compressing context…")
                    self.bridge.send_command("compress_context")
                    self.log_line("compress requested — view will clear when done", style="system")
            except Exception as e:
                self.log_line(str(e), style="error")

        def _agent_session_key(self) -> str:
            """Canonical agent_id for session APIs (dir_name → registry id)."""
            try:
                return self.client.resolve_agent_ws_id(self.agent or "")
            except Exception:
                return self.agent or ""

        @work(thread=True, group="session-list")
        def _fetch_and_show_sessions(self) -> None:
            try:
                key = self._agent_session_key()
                data = self.client.ai_web_get(f"agent-sessions/{key}/list", timeout=20)
                sessions = list(data.get("sessions") or [])
                current = data.get("current_session_id")
                self.call_from_thread(lambda: self._show_sessions_as_nav(sessions, current))
            except Exception as e:
                self.log_line(str(e), style="error")
                self.log_line(
                    "Tip: use /session <n> after list, or ensure agent id resolves (agent305 → agent305-001)",
                    style="system",
                )
            finally:
                self.end_wait()

        def _show_sessions_as_nav(self, sessions: list[dict], current_id: str | None) -> None:
            from opensquad.cli.tui.nav_menus import build_session_menu

            self._session_current_id = current_id
            self._session_pick_items = sessions  # keep for /session <n>
            title, items = build_session_menu(sessions, current_id, self.agent)
            self._push_nav(title, items, replace=True)

        def _show_session_picker(self, sessions: list[dict], current_id: str | None) -> None:
            # Back-compat alias
            self._show_sessions_as_nav(sessions, current_id)

        def _paint_session_picker(self) -> None:
            menu = self.query_one("#slash-menu", Static)
            if not self._session_pick_active or not self._session_pick_items:
                menu.update("")
                menu.remove_class("visible")
                return
            # Show a window around the selection
            items = self._session_pick_items
            idx = max(0, min(self._session_pick_index, len(items) - 1))
            self._session_pick_index = idx
            window = 10
            start = max(0, idx - window // 2)
            end = min(len(items), start + window)
            start = max(0, end - window)
            lines: list[str] = []
            lines.append(f"[bold #e6edf3] Sessions · {self.agent}[/]  [dim]{len(items)} total[/]")
            for i in range(start, end):
                s = items[i]
                sid = str(s.get("id") or "")
                title = (s.get("title") or "(untitled)").replace("\n", " ")
                if len(title) > 36:
                    title = title[:35] + "…"
                mark = "*" if (s.get("current") or sid == self._session_current_id) else " "
                row = f" [{i + 1}]{mark} {sid[:14]:<14}  {title}"
                if i == idx:
                    lines.append(f"[bold black on #f59e0b]{self._escape_markup(row)}[/]")
                else:
                    lines.append(f"[#c9d1d9]{self._escape_markup(row)}[/]")
            lines.append("[dim]  ↑↓ 选择 · Enter/Tab 切换 · Esc 取消 · /session <n> 直达[/]")
            menu.update("\n".join(lines))
            menu.add_class("visible")

        def _hide_session_picker(self) -> None:
            self._session_pick_active = False
            self._session_pick_items = []
            self._session_pick_index = 0
            try:
                menu = self.query_one("#slash-menu", Static)
                if not self._slash_items:
                    menu.update("")
                    menu.remove_class("visible")
            except Exception:
                pass

        def _confirm_session_pick(self) -> None:
            if not self._session_pick_active or not self._session_pick_items:
                return
            idx = max(0, min(self._session_pick_index, len(self._session_pick_items) - 1))
            s = self._session_pick_items[idx]
            sid = str(s.get("id") or "")
            title = str(s.get("title") or sid)
            self._hide_session_picker()
            if not sid:
                self.log_line("Invalid session", style="error")
                return
            self._switch_session(sid, title)

        def _switch_session_ref(self, ref: str) -> None:
            """Switch by 1-based index or session id prefix/full id."""
            ref = (ref or "").strip()
            if not ref:
                return
            if self._session_pick_items and ref.isdigit():
                n = int(ref)
                if 1 <= n <= len(self._session_pick_items):
                    s = self._session_pick_items[n - 1]
                    self._hide_session_picker()
                    self._switch_session(str(s.get("id") or ""), str(s.get("title") or ""))
                    return
            # Fetch list then resolve
            self.begin_wait("Resolving session…")
            self._resolve_and_switch(ref)

        @work(thread=True, group="session-switch")
        def _resolve_and_switch(self, ref: str) -> None:
            try:
                key = self._agent_session_key()
                data = self.client.ai_web_get(f"agent-sessions/{key}/list", timeout=20)
                sessions = list(data.get("sessions") or [])
                target = None
                if ref.isdigit():
                    n = int(ref)
                    if 1 <= n <= len(sessions):
                        target = sessions[n - 1]
                if target is None:
                    for s in sessions:
                        sid = str(s.get("id") or "")
                        if sid == ref or sid.startswith(ref):
                            target = s
                            break
                if not target:
                    self.log_line(f"Session not found: {ref}", style="error")
                    return
                sid = str(target.get("id") or "")
                title = str(target.get("title") or sid)
                self.call_from_thread(lambda: self._switch_session(sid, title))
            except Exception as e:
                self.log_line(str(e), style="error")
            finally:
                self.end_wait()

        def _switch_session(self, sid: str, title: str = "") -> None:
            if not sid:
                return
            if not self.bridge or not getattr(self.bridge, "is_open", False):
                self.log_line("Agent offline — /start then /session", style="error")
                return
            self.log_line(f"Switching → {title or sid}", style="system")
            self.begin_wait("Switching session…")
            self._do_switch_session(sid, title)

        @work(thread=True, group="session-switch")
        def _do_switch_session(self, sid: str, title: str) -> None:
            try:
                assert self.bridge is not None
                self.bridge.send_command(
                    "switch_and_reply",
                    {"session_id": sid, "content": ""},
                )
                key = self._agent_session_key()
                hist = None
                try:
                    hist = self.client.ai_web_get(
                        f"agent-sessions/{key}/{sid}/paged",
                        params={"offset": 0, "limit": 40},
                        timeout=20,
                    )
                except Exception:
                    hist = None
                session = (hist or {}).get("session") if isinstance(hist, dict) else hist
                self.call_from_thread(lambda: self._render_switched_session(sid, title, session))
            except Exception as e:
                self.log_line(f"switch failed: {e}", style="error")
            finally:
                self.end_wait()
                self.call_from_thread(self._focus_input)

        def _render_switched_session(self, sid: str, title: str, session: Any) -> None:
            try:
                log = self.query_one("#chat-log", RichLog)
                log.clear()
            except Exception:
                pass
            self._session_current_id = sid
            self._chat_write(
                f"[bold #e6edf3]Session[/]  {self._escape_markup(title or sid)}  [dim]{sid}[/]",
                follow=True,
            )
            msgs = []
            if isinstance(session, dict):
                msgs = session.get("messages") or []
            shown = 0
            for m in msgs[-20:]:
                if not isinstance(m, dict):
                    continue
                role = (m.get("role") or "").lower()
                content = m.get("content") or m.get("text") or ""
                if isinstance(content, list):
                    parts = []
                    for p in content:
                        if isinstance(p, dict) and p.get("text"):
                            parts.append(str(p["text"]))
                        elif isinstance(p, str):
                            parts.append(p)
                    content = "\n".join(parts)
                text = str(content).strip()
                if not text:
                    continue
                if len(text) > 500:
                    text = text[:500] + "…"
                if role in ("user", "human"):
                    self.log_line(text, style="user")
                elif role in ("assistant", "agent", "ai"):
                    self.log_line(text, style="agent")
                else:
                    self.log_line(text, style="system")
                shown += 1
            self.log_line(
                f"Switched session ({shown} recent messages). Continue chatting.",
                style="system",
            )
            self._refresh_chrome()

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
            self.pending_media.clear()
            self.log_line(f"cleared {n} pending", style="system")
            self._refresh_chrome()

        def set_muted(self, muted: bool) -> None:
            self.muted = muted
            if self.group:
                self.group.set_muted(muted)
            self.log_line(f"background alerts {'muted' if muted else 'on'}", style="system")

        def show_history(self, n: int = 20) -> None:
            if self.mode == "group" and self.group and self.group.group_id:
                from opensquad.cli.group_render import format_message_lines

                try:
                    msgs = self.client.get(
                        f"/api/groups/{self.group.group_id}/messages",
                        params={"limit": n},
                    )
                    if isinstance(msgs, list):
                        for m in msgs:
                            if isinstance(m, dict):
                                for line in format_message_lines(m, shell_style=True):
                                    self.log_line(line.lstrip("\n") or line)
                except Exception as e:
                    self.log_line(str(e), style="error")
                return
            self._session_cmd("sessions")

        def approve(self, approval_id: str | None = None, note: str = "") -> None:
            if not self.group:
                self.log_line("Join a group first", style="error")
                return
            try:
                self.group.resolve_approval(approval_id, reject=False, note=note)
                self.log_line(f"approved {approval_id or '(latest)'}", style="system")
            except Exception as e:
                self.log_line(str(e), style="error")

        def reject(self, approval_id: str | None = None, note: str = "") -> None:
            if not self.group:
                self.log_line("Join a group first", style="error")
                return
            try:
                self.group.resolve_approval(approval_id, reject=True, note=note)
                self.log_line(f"rejected {approval_id or '(latest)'}", style="system")
            except Exception as e:
                self.log_line(str(e), style="error")

        def choose(self, proposal_id: str | None, value: str) -> None:
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
