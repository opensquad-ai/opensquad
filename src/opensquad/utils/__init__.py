# -*- coding: utf-8 -*-
import sys
import re


def extract_and_remove_first_tag(text):
    """
    Extract <tag> content from the beginning of a string and return the content along with the cleaned text.
    Used for identifying Task IDs and similar scenarios.
    """
    extracted_content = None
    def replacement(match):
        nonlocal extracted_content
        extracted_content = match.group(1)
        return ""
    # Replace only the first match
    cleaned_text = re.sub(r"<([^>]+)>", replacement, text, count=1)
    return extracted_content, cleaned_text


class CharPrinter:
    """Terminal-safe character printer that correctly handles ANSI escape sequences.

    Extracted from chat_api.py to eliminate triplicated definitions across
    ChatAPI, ClaudeAPI and GoogleAPI.
    """

    def __init__(self, max_width=40):
        self.max_width = max_width
        self.buffer = []
        self.visible_length = 0

    def _is_ansi(self, char):
        # Only ESC (\x1b) starts an ANSI escape sequence.
        # The old range check \x1b..\x1f incorrectly flagged all C0 controls.
        return char == '\x1b'

    def add_char(self, char):
        try:
            if char in ('\n', '\r'):
                self.flush()
                self.buffer.append(char)
                return
        except UnicodeEncodeError:
            return

        add_len = 1 if not self._is_ansi(char) else 0
        new_visible = self.visible_length + add_len

        if new_visible > self.max_width:
            self.flush()

        self.buffer.append(char)
        self.visible_length = new_visible

    def flush(self):
        if self.buffer:
            try:
                sys.stdout.write(''.join(self.buffer))
                sys.stdout.flush()
            except UnicodeEncodeError:
                pass
            self.buffer = []
            self.visible_length = 0

    def dynamic_single_callback(self, c):
        self.add_char(c)
        self.flush()
