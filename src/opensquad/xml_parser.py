import re
from collections.abc import Callable

# Streaming tags: content dispatched character-by-character in real time; no nested tag recognition inside
_STREAMING_TAGS = frozenset({"to_user", "to_user_reply", "to_user_end_task", "thought", "think"})

# Commit-type tool tags. Unclosed buffers must NOT leak as chat text; Agent Web
# peeks them so a tool row appears before </tool_call>.
_TOOL_COMMIT_TAGS = frozenset(
    {
        "tool_call",
        "tool_calls",
        "function_calls",
        "calls",
        "invoke",
        "arguments",
        "func",
        "parameter",
    }
)

# DSML / invoke tool-call tags. Register as no-op handlers so the stream parser
# swallows them instead of leaking the markup as plain chat text.
DSML_TOOL_TAG_NAMES = ("tool_calls", "function_calls", "calls", "invoke", "parameter")

_FW_BAR = "\uff5c"
_RE_DSML_HEAD = re.compile(
    r"<("
    rf"(?:{_FW_BAR}{{1,2}}|\|{{1,2}})"
    rf"(?:(?:DSML|mcp)(?:{_FW_BAR}{{1,2}}|\|{{1,2}}))?"
    r"\s*"
    r"(tool_calls|function_calls|calls|invoke|parameter)"
    r")\b",
    re.IGNORECASE,
)


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
        self._handler_key: str = ""
        self._buffer: str = ""  # complete content buffer for commit-type tags
        self._tag_attrs: dict[str, str] = {}
        self._unclosed_commit: dict | None = None
        self._commit_progress_callback: Callable[[str, str, dict[str, str]], None] | None = None

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
            self._cycle_len = 40
        else:
            end_tags = [f"</{k}>" for k in self._handlers]
            self._cycle_len = max(map(len, end_tags)) + 20
        self._cycle = [""] * self._cycle_len
        self._cycle_head = 0

    def clean(self) -> None:
        self._state = "OUT"
        self._head_buf = ""
        self._tag_name = ""
        self._handler_key = ""
        self._buffer = ""
        self._tag_attrs = {}
        self._in_protected_tag = False
        self._update_cycle_len()

    # -- Public API --

    def feed(self, data: str):
        for ch in data:
            self._feed(ch)
        self._fire_commit_progress()

    def set_commit_progress_callback(self, callback: Callable[[str, str, dict[str, str]], None] | None) -> None:
        """Called once per feed() while a tool commit-tag is open (live UI preview)."""
        self._commit_progress_callback = callback

    def peek_commit_state(self) -> tuple[str, str, dict[str, str]] | None:
        """Return (handler_key, buffer, attrs) when IN_CONTENT on a commit-type tag."""
        if self._state != "IN_CONTENT" or self._is_streaming_tag():
            return None
        key = (self._handler_key or self._tag_name or "").split(":")[-1]
        if not key:
            return None
        return key, self._buffer, dict(self._tag_attrs or {})

    def take_unclosed_commit(self) -> dict | None:
        data = self._unclosed_commit
        self._unclosed_commit = None
        return data

    def _is_tool_commit_tag(self) -> bool:
        key = (self._handler_key or self._tag_name or "").split(":")[-1].lower()
        raw = (self._tag_name or "").split(":")[-1].lower()
        return key in _TOOL_COMMIT_TAGS or raw in _TOOL_COMMIT_TAGS

    def _fire_commit_progress(self) -> None:
        cb = self._commit_progress_callback
        if not cb or self._state != "IN_CONTENT" or self._is_streaming_tag():
            return
        if not self._is_tool_commit_tag():
            return
        key = (self._handler_key or self._tag_name or "").split(":")[-1]
        try:
            cb(key, self._buffer, dict(self._tag_attrs or {}))
        except Exception:
            pass

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
            if self._is_streaming_tag():
                # Streaming tag truncated: emit remaining cycle buffer
                remaining = self._cycle_drain()
                handler = self._lookup_handler()
                if remaining and handler:
                    handler(remaining)
            else:
                # Unclosed tool tags are parsed as live/final tool calls — leaking
                # them as chat text is what made Agent Web show `websearch / query
                # / 福州天气` as a plain bubble after refresh.
                if self._is_tool_commit_tag():
                    self._unclosed_commit = {
                        "tag": (self._handler_key or self._tag_name or "").split(":")[-1],
                        "raw": self._tag_name,
                        "buffer": self._buffer,
                        "attrs": dict(self._tag_attrs or {}),
                    }
                    self._buffer = ""
                else:
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

    def _is_streaming_tag(self) -> bool:
        return self._handler_key in _STREAMING_TAGS or self._tag_name in _STREAMING_TAGS

    def _lookup_handler(self):
        return self._handlers.get(self._handler_key) or self._handlers.get(self._tag_name)

    def _begin_content_tag(self, raw_tag_name: str, handler_key: str, head_buf: str = "") -> None:
        self._tag_name = raw_tag_name
        self._handler_key = handler_key
        self._state = "IN_CONTENT"
        self._buffer = ""
        self._tag_attrs = {}
        for m in re.finditer(r'([a-zA-Z_][\w:-]*)\s*=\s*"([^"]*)"', head_buf or ""):
            self._tag_attrs[m.group(1).lower()] = m.group(2)
        end_tag = f"</{raw_tag_name}>"
        needed = len(end_tag) + 8
        if needed > self._cycle_len:
            self._cycle_len = needed
        self._cycle = [""] * self._cycle_len
        self._cycle_head = 0

    def _resolve_open_tag(self) -> tuple[str, str] | None:
        """Return (raw_tag_name, handler_key) if this open tag has a registered handler."""
        match = re.match(r"<([a-zA-Z0-9_]+(?::[a-zA-Z0-9_]+)?)", self._head_buf)
        if match:
            raw_tag_name = match.group(1)
            tag_name = raw_tag_name.split(":")[-1] if ":" in raw_tag_name else raw_tag_name
            if tag_name in self._handlers:
                return raw_tag_name, tag_name
            return None
        dsml = _RE_DSML_HEAD.match(self._head_buf)
        if not dsml:
            return None
        raw_tag_name = dsml.group(1)
        tag_name = (dsml.group(2) or "").lower()
        aliases = [tag_name]
        if tag_name in ("calls", "function_calls"):
            aliases.append("tool_calls")
        elif tag_name == "tool_calls":
            aliases.extend(["calls", "function_calls"])
        for key in aliases:
            if key in self._handlers:
                return raw_tag_name, key
        return None

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
                resolved = self._resolve_open_tag()
                if resolved:
                    raw_tag_name, handler_key = resolved
                    if self._head_buf.strip().endswith("/>"):
                        self._handlers[handler_key]("")
                        self._state = "OUT"
                    else:
                        self._begin_content_tag(raw_tag_name, handler_key, self._head_buf)
                    self._head_buf = ""
                else:
                    self._emit_default(self._head_buf)
                    self._head_buf = ""
                    self._state = "OUT"

            elif ch == "\n" and len(self._head_buf) <= 240 and re.match(r"<[A-Za-z0-9_:\uFF5C|]", self._head_buf):
                # Pretty-printed open tags: <tool_call\n name="websearch.search">
                return
            elif len(self._head_buf) > 240 or ch == "\n":
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
            handler = self._lookup_handler()

            if self._is_streaming_tag():
                # -- Streaming tag: dispatch overflow character-by-character --
                overflow = self._cycle_push(ch)
                if overflow and handler:
                    handler(overflow)

                if self._cycle_tail(len(end_tag)) == end_tag:
                    remaining = self._cycle_content_before_end(end_tag)
                    if remaining and handler:
                        handler(remaining)
                    self._state = "OUT"
                    self._handler_key = ""
                    self._in_protected_tag = False

            else:
                # -- Commit-type tag: full buffer, dispatch once on close --
                self._buffer += ch
                self._cycle_push(ch)

                if self._cycle_tail(len(end_tag)) == end_tag:
                    content = self._buffer[: -len(end_tag)]
                    if content and handler:
                        handler(content)
                    self._buffer = ""
                    self._state = "OUT"
                    self._handler_key = ""

            return
