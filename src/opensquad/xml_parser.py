import functools
import re
from collections.abc import Callable

# Streaming tags: content dispatched character-by-character in real time; no nested tag recognition inside
_STREAMING_TAGS = frozenset({"to_user", "to_user_reply", "to_user_end_task", "thought", "think"})

# User-facing XML. Inner text is chat; the wrappers themselves are not shown.
USER_VISIBLE_XML_TAGS = frozenset({"to_user", "to_user_reply", "to_user_end_task"})

# Agent↔runtime protocol. Never surface as chat — including inner text.
# thought/think still stream to the workflow panel when a handler is registered;
# they stay in this set so a missing handler cannot leak them as a bubble.
PROTOCOL_SILENT_TAGS = frozenset(
    {
        "timeout",
        "sleep",
        "wake",
        "state",
        "to_system",
        "title",
        "option",
        "forward",
        "system_reminder",
        "task_start",
        "task_complete",
        "task_failed",
        "plan",
        "thought",
        "think",
        "tool_call",
        "tool_calls",
        "tool_result",
        "result",
        "tool_response",
        "arguments",
        "func",
        "function",
        "function_calls",
        "calls",
        "invoke",
        "parameter",
        "dots_function_call",
    }
)

KNOWN_PROTOCOL_XML_TAGS = PROTOCOL_SILENT_TAGS | USER_VISIBLE_XML_TAGS

# Models sometimes emit the tool name as the XML tag itself:
#   <system.run_session_job>git status</system.run_session_job>
# Dots are not in PROTOCOL_SILENT_TAGS (those are bare names). Treat these
# as protocol so live+refresh cannot dump the command into a chat bubble.
_NAMESPACED_TOOL_TAG = re.compile(
    r"(?:system|filesystem|websearch|browser|mcp|anysearch|bocha|sequential_think)"
    r"(?:\.[A-Za-z_][\w]*)+",
    re.IGNORECASE,
)
_NAMESPACED_TOOL_OPEN = re.compile(
    r"<(" + _NAMESPACED_TOOL_TAG.pattern + r")\b",
    re.IGNORECASE,
)
_NAMESPACED_TOOL_BLOCK = re.compile(
    r"<(" + _NAMESPACED_TOOL_TAG.pattern + r")\b[^>]*>.*</\1\s*>",
    re.DOTALL | re.IGNORECASE,
)
_NAMESPACED_TOOL_UNCLOSED = re.compile(
    r"<(" + _NAMESPACED_TOOL_TAG.pattern + r")\b[^>]*>.*",
    re.DOTALL | re.IGNORECASE,
)
_NAMESPACED_TOOL_ORPHAN = re.compile(
    r"</?" + _NAMESPACED_TOOL_TAG.pattern + r"\b[^>]*>",
    re.IGNORECASE,
)


def _is_namespaced_tool_tag(name: str) -> bool:
    return bool(name and _NAMESPACED_TOOL_TAG.fullmatch(name.strip()))


def _discard_protocol_tag(_text: str) -> None:
    return None


def protocol_silent_handlers() -> dict[str, Callable[[str], None]]:
    """No-op handlers so protocol tags never fall through to to_user_stream."""
    return {name: _discard_protocol_tag for name in PROTOCOL_SILENT_TAGS}


@functools.lru_cache(maxsize=64)
def _silent_tag_patterns(tag: str) -> tuple[re.Pattern[str], re.Pattern[str], re.Pattern[str]]:
    escaped = re.escape(tag)
    return (
        re.compile(rf"<{escaped}\b[^>]*>.*?</{escaped}>", re.DOTALL | re.IGNORECASE),
        re.compile(rf"<{escaped}\b[^>]*/>", re.IGNORECASE),
        re.compile(rf"<{escaped}\b[^>]*>.*", re.DOTALL | re.IGNORECASE),
    )


def strip_silent_protocol_blocks(text: str) -> str:
    """Drop protocol XML blocks including inner text (timeout, plan, sleep, …)."""
    if not text:
        return text
    result = text
    for _ in range(8):
        nxt = _NAMESPACED_TOOL_BLOCK.sub("", result)
        if nxt == result:
            break
        result = nxt
    result = _NAMESPACED_TOOL_UNCLOSED.sub("", result)
    result = _NAMESPACED_TOOL_ORPHAN.sub("", result)
    for tag in PROTOCOL_SILENT_TAGS:
        paired, self_closing, unclosed = _silent_tag_patterns(tag)
        result = paired.sub("", result)
        result = self_closing.sub("", result)
        result = unclosed.sub("", result)
    return result


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
        "dots_function_call",
    }
)

