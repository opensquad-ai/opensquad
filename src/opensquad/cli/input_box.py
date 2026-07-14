"""Claude-Code-like framed input box for opensquad chat (prompt_toolkit)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from opensquad.cli.commands.chat_cmd import InteractiveShell


def build_chat_session(shell: InteractiveShell):
    """
    Build a PromptSession that looks like Claude Code / OpenCode bottom input:
      ╭──────────────────────────────────────────╮
      │ ❯ your message…                          │
      ╰──────────────────────────────────────────╯
      agent · solo · Enter send · Alt+Enter newline
    """
    import os

    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style

    from opensquad.cli.completer import build_completer
    from opensquad.cli.media import attach_from_clipboard, chip_label

    hist_path = os.path.join(os.path.expanduser("~"), ".opensquad", "chat_history")
    os.makedirs(os.path.dirname(hist_path), exist_ok=True)

    kb = KeyBindings()

    # Enter = send (Claude Code style); Alt+Enter / Ctrl+J = newline
    @kb.add("enter")
    def _accept(event) -> None:
        event.current_buffer.validate_and_handle()

    @kb.add("escape", "enter")
    def _newline_esc(event) -> None:
        event.current_buffer.newline()

    @kb.add("c-j")
    def _newline_cj(event) -> None:
        event.current_buffer.newline()

    @kb.add("c-v")
    def _paste(event) -> None:
        try:
            media = attach_from_clipboard()
        except Exception:
            media = None
        if media:
            shell.pending_media.append(media)
            # Show chip above the box without breaking the prompt layout much
            print(f"\n  attached {chip_label(media)}  (/detach to clear)")
            event.app.invalidate()
            return
        try:
            from prompt_toolkit.selection import PasteMode

            data = event.app.clipboard.get_data()
            event.current_buffer.paste_clipboard_data(data, paste_mode=PasteMode.INPLACE)
        except Exception:
            pass

    def bottom_toolbar() -> Any:
        from opensquad.cli.banner import status_right
        from opensquad.cli.media import format_pending_chips

        base = status_right(
            agent=shell.agent,
            mode=shell.mode,
            group_name=(shell.group.group_name if shell.group else None),
            pending_n=len(shell.pending_media),
        )
        chips = format_pending_chips(shell.pending_media)
        hint = "Enter send · Alt+Enter newline · /commands · Ctrl+C cancel"
        if chips:
            return HTML(f"<b>{base}</b>  {chips}  <style bg='ansibrightblack'>{hint}</style>")
        return HTML(f"<b>{base}</b>  <style bg='ansibrightblack'>{hint}</style>")

    def placeholder() -> Any:
        if shell.mode == "group":
            g = (shell.group.group_name if shell.group else None) or "group"
            return HTML(f"<style color='ansibrightblack'>Message {g}…</style>")
        ag = shell.agent or "agent"
        return HTML(f"<style color='ansibrightblack'>Message {ag}…  type /help</style>")

    def continuation(width: int, line_number: int, wrap_count: int) -> str:
        return " " * max(0, width - 2) + "… "

    style = Style.from_dict(
        {
            "prompt": "ansicyan bold",
            "frame.border": "ansicyan",
            "frame": "",
            "bottom-toolbar": "noreverse bg:#1a1a1a fg:#cccccc",
            "bottom-toolbar.text": "bg:#1a1a1a fg:#cccccc",
            "placeholder": "ansibrightblack italic",
            "completion-menu.completion": "bg:#222222 #aaaaaa",
            "completion-menu.completion.current": "bg:#444444 #ffffff",
            "completion-menu.meta.completion": "bg:#222222 #888888",
            "completion-menu.meta.completion.current": "bg:#444444 #ffffff",
        }
    )

    return PromptSession(
        history=FileHistory(hist_path),
        completer=build_completer(),
        complete_while_typing=True,
        complete_in_thread=True,
        enable_history_search=True,
        multiline=True,
        wrap_lines=True,
        show_frame=True,
        bottom_toolbar=bottom_toolbar,
        placeholder=placeholder,
        prompt_continuation=continuation,
        key_bindings=kb,
        style=style,
        reserve_space_for_menu=6,
        # Refresh toolbar so mode/agent/pending chips update live
        refresh_interval=0.5,
    )


def prompt_message(shell: InteractiveShell) -> Any:
    """Left-side glyph inside the frame (❯ for solo, g❯ for group)."""
    from prompt_toolkit.formatted_text import HTML

    if shell.mode == "group":
        return HTML("<ansigreen><b>g❯</b></ansigreen> ")
    return HTML("<ansicyan><b>❯</b></ansicyan> ")
