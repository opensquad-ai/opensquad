"""Session list / switch flow (extracted from app.py)."""

from __future__ import annotations

from typing import Any

from textual import work
from textual.widgets import Static

from opensquad.cli.tui.i18n import t
from opensquad.cli.tui.selectable_rich_log import SelectableRichLog as RichLog


class SessionsMixin:
    """Mixin methods moved from cli/tui/app.py (see app.py for the app class)."""

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
                self._hold_queue_after_stop = False
                self.bridge.send_command("stop_task", {"all": True})
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
        lines.append(f"[dim]  {t('hint_session_picker')}[/]")
        menu.update("\n".join(lines))
        menu.add_class("visible")
        self._sync_prompt_dock_menu()

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
        self._sync_prompt_dock_menu()

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
