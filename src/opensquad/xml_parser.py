import re
from collections.abc import Callable

# Streaming tags: content dispatched character-by-character in real time; no nested tag recognition inside
_STREAMING_TAGS = frozenset({"to_user", "to_user_reply", "to_user_end_task", "thought", "think"})


class StreamingTagParser:
    """
    Enhanced streaming tag parser v4.1

    Tags fall into two categories:

    1. Streaming tags (to_user / thought / think)
       - Content is dispatched to the handler character-by-character via cycle buffer overflow.
       - The IN_CONTENT state machine never transitions back to OUT / IN_TAG_HEAD,
         so strings like <plan> inside a tag are treated as plain character stream,
         and are not parsed as new tags.
       - When the stream is truncated, remaining cycle buffer contents are dispatched to the handler.

    2. Commit-type tags (plan / tool_call / arguments / state, and all other tags)
       - Content is fully buffered until the closing tag appears, then dispatched atomically (Commit-on-Close).
       - If the stream ends without a closing tag (false positive / truncation),
         "<tag>buffered_content" is handed back to _default_handler to avoid data loss.

    API is fully compatible with v3/v4.
    """

    def __init__(
        self,
        handlers: dict[str, Callable[[str], None]],
        buffered_tags: list | None = None,
        default_handler: Callable[[str], None] | None = None,
    ):
        self._handlers = handlers
        self._buffered_tags = set(buffered_tags or ["option"])  # retained for API compatibility
        self._default_handler = default_handler

        self._state: str = "OUT"
        self._head_buf: str = ""
        self._tag_name: str = ""
        self._buffer: str = ""  # complete content buffer for commit-type tags

        # Sliding window: used only for end-tag detection
        self._cycle_len: int = 0
        self._cycle: list = []
        self._cycle_head: int = 0
        self._update_cycle_len()

        # Retained for API compatibility (no effect on logic)
        self._protected_tags = {"thought", "think"}
        self._in_protected_tag = False

    # -- Init / Reset --

    def _update_cycle_len(self):
        if not self._handlers:
            self._cycle_len = 20
        else:
            end_tags = [f"</{k}>" for k in self._handlers]
            self._cycle_len = max(map(len, end_tags)) + 5
        self._cycle = [""] * self._cycle_len
        self._cycle_head = 0

    def clean(self) -> None:
        self._state = "OUT"
        self._head_buf = ""
        self._tag_name = ""
        self._buffer = ""
        self._in_protected_tag = False
        self._update_cycle_len()

    # -- Public API --

    def feed(self, data: str):
        for ch in data:
            self._feed(ch)

    def finish(self):
        """
        Called when the stream ends.

        - Streaming tags (to_user/thought/think) in IN_CONTENT:
          Dispatch remaining cycle buffer characters to the handler (may be legitimately truncated content).
        - Commit-type tags in IN_CONTENT without a closing tag:
          Restore "<tag>buffered_content" as plain text to avoid losing content.
        - IN_TAG_HEAD: emit the incomplete tag head as plain text.
        """
        if self._state == "IN_CONTENT":
            if self._tag_name in _STREAMING_TAGS:
                # Streaming tag truncated: emit remaining cycle buffer
                remaining = self._cycle_drain()
                if remaining:
                    self._handlers[self._tag_name](remaining)
            else:
                # Commit-type tag false-positive/truncation: restore as plain text
                recovery = f"<{self._tag_name}>" + self._buffer
                self._emit_default(recovery)
                self._buffer = ""

        elif self._state == "IN_TAG_HEAD":
            self._emit_default(self._head_buf)

        self.clean()

    # -- Internal utilities --

    def _emit_default(self, text: str):
        if text and self._default_handler:
            self._default_handler(text)

    def _cycle_push(self, ch: str) -> str:
        """Push a character into the sliding window; return the oldest evicted character (overflow)."""
        old = self._cycle[self._cycle_head]
        self._cycle[self._cycle_head] = ch
        self._cycle_head = (self._cycle_head + 1) % self._cycle_len
        return old

    def _cycle_tail(self, n: int) -> str:
        """Return the last n characters in the sliding window (for end-tag detection)."""
        idx = (self._cycle_head - n) % self._cycle_len
        return "".join(self._cycle[(idx + i) % self._cycle_len] for i in range(n))

    def _cycle_drain(self) -> str:
        """Drain all non-empty characters from the cycle buffer in order (called at stream end)."""
        chars = []
        for i in range(self._cycle_len):
            idx = (self._cycle_head + i) % self._cycle_len
            if self._cycle[idx]:
                chars.append(self._cycle[idx])
        return "".join(chars)

    def _cycle_content_before_end(self, end_tag: str) -> str:
        """
        After the end tag has been confirmed at the tail of the cycle buffer, extract the content
        before it (i.e. the last few characters not yet dispatched via overflow).
        """
        end_tag_len = len(end_tag)
        chars = []
        for i in range(self._cycle_len - end_tag_len):
            idx = (self._cycle_head - self._cycle_len + i) % self._cycle_len
            if self._cycle[idx]:
                chars.append(self._cycle[idx])
        return "".join(chars)

    # -- Core state machine --

    def _feed(self, ch: str):
        # - OUT -
        if self._state == "OUT":
            if ch == "<":
                self._state = "IN_TAG_HEAD"
                self._head_buf = "<"
            elif ch == "t":
                # Proactive interception: bare tool_call (missing <)
                self._state = "POTENTIAL_LAZY_TAG"
                self._head_buf = "t"
            else:
                self._emit_default(ch)
            return

        # - POTENTIAL_LAZY_TAG -
        if self._state == "POTENTIAL_LAZY_TAG":
            self._head_buf += ch
            target = "tool_call"
            if target.startswith(self._head_buf):
                if self._head_buf == target:
                    self._head_buf = "<" + self._head_buf
                    self._state = "IN_TAG_HEAD"
                return
            else:
                for c in self._head_buf:
                    self._emit_default(c)
                self._head_buf = ""
                self._state = "OUT"
            return

        # - IN_TAG_HEAD -
        if self._state == "IN_TAG_HEAD":
            self._head_buf += ch

            if ch == ">":
                # Support namespaced tags like <minimax:tool_call>
                match = re.match(r"<([a-zA-Z0-9_]+(?::[a-zA-Z0-9_]+)?)", self._head_buf)
                if match:
                    raw_tag_name = match.group(1)
                    # Map namespaced tag to base handler name (e.g. minimax:tool_call -> tool_call)
                    tag_name = raw_tag_name.split(":")[-1] if ":" in raw_tag_name else raw_tag_name
                    if tag_name in self._handlers:
                        self._tag_name = raw_tag_name  # use raw name for correct end-tag matching
                        if self._head_buf.strip().endswith("/>"):
                            self._handlers[tag_name]("")
                            self._state = "OUT"
                        else:
                            # BUGFIX: keep raw_tag_name (e.g. "minimax:tool_call")
                            # so end-tag detection at line ~225 matches f"</{self._tag_name}>" correctly
                            self._tag_name = raw_tag_name
                            self._state = "IN_CONTENT"
                            self._buffer = ""
                            self._cycle = [""] * self._cycle_len
                            self._cycle_head = 0
                        self._head_buf = ""
                    else:
                        self._emit_default(self._head_buf)
                        self._head_buf = ""
                        self._state = "OUT"
                else:
                    self._emit_default(self._head_buf)
                    self._head_buf = ""
                    self._state = "OUT"

            elif len(self._head_buf) > 100 or ch == "\n":
                self._emit_default(self._head_buf)
                self._head_buf = ""
                self._state = "OUT"

            return

        # - IN_CONTENT -
        #
        # Key guarantee: in this state the machine never transitions back to OUT or IN_TAG_HEAD
        # (unless closing tag found), so < characters inside tag content do not trigger nested tag parsing.
        #
        if self._state == "IN_CONTENT":
            end_tag = f"</{self._tag_name}>"

            if self._tag_name in _STREAMING_TAGS:
                # -- Streaming tag: dispatch overflow character-by-character --
                overflow = self._cycle_push(ch)
                if overflow:
                    self._handlers[self._tag_name](overflow)

                if self._cycle_tail(len(end_tag)) == end_tag:
                    remaining = self._cycle_content_before_end(end_tag)
                    if remaining:
                        self._handlers[self._tag_name](remaining)
                    self._state = "OUT"
                    self._in_protected_tag = False

            else:
                # -- Commit-type tag: full buffer, dispatch once on close --
                self._buffer += ch
                self._cycle_push(ch)

                if self._cycle_tail(len(end_tag)) == end_tag:
                    content = self._buffer[: -len(end_tag)]
                    if content:
                        self._handlers[self._tag_name](content)
                    self._buffer = ""
                    self._state = "OUT"

            return