# DSML / invoke tool-call tags. Register as no-op handlers so the stream parser
# swallows them instead of leaking the markup as plain chat text.
DSML_TOOL_TAG_NAMES = ("tool_calls", "function_calls", "calls", "invoke", "parameter")

_FW_BAR = "\uff5c"
_DSML_BARS = rf"(?:{_FW_BAR}{{1,2}}|\|{{1,2}})"
_DSML_PREFIX = rf"(?:{_DSML_BARS}(?:(?:DSML|mcp){_DSML_BARS})?\s*)"
_DSML_NAMES = r"(tool_calls|function_calls|calls|invoke|parameter)"
_RE_DSML_HEAD = re.compile(
    r"<(" rf"{_DSML_PREFIX}" rf"{_DSML_NAMES}" r")\b",
    re.IGNORECASE,
)
_RE_MARKUP_CLOSE = re.compile(
    r"</(?:"
    rf"{_DSML_PREFIX}"
    r")?"
    r"(tool_calls|function_calls|calls|invoke|parameter|tool_call|arguments|func|tool_result|result|tool_response|dots_function_call)"
    r"\s*>$",
    re.IGNORECASE,
)
_CLOSE_ALIASES: dict[str, frozenset[str]] = {
    "calls": frozenset({"calls", "tool_calls", "function_calls"}),
    "tool_calls": frozenset({"calls", "tool_calls", "function_calls"}),
    "function_calls": frozenset({"calls", "tool_calls", "function_calls"}),
    "invoke": frozenset({"invoke"}),
    "parameter": frozenset({"parameter"}),
    "tool_call": frozenset({"tool_call"}),
    "arguments": frozenset({"arguments"}),
    "func": frozenset({"func"}),
    "dots_function_call": frozenset({"dots_function_call"}),
}


def _logical_close_name(buf: str) -> str | None:
    """If *buf* ends with a tool/DSML close tag, return its logical name."""
    if not buf.endswith(">"):
        return None
    tail = buf[-200:] if len(buf) > 200 else buf
    m = _RE_MARKUP_CLOSE.search(tail)
    if not m:
        return None
    return (m.group(1) or "").lower()


