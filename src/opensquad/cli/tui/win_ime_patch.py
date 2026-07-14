"""
Fix Windows IME (Chinese/Japanese/Korean) commit into Textual Input.

Upstream Textual win32 EventMonitor drops KEY_EVENTs when::

    dwControlKeyState != 0  and  wVirtualKeyCode == 0

IME-committed CJK characters are typically delivered exactly that way
(VK=0), and ``dwControlKeyState`` is often non-zero because NumLock /
CapsLock bits are set. Result: Space/Enter confirms the candidate in the
IME UI, but nothing is inserted into the Textual Input.

See also: Textualize/textual win32 EventMonitor key filter.
"""

from __future__ import annotations

import sys
from typing import Any

_PATCHED = False


def apply_win_ime_patch() -> bool:
    """Monkey-patch Textual's Windows EventMonitor. Safe to call multiple times."""
    global _PATCHED
    if _PATCHED or sys.platform != "win32":
        return _PATCHED
    try:
        from textual.drivers import win32
    except ImportError:
        return False

    # Replace only the key-filter branch by wrapping run() with a fixed copy.
    # Keep behavior otherwise identical to textual.drivers.win32.EventMonitor.run.
    from ctypes import byref, wintypes

    from textual import constants
    from textual._xterm_parser import XTermParser
    from textual.drivers.win32 import (
        INPUT_RECORD,
        KERNEL32,
        STD_INPUT_HANDLE,
        GetStdHandle,
        wait_for_handles,
    )

    def _fixed_run(self: Any) -> None:  # noqa: ANN401
        exit_requested = self.exit_event.is_set
        parser = XTermParser(debug=constants.DEBUG)

        try:
            read_count = wintypes.DWORD(0)
            hIn = GetStdHandle(STD_INPUT_HANDLE)

            MAX_EVENTS = 1024
            KEY_EVENT = 0x0001
            WINDOW_BUFFER_SIZE_EVENT = 0x0004

            arrtype = INPUT_RECORD * MAX_EVENTS
            input_records = arrtype()
            ReadConsoleInputW = KERNEL32.ReadConsoleInputW
            keys: list[str] = []
            append_key = keys.append

            while not exit_requested():
                for event in parser.tick():
                    self.process_event(event)

                if wait_for_handles([hIn], 100) is None:
                    continue

                ReadConsoleInputW(hIn, byref(input_records), MAX_EVENTS, byref(read_count))
                read_input_records = input_records[: read_count.value]

                del keys[:]
                new_size: tuple[int, int] | None = None

                for input_record in read_input_records:
                    event_type = input_record.EventType

                    if event_type == KEY_EVENT:
                        key_event = input_record.Event.KeyEvent
                        key = key_event.uChar.UnicodeChar
                        if not key_event.bKeyDown:
                            continue
                        # Skip NUL only. Do NOT drop VK==0 + NumLock/CapsLock —
                        # that is how Windows delivers IME-committed CJK.
                        if not key:
                            continue
                        append_key(key)
                    elif event_type == WINDOW_BUFFER_SIZE_EVENT:
                        size = input_record.Event.WindowBufferSizeEvent.dwSize
                        new_size = (size.X, size.Y)

                if keys:
                    for event in parser.feed("".join(keys).encode("utf-16", "surrogatepass").decode("utf-16")):
                        self.process_event(event)
                if new_size is not None:
                    self.on_size_change(*new_size)

        except Exception as error:
            self.app.log.error("EVENT MONITOR ERROR", error)

    win32.EventMonitor.run = _fixed_run  # type: ignore[method-assign]
    _PATCHED = True
    return True
