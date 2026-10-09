"""The surface the TUI mixins borrow from their host.

``app.py`` builds the real ``App`` subclass *inside* ``_build_app_class()``, so the
mixins (``_nav.py``, ``_sessions.py``, ``_formatting.py``, …) are checked by mypy on
their own — where the app's own helpers (``log_line``, ``_focus_input``, …) and
Textual's ``App`` API they call on ``self`` (``query_one``, ``set_interval``, ``run``,
…) do not exist. That is why the type checker reported every one of those calls as
"has no attribute", hundreds of times.

Declaring them here tells mypy the shape of the host without moving a single
implementation:

* **annotations only, never values.** A bare annotation creates no class attribute, so
  MRO resolution is untouched — ``self.query_one`` still finds Textual's real method and
  ``self._escape_markup`` still finds ``FormattingMixin``'s. Every member below is
  implemented by the app, by a sibling mixin, or by Textual; if one were not, the TUI
  would already fail at runtime.
* **``Any``, because the real types live behind lazy imports.** ``_host`` must stay
  import-cheap: importing ``textual`` (or a gateway client) at module scope is exactly
  what ``app.py`` goes out of its way to avoid.

Keep this list to members the mixins actually use, and keep it honest: an entry whose
implementation is deleted later would silently stop being a type error here.
"""

from __future__ import annotations

from typing import Any


class TuiHost:
    log_line: Any
    client: Any
    call_from_thread: Any
    _focus_input: Any
    _escape_markup: Any
    bridge: Any
    _refresh_chrome: Any
    agent: Any
    query_one: Any
    _theme_hex: Any
    group: Any
    begin_wait: Any
    end_wait: Any
    _sync_prompt_dock_menu: Any
    _side_hub: Any
    _is_selecting: Any
    _chat_write_counted: Any
    update_wait: Any
    _tool_detail_pending: Any
    set_interval: Any
    _static_set: Any
    _paint_prompt_meta_only: Any
    _schedule_ui: Any
    _tool_args_by_key: Any
    _paint_cache: Any
    _paint_live_think: Any
    _fmt_tokens_smooth: Any
    _fmt_duration: Any
    _hide_slash_menu: Any
    _hide_session_picker: Any
    notify: Any
    _chat_write: Any
    _send_queue: Any
    _slash_items: Any
    _resolve_project_path: Any
    get_css_variables: Any
    _context: Any
    _tool_markup_parts: Any
    _chat_replace_open: Any
    _hide_nav: Any
    _chat_pop_strips: Any
    _tool_line_failed: Any
    _fold_detail_text: Any
    _open_tool_keys: Any
    _pin_chat_bottom: Any
    _ensure_shimmer_timer: Any
    _thinking_markup: Any
    _bootstrap_agent: Any
    _push_nav: Any
    _session_cmd: Any
    available_themes: Any
    _paint_decision: Any
    _paint_slash_menu: Any
    _paint_session_picker: Any
    _wait_label: Any
    _rewrite_detail_blocks: Any
    _session_pick_active: Any
    _switch_agent: Any
    start_agent: Any
    join_group: Any
    _switch_session: Any
    _pretty_model_label: Any
    run: Any
