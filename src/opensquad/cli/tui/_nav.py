"""Navigation menu / provider / model / skill flows (extracted from app.py)."""

from __future__ import annotations

import time
from typing import Any

from textual import work
from textual.widgets import Static

from opensquad.cli.tui.i18n import get_locale, normalize_locale, set_locale, t
from opensquad.cli.tui.redact import redact_secrets
from opensquad.cli.tui.selectable_rich_log import SelectableRichLog as RichLog
from opensquad.cli.tui.themes import (
    list_theme_names,
    save_theme,
)


class NavMixin:
    """Mixin methods moved from cli/tui/app.py (see app.py for the app class)."""

    def open_nav(self, kind: str) -> None:
        """Entry from slash_dispatch: open interactive list for a resource kind."""
        kind = (kind or "").strip().lower()
        if kind in ("agents", "agentctl"):
            kind = "agent"
        if kind in ("theme", "themes"):
            self._open_theme_nav()
            return
        if kind in ("language", "lang", "locale"):
            self._open_language_nav()
            return
        if kind == "session" or kind == "sessions":
            self._session_cmd("sessions")
            return
        self.begin_wait(t("wait_loading", kind=kind))
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

    def _open_language_nav(self) -> None:
        from opensquad.cli.tui.nav_menus import build_language_menu

        title, items = build_language_menu(self, current=str(getattr(self, "_locale", None) or get_locale()))
        cur = str(getattr(self, "_locale", None) or get_locale())
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

    def apply_locale(self, name: str) -> None:
        """Switch TUI language (en/zh) and persist preference."""
        name = (name or "").strip()
        if not name:
            self._open_language_nav()
            return
        code = normalize_locale(name)
        if not code:
            self.log_line(t("language_unknown", name=name), style="error")
            self._open_language_nav()
            return
        self._locale = set_locale(code, persist=True)
        self._refresh_chrome()
        if self._nav_active:
            self._hide_nav()
        if getattr(self, "_decision", None):
            self._paint_decision()
        if getattr(self, "_slash_visible", False):
            self._paint_slash_menu()
        if getattr(self, "_session_pick_active", False):
            self._paint_session_picker()
        try:
            thinking = t("wait_thinking")
            connecting = t("wait_connecting")
            replying = t("wait_replying")
            # Re-label wait banner if currently showing a known status
            cur = self._wait_label or ""
            if cur in ("Thinking…", "思考中…", thinking):
                self.update_wait(thinking)
            elif cur in ("Connecting…", "连接中…", connecting):
                self.update_wait(connecting)
            elif cur in ("Replying…", "回复中…", replying):
                self.update_wait(replying)
        except Exception:
            pass
        self.log_line(t("language_set", code=code), style="system")
        self._focus_input()

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

    def action_toggle_detail(self) -> None:
        """Ctrl+O — expand/collapse all thinking & tool output (past + future)."""
        self._detail_expanded = not self._detail_expanded
        self.notify(t("detail_on") if self._detail_expanded else t("detail_off"), timeout=2)
        try:
            self._rewrite_detail_blocks()
        except Exception:
            pass
        if getattr(self, "_think_pending", False):
            self._paint_cache.pop("live-think", None)
            self._paint_live_think()
        self._focus_input()

    def action_toggle_live(self) -> None:
        if getattr(self, "_live_side_open", False):
            self._close_live_side()
        else:
            self._open_live_side()

    def _on_side_chunk(self, key: str, kind: str, title: str, text: str, *, fresh: bool = False) -> None:
        self._side_hub.append(key, text, kind=kind, title=title, fresh=fresh)
        self._live_side_key = key
        self._refresh_chrome()
        if not getattr(self, "_live_side_open", False):
            return
        # Throttle full repaint while tokens stream (avoid flicker / CPU spin)
        now = time.monotonic()
        if now - getattr(self, "_side_paint_at", 0.0) < 0.08 and not fresh:
            return
        self._side_paint_at = now
        self._paint_live_side()

    def _on_side_done(self, key: str) -> None:
        self._side_hub.mark_done(key)
        self._refresh_chrome()
        if getattr(self, "_live_side_open", False):
            self._side_paint_at = 0.0
            self._paint_live_side()

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
        self.begin_wait("Connecting provider…")
        self._save_provider_card(provider, key)

    def _reload_model_nav(self) -> None:
        """Rebuild /model root menu (provider-grouped). Call from worker or UI thread."""
        from opensquad.cli.tui.nav_menus import build_model_menu

        try:
            title, items = build_model_menu(
                self.client,
                self.agent,
                current_card=getattr(self, "_model_card", None) or "",
                current_model=getattr(self, "_model_name", None) or "",
            )
            self.call_from_thread(lambda: self._push_nav(title, items, replace=True))
        except Exception as e:
            self.log_line(f"[model] {e}", style="error")

    def _current_model_name_hint(self) -> str:
        return str(getattr(self, "_model_name", "") or "")

    @work(thread=True, group="nav-action")
    def _save_provider_card(self, provider: dict, api_key: str) -> None:
        from opensquad.cli.tui.nav_menus import provider_card_name

        try:
            models = list(provider.get("models") or [])
            model = models[0] if models else {}
            pid = str(provider.get("id") or "provider")
            slug = provider_card_name(pid)
            mn = str(model.get("model_name") or "default")
            # Keep existing card fields if reconnecting (except api_key / defaults)
            existing: dict = {}
            try:
                existing = (self.client.admin_get(f"model-cards/{slug}") or {}).get("card") or {}
            except Exception:
                existing = {}
            if not isinstance(existing, dict):
                existing = {}
            card = dict(existing)
            card.update(
                {
                    "name": slug,
                    "title": str(model.get("title") or existing.get("title") or mn),
                    "provider": str(provider.get("provider") or provider.get("label") or pid),
                    "base_url": str(provider.get("base_url") or ""),
                    "api_protocol": str(provider.get("api_protocol") or "openai_compat"),
                    "api_key": api_key,
                    "model_name": str(existing.get("model_name") or mn),
                    "token_max": int(existing.get("token_max") or model.get("token_max") or 128000),
                    "temperature": existing.get("temperature", model.get("temperature", 0)),
                    "is_think": bool(existing.get("is_think", model.get("is_think"))),
                    "is_image": bool(existing.get("is_image", model.get("is_image"))),
                    "is_audio": bool(
                        existing.get(
                            "is_audio",
                            model.get("is_audio") or model.get("is_audio_output"),
                        )
                    ),
                }
            )
            self.client.admin_put(f"model-cards/{slug}", card)
            masked = api_key[:4] + "…" if len(api_key) > 4 else "***"
            plabel = provider.get("label") or provider.get("provider") or pid
            self.log_line(
                f"Connected {plabel} (card '{slug}', key {masked}) — pick a model below",
                style="system",
            )
            self._model_card = slug
            self._model_name = str(card.get("model_name") or "")
            self._model_label = str(card.get("title") or card.get("model_name") or "")
            self._model_provider_label = str(plabel)
            self._reload_model_nav()
            self.call_from_thread(self._refresh_chrome)
        except Exception as e:
            self.log_line(f"[model] save failed: {e}", style="error")
        finally:
            self.end_wait()
            self.call_from_thread(self._focus_input)

    @work(thread=True, group="nav-action")
    def _nav_provider_use_model(self, provider: dict, model: dict, card_name: str, key_card_name: str = "") -> None:
        from opensquad.cli.tui.nav_menus import provider_card_name

        try:
            pid = str(provider.get("id") or "provider")
            mn = str(model.get("model_name") or "")
            slug = (card_name or provider_card_name(pid)).replace("/", "-")[:64]
            # Load canonical card; fall back to legacy key_card for api_key
            existing: dict = {}
            try_names = [slug]
            if key_card_name and key_card_name not in try_names:
                try_names.append(key_card_name)
            legacy = f"{pid}-{mn}".replace("/", "-")
            if legacy not in try_names:
                try_names.append(legacy)
            for try_name in try_names:
                if not try_name:
                    continue
                try:
                    got = (self.client.admin_get(f"model-cards/{try_name}") or {}).get("card") or {}
                    if isinstance(got, dict) and got.get("api_key"):
                        existing = got
                        break
                    if isinstance(got, dict) and got and not existing:
                        existing = got
                except Exception:
                    pass
            if not isinstance(existing, dict):
                existing = {}
            card = dict(existing)
            card.update(
                {
                    "name": slug,
                    "title": str(model.get("title") or mn),
                    "provider": str(provider.get("provider") or provider.get("label") or pid),
                    "base_url": str(provider.get("base_url") or card.get("base_url") or ""),
                    "api_protocol": str(provider.get("api_protocol") or card.get("api_protocol") or "openai_compat"),
                    "model_name": mn,
                    "token_max": int(model.get("token_max") or card.get("token_max") or 128000),
                    "temperature": model.get("temperature", card.get("temperature", 0)),
                    "is_think": bool(model.get("is_think")),
                    "is_image": bool(model.get("is_image")),
                    "is_audio": bool(model.get("is_audio") or model.get("is_audio_output") or card.get("is_audio")),
                }
            )
            if not card.get("api_key"):
                self.log_line(
                    f"No API key for {provider.get('label') or pid} — Connect a provider first",
                    style="error",
                )
                return
            self.client.admin_put(f"model-cards/{slug}", card)
            if self.agent:
                body = dict(card)
                body["card_name"] = slug
                self.client.admin_put(f"agents/{self.agent}/model-card", body)
                if self.bridge and getattr(self.bridge, "is_open", False):
                    self.bridge.send_command("switch_model", {"card": slug})
            self._model_card = slug
            self._model_name = mn
            self._model_label = str(card.get("title") or mn)
            self._model_provider_label = str(provider.get("label") or provider.get("provider") or pid)
            plabel = self._model_provider_label
            self.log_line(f"Model → {plabel} / {self._model_label}", style="system")
            self.call_from_thread(self._hide_nav)
            self.call_from_thread(self._refresh_chrome)
        except Exception as e:
            self.log_line(f"[model] {e}", style="error")

    def _nav_provider_show(self, provider: dict, model: dict, card_name: str) -> None:
        pid = str(provider.get("id") or "")
        plabel = str(provider.get("label") or provider.get("provider") or pid)
        mn = str(model.get("model_name") or "")
        title = str(model.get("title") or mn)
        lines = [
            f"Model: {title}",
            f"  model_name: {mn}",
            f"  provider: {plabel}",
            f"  card: {card_name or '—'}",
            f"  base_url: {provider.get('base_url') or '—'}",
            f"  api_protocol: {provider.get('api_protocol') or '—'}",
            f"  token_max: {model.get('token_max') or '—'}",
            f"  temperature: {model.get('temperature', 0)}",
            f"  is_think: {bool(model.get('is_think'))}",
            f"  is_image: {bool(model.get('is_image'))}",
        ]
        self.log_line("[model] info", style="system")
        for line in lines:
            self.log_line(line, style="system")
        self._focus_input()

    def _nav_provider_edit_field(self, data: dict[str, Any]) -> None:
        field = str(data.get("field") or "").strip()
        if not field:
            return
        # Keep nav visible underneath; capture next Enter as value
        self._await_model_field = dict(data)
        self._await_model_field["mode"] = "provider"
        self.log_line(f"Enter new value for {field} then Enter (Esc cancel)", style="system")
        self._refresh_chrome()
        self._focus_input()

    def _nav_card_edit_field(self, data: dict[str, Any]) -> None:
        field = str(data.get("field") or "").strip()
        if not field:
            return
        self._await_model_field = dict(data)
        self._await_model_field["mode"] = "card"
        self.log_line(f"Enter new value for {field} then Enter (Esc cancel)", style="system")
        self._refresh_chrome()
        self._focus_input()

    def _finish_model_field_edit(self, pending: dict, value: str) -> None:
        field = str(pending.get("field") or "")
        raw = (value or "").strip()
        if not field or not raw:
            self.log_line("Empty value — cancelled", style="system")
            self._refresh_chrome()
            self._focus_input()
            return
        mode = pending.get("mode") or "provider"
        if mode == "card":
            self._apply_card_field(str(pending.get("name") or ""), field, raw)
        else:
            self._apply_provider_model_field(pending, field, raw)

    @work(thread=True, group="nav-action")
    def _apply_provider_model_field(self, pending: dict, field: str, raw: str) -> None:
        from opensquad.cli.tui.nav_menus import provider_card_name

        try:
            provider = pending.get("provider") or {}
            model = dict(pending.get("model") or {})
            pid = str(provider.get("id") or "provider")
            slug = str(pending.get("card_name") or provider_card_name(pid))
            key_card = str(pending.get("key_card_name") or slug)
            existing: dict = {}
            for try_name in (slug, key_card):
                try:
                    got = (self.client.admin_get(f"model-cards/{try_name}") or {}).get("card") or {}
                    if isinstance(got, dict) and got:
                        existing = got
                        if got.get("api_key"):
                            break
                except Exception:
                    pass
            card = dict(existing) if isinstance(existing, dict) else {}
            # Seed from preset model then overlay edit
            mn = str(model.get("model_name") or card.get("model_name") or "default")
            card.setdefault("name", slug)
            card.setdefault("model_name", mn)
            card.setdefault(
                "provider",
                str(provider.get("provider") or provider.get("label") or pid),
            )
            card.setdefault("base_url", str(provider.get("base_url") or ""))
            card.setdefault(
                "api_protocol",
                str(provider.get("api_protocol") or "openai_compat"),
            )
            card.setdefault("title", str(model.get("title") or mn))
            parsed: Any = raw
            if field in ("temperature",):
                parsed = float(raw)
            elif field in ("token_max",):
                parsed = int(float(raw))
            elif field.startswith("is_"):
                parsed = raw.lower() in ("1", "true", "yes", "on")
            card[field] = parsed
            # Also reflect onto in-memory model for menu refresh
            model[field] = parsed
            if not card.get("api_key"):
                self.log_line("No API key on card — Connect provider first", style="error")
                return
            self.client.admin_put(f"model-cards/{slug}", card)
            self.log_line(f"Updated {field} = {parsed} on '{slug}'", style="system")
            # Refresh L3 menu details
            from opensquad.cli.tui.nav_menus import _provider_model_edit_menu

            title = f"Edit · {card.get('title') or mn}"
            items = _provider_model_edit_menu(provider, model, slug, key_card)
            self.call_from_thread(lambda: self._push_nav(title, items, replace=True))
            self.call_from_thread(self._refresh_chrome)
        except Exception as e:
            self.log_line(f"[model] edit failed: {e}", style="error")
        finally:
            self.call_from_thread(self._focus_input)

    @work(thread=True, group="nav-action")
    def _apply_card_field(self, name: str, field: str, raw: str) -> None:
        if not name:
            return
        try:
            existing = (self.client.admin_get(f"model-cards/{name}") or {}).get("card") or {}
            card = dict(existing) if isinstance(existing, dict) else {"name": name}
            parsed: Any = raw
            if field in ("temperature",):
                parsed = float(raw)
            elif field in ("token_max",):
                parsed = int(float(raw))
            elif field.startswith("is_"):
                parsed = raw.lower() in ("1", "true", "yes", "on")
            card[field] = parsed
            self.client.admin_put(f"model-cards/{name}", card)
            self.log_line(f"Updated {field} = {parsed} on '{name}'", style="system")
            self.call_from_thread(self._refresh_chrome)
        except Exception as e:
            self.log_line(f"[model] edit failed: {e}", style="error")
        finally:
            self.call_from_thread(self._focus_input)

    @work(thread=True, group="nav-action")
    def _nav_provider_toggle_field(self, data: dict[str, Any]) -> None:
        from opensquad.cli.tui.nav_menus import _provider_model_edit_menu, provider_card_name

        try:
            field = str(data.get("field") or "")
            if not field.startswith("is_"):
                return
            provider = data.get("provider") or {}
            model = dict(data.get("model") or {})
            pid = str(provider.get("id") or "provider")
            slug = str(data.get("card_name") or provider_card_name(pid))
            key_card = str(data.get("key_card_name") or slug)
            existing: dict = {}
            for try_name in (slug, key_card):
                try:
                    got = (self.client.admin_get(f"model-cards/{try_name}") or {}).get("card") or {}
                    if isinstance(got, dict) and got:
                        existing = got
                        if got.get("api_key"):
                            break
                except Exception:
                    pass
            card = dict(existing) if isinstance(existing, dict) else {}
            mn = str(model.get("model_name") or card.get("model_name") or "default")
            card.setdefault("name", slug)
            card.setdefault("model_name", mn)
            card.setdefault(
                "provider",
                str(provider.get("provider") or provider.get("label") or pid),
            )
            card.setdefault("base_url", str(provider.get("base_url") or ""))
            card.setdefault("title", str(model.get("title") or mn))
            new_val = not bool(card.get(field, model.get(field)))
            card[field] = new_val
            model[field] = new_val
            if not card.get("api_key"):
                # Allow toggling preset defaults into a new card only if key exists
                self.log_line("No API key — Connect provider first", style="error")
                return
            self.client.admin_put(f"model-cards/{slug}", card)
            self.log_line(f"Toggled {field} → {'on' if new_val else 'off'}", style="system")
            title = f"Edit · {card.get('title') or mn}"
            items = _provider_model_edit_menu(provider, model, slug, key_card)
            self.call_from_thread(lambda: self._push_nav(title, items, replace=True))
        except Exception as e:
            self.log_line(f"[model] {e}", style="error")
        finally:
            self.call_from_thread(self._focus_input)

    @work(thread=True, group="nav-action")
    def _nav_card_toggle_field(self, data: dict[str, Any]) -> None:
        name = str(data.get("name") or "")
        field = str(data.get("field") or "")
        if not name or not field.startswith("is_"):
            return
        try:
            existing = (self.client.admin_get(f"model-cards/{name}") or {}).get("card") or {}
            card = dict(existing) if isinstance(existing, dict) else {"name": name}
            new_val = not bool(card.get(field))
            card[field] = new_val
            self.client.admin_put(f"model-cards/{name}", card)
            self.log_line(f"Toggled {field} → {'on' if new_val else 'off'}", style="system")
        except Exception as e:
            self.log_line(f"[model] {e}", style="error")
        finally:
            self.call_from_thread(self._focus_input)

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
                title, items = build_model_menu(
                    self.client,
                    self.agent,
                    current_card=getattr(self, "_model_card", None) or "",
                    current_model=getattr(self, "_model_name", None) or self._current_model_name_hint(),
                )
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
            t("hint_nav_push", title=title),
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
        lines.append(f"[dim]  {t('hint_nav_paint')}[/]")
        menu.update("\n".join(lines))
        menu.add_class("visible")
        self._sync_prompt_dock_menu()

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
        self._sync_prompt_dock_menu()

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
            label = str(getattr(item, "label", "") or "").strip()
            self._push_nav(label, list(children), replace=False)
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
                    str(data.get("key_card_name") or ""),
                )
            elif action == "model.provider_show":
                self._nav_provider_show(
                    data.get("provider") or {},
                    data.get("model") or {},
                    str(data.get("card_name") or ""),
                )
            elif action == "model.provider_edit_field":
                self._nav_provider_edit_field(data)
            elif action == "model.provider_toggle_field":
                self._nav_provider_toggle_field(data)
            elif action == "model.card_edit_field":
                self._nav_card_edit_field(data)
            elif action == "model.card_toggle_field":
                self._nav_card_toggle_field(data)
            elif action == "theme.apply":
                self.apply_theme(str(data.get("name") or ""))
            elif action == "language.apply":
                self.apply_locale(str(data.get("code") or ""))
            elif action == "skill.compose":
                self._nav_skill_compose(
                    str(data.get("name") or ""),
                    display=str(data.get("display") or ""),
                )
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
                self._model_name = str(card.get("model_name") or "")
                self._model_provider_label = str(card.get("provider") or "").strip()
            self._model_label = title or self._pretty_model_label("", self._model_name)
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

    def _nav_skill_compose(self, name: str, display: str = "") -> None:
        """Attach skill chip for next message (Web pendingSkill)."""
        dir_name = (name or "").strip()
        if not dir_name:
            self.log_line("No skill name", style="error")
            return
        self.pending_skill = {
            "dir": dir_name,
            "name": (display or dir_name).strip() or dir_name,
        }
        self._hide_nav()
        self._refresh_chrome()
        self.log_line(f"Skill /{dir_name} attached — type a message or Enter to send", style="system")
        self._focus_input()

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
