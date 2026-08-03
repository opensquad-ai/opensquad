"""Bridge decision / approval card flow (extracted from app.py)."""

from __future__ import annotations

from textual import work
from textual.widgets import Input, Static

from opensquad.cli.tui.decision_picker import (
    PendingDecision,
    from_group_approval,
    from_mode_switch,
    from_propose_options,
    render_decision_markup,
)
from opensquad.cli.tui.i18n import t


class DecisionsMixin:
    """Mixin methods moved from cli/tui/app.py (see app.py for the app class)."""

    def _on_bridge_decision(self, event: str, data: dict) -> None:
        evt = str(event or "")
        if evt == "propose_options":
            d = from_propose_options(data, source="solo")
            if d:
                self._enqueue_decision(d)
            return
        if evt == "mode_switch_approval":
            d = from_mode_switch(data)
            if d:
                self._enqueue_decision(d)
            return
        if evt == "propose_options_resolved":
            rid = str(data.get("id") or "")
            self._clear_decision_by_id(rid)
            status = str(data.get("status") or "chosen")
            self.log_line(f"→ Options {status}", style="system")
            return
        if evt == "mode_switch_resolved":
            rid = str(data.get("id") or "")
            self._clear_decision_by_id(rid)
            status = str(data.get("status") or "")
            if status:
                self.log_line(f"→ Mode switch {status}", style="system")
            return

    def _enqueue_decision(self, d: PendingDecision) -> None:
        # Replace same id if already queued/open
        self._decision_queue = [x for x in self._decision_queue if x.id != d.id]
        if self._decision and self._decision.id == d.id:
            self._decision = d
            self._await_custom_answer = False
            self._paint_decision()
            self._focus_input()
            return
        if self._decision is None:
            self._open_decision(d)
        else:
            self._decision_queue.append(d)
            self.log_line(f"[decision] queued: {d.prompt.splitlines()[0][:60]}", style="system")

    def _open_decision(self, d: PendingDecision) -> None:
        self._hide_slash_menu()
        self._hide_nav()
        self._hide_session_picker()
        self._decision = d
        self._await_custom_answer = False
        self._paint_decision()
        # Compact line in transcript (OpenCode: "→ Asked 1 question")
        n = len(d.options)
        kind = "question" if d.kind.endswith("options") else "approval"
        self.log_line(f"→ Asked {n} {kind}{'s' if n != 1 else ''}", style="tool")
        self._refresh_chrome()
        self._focus_input()

    def _paint_decision(self) -> None:
        d = self._decision
        try:
            menu = self.query_one("#slash-menu", Static)
        except Exception:
            return
        if not d:
            menu.update("")
            menu.remove_class("visible")
            menu.remove_class("decision")
            return
        markup = render_decision_markup(
            d,
            escape=self._escape_markup,
            primary=self._theme_hex("primary", "#58a6ff"),
            muted=self._theme_hex("text-muted", "#8b949e"),
            fg=self._theme_hex("foreground", "#e6edf3"),
        )
        if self._await_custom_answer:
            markup += f"\n[bold]{t('custom_answer_banner')}[/]"
        menu.update(markup)
        menu.add_class("visible")
        menu.add_class("decision")
        self._sync_prompt_dock_menu()

    def _hide_decision(self) -> None:
        self._decision = None
        self._await_custom_answer = False
        try:
            menu = self.query_one("#slash-menu", Static)
            menu.update("")
            menu.remove_class("visible")
            menu.remove_class("decision")
        except Exception:
            pass
        self._sync_prompt_dock_menu()
        # Show next queued decision
        if self._decision_queue:
            nxt = self._decision_queue.pop(0)
            self._open_decision(nxt)
        else:
            self._refresh_chrome()
            self._focus_input()

    def _clear_decision_by_id(self, rid: str) -> None:
        if not rid:
            return
        self._decision_queue = [x for x in self._decision_queue if x.id != rid]
        if self._decision and self._decision.id == rid:
            self._hide_decision()

    def _decision_confirm(self) -> None:
        d = self._decision
        if not d:
            return
        d.clamp_index()
        rows = d.rows
        if not rows:
            return
        opt = rows[d.index]
        if opt.id == d.custom_row_id:
            self._await_custom_answer = True
            try:
                inp = self.query_one("#chat-input", Input)
                inp.placeholder = t("placeholder_custom")
            except Exception:
                pass
            self._paint_decision()
            self._focus_input()
            return
        if d.allow_multiple and d.kind in ("options", "group_options"):
            ids = sorted(d.selected_ids) if d.selected_ids else [opt.id]
            if not ids:
                self.notify(t("notify_space_toggle"), timeout=2)
                return
            self._resolve_decision_choose(d, ids)
            return
        if d.kind == "mode_switch":
            if opt.id == "approve":
                self._resolve_mode_switch(d, approve=True)
            else:
                self._resolve_mode_switch(d, approve=False)
            return
        if d.kind == "group_approval":
            self._resolve_group_approval(d, reject=(opt.id != "approve"))
            return
        self._resolve_decision_choose(d, [opt.id])

    def _decision_toggle_multi(self) -> None:
        d = self._decision
        if not d or not d.allow_multiple or self._await_custom_answer:
            return
        d.clamp_index()
        rows = d.rows
        if not rows:
            return
        opt = rows[d.index]
        if opt.id == d.custom_row_id:
            return
        if opt.id in d.selected_ids:
            d.selected_ids.discard(opt.id)
        else:
            d.selected_ids.add(opt.id)
        self._paint_decision()

    def action_decision_space(self) -> None:
        """Space toggles multi-select on a decision card; otherwise let Input type a space."""
        from textual.actions import SkipAction

        d = getattr(self, "_decision", None)
        if not d or not d.allow_multiple or self._await_custom_answer:
            raise SkipAction()
        try:
            inp = self.query_one("#chat-input", Input)
            if (inp.value or "").strip():
                raise SkipAction()
        except SkipAction:
            raise
        except Exception:
            pass
        self._decision_toggle_multi()

    def _decision_dismiss(self) -> None:
        d = self._decision
        if not d:
            return
        if d.kind == "mode_switch":
            self._resolve_mode_switch(d, approve=False)
            return
        if d.kind == "group_approval":
            self._resolve_group_approval(d, reject=True)
            return
        # options → ignore
        self._resolve_decision_ignore(d)

    def _submit_custom_decision(self, text: str) -> None:
        d = self._decision
        if not d:
            return
        self._await_custom_answer = False
        if d.kind in ("options",) and d.source == "solo":
            if not self.bridge or not getattr(self.bridge, "is_open", False):
                self.log_line("Not connected", style="error")
                return
            try:
                self.bridge.send_command(
                    "resolve_proposed_options",
                    {"id": d.id, "custom_answer": text, "ignored": False},
                )
                self.log_line(f"→ Custom: {text[:80]}", style="system")
            except Exception as e:
                self.log_line(f"resolve failed: {e}", style="error")
                return
            self._hide_decision()
            return
        if d.kind == "group_options" and self.group:
            self._group_choose_action_work(d.id, text, f"[group] custom: {text[:80]}", "custom")
            return
        self._hide_decision()

    def _resolve_decision_choose(self, d: PendingDecision, ids: list[str]) -> None:
        titles = []
        for oid in ids:
            for o in d.options:
                if o.id == oid:
                    titles.append(o.title)
                    break
        label = ", ".join(titles) if titles else ",".join(ids)
        if d.source == "solo":
            if not self.bridge or not getattr(self.bridge, "is_open", False):
                self.log_line("Not connected", style="error")
                return
            try:
                self.bridge.send_command(
                    "resolve_proposed_options",
                    {
                        "id": d.id,
                        "chosen_option_id": ids[0] if ids else "",
                        "chosen_option_ids": ids,
                        "ignored": False,
                    },
                )
                self.log_line(f"→ Chose: {label}", style="system")
            except Exception as e:
                self.log_line(f"resolve failed: {e}", style="error")
                return
        else:
            if not self.group:
                self.log_line("No group", style="error")
                return
            self._resolve_group_choose_work(d, ids, label)
            return
        self._hide_decision()

    @work(thread=True, group="group-action")
    def _resolve_group_choose_work(self, d: PendingDecision, ids: list[str], label: str) -> None:
        if not self.group:
            self.log_line("No group", style="error")
            return
        try:
            value = ",".join(ids)
            self.group.resolve_choose(d.id, value)
            self.log_line(f"[group] chose: {label}", style="system")
        except Exception as e:
            self.log_line(str(e), style="error")
            return
        self._schedule_ui(self._hide_decision)

    def _resolve_decision_ignore(self, d: PendingDecision) -> None:
        if d.source == "solo":
            if self.bridge and getattr(self.bridge, "is_open", False):
                try:
                    self.bridge.send_command(
                        "resolve_proposed_options",
                        {"id": d.id, "ignored": True},
                    )
                except Exception as e:
                    self.log_line(f"ignore failed: {e}", style="error")
                    return
            self.log_line("→ Options dismissed", style="system")
        else:
            if self.group:
                self._group_choose_action_work(d.id, "", "[group] options ignored", "ignore")
                return
            self.log_line("[group] options ignored", style="system")
        self._hide_decision()

    @work(thread=True, group="group-action")
    def _group_choose_action_work(self, proposal_id: str, value: str, ok_msg: str, action: str = "choose") -> None:
        if not self.group:
            self.log_line("No group", style="error")
            return
        try:
            self.group.resolve_choose(proposal_id, value, action=action)
            self.log_line(ok_msg, style="system")
        except Exception as e:
            self.log_line(str(e), style="error")
            return
        self._schedule_ui(self._hide_decision)

    def _resolve_mode_switch(self, d: PendingDecision, *, approve: bool) -> None:
        if not self.bridge or not getattr(self.bridge, "is_open", False):
            self.log_line("Not connected", style="error")
            return
        try:
            if approve:
                self.bridge.send_command(
                    "set_agent_mode",
                    {
                        "mode": d.to_mode,
                        "id": d.id,
                        "approved_request_id": d.id,
                    },
                )
                self._agent_mode = d.to_mode
                self.log_line(f"→ Approved mode → {d.to_mode}", style="system")
            else:
                self.bridge.send_command(
                    "deny_mode_switch",
                    {"id": d.id, "reason": "User denied"},
                )
                self.log_line("→ Mode switch denied", style="system")
        except Exception as e:
            self.log_line(f"mode resolve failed: {e}", style="error")
            return
        self._refresh_chrome()
        self._hide_decision()

    def _resolve_group_approval(self, d: PendingDecision, *, reject: bool = False) -> None:
        """UI entry: schedule HTTP off the main thread (avoids WS deadlock)."""
        self._resolve_group_approval_work(d, reject=reject)

    @work(thread=True, group="group-action")
    def _resolve_group_approval_work(self, d: PendingDecision, reject: bool = False) -> None:
        if not self.group:
            self.log_line("No group", style="error")
            return
        try:
            self.group.resolve_approval(d.id, reject=reject)
            self.log_line(
                f"[group] {'rejected' if reject else 'approved'} {d.id}",
                style="system",
            )
        except Exception as e:
            self.log_line(str(e), style="error")
            return
        self._schedule_ui(self._hide_decision)

    def _open_group_pending_decision(self) -> None:
        """Open latest *pending* group approval/options as the decision picker."""
        if not self.group:
            return
        if self._decision:
            return
        # Prefer options, then approvals — only truly pending cards
        pr = None
        ap = None
        try:
            pr = self.group.latest_pending_proposal()
            ap = self.group.latest_pending_approval()
        except Exception:
            return
        if pr and pr.options and (getattr(pr, "status", None) or "pending") == "pending":
            raw = dict(pr.raw or {})
            raw.setdefault("id", pr.id)
            raw.setdefault("prompt", pr.title)
            # Rebuild options from parsed (label, value) if needed
            if not raw.get("options"):
                raw["options"] = [{"id": value, "title": label, "description": ""} for label, value in pr.options]
            raw["group_id"] = pr.group_id
            raw["message_id"] = pr.message_id
            d = from_propose_options(raw, source="group")
            if d:
                self._enqueue_decision(d)
                return
        if ap and ap.status == "pending":
            d = from_group_approval(
                approval_id=ap.id,
                title=ap.title,
                group_id=ap.group_id,
                message_id=ap.message_id,
                summary=str((ap.raw or {}).get("summary") or ""),
            )
            self._enqueue_decision(d)

    def _command_palette_open(self) -> bool:
        """True while Textual's Ctrl+P command palette owns navigation keys."""
        try:
            from textual.command import CommandPalette

            return bool(CommandPalette.is_open(self))
        except Exception:
            return False