def _is_swallowed_markup_head(head: str) -> bool:
    """Orphan DSML/tool/protocol close tags (and partial DSML heads) must not leak as chat."""
    s = (head or "").strip()
    if not s.startswith("<"):
        return False
    if _RE_MARKUP_CLOSE.search(s):
        return True
    ns = re.match(r"</?([a-zA-Z_][\w.]*)", s)
    if ns and _is_namespaced_tool_tag(ns.group(1)):
        return True
    proto = re.match(r"</?([a-zA-Z0-9_]+)", s)
    if proto and proto.group(1).lower() in PROTOCOL_SILENT_TAGS:
        return True
    if re.match(r"</?(?:tool_call|tool_calls|function_calls|invoke|parameter|func|arguments)\b", s, re.I):
        return True
    return bool(re.match(rf"</?{_DSML_BARS}", s) or "DSML" in s or re.match(r"</?(?:mcp)\b", s, re.I))


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
        return (
            key in _TOOL_COMMIT_TAGS
            or raw in _TOOL_COMMIT_TAGS
            or _is_namespaced_tool_tag(raw)
            or _is_namespaced_tool_tag(key)
        )

    def _closed_commit_tag(self) -> bool:
        """True when the commit buffer ends with a close tag for the open handler.

        DSML closers often differ from the opener (space after delimiter,
        ``calls`` vs ``tool_calls``, fullwidth vs halfwidth bars).
        """
        end_tag = f"</{self._tag_name}>"
        if self._tag_name and self._cycle_tail(len(end_tag)) == end_tag:
            return True
        name = _logical_close_name(self._buffer)
        if not name:
            return False
        key = (self._handler_key or "").split(":")[-1].lower()
        aliases = _CLOSE_ALIASES.get(key, frozenset({key}) if key else frozenset())
        return name in aliases

    def _commit_inner_content(self) -> str:
        end_tag = f"</{self._tag_name}>"
        if self._tag_name and self._buffer.endswith(end_tag):
            return self._buffer[: -len(end_tag)]
        m = _RE_MARKUP_CLOSE.search(self._buffer[-200:] if len(self._buffer) > 200 else self._buffer)
        if m:
            # Match is on a tail slice; map start back onto the full buffer.
            tail = self._buffer[-200:] if len(self._buffer) > 200 else self._buffer
            abs_start = len(self._buffer) - len(tail) + m.start()
            return self._buffer[:abs_start]
        return self._buffer

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
          Tool tags are parked as unclosed commits. Protocol-silent tags
          (timeout/sleep/plan/…) are discarded. Other tags restore
          "<tag>buffered_content" as plain text to avoid losing content.
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
                elif self._is_protocol_silent_tag():
                    # <timeout>60 / <sleep>5 without a closer is runtime protocol,
                    # not chat. Recovering it as plain text is what showed "60".
                    self._buffer = ""
                else:
                    recovery = f"<{self._tag_name}>" + self._buffer
                    self._emit_default(recovery)
                    self._buffer = ""

        elif self._state == "IN_TAG_HEAD":
            if not _is_swallowed_markup_head(self._head_buf) and not _is_swallowed_markup_head(self._head_buf + ">"):
                self._emit_default(self._head_buf)

        elif self._state == "POTENTIAL_LAZY_TAG":
            for c in self._head_buf:
                self._emit_default(c)

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
        key = (self._handler_key or "").lower()
        raw = (self._tag_name or "").split(":")[-1].lower()
        return key in _STREAMING_TAGS or raw in _STREAMING_TAGS

    def _is_protocol_silent_tag(self) -> bool:
        key = (self._handler_key or self._tag_name or "").split(":")[-1].lower()
        return key in PROTOCOL_SILENT_TAGS or _is_namespaced_tool_tag(key)

    def _lookup_handler(self):
        return (
            self._handlers.get(self._handler_key)
            or self._handlers.get(self._tag_name)
            or self._handlers.get((self._handler_key or "").lower())
            or self._handlers.get((self._tag_name or "").split(":")[-1].lower())
        )

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
        """Return (raw_tag_name, handler_key) if this open tag should be parsed.

        Registered handlers win. Protocol-silent tags are also claimed even
        without a handler so ``<timeout>60</timeout>`` cannot leak via default.
        """
        match = re.match(r"<([a-zA-Z0-9_]+(?::[a-zA-Z0-9_]+)?)", self._head_buf)
        if match:
            raw_tag_name = match.group(1)
            tag_name = raw_tag_name.split(":")[-1] if ":" in raw_tag_name else raw_tag_name
            key = tag_name.lower()
            if key in self._handlers or key in PROTOCOL_SILENT_TAGS:
                return raw_tag_name, key
        ns = _NAMESPACED_TOOL_OPEN.match(self._head_buf)
        if ns:
            raw_tag_name = ns.group(1)
            return raw_tag_name, "tool_call"
        if match:
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
            if key in self._handlers or key in PROTOCOL_SILENT_TAGS:
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
            # Mismatch: emit the prefix, then re-process the current char so a
            # following "<" can still open a real tag. Without re-feed, a word
            # ending in "t" ("text", "result") plus "</||DSML||calls>" leaks
            # the closer as plain chat and splits the tool stream.
            prior = self._head_buf[:-1]
            current = self._head_buf[-1]
            self._head_buf = ""
            self._state = "OUT"
            for c in prior:
                self._emit_default(c)
            self._feed(current)
            return

        # - IN_TAG_HEAD -
        if self._state == "IN_TAG_HEAD":
            self._head_buf += ch

            if ch == ">":
                resolved = self._resolve_open_tag()
                if resolved:
                    raw_tag_name, handler_key = resolved
                    if self._head_buf.strip().endswith("/>"):
                        handler = self._handlers.get(handler_key)
                        if handler:
                            handler("")
                        self._state = "OUT"
                    else:
                        self._begin_content_tag(raw_tag_name, handler_key, self._head_buf)
                    self._head_buf = ""
                elif _is_swallowed_markup_head(self._head_buf):
                    # Orphan </||DSML||calls> after the wrapper already closed,
                    # or a close tag whose bars/spacing don't match the opener.
                    self._head_buf = ""
                    self._state = "OUT"
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

                if self._closed_commit_tag():
                    content = self._commit_inner_content()
                    if content and handler:
                        handler(content)
                    self._buffer = ""
                    self._state = "OUT"
                    self._handler_key = ""

            return
