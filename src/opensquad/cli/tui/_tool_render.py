"""Tool-line rendering for the OpenSquad TUI (extracted from app.py)."""

from __future__ import annotations

import re
import time
from typing import Any

from opensquad.cli.tui.i18n import t
from opensquad.cli.tui.selectable_rich_log import SelectableRichLog as RichLog


class ToolRenderMixin:
    """Mixin methods moved from cli/tui/app.py (see app.py for the app class)."""

    def _shift_open_tool_starts(self, at: int, delta: int) -> None:
        """After splicing chat strips, keep later open-tool indices in sync."""
        if delta == 0 or at < 0:
            return
        for meta in (getattr(self, "_open_tools", None) or {}).values():
            try:
                start = int(meta.get("start", -1))
            except Exception:
                continue
            if start >= at:
                meta["start"] = start + delta

    def _chat_replace_open(
        self,
        open_meta: dict[str, Any] | None,
        content: Any,
        *,
        follow: bool | None = True,
    ) -> dict[str, Any] | None:
        """Replace a previously written open row (tool/think) in-place when possible."""
        log = self.query_one("#chat-log", RichLog)
        if open_meta:
            start = int(open_meta.get("start", -1))
            strips = int(open_meta.get("strips", 0))
            end = start + strips
            # Prefer rewrite when the open row is still the tail (common path)
            if strips > 0 and start >= 0 and end == len(log.lines):
                self._chat_pop_strips(strips)
                n = self._chat_write_counted(content, follow=follow, shrink=True)
                self._shift_open_tool_starts(start + 1, n - strips)
                return {"start": start, "strips": n, "name": open_meta.get("name", "")}
            elif strips > 0 and 0 <= start < len(log.lines) and end <= len(log.lines):
                # Content was appended after the open row — splice it out
                try:
                    from textual.geometry import Size

                    tail = list(log.lines[end:]) if end < len(log.lines) else []
                    del log.lines[start:]
                    log._line_cache.clear()
                    log.virtual_size = Size(
                        int(getattr(log, "_widest_line_width", 0) or 0),
                        len(log.lines),
                    )
                    n = self._chat_write_counted(content, follow=False, shrink=True)
                    if tail:
                        log.lines.extend(tail)
                        log.virtual_size = Size(
                            int(getattr(log, "_widest_line_width", 0) or 0),
                            len(log.lines),
                        )
                        log.refresh()
                    # Later open rows that lived in `tail` shift by (n - strips)
                    self._shift_open_tool_starts(end, n - strips)
                    if follow and getattr(self, "_follow_chat", True) and not self._is_selecting():
                        try:
                            log.scroll_end(animate=False)
                        except Exception:
                            pass
                    return {"start": start, "strips": n, "name": open_meta.get("name", "")}
                except Exception:
                    pass
            else:
                # Stale handle — just append
                pass
        start = len(log.lines)
        n = self._chat_write_counted(content, follow=follow, shrink=True)
        return {"start": start, "strips": n, "name": (open_meta or {}).get("name", "")}

    def _parse_tool_line(self, text: str) -> tuple[str, str, str, str]:
        """Return (kind, name, detail, state).

        kind: call | result | other
        state: progress | done | error

        Bridge may tag ``name#call_id`` so we can dedupe by id.
        """
        raw = str(text or "")
        stripped = raw.strip()
        # Drop live-panel hints / running suffix so regex matches cleanly
        stripped = re.sub(r"\s*\[dim\].*$", "", stripped)
        stripped = re.sub(r"\s*\(running\)\s*$", "", stripped, flags=re.I)
        failed = self._tool_line_failed(raw)

        def _split_call_tag(label: str) -> tuple[str, str]:
            """Split ``name#call_id`` → (display_name, dedupe_key)."""
            lab = (label or "").strip()
            if "#" in lab:
                base, cid = lab.rsplit("#", 1)
                base = base.strip() or lab
                cid = cid.strip()
                if cid:
                    return base, f"id:{cid}"
            return lab, f"name:{lab}" if lab else "name:tool"

        if stripped.startswith("✓"):
            body = re.sub(r"^✓\s*", "", stripped).strip()
            # "Shell done: …" / "Sub-agent done: …" → keep prior tool name
            m_done = re.match(r"^(Shell|Sub-agent)\s+done\b[:\s]*(.*)$", body, flags=re.I | re.S)
            if m_done:
                label = m_done.group(1)
                rest = (m_done.group(2) or "").strip()
                prior = (getattr(self, "_last_tool_name", "") or "").strip()
                if prior:
                    name = prior
                else:
                    name = label
                disp, _key = _split_call_tag(name)
                state = "error" if failed else "done"
                return "result", disp, rest, state
            # Prefer ``name#call_id`` (stable); ignore varying result body
            head = body.split(":", 1)[0].strip() if body else ""
            tagged = head or body
            disp, _key = _split_call_tag(tagged)
            if not disp:
                disp = (getattr(self, "_last_tool_name", "") or "").strip()
            state = "error" if failed else "done"
            return "result", disp, "", state

        # ⚙ name(#call_id)?(args) — optionally "Sub-agent:" / "Shell:" prefix
        m = re.match(
            r"^⚙\s*((?:Sub-agent|Shell):\s*)?([^\(]+?)(?:\((.*)\))?\s*$",
            stripped,
            flags=re.S,
        )
        if m:
            prefix = (m.group(1) or "").strip()  # "Shell:" / "Sub-agent:" / ""
            name = (m.group(2) or "").strip()
            args = (m.group(3) or "").strip()
            if prefix:
                kind_l = prefix.rstrip(":").strip()
                label = f"{kind_l}: {name}" if name else kind_l
            else:
                label = name or "tool"
            label = re.sub(r"\s+", " ", label).strip()
            disp, _key = _split_call_tag(label)
            state = "error" if failed else "progress"
            if disp:
                self._last_tool_name = disp
            return "call", disp or "tool", args, state

        if "⚙" in stripped[:6]:
            name = re.sub(r"^.*?⚙\s*", "", stripped).split("(")[0].strip() or "tool"
            name = re.sub(r"\s+", " ", name).strip()
            disp, _key = _split_call_tag(name)
            self._last_tool_name = disp
            return "call", disp, "", "error" if failed else "progress"

        state = "error" if failed else "done"
        return "other", "", stripped, state

    def _tool_markup(self, text: str) -> str | None:
        """Tool line: white bold name + signal lamp; hide args/result unless ^O.

        Returns None to skip writing the line entirely.
        """
        kind, name, detail, state = self._parse_tool_line(text)
        return self._tool_markup_parts(kind, name, detail, state)

    def _tool_markup_parts(
        self,
        kind: str,
        name: str,
        detail: str,
        state: str,
        *,
        open_name: str = "",
    ) -> str | None:
        lamp = self._signal_lamp(state)
        expanded = bool(getattr(self, "_detail_expanded", False))
        white = "#e6edf3"
        muted = self._theme_hex("text-muted", "#8b949e")

        if kind == "call":
            # In-progress tool title: soft gray light sweeping across the name
            name_mk = self._shimmer_markup(name or "tool")
            if expanded and detail:
                args_s = self._escape_markup(detail)
                return f"{lamp}{name_mk}[dim {muted}]({args_s})[/]"
            return f"{lamp}{name_mk}"

        if kind == "result":
            # Prefer the open-call label so yellow→green keeps the same text
            label = open_name or name or getattr(self, "_last_tool_name", "") or ""
            name_s = self._escape_markup(label) if label else ""
            if state == "error":
                body = self._escape_markup((detail or "failed")[:160])
                if name_s:
                    return f"{lamp}[bold {white}]{name_s}[/] [bold red]{body}[/]"
                return f"{lamp}[bold red]{body}[/]"
            if not expanded:
                # Compact success: green lamp + same label as the running row
                if name_s:
                    return f"{lamp}[bold {white}]{name_s}[/]"
                return f"{lamp}[bold {white}]done[/]"
            # Expanded: show args/result (may be multi-line)
            body = self._fold_detail_text(detail) if detail else ""
            if name_s and body:
                safe = self._escape_markup(body)
                return f"{lamp}[bold {white}]{name_s}[/]\n[dim {muted}]{safe}[/]"
            if name_s:
                return f"{lamp}[bold {white}]{name_s}[/]"
            if body:
                return f"{lamp}[dim {muted}]{self._escape_markup(body)}[/]"
            return None

        if state == "error":
            return f"{lamp}[bold red]{self._escape_markup(detail or name or 'error')}[/]"
        if not expanded:
            return None
        return f"{lamp}[dim {muted}]{self._escape_markup(detail or name)}[/]"

    def _agent_footer_markup(self) -> str:
        """OpenCode-style turn footer: · agent · Build · model · 3.2s."""
        muted = self._theme_hex("text-muted", "#8b949e")
        agent = self._escape_markup(self.agent or "agent")
        mode = getattr(self, "_agent_mode", "build") or "build"
        mode_plain = t("mode_plan") if mode == "plan" else t("mode_build")
        model = self._escape_markup(getattr(self, "_model_label", None) or "—")
        # Prefer live elapsed if turn still open; else frozen last turn
        started = getattr(self, "_turn_started_at", None)
        if started is not None:
            secs = time.monotonic() - float(started)
        else:
            secs = getattr(self, "_last_turn_elapsed", None)
        time_bit = ""
        if secs is not None:
            time_bit = f" · [{muted}]{self._escape_markup(self._fmt_duration(float(secs)))}[/]"
        # agent / mode / model all muted grey (no white highlight)
        return (
            f"  [dim]·[/] [{muted}]{agent}[/] · [{muted}]{self._escape_markup(mode_plain)}[/]"
            f" · [{muted}]{model}[/]{time_bit}"
        )

    def _signal_lamp(self, state: str) -> str:
        """Left traffic light for agent/tool blocks.

        progress → yellow · error → red · done → green
        """
        st = (state or "").strip().lower()
        if st in ("error", "fail", "failed", "red"):
            color = self._theme_hex("error", "#f85149")
        elif st in ("done", "ok", "success", "complete", "green"):
            color = self._theme_hex("success", "#3fb950")
        else:
            color = self._theme_hex("warning", "#e3b341")
        return f"[{color}]●[/] "

    def _shimmer_markup(self, plain: str, *, base: str | None = None) -> str:
        """Soft light sweep — small steps at high Hz so motion looks fluid.

        ``base`` selects the resting color. Thinking uses a muted OpenCode tan;
        wait/tool titles keep cool gray.
        """
        text = str(plain or "")
        if not text:
            return ""
        if base:
            # Warm, low-chroma band (OpenCode Thinking depth)
            base_c = base
            soft = "#b49a74"
            mid = "#c4b08c"
            hi = "#d2c4a6"
        else:
            base_c = self._theme_hex("text-muted", "#8b949e")
            soft = "#9aa3ad"
            mid = "#b6bec6"
            hi = "#c9d1d9"
        tick = int(getattr(self, "_shimmer_tick", 0) or 0)
        n = len(text)
        # 1 cell/tick (not 3) — same overall pace with a higher timer rate
        span = max(n + 8, 10)
        pos = tick % span - 2
        out: list[str] = []
        for i, ch in enumerate(text):
            esc = self._escape_markup(ch)
            d = i - pos
            if d == 0:
                out.append(f"[{hi}]{esc}[/]")
            elif d in (-1, 1):
                out.append(f"[{mid}]{esc}[/]")
            elif d in (-2, 2):
                out.append(f"[{soft}]{esc}[/]")
            else:
                out.append(f"[dim {base_c}]{esc}[/]")
        return "".join(out)

    def _tool_dedupe_key(self, kind: str, name: str, raw: str) -> str:
        """Stable key for one green/orange tool row (prefer call_id)."""
        s = (raw or "").strip()
        # Explicit #call_id from bridge
        m = re.search(r"[#]([A-Za-z0-9_.:\-]+)", s)
        if m and ("⚙" in s[:4] or "✓" in s[:4] or s.startswith(("⚙", "✓"))):
            return f"id:{m.group(1)}"
        label = (name or getattr(self, "_last_tool_name", "") or "tool").strip()
        return f"{kind}:{label}"

    def _claim_tool_line(self, raw: str) -> bool:
        """Return False if this tool line was already claimed (skip duplicate).

        Must run on the WS/caller thread *before* schedule_ui so concurrent
        redelivered tool_result events cannot all paint.
        """
        kind, name, _detail, _state = self._parse_tool_line(raw)
        if kind == "call":
            key = self._tool_dedupe_key("call", name, raw)
            done_key = key.replace("call:", "done:", 1) if key.startswith("call:") else f"done:{key}"
            # New call of same tool/id → allow a future green lamp
            self._done_tool_keys.discard(done_key)
            if key.startswith("id:"):
                self._done_tool_keys.discard(f"done:{key}")
            else:
                # name-based
                self._done_tool_keys.discard(f"done:name:{name}")
            self._open_tool_keys.add(key if key.startswith("id:") else f"name:{name}")
            return True
        if kind == "result":
            key = self._tool_dedupe_key("done", name, raw)
            if not key.startswith("id:"):
                key = f"done:name:{(name or getattr(self, '_last_tool_name', '') or 'tool').strip()}"
            else:
                key = f"done:{key}"
            if key in self._done_tool_keys:
                return False
            self._done_tool_keys.add(key)
            if len(self._done_tool_keys) > 500:
                # keep recent-ish by clearing oldest half via rebuild
                keep = list(self._done_tool_keys)[-250:]
                self._done_tool_keys = set(keep)
            return True
        return True

    def _pop_open_tool(self, result_key: str, label: str) -> dict[str, Any] | None:
        """Find and remove the yellow open-row matching this result (by call_id, else FIFO name)."""
        tools = getattr(self, "_open_tools", None)
        if not isinstance(tools, dict) or not tools:
            return None
        if result_key.startswith("id:"):
            meta = tools.pop(result_key, None)
            if meta is not None:
                return meta
        # FIFO match by tool name (parallel same-name without call_id)
        for key, meta in list(tools.items()):
            if str(meta.get("name") or "") == label:
                return tools.pop(key, None)
        return None

    def _write_tool_line(self, raw: str) -> None:
        """Paint tool progress in chat (yellow) + wait-banner; green on result.

        Call rows are written immediately so tools stay visible between Thinking
        blocks. Result upgrades the matching yellow row in-place when possible.
        Dedup is by call_id (not bare tool name) so repeated read_file etc. all show.
        """
        kind, name, detail, state = self._parse_tool_line(raw)
        if kind == "call":
            label = name or "tool"
            self._last_tool_name = label
            key = self._tool_dedupe_key("call", label, raw)
            if detail:
                self._tool_args_by_key[key] = detail
                self._tool_args_by_key[f"name:{label}"] = detail
            # Unique store key so parallel same-name calls each keep a yellow row
            if key.startswith("id:"):
                store_key = key
            else:
                self._open_tool_seq = int(getattr(self, "_open_tool_seq", 0) or 0) + 1
                store_key = f"name:{label}:{self._open_tool_seq}"
            # Paint yellow lamp in chat immediately (between Thinking blocks)
            mk = self._tool_markup_parts("call", label, detail, "progress")
            open_meta: dict[str, Any] = {
                "name": label,
                "key": store_key,
                "detail": detail or "",
            }
            if mk is not None:
                try:
                    start = len(self.query_one("#chat-log", RichLog).lines)
                except Exception:
                    start = 0
                n = self._chat_write_counted(mk, follow=True)
                open_meta["start"] = start
                open_meta["strips"] = n
                self._pin_chat_bottom()
            if not isinstance(getattr(self, "_open_tools", None), dict):
                self._open_tools = {}
            self._open_tools[store_key] = open_meta
            self.update_wait(f"● {label}")
            self._ensure_shimmer_timer()
            return
        if kind == "result":
            label = name or (getattr(self, "_last_tool_name", "") or "tool")
            # Dedup by call_id — never drop a second same-name tool (e.g. read_file ×N)
            result_key = self._tool_dedupe_key("done", label, raw)
            if result_key.startswith("id:"):
                dedup_key = f"done:{result_key}"
            else:
                dedup_key = f"done:name:{label}:{int(getattr(self, '_tool_result_seq', 0) or 0)}"
                self._tool_result_seq = int(getattr(self, "_tool_result_seq", 0) or 0) + 1
            if dedup_key == getattr(self, "_last_tool_result_key", ""):
                return
            self._last_tool_result_key = dedup_key
            open_meta = self._pop_open_tool(result_key, label)
            if open_meta and not name:
                label = str(open_meta.get("name") or label)
            full_detail = self._take_tool_detail(label, raw)
            if not full_detail and detail:
                full_detail = detail
            args_key = result_key if result_key.startswith("id:") else f"name:{label}"
            args = self._tool_args_by_key.pop(args_key, None) or self._tool_args_by_key.pop(f"name:{label}", None) or ""
            if args and full_detail:
                combined = f"({args})\n{full_detail}"
            elif args:
                combined = f"({args})"
            else:
                combined = full_detail
            open_label = str(open_meta.get("name") or "") if open_meta else ""
            mk = self._tool_markup_parts("result", label, combined, state, open_name=open_label)
            if mk is not None:
                can_replace = bool(open_meta and int(open_meta.get("strips", 0) or 0) > 0)
                updated = self._chat_replace_open(open_meta, mk, follow=True) if can_replace else None
                if updated:
                    start = int(updated.get("start", 0))
                    n = int(updated.get("strips", 0))
                else:
                    try:
                        start = len(self.query_one("#chat-log", RichLog).lines)
                    except Exception:
                        start = 0
                    n = self._chat_write_counted(mk, follow=True)
                self._detail_blocks.append(
                    {
                        "kind": "tool",
                        "name": label,
                        "detail": combined,
                        "state": state,
                        "start": start,
                        "strips": n,
                    }
                )
                if len(self._detail_blocks) > 200:
                    self._detail_blocks = self._detail_blocks[-150:]
            if getattr(self, "_wait_label", None) and getattr(self, "_turn_started_at", None):
                self.update_wait(t("wait_thinking"))
            return
        mk = self._tool_markup_parts(kind, name, detail, state)
        if mk is not None:
            self._chat_write(mk, follow=True)

    def _on_tool_detail(self, name: str, call_id: str, text: str) -> None:
        """Bridge: stash full tool result body until the compact ✓ line arrives."""
        body = str(text or "")
        if call_id:
            self._tool_detail_pending[f"id:{call_id}"] = body
        label = (name or "tool").strip() or "tool"
        self._tool_detail_pending[f"name:{label}"] = body

    def _take_tool_detail(self, name: str, raw: str) -> str:
        s = (raw or "").strip()
        label = (name or getattr(self, "_last_tool_name", "") or "tool").strip()
        m = re.search(r"[#]([A-Za-z0-9_.:\-]+)", s)
        body = ""
        if m:
            body = self._tool_detail_pending.pop(f"id:{m.group(1)}", "") or ""
        if not body:
            body = self._tool_detail_pending.pop(f"name:{label}", "") or ""
        else:
            # Drop name alias so a later take cannot revive the same body
            self._tool_detail_pending.pop(f"name:{label}", None)
        return body

    def _detail_block_markup(self, entry: dict[str, Any]) -> str | None:
        kind = str(entry.get("kind") or "")
        if kind == "thinking":
            return self._thinking_markup(str(entry.get("detail") or ""), live=False)
        if kind == "tool":
            return self._tool_markup_parts(
                "result",
                str(entry.get("name") or "tool"),
                str(entry.get("detail") or ""),
                str(entry.get("state") or "done"),
            )
        return None

    def _rewrite_detail_blocks(self) -> None:
        """Re-render all stored thinking/tool rows after Ctrl+O toggle."""
        blocks = getattr(self, "_detail_blocks", None) or []
        if not blocks:
            return
        # End→start so strip-index shifts stay correct
        for entry in reversed(blocks):
            mk = self._detail_block_markup(entry)
            if mk is None:
                continue
            meta = {
                "start": int(entry.get("start", -1)),
                "strips": int(entry.get("strips", 0)),
                "name": entry.get("name") or entry.get("kind") or "",
            }
            updated = self._chat_replace_open(meta, mk, follow=False)
            if updated:
                old_start = int(entry.get("start", -1))
                old_strips = int(entry.get("strips", 0))
                new_start = int(updated.get("start", old_start))
                new_strips = int(updated.get("strips", old_strips))
                delta = new_strips - old_strips
                entry["start"] = new_start
                entry["strips"] = new_strips
                if delta:
                    for other in blocks:
                        if other is entry:
                            continue
                        if int(other.get("start", -1)) > old_start:
                            other["start"] = int(other["start"]) + delta
        try:
            if getattr(self, "_follow_chat", True) and not self._is_selecting():
                self.query_one("#chat-log", RichLog).scroll_end(animate=False)
        except Exception:
            pass
