"""Wait status, shimmer and turn-meter timers (extracted from app.py)."""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from functools import partial

from opensquad.cli.tui.i18n import t


class WaitTimersMixin:
    """Mixin methods moved from cli/tui/app.py (see app.py for the app class)."""

    def _schedule_ui(self, callback, *args, **kwargs) -> None:
        """Run ``callback`` on the UI thread without blocking the caller.

        Textual's ``call_from_thread`` waits on ``future.result()``. If the UI
        thread is mid HTTP (e.g. /group approve) while the group WS thread
        schedules a log/picker update, both sides deadlock. Fire-and-forget
        keeps the WS reader free so the Gateway can finish the HTTP response.
        """
        if getattr(self, "_thread_id", None) == threading.get_ident():
            callback(*args, **kwargs)
            return
        loop = getattr(self, "_loop", None)
        if loop is None:
            return

        bound = partial(callback, *args, **kwargs)

        async def _run() -> None:
            with self._context():
                from textual._callback import invoke

                await invoke(bound)

        try:
            asyncio.run_coroutine_threadsafe(_run(), loop)
        except Exception:
            pass

    def begin_wait(self, label: str) -> None:
        """Show spinner for long ops. Banner row is always reserved (no layout jump)."""
        self._wait_gen = int(getattr(self, "_wait_gen", 0) or 0) + 1
        gen = self._wait_gen

        def _start() -> None:
            # Drop stale begin if end_wait already advanced the generation
            if gen != getattr(self, "_wait_gen", 0):
                return
            self._wait_label = self._sanitize_wait_label(label)
            self._wait_tick = 0
            if self._wait_timer is None:
                # Win: ≤1Hz — frequent Static/Input updates ghost the dock
                interval = 1.0 if sys.platform == "win32" else 0.25
                self._wait_timer = self.set_interval(interval, self._tick_wait)
            self._paint_wait()

        try:
            self.call_from_thread(_start)
        except Exception:
            try:
                _start()
            except Exception:
                pass

    def update_wait(self, label: str) -> None:
        gen = int(getattr(self, "_wait_gen", 0) or 0)

        def _upd() -> None:
            if gen != getattr(self, "_wait_gen", 0):
                return
            new_label = self._sanitize_wait_label(label)
            if new_label == self._wait_label:
                return
            self._wait_label = new_label
            self._paint_wait()

        try:
            self.call_from_thread(_upd)
        except Exception:
            try:
                _upd()
            except Exception:
                pass

    def end_wait(self) -> None:
        # Invalidate any in-flight begin_wait / update_wait from workers
        self._wait_gen = int(getattr(self, "_wait_gen", 0) or 0) + 1

        def _stop() -> None:
            self._wait_label = None
            if self._wait_timer is not None:
                try:
                    self._wait_timer.stop()
                except Exception:
                    pass
                self._wait_timer = None
            self._static_set("#wait-banner", "")
            # Do NOT full _refresh_chrome here — thrash Win CMD dock rows
            if not self._shimmer_active():
                self._stop_shimmer_timer()

        try:
            self.call_from_thread(_stop)
        except Exception:
            try:
                _stop()
            except Exception:
                pass

    def _sanitize_wait_label(self, label: str) -> str:
        """Short, human status only — never echo URLs / API paths into the banner."""
        text = " ".join(str(label or "").split())
        if not text:
            return t("wait_working")
        # Tool activity from _write_tool_line — keep label (do not map to Preparing)
        if text.startswith(("●", "⚙")):
            return text[:56] + ("…" if len(text) > 56 else "")
        low = text.lower()
        # Keep intentional boot / agent-start progress (do not collapse to Preparing)
        boot_keep = (
            "starting services",
            "starting agent",
            "waiting for",
            "connecting to",
            "agent ready",
            "正在启动",
            "等待",
            "正在连接",
            "已就绪",
        )
        if any(k in low or k in text for k in boot_keep):
            if len(text) > 56:
                return text[:55] + "…"
            return text
        if not getattr(self, "_debug_mode", False):
            if "thinking" in low or "思考" in text:
                return t("wait_thinking")
            if "reply" in low or "回复" in text:
                return t("wait_replying")
            # Generic lifecycle chatter only
            if any(
                k in low
                for k in (
                    "websocket",
                    "preparing",
                    "booting",
                )
            ):
                return t("wait_preparing")
        if "http://" in low or "https://" in low or "/api/" in low:
            if "thinking" in low or "思考" in text:
                return t("wait_thinking")
            if "reply" in low or "回复" in text:
                return t("wait_replying")
            if "connect" in low or "连接" in text:
                return t("wait_connecting")
            return t("wait_working")
        # Cap length so stream previews cannot shove URLs under the prompt
        if len(text) > 56:
            text = text[:55] + "…"
        return text

    def _tick_wait(self) -> None:
        # Idle: stop timer so we never keep repainting the dock on Win CMD
        if not self._wait_label and getattr(self, "_turn_started_at", None) is None:
            if self._wait_timer is not None:
                try:
                    self._wait_timer.stop()
                except Exception:
                    pass
                self._wait_timer = None
            return
        self._wait_tick = int(getattr(self, "_wait_tick", 0) or 0) + 1
        # ↓ odometer is owned by _turn_meter_timer (smooth +1); wait tick only
        # refreshes the banner / keeps the wait timer alive.
        if self._wait_label:
            self._paint_wait()
        elif getattr(self, "_turn_started_at", None) is not None:
            # No banner but turn still open — ensure meter timer is running
            self._ensure_turn_meter_timer()

    def _paint_wait(self) -> None:
        if self._is_selecting():
            return
        if not self._wait_label:
            self._static_set("#wait-banner", "")
            return
        # Win CMD: static title only — shimmer next to bordered #prompt-frame ghosts the dock
        if sys.platform == "win32":
            muted = self._theme_hex("text-muted", "#8b949e")
            label_mk = f"[dim {muted}]{self._escape_markup(str(self._wait_label))}[/]"
            self._static_set("#wait-banner", f" {label_mk}")
        else:
            label_mk = self._shimmer_markup(str(self._wait_label))
            self._static_set("#wait-banner", f" {label_mk}")
        if self._shimmer_active():
            self._ensure_shimmer_timer()

    def _shimmer_active(self) -> bool:
        # Chat-area animation (thinking / open tools)
        if getattr(self, "_think_pending", False) or (getattr(self, "_open_tools", None) or {}):
            return True
        # Wait-banner shimmer only off Win (dock ghosting beside #prompt-frame)
        if sys.platform != "win32" and getattr(self, "_wait_label", None):
            return True
        return False

    def _ensure_shimmer_timer(self) -> None:
        if getattr(self, "_shimmer_timer", None) is not None:
            return

        def _start() -> None:
            if getattr(self, "_shimmer_timer", None) is not None:
                return
            # Higher Hz + 1-cell steps ≈ prior sweep speed, but much smoother
            interval = 0.055 if sys.platform == "win32" else 0.045
            self._shimmer_timer = self.set_interval(interval, self._tick_shimmer)

        if getattr(self, "_thread_id", None) == threading.get_ident():
            _start()
            return
        try:
            self.call_from_thread(_start)
        except Exception:
            try:
                _start()
            except Exception:
                pass

    def _stop_shimmer_timer(self) -> None:
        timer = getattr(self, "_shimmer_timer", None)
        if timer is None:
            return
        try:
            timer.stop()
        except Exception:
            pass
        self._shimmer_timer = None

    def _repaint_open_tools_shimmer(self) -> None:
        """Rewrite in-progress tool title rows so the sweep keeps moving."""
        tools = getattr(self, "_open_tools", None)
        if not isinstance(tools, dict) or not tools:
            return
        # High→low start so strip-index shifts stay correct
        for _key, meta in sorted(
            tools.items(),
            key=lambda kv: int(kv[1].get("start", -1)),
            reverse=True,
        ):
            if int(meta.get("strips", 0) or 0) <= 0:
                continue
            label = str(meta.get("name") or "tool")
            detail = str(meta.get("detail") or "")
            mk = self._tool_markup_parts("call", label, detail, "progress")
            if mk is None:
                continue
            updated = self._chat_replace_open(meta, mk, follow=False)
            if updated:
                meta["start"] = int(updated.get("start", meta.get("start", 0)))
                meta["strips"] = int(updated.get("strips", meta.get("strips", 0)))

    def _tick_shimmer(self) -> None:
        if not self._shimmer_active():
            self._stop_shimmer_timer()
            return
        if self._is_selecting():
            return
        self._shimmer_tick = int(getattr(self, "_shimmer_tick", 0) or 0) + 1
        tick = self._shimmer_tick
        # Win: never repaint #wait-banner here (static title only in _paint_wait)
        if getattr(self, "_wait_label", None) and sys.platform != "win32":
            self._paint_wait()
        if getattr(self, "_think_pending", False):
            self._paint_cache.pop("live-think", None)
            self._paint_live_think()
        # RichLog splice is expensive — every 2nd frame keeps motion, cuts hitch
        if getattr(self, "_open_tools", None) and (tick % 2 == 0):
            try:
                self._repaint_open_tools_shimmer()
            except Exception:
                pass

    def _schedule_meter_paint(self) -> None:
        """Target rose from stream — keep the smooth ↓ odometer ticking."""
        self._ensure_turn_meter_timer()

    def _ensure_turn_meter_timer(self) -> None:
        """High-frequency tick so ↓ climbs like +1 instead of jumping by dozens."""
        if getattr(self, "_turn_meter_timer", None) is not None:
            return

        def _start() -> None:
            if getattr(self, "_turn_meter_timer", None) is not None:
                return
            # Win CMD: ~12Hz — faster ↓ climb; wait-banner is static so dock stays stable
            interval = 0.08 if sys.platform == "win32" else 0.04
            self._turn_meter_timer = self.set_interval(interval, self._tick_turn_meter)

        if getattr(self, "_thread_id", None) == threading.get_ident():
            _start()
            return
        try:
            self.call_from_thread(_start)
        except Exception:
            try:
                _start()
            except Exception:
                pass

    def _stop_turn_meter_timer(self) -> None:
        timer = getattr(self, "_turn_meter_timer", None)
        if timer is None:
            return
        try:
            timer.stop()
        except Exception:
            pass
        self._turn_meter_timer = None

    def _tick_turn_meter(self) -> None:
        if getattr(self, "_turn_started_at", None) is None:
            self._stop_turn_meter_timer()
            return
        if self._is_selecting():
            return
        changed = self._advance_out_display()
        # Also refresh when the elapsed clock text changes (even if ↓ idle)
        try:
            clock = self._turn_meter_plain()
        except Exception:
            clock = ""
        if changed or clock != getattr(self, "_meter_last_clock", None):
            self._meter_last_clock = clock
            self._paint_prompt_meta_only()

    def _advance_out_display(self) -> bool:
        """Move ↓ toward target like an odometer (+1); return True if changed."""
        target = max(0, int(getattr(self, "_turn_out_target", 0) or 0))
        display = max(0, int(getattr(self, "_turn_out_display", 0) or 0))
        if display >= target:
            return False
        gap = target - display
        # Odometer feel; Win paints ~12Hz with larger steps so catch-up feels snappy
        if sys.platform == "win32":
            if gap <= 8:
                step = 1
            elif gap <= 30:
                step = 2
            elif gap <= 80:
                step = 4
            elif gap <= 200:
                step = 6
            else:
                step = 8
        elif gap <= 80:
            step = 1
        elif gap <= 200:
            step = 2
        else:
            step = 3
        self._turn_out_display = display + min(gap, step)
        return True

    def _turn_meter_plain(self) -> str:
        """Up = context; down = this-turn output. Elapsed only while turn is active."""
        up = int(getattr(self, "_token_used", 0) or 0)
        started = getattr(self, "_turn_started_at", None)
        if started is not None:
            down = int(getattr(self, "_turn_out_display", 0) or 0)
            elapsed = time.monotonic() - float(started)
            return f"↑{self._fmt_tokens_smooth(up)} ↓{self._fmt_tokens_smooth(down)} · {self._fmt_duration(elapsed)}"
        # After turn ends: tokens only (elapsed lives on agent footer)
        down = int(getattr(self, "_last_turn_out", 0) or 0)
        if up or down:
            return f"↑{self._fmt_tokens_smooth(up)} ↓{self._fmt_tokens_smooth(down)}"
        return ""

    def _begin_turn_meter(self) -> None:
        """Start per-turn up/down / clock when the user sends a message."""

        def _start() -> None:
            self._turn_started_at = time.monotonic()
            self._turn_baseline_out = int(getattr(self, "_session_out_tokens", 0) or 0)
            self._turn_out_target = 0
            self._turn_out_display = 0
            self._last_turn_elapsed = None
            self._meter_paint_at = 0.0
            self._meter_last_clock = ""
            self._paint_prompt_meta_only()
            self._paint_wait()
            self._ensure_turn_meter_timer()

        try:
            self.call_from_thread(_start)
        except Exception:
            try:
                _start()
            except Exception:
                pass

    def _end_turn_meter(self) -> None:
        """Freeze elapsed + snap down to final turn output."""

        def _stop() -> None:
            self._stop_turn_meter_timer()
            started = getattr(self, "_turn_started_at", None)
            if started is not None:
                self._last_turn_elapsed = time.monotonic() - float(started)
            target = max(
                int(getattr(self, "_turn_out_target", 0) or 0),
                int(getattr(self, "_turn_out_display", 0) or 0),
            )
            real = max(
                0,
                int(getattr(self, "_session_out_tokens", 0) or 0) - int(getattr(self, "_turn_baseline_out", 0) or 0),
            )
            if real > 0:
                target = max(target, real)
            self._turn_out_display = target
            self._turn_out_target = target
            self._last_turn_out = target
            self._turn_started_at = None
            self._paint_prompt_meta_only(force=True)

        try:
            self.call_from_thread(_stop)
        except Exception:
            try:
                _stop()
            except Exception:
                pass
