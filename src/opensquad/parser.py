import ast
import json
import re

# ---------------------------------------------------------------------------
# Parameter / tool-name normalization helpers (DSML tolerance)
# ---------------------------------------------------------------------------
# LLMs frequently switch between naming conventions for the same conceptual
# argument, e.g. "startLine" vs "start_line" vs "start-line". Downstream tool
# schemas usually expect one canonical form (snake_case). These helpers make
# the parser forgiving: arguments and tool names are normalized so that
# downstream matching against a tool schema succeeds more often.
import re as _re_norm
from typing import Any

from .log_setup import get_tool_call_debug_logger

_DELIM_RE = _re_norm.compile(r"[\s_\-]+")


def _camel_to_snake(name: str) -> str:
    """Convert camelCase / PascalCase to snake_case.
    Examples:
        startLine   -> start_line
        endLine     -> end_line
        HTTPServer  -> http_server
    """
    if not name:
        return name
    s1 = _re_norm.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s2 = _re_norm.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
    return s2.lower()


def _normalize_key(key: str) -> str:
    """Normalize a parameter key to canonical snake_case form.

    Steps:
        1. Strip leading/trailing whitespace.
        2. Lower-case.
        3. Collapse any of [ '_' , '-' , ' ' ] into a single underscore.

    Examples:
        startLine  -> startline
        start_line -> start_line
        start-line -> start_line
        "  FooBar" -> "foobar"
    """
    if not key:
        return key
    k = key.strip()
    k = _DELIM_RE.sub("_", k)
    return k.lower()


def _normalize_arg_key(key: str) -> str:
    """Normalize a parameter key with camelCase awareness.

    Order of operations:
        1. _camel_to_snake to break camelCase boundaries.
        2. _normalize_key to collapse separators and lowercase.
    """
    return _normalize_key(_camel_to_snake(key))


def _normalize_tool_name(name: str) -> str:
    """Normalize a tool/function name.

    Rules:
        - strip
        - if there's a dot (namespace.func), preserve but normalize each side:
            "Filesystem.Read_File" -> "filesystem.read_file"
        - if there's a double-underscore (Native FC / MCP), preserve segments:
            "mcp__Playwright__Browser_Navigate" -> "mcp__playwright__browser_navigate"
            "Filesystem__Read_File" -> "filesystem__read_file"
          (must NOT collapse ``__`` to ``_`` — registry routes MCP on ``mcp__``
          and Native FC on ``namespace__function``)
        - else: just lower-case + collapse separators.
    """
    if not name:
        return name
    n = name.strip()
    if "." in n:
        parts = n.split(".")
        parts = [_normalize_key(p) for p in parts if p]
        return ".".join(parts)
    if "__" in n:
        # MCP / Native-FC names are built as ``mcp__{server}__{tool}`` and the
        # server segment is whatever the user called the server in
        # ``mcp_config.json`` — ``windows-cli``, ``chrome-devtools``,
        # ``zai-mcp-server``. That hyphen is part of the real registry key, so
        # folding it to ``_`` (what ``_normalize_key`` does for parameter keys)
        # renamed the call into a tool that does not exist and the registry
        # lookup missed. Only whitespace is folded here, as before.
        parts = [re.sub(r"\s+", "_", p.strip().lower()) for p in n.split("__") if p]
        return "__".join(parts)
    return _normalize_key(n)


_INVOKE_PLACEHOLDERS = frozenset({"tool_call", "tool", "function", "invoke", "call", "llm_recovered"})
_PREVIEW_NAME_SHORTHAND = frozenset({"websearch", "grep", "glob", "shell", "bash", "read", "ls"})


def _is_preview_tool_name_ready(name: str, *, closed: bool) -> bool:
    """True when a streaming XML tool name is complete enough to show / execute."""
    n = (name or "").strip()
    if not n or any(ch.isspace() for ch in n):
        return False
    key = n.lower()
    if key in _INVOKE_PLACEHOLDERS or key in _BARE_TOOL_NAME_DENY:
        return False
    if "." in n and not n.endswith("."):
        return bool(n.split(".", 1)[1])
    if "__" in n and not n.endswith("__"):
        return bool(n.rsplit("__", 1)[-1])
    if key in _PREVIEW_NAME_SHORTHAND:
        return True
    return bool(closed and any("\u4e00" <= ch <= "\u9fff" for ch in n) and len(n) >= 2)


def _name_from_func_xml(body: str) -> str | None:
    m = re.search(r"<func\s*>([^<]+)</func\s*>", body or "", re.IGNORECASE)
    if not m:
        return None
    name = (m.group(1) or "").strip()
    return _normalize_tool_name(name) if name else None


def _resolve_parsed_tool_name(name: str, body: str, args: dict[str, Any] | None = None) -> str | None:
    """Map DSML ``invoke name="tool_call"`` / llm_recovered onto the real func."""
    args = args or {}
    norm = _normalize_tool_name(name) if name else ""
    key = (norm or "").lower()
    if key in _INVOKE_PLACEHOLDERS:
        inner = _name_from_func_xml(body)
        if inner:
            return inner
        func_arg = args.get("func") or args.get("function")
        if func_arg:
            return _normalize_tool_name(str(func_arg))
        return None
    return norm or None


# A hyphen is a legal character in a tool name: MCP tools are registered as
# ``mcp__{server}__{tool}`` and the server segment is a free-form user choice
# (``windows-cli``, ``chrome-devtools``, ``zai-mcp-server``, ``sequential-thinking``).
# Excluding it here meant ``<tool_call>mcp__windows-cli__execute_command`` was
# silently not parsed at all — see ``_normalize_tool_name`` for the other half.
_BARE_TOOL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]{0,80}$")
_BARE_TOOL_NAME_DENY = frozenset(
    {
        "arg_key",
        "arg_value",
        "parameter",
        "arguments",
        "func",
        "function",
        "invoke",
        "thought",
        "think",
        "tool_call",
        "tool_calls",
        "plan",
        "to_user",
    }
)
_KNOWN_TOOL_PREFIX_RE = re.compile(
    r"^(websearch|bocha|filesystem|anysearch|browser|mcp__|system|im|vision|"
    r"reminder|help|agent_|workspace|grep|memory|quick_note|external_api|"
    r"feishu|whisper|sensevoice|long_memory|step_voice)",
    re.IGNORECASE,
)
_ARG_KEY_VALUE_PAIR_RE = re.compile(
    r"<arg_key\s*>(.*?)</arg_key\s*>\s*<arg_value\s*>(.*?)</arg_value\s*>",
    re.DOTALL | re.IGNORECASE,
)
_ARG_KEY_VALUE_OPEN_RE = re.compile(
    r"<arg_key\s*>(.*?)</arg_key\s*>\s*<arg_value\s*>([^<]*)$",
    re.DOTALL | re.IGNORECASE,
)


def _looks_like_xml_tool_name(name: str, blob: str = "") -> bool:
    """True for Qwen/Ling first-line names like ``websearch`` / ``mcp__x__y``."""
    cand = (name or "").strip()
    if not cand or not _BARE_TOOL_NAME_RE.match(cand):
        return False
    if cand.lower() in _BARE_TOOL_NAME_DENY:
        return False
    if "." in cand or "__" in cand or _KNOWN_TOOL_PREFIX_RE.match(cand):
        return True
    return bool(re.search(r"<arg_key\b|<query\b|<arguments\b|<parameter\b", blob, re.IGNORECASE))


def _first_line_tool_name(xml_content: str) -> str:
    """Extract a bare tool name sitting on the first line of a <tool_call> body."""
    if not xml_content:
        return ""
    first = xml_content.lstrip().split("\n", 1)[0].strip()
    first = re.split(r"<", first, maxsplit=1)[0].strip()
    return first if _looks_like_xml_tool_name(first, xml_content) else ""


def _parse_arg_key_value_pairs(xml_content: str) -> dict[str, Any]:
    """Qwen / Ling / GLM: <arg_key>query</arg_key><arg_value>福州天气</arg_value>."""
    if not xml_content:
        return {}
    result: dict[str, Any] = {}
    for match in _ARG_KEY_VALUE_PAIR_RE.finditer(xml_content):
        key = match.group(1).strip()
        if not key or key.lower() in _BARE_TOOL_NAME_DENY:
            continue
        result[key] = ResponseParser.parse_param_value(match.group(2).strip())
    open_m = _ARG_KEY_VALUE_OPEN_RE.search(xml_content)
    if open_m:
        key = open_m.group(1).strip()
        val = open_m.group(2).strip()
        if key and val and key not in result:
            result[key] = ResponseParser.parse_param_value(val)
    return result


# Mapping of Python escape sequences that must be restored to literal backslash + char
# after ast.literal_eval() or string fallback. These are common Windows path characters
# that LLMs output as single-backslash paths (e.g., "C:\Users\...") which Python
# interprets as escape sequences.
_ESCAPE_RESTORE_MAP = {
    "\a": r"\a",  # bell (0x07)  -> \a  (e.g., C:\app)
    "\b": r"\b",  # backspace    -> \b  (e.g., C:\bin)
    "\f": r"\f",  # form feed    -> \f
    "\v": r"\v",  # vertical tab -> \v
    # Do not restore \n / \r globally: multiline tool args (file writes) must
    # keep real newlines. Tabs are restored only for single-line values below
    # (Windows path accident: docs\\tool_x → docs + TAB + ool_x).
}


def _restore_windows_path_escapes(value: str) -> str:
    """
    Restore accidental escape characters back to literal backslash + letter.

    When LLMs output Windows paths like "C:\\Users\\bin", ast.literal_eval()
    interprets \\a -> bell, \\b -> backspace, etc. This function reverses those
    transformations for characters that are unlikely to be intentional in paths.

    Only applied to string values. Safe because we only restore to \\X form,
    which is the correct literal representation for file paths.
    """
    result = value
    for escaped_char, literal_form in _ESCAPE_RESTORE_MAP.items():
        result = result.replace(escaped_char, literal_form)
    # Quoted path "docs\\tool_result.md" becomes a TAB inside a single-line string.
    if "\t" in result and "\n" not in result and "\r" not in result:
        result = result.replace("\t", r"\t")
    return result


# ---------------------------------------------------------------------------
# DSML (DeepSeek Markup Language) tag normalization
# ---------------------------------------------------------------------------
# Models emit several near-equivalent forms, including extra spaces and a
# wrapper named `calls` instead of `tool_calls`:
#   <｜｜DSML｜｜tool_calls>     canonical (no space)
#   <｜｜DSML｜｜ calls>         space after delimiter, short wrapper
#   <｜｜invoke name="...">     delimiter only, no "DSML" token
#   <|mcp|invoke>               mcp delimiter
#   <｜DSML｜invoke>            single-bar DeepSeek special-token style
_FW_BAR = "\uff5c"
_DSML_BARS = rf"(?:{_FW_BAR}{{1,2}}|\|{{1,2}})"
_DSML_PREFIX = rf"(?:{_DSML_BARS}(?:DSML|mcp){_DSML_BARS}|{_FW_BAR}{{2}}|\|{{2}})"
_DSML_TAG_NAMES = r"(?:tool_calls|function_calls|calls|invoke|parameter)"
_RE_DSML_TAG = _re_norm.compile(
    rf"<(/?){_DSML_PREFIX}\s*({_DSML_TAG_NAMES})\b([^>]*)>",
    _re_norm.IGNORECASE,
)
_DSML_WRAPPER_ALIASES = {"calls": "tool_calls", "function_calls": "tool_calls"}
_RE_ANY_TOOL_CALL_START = _re_norm.compile(
    r"<tool_call\b"
    r"|<dots_function_call\b"
    rf"|<{_DSML_PREFIX}\s*(?:tool_calls|function_calls|calls|invoke)\b",
    _re_norm.IGNORECASE,
)


def _canonicalize_dsml(text: str) -> str:
    """Rewrite DSML tag variants to ``<||DSML||tag ...>`` with no extra spaces."""
    if not text:
        return text
    if "DSML" not in text and "mcp" not in text.lower() and _FW_BAR not in text and "||" not in text:
        return text

    def _repl(m: _re_norm.Match) -> str:
        slash = m.group(1)
        tag = (m.group(2) or "").lower()
        rest = m.group(3) or ""
        tag = _DSML_WRAPPER_ALIASES.get(tag, tag)
        return f"<{slash}||DSML||{tag}{rest}>"

    return _RE_DSML_TAG.sub(_repl, text)


_DSML_ARG_LINE_RE = _re_norm.compile(r'^\s*(?:[\w.\-]+\s*[:=]|<[^>]*>|[}\]"\'],?)$')
_DSML_ANY_TAG_RE = _re_norm.compile(r"</?(?:\uff5c{1,2}|\|{1,2})[^>]*>")


def _dsml_tail_is_arguments(tail: str) -> bool:
    """Does the text after an unclosed DSML tag look like leaked tool arguments?

    Only then is it safe to drop everything through end-of-string. A model that
    merely *describes* DSML syntax (e.g. answering "how do I pass a parameter?")
    leaves ordinary prose behind the tag, and swallowing that prose would erase
    the user-visible answer.
    """
    stripped = (tail or "").strip()
    if not stripped:
        return True
    # Ignore nested DSML wrapper tags — only their inner payload matters.
    stripped = _DSML_ANY_TAG_RE.sub("", stripped).strip()
    if not stripped:
        return True
    # JSON / array argument payload.
    if stripped[0] in "{[":
        return True
    lines = [ln for ln in stripped.splitlines() if ln.strip()]
    if not lines:
        return True
    # Every remaining line must look like a parameter fragment, wrapper tag, or
    # stray bracket — a single prose line is enough to keep the text.
    return all(_DSML_ARG_LINE_RE.match(ln) for ln in lines)


def strip_dsml_tool_markup(text: str) -> str:
    """Remove DSML / invoke tool-call blocks (including inner content) from display text."""
    if not text:
        return text
    t = _canonicalize_dsml(text)
    for tag in ("tool_calls", "invoke", "parameter"):
        t = _re_norm.sub(
            rf"<\|\|DSML\|\|{tag}\b[^>]*>.*?</\|\|DSML\|\|{tag}\s*>",
            "",
            t,
            flags=_re_norm.DOTALL | _re_norm.IGNORECASE,
        )
        # Unclosed opener (stream hung mid-call). Drop the tag, and drop the
        # remainder only when it still reads as tool arguments — see
        # _dsml_tail_is_arguments for why an unconditional `.*$` is unsafe.
        opener = _re_norm.compile(rf"<\|\|DSML\|\|{tag}\b[^>]*>", _re_norm.IGNORECASE)
        match = opener.search(t)
        if match:
            tail = t[match.end() :]
            t = t[: match.start()] + ("" if _dsml_tail_is_arguments(tail) else tail)
    t = _re_norm.sub(
        r"</?\|\|DSML\|\|(?:tool_calls|function_calls|calls|invoke|parameter)\b[^>]*>",
        "",
        t,
        flags=_re_norm.IGNORECASE,
    )
    return t


def _parse_jsonish_object(value: str) -> dict[str, Any] | None:
    """Parse a JSON/Python dict, recovering common LLM quote/escape mistakes."""
    s = (value or "").strip()
    if not s.startswith("{") or not s.endswith("}"):
        return None
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    try:
        obj = ast.literal_eval(s)
        if isinstance(obj, dict):
            return obj
    except (ValueError, SyntaxError, TypeError, MemoryError):
        pass
    # Single-key object whose string value contains unescaped quotes
    # e.g. {"command": "dir "c:\users\foo" /b"}
    m = _re_norm.match(r'\{\s*"([^"\\]+)"\s*:\s*"(.*)"\s*\}\s*$', s, _re_norm.DOTALL)
    if m:
        return {m.group(1): m.group(2)}
    return None


def _expand_dsml_arguments(args: dict[str, Any]) -> dict[str, Any]:
    """Unwrap a DSML ``parameter name="arguments"`` JSON blob into tool args."""
    if not args or "arguments" not in args:
        return args
    blob = args.get("arguments")
    parsed: Any = blob
    if isinstance(blob, str):
        parsed = _parse_jsonish_object(blob)
        if parsed is None:
            return args
    if not isinstance(parsed, dict):
        return args
    if set(args.keys()) <= {"arguments"}:
        return parsed
    merged = dict(parsed)
    for k, v in args.items():
        if k != "arguments" and k not in merged:
            merged[k] = v
    return merged


class ResponseParser:
    """Parses XML-structured responses from AI models."""

    @staticmethod
    def strip_reasoning_blocks(text: str) -> str:
        """Remove <think>/<thought> regions so nested tag mentions cannot leak."""
        if not text:
            return ""
        cleaned = re.sub(
            r"<think\b[^>]*>.*?</think>",
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        cleaned = re.sub(
            r"<thought\b[^>]*>.*?</thought>",
            "",
            cleaned,
            flags=re.DOTALL | re.IGNORECASE,
        )
        return cleaned

    @staticmethod
    def extract_tag(text: str, tag: str) -> str:
        """Extract tag content from text; supports opening tags with attributes.

        For non-reasoning tags (e.g. plan), strip <think>/<thought> first so a
        literal mention like `` `<plan>` `` inside reasoning does not capture
        everything through the real ``</plan>``.
        """
        if not text:
            return ""
        search_text = text
        if tag.lower() not in ("think", "thought"):
            search_text = ResponseParser.strip_reasoning_blocks(text)
        match = re.search(
            rf"<{re.escape(tag)}(?:\s+[^>]*)?>(.*?)</{re.escape(tag)}>",
            search_text,
            re.DOTALL | re.IGNORECASE,
        )
        return match.group(1).strip() if match else ""

    @staticmethod
    def parse_param_value(value_str: str) -> Any:
        """
        Parse a parameter value using ast.literal_eval() with intelligent fallback.

        Parsing strategy:
        1. Attempt ast.literal_eval() (safe eval supporting strings, numbers, lists, dicts, etc.)
        2. If that fails, fall back to returning the raw string (tolerates LLM forgetting quotes).
        3. For string results, restore Windows path escape sequences (e.g., bell -> \\a).

        Examples:
            "Fuzhou weather"  -> "Fuzhou weather" (str)
            Fuzhou weather     -> "Fuzhou weather" (str, fallback)
            10                 -> 10 (int)
            3.14               -> 3.14 (float)
            True               -> True (bool)
            [1, 2, 3]          -> [1, 2, 3] (list)
            ["a", "b"]        -> ["a", "b"] (list)
            "C:\\Users"        -> "C:\\Users" (str, escape restored)
        """
        value_str = value_str.strip()

        if not value_str:
            return ""

        # Attempt ast.literal_eval()
        try:
            result = ast.literal_eval(value_str)
        except (ValueError, SyntaxError):
            # Parsing failed, fall back to string
            result = value_str

        # Ellipsis (...) is not JSON-serializable — convert to string
        if result is ...:
            result = "..."

        # Post-process: restore accidental escape chars in string results
        # This fixes Windows paths where \a -> bell, \b -> backspace, etc.
        if isinstance(result, str):
            result = _restore_windows_path_escapes(result)

        return result

    @staticmethod
    def parse_xml_arguments(xml_content: str) -> dict[str, Any]:
        """
        Convert XML parameters inside a tool_call block to a dict.

        Example:
            <query>"Fuzhou weather"</query>
            <max_results>10</max_results>
        Returns:
            {"query": "Fuzhou weather", "max_results": 10}

        CDATA support:
            <content><![CDATA[<html>]]></content>
        Returns:
            {"content": "<html>"}
        """
        tc_log = get_tool_call_debug_logger()

        if not xml_content or not xml_content.strip():
            return {}

        result = {}
        # Qwen/Ling: <arg_key>query</arg_key><arg_value>...</arg_value>
        # must be paired before treating those tags as argument names.
        result.update(_parse_arg_key_value_pairs(xml_content))

        # Match all <key>value</key> tags (standard format)
        # CDATA support: <key><![CDATA[value]]></key>
        pattern = r"<([a-zA-Z_][a-zA-Z0-9_]*)\s*>(.*?)</\1\s*>"

        for match in re.finditer(pattern, xml_content, re.DOTALL):
            key = match.group(1)
            value_raw = match.group(2)

            # Skip the <func> tag (this is the tool name, not a parameter)
            # arg_key/arg_value are paired above, not standalone args.
            if key.lower() in {"func", "arg_key", "arg_value"}:
                continue

            # Handle CDATA
            cdata_match = re.match(r"^\s*<!\[CDATA\[(.*?)\]\]>\s*$", value_raw, re.DOTALL)
            value = cdata_match.group(1) if cdata_match else value_raw.strip()

            # Parse parameter value using literal_eval()
            parsed_value = ResponseParser.parse_param_value(value)
            result[key] = parsed_value

            tc_log.debug(
                "[parse_xml_arguments] Parsed %s: %r -> %r (type=%s)",
                key,
                value[:50] if len(value) > 50 else value,
                parsed_value,
                type(parsed_value).__name__,
            )

        # Lenient format: <parameter=key>value</parameter> (some LLMs output this)
        # Also handle unquoted key like <parameter=url>...</parameter>
        param_pattern = r'<parameter\s*=\s*(?:")?([a-zA-Z_][a-zA-Z0-9_]*)(?:")?\s*>(.*?)</parameter\s*>'
        for match in re.finditer(param_pattern, xml_content, re.DOTALL | re.IGNORECASE):
            key = match.group(1)
            value_raw = match.group(2)
            if key.lower() == "func":
                continue

            cdata_match = re.match(r"^\s*<!\[CDATA\[(.*?)\]\]>\s*$", value_raw, re.DOTALL)
            value = cdata_match.group(1) if cdata_match else value_raw.strip()

            parsed_value = ResponseParser.parse_param_value(value)
            if key not in result:
                result[key] = parsed_value
                tc_log.debug(
                    "[parse_xml_arguments] Lenient-param %s: %r -> %r (type=%s)",
                    key,
                    value[:50] if len(value) > 50 else value,
                    parsed_value,
                    type(parsed_value).__name__,
                )

        # ---- Cross-style fallback: <name>value</...DSML...parameter> ----
        # When the model opened a parameter with the halfwidth form <path> and
        # closed it with the DSML fullwidth form </｜｜DSML｜｜parameter>, the
        # standard <key>...</key> pattern (which uses a backreference) cannot
        # match. Recover by matching the OPEN tag against the halfwidth form
        # only and the CLOSE tag against either style.
        delim = ResponseParser._DSML_DELIM
        dsml_close_param = r"</" + delim + r"DSML" + delim + r"\s*parameter(?:\s*)>"
        # Match cross-style parameter tags (opening tag is plain <key>, closing
        # tag is </parameter> or DSML-style). Use a negative-lookbehind with
        # separate fixed-width alternatives to avoid the variable-width
        # lookbehind restriction in Python's re module.
        cross_pat = re.compile(
            r"(?<!<func)(?<!<function)(?<!<invoke)"
            r"<([a-zA-Z_][a-zA-Z0-9_]*)>(.*?)"
            r"(?:</parameter(?:\s*)>|" + dsml_close_param + r")",
            re.DOTALL,
        )
        for match in cross_pat.finditer(xml_content):
            key = match.group(1)
            value_raw = match.group(2)
            if key in {"func", "function", "invoke"}:
                continue
            if key in result:
                # Standard pattern already captured this argument; don't overwrite.
                continue
            cdata_match = re.match(r"^\s*<!\[CDATA\[(.*?)\]\]>\s*$", value_raw, re.DOTALL)
            value = cdata_match.group(1) if cdata_match else value_raw.strip()
            parsed_value = ResponseParser.parse_param_value(value)
            result[key] = parsed_value
            tc_log.debug("[parse_xml_arguments] Cross-style %s: %r -> %r", key, value, parsed_value)

        return result

    @staticmethod
    def parse_single_tool_call(xml_content: str) -> tuple[str, dict[str, Any]] | None:
        """Parse a single <tool_call> block's inner content into (tool_name, args).

        Supports multiple non-standard LLM output formats:
        1. Standard: <func>tool_name</func> <param>value</param>
        2. Lenient: <function=name> <parameter=key>value</parameter> (with closing tags)
        3. JSON-inside: {"name": "...", "arguments": {...}} inside <tool_call>
        4. Qwen/Ling: first-line name + <arg_key>/<arg_value> pairs
        """
        tc_log = get_tool_call_debug_logger()

        # GLM-5 patch: convert literal \\n to real newline characters.
        xml_content = xml_content.replace("\\n", "\n").replace("\\t", "\t").replace("\\r", "\r")

        tool_name = None
        strip_for_args = xml_content  # Content after stripping function wrapper

        # --- Strategy A: Standard <func>tool_name</func> ---
        func_match = re.search(r"<func\s*>(.*?)</func\s*>", xml_content, re.DOTALL | re.IGNORECASE)
        if func_match:
            tool_name = func_match.group(1).strip()

        # --- Strategy B: <function="name"> or <function=name> (lenient LLM format) ---
        if not tool_name:
            # Quoted: <function="namespace.tool_name">
            func_match = re.search(r'<function\s*=\s*"([^"]+)"\s*/?\s*>', xml_content, re.IGNORECASE)
            if not func_match:
                # Unquoted: <function=namespace.tool_name>  or  <function=name/>
                func_match = re.search(
                    r"<function\s*=\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*/?\s*>", xml_content, re.IGNORECASE
                )
            if func_match:
                tool_name = func_match.group(1).strip()
                # Strip the <function=...>...</function> wrapper to extract inner params
                strip_for_args = re.sub(r"<function\s*=[^>]*/?\s*>", "", xml_content, flags=re.IGNORECASE)
                strip_for_args = re.sub(r"</function\s*>", "", strip_for_args, flags=re.IGNORECASE)

        # --- Strategy C: JSON format inside tool_call {"name": "...", "arguments": {...}} ---
        if not tool_name:
            json_stripped = xml_content.strip()
            if json_stripped.startswith("{"):
                try:
                    data = json.loads(json_stripped)
                    if isinstance(data, dict) and "name" in data:
                        tool_name = data["name"]
                        raw_args = data.get("arguments", {})
                        if isinstance(raw_args, str):
                            try:
                                raw_args = json.loads(raw_args)
                            except json.JSONDecodeError:
                                raw_args = {}
                        if isinstance(raw_args, dict):
                            tc_log.info(
                                "[parse_single_tool_call] JSON-inside format: name=%r, args=%r", tool_name, raw_args
                            )
                            return tool_name, raw_args
                except json.JSONDecodeError:
                    pass

        # --- Strategy D: first-line tool name + <arg_key>/<arg_value> (Qwen/Ling) ---
        if not tool_name:
            bare = _first_line_tool_name(xml_content)
            if bare:
                tool_name = bare
                rest = xml_content.lstrip()
                split_at = rest.find("\n")
                glued = re.match(re.escape(bare) + r"(\s*<)", rest)
                if glued:
                    strip_for_args = rest[len(bare) :]
                elif split_at >= 0 and rest[:split_at].strip() == bare:
                    strip_for_args = rest[split_at + 1 :]
                elif rest.startswith(bare):
                    strip_for_args = rest[len(bare) :]
                else:
                    strip_for_args = xml_content

        if not tool_name:
            tc_log.error("[parse_single_tool_call] No func/function tag found. Content: %s", xml_content[:200])
            return None

        args_dict = ResponseParser.parse_xml_arguments(strip_for_args)
        tc_log.info("[parse_single_tool_call] name=%r, args=%r", tool_name, args_dict)
        return tool_name, args_dict

    @staticmethod
    def parse_tool_call(text: str) -> tuple[str, dict[str, Any]] | None:
        """
        Parse a single tool call (backward compatible).

        Returns: (tool_name, arguments_dict) or None
        """
        results = ResponseParser.parse_tool_calls(text)
        return results[0] if results else None

    @staticmethod
    def _parse_json_tool_calls(text: str) -> list[tuple[str, dict[str, Any]]]:
        """
        Try to parse native Function Calling JSON format from text.

        Supports:
        1. OpenAI array format: [{"name": "xxx", "arguments": {...}}, ...]
        2. Single object: {"name": "xxx", "arguments": {...}}
        3. OpenAI FC format: {"function": {"name": "xxx", "arguments": "..."}}
        4. Nesting depth: also matches {name:..., arguments:{...}} inline
        """
        tc_log = get_tool_call_debug_logger()

        # Strategy A: find JSON arrays containing tool_call-like objects
        # Match [...] containing objects with "name" and "arguments" keys
        array_match = re.search(r'\[\s*\{[^]]*"name"\s*:\s*"[^"]*"[^]]*"arguments"\s*:', text, re.DOTALL)
        if array_match:
            # Extract the full array starting from the match position
            brace_depth = 0
            start = array_match.start()
            for i in range(start, len(text)):
                if text[i] == "[":
                    brace_depth += 1
                elif text[i] == "]":
                    brace_depth -= 1
                    if brace_depth == 0:
                        json_str = text[start : i + 1]
                        break
            else:
                json_str = text[array_match.start() :]

            try:
                calls = json.loads(json_str)
                if isinstance(calls, list):
                    results = []
                    for call in calls:
                        if not isinstance(call, dict):
                            continue
                        name = call.get("name", "") or call.get("function", {}).get("name", "")
                        raw_args = call.get("arguments", {})
                        if isinstance(raw_args, str):
                            try:
                                raw_args = json.loads(raw_args)
                            except json.JSONDecodeError:
                                raw_args = {"_raw": raw_args}
                        if name and isinstance(raw_args, dict):
                            results.append((name, raw_args))
                    if results:
                        tc_log.info("[parse_tool_calls] JSON array format: %d tool call(s)", len(results))
                        return results
            except (json.JSONDecodeError, Exception):
                pass

        # Strategy B: find standalone {"name": "...", "arguments": {...}} objects
        obj_match = re.search(r'\{\s*"[nN]ame"\s*:\s*"[^"]*"\s*,\s*"[aA]rguments"\s*:', text, re.DOTALL)
        if obj_match:
            brace_depth = 0
            start = obj_match.start()
            for i in range(start, len(text)):
                if text[i] == "{":
                    brace_depth += 1
                elif text[i] == "}":
                    brace_depth -= 1
                    if brace_depth == 0:
                        json_str = text[start : i + 1]
                        break
            else:
                json_str = text[obj_match.start() :]

            try:
                call = json.loads(json_str)
                if isinstance(call, dict):
                    name = call.get("name", "") or call.get("function", {}).get("name", "")
                    raw_args = call.get("arguments", {})
                    if isinstance(raw_args, str):
                        try:
                            raw_args = json.loads(raw_args)
                        except json.JSONDecodeError:
                            raw_args = {"_raw": raw_args}
                    if name and isinstance(raw_args, dict):
                        tc_log.info("[parse_tool_calls] JSON object format: 1 tool call")
                        return [(name, raw_args)]
            except (json.JSONDecodeError, Exception):
                pass

        # Strategy C: OpenAI FC format {"function": {"name": "xxx"}, "arguments": {...}}
        # where "name" is nested inside "function", not at top level
        fc_match = re.search(
            r'\{\s*"[fF]unction"\s*:\s*\{[^}]*"[nN]ame"\s*:\s*"[^"]*"[^}]*\}\s*,?\s*"[aA]rguments"\s*:', text, re.DOTALL
        )
        if fc_match:
            brace_depth = 0
            start = fc_match.start()
            for i in range(start, len(text)):
                if text[i] == "{":
                    brace_depth += 1
                elif text[i] == "}":
                    brace_depth -= 1
                    if brace_depth == 0:
                        json_str = text[start : i + 1]
                        break
            else:
                json_str = text[fc_match.start() :]

            try:
                call = json.loads(json_str)
                if isinstance(call, dict):
                    func_obj = call.get("function", {})
                    name = func_obj.get("name", "") if isinstance(func_obj, dict) else ""
                    raw_args = call.get("arguments", {})
                    if isinstance(raw_args, str):
                        try:
                            raw_args = json.loads(raw_args)
                        except json.JSONDecodeError:
                            raw_args = {"_raw": raw_args}
                    if name and isinstance(raw_args, dict):
                        tc_log.info("[parse_tool_calls] OpenAI FC format: 1 tool call")
                        return [(name, raw_args)]
            except (json.JSONDecodeError, Exception):
                pass

        return []

    # Compiled patterns for Minimax/namespace format
    _TOOL_CALL_NS_PATTERN = re.compile(
        r"<([a-zA-Z_][a-zA-Z0-9_-]*):tool_call\b[^>]*>(.*?)</\1:tool_call>", re.DOTALL | re.IGNORECASE
    )
    _INVOKE_PATTERN = re.compile(r'<invoke\s+name="([^"]+)"\s*>(.*?)</invoke>', re.DOTALL | re.IGNORECASE)
    _PARAM_PATTERN = re.compile(
        r'<(?:param|parameter)\s+name="([^"]+)"\s*>(.*?)</(?:param|parameter)>', re.DOTALL | re.IGNORECASE
    )

    # DSML delimiter: supports both fullwidth ｜｜ (U+FF5C x2) and halfwidth || (U+007C x2)
    _DSML_DELIM = r"(?:｜｜|\|\|)"

    # Compiled patterns for DSML format
    # Each pattern matches EITHER <invoke name="...">...<something-invoke>
    # where the <something-invoke> closing tag can be halfwidth </invoke> OR
    # fullwidth </｜｜DSML｜｜invoke> (and likewise for the opening tag).
    # This way mixed halfwidth / fullwidth combinations are tolerated.
    _DSML_INVOKE_PATTERN = re.compile(
        rf'<(?:invoke|{_DSML_DELIM}DSML{_DSML_DELIM}\s*invoke)\s+name\s*=\s*"([^"]+)"\s*>'
        rf"(.*?)</(?:invoke|{_DSML_DELIM}DSML{_DSML_DELIM}\s*invoke)\s*>",
        re.DOTALL | re.IGNORECASE,
    )
    _DSML_PARAM_PATTERN = re.compile(
        rf'<(?:parameter|{_DSML_DELIM}DSML{_DSML_DELIM}\s*parameter)\s+name\s*=\s*"([^"]+)"'
        rf'(?:\s+\w+\s*=\s*"[^"]*")*\s*>(.*?)</(?:parameter|{_DSML_DELIM}DSML{_DSML_DELIM}\s*parameter)\s*>',
        re.DOTALL | re.IGNORECASE,
    )
    # Plain XML form for invoke / parameter (so an opening <parameter> paired
    # with a DSML-style closing tag is also caught, and vice versa).
    _PLAIN_INVOKE_PATTERN = _DSML_INVOKE_PATTERN
    _PLAIN_PARAM_PATTERN = _DSML_PARAM_PATTERN

    @staticmethod
    def _parse_dsml_tool_calls(text: str) -> list[tuple[str, dict[str, Any]]]:
        """
        Parse DSML (DeepSeek Markup Language) format:
          <｜｜DSML｜｜tool_calls>
            <｜｜DSML｜｜invoke name="filesystem.list_directory">
              <｜｜DSML｜｜parameter name="path" string="true">C:\\...</｜｜DSML｜｜parameter>
            </｜｜DSML｜｜invoke>
          </｜｜DSML｜｜tool_calls>

        Also supports:
          - standalone <｜｜DSML｜｜invoke> without outer wrapper
          - spaced tags: <｜｜DSML｜｜ calls> / <｜｜DSML｜｜ invoke>
          - short wrapper name ``calls`` / ``function_calls``
          - delimiter-only tags: <｜｜invoke>
          - ``parameter name="arguments"`` JSON blobs (unwrapped into tool args)

        Lenient mode (tolerance for malformed LLM output):
          - If no <invoke> was found, scan for orphaned <parameter> tokens anywhere
            in the text and treat them as a single synthetic tool call's arguments
            (attributed to a generic "llm_recovered" function). This recovers cases
            where the model forgot the <invoke> wrapper but still wrote the
            <parameter> tokens in the right shape.
          - Tool name and argument keys are normalized to snake_case to make
            downstream schema matching forgiving (e.g. startLine -> start_line).
        """
        tc_log = get_tool_call_debug_logger()
        text = _canonicalize_dsml(text)
        delim = ResponseParser._DSML_DELIM
        # Wrapper accepts BOTH <｜｜DSML｜｜tool_calls>...</｜｜DSML｜｜tool_calls>
        # and <tool_calls>...</tool_calls> as outer container. The opening tag
        # and closing tag are matched INDEPENDENTLY so halfwidth / fullwidth
        # mixing across the two is also tolerated. `(?:tool_)?calls` covers the
        # short wrapper name some models emit after canonicalize.
        dsml_tool_calls = rf"{delim}DSML{delim}\s*(?:tool_)?calls"
        # Plain (halfwidth) form: <tool_call>...</tool_call> (note: NO trailing 's')
        plain_open = r"<tool_call(?:\s*)>"
        plain_close = r"</tool_call(?:\s*)>"
        wrapper_pat = re.compile(
            rf"(?:{plain_open}|<{dsml_tool_calls}>)(.*?)(?:{plain_close}|</{dsml_tool_calls}>)",
            re.DOTALL | re.IGNORECASE,
        )

        # Collect all wrapped inner blocks (each <tool_calls>...</tool_calls> is one batch).
        inner_blocks = [m.group(1) for m in wrapper_pat.finditer(text)]
        combined = "\n".join(inner_blocks) if inner_blocks else text

        results: list[tuple[str, dict[str, Any]]] = []

        # ---- Pass 1: DSML-style invoke (halfwidth or fullwidth) ----
        for inv_match in ResponseParser._DSML_INVOKE_PATTERN.finditer(combined):
            name = inv_match.group(1).strip()
            param_body = inv_match.group(2)
            args: dict[str, Any] = {}
            for pm in ResponseParser._DSML_PARAM_PATTERN.finditer(param_body):
                key = pm.group(1)
                value_raw = pm.group(2).strip()
                norm_key = _normalize_arg_key(key)
                args[norm_key] = ResponseParser.parse_param_value(value_raw)
            args = _expand_dsml_arguments(args)
            xml_args = ResponseParser.parse_xml_arguments(param_body)
            for k, v in xml_args.items():
                args.setdefault(k, v)
            if name:
                resolved = _resolve_parsed_tool_name(name, param_body, args)
                if not resolved:
                    continue
                args.pop("func", None)
                args.pop("function", None)
                tc_log.info("[_parse_dsml] DSML-invoke name=%r -> %r, args=%r", name, resolved, args)
                results.append((resolved, args))

        # ---- Pass 2: plain XML invoke (paired with either closing style) ----
        if not results:
            for inv_match in ResponseParser._PLAIN_INVOKE_PATTERN.finditer(combined):
                name = inv_match.group(1).strip()
                param_body = inv_match.group(2)
                args = {}
                for pm in ResponseParser._PLAIN_PARAM_PATTERN.finditer(param_body):
                    key = pm.group(1)
                    value_raw = pm.group(2).strip()
                    norm_key = _normalize_arg_key(key)
                    args[norm_key] = ResponseParser.parse_param_value(value_raw)
                args = _expand_dsml_arguments(args)
                xml_args = ResponseParser.parse_xml_arguments(param_body)
                for k, v in xml_args.items():
                    args.setdefault(k, v)
                if name:
                    resolved = _resolve_parsed_tool_name(name, param_body, args)
                    if not resolved:
                        continue
                    args.pop("func", None)
                    args.pop("function", None)
                    tc_log.info("[_parse_dsml] plain-invoke name=%r -> %r, args=%r", name, resolved, args)
                    results.append((resolved, args))

        # ---- Pass 3: recover orphan <parameter> tokens (no <invoke> wrapper) ----
        if not results:
            # Both DSML and plain halfwidth variants.
            orphan_pat = re.compile(
                rf'<(?:parameter|{delim}DSML{delim}\s*parameter)\s+name="([^"]+)"'
                rf'(?:\s+\w+\s*=\s*"[^"]*")*\s*>([^<]*)',
                re.DOTALL | re.IGNORECASE,
            )
            recovered_args: dict[str, Any] = {}
            for m in orphan_pat.finditer(text):
                key = m.group(1)
                value_raw = m.group(2).strip().rstrip(",").strip()
                if not value_raw:
                    continue
                norm_key = _normalize_arg_key(key)
                recovered_args[norm_key] = ResponseParser.parse_param_value(value_raw)
            recovered_args = _expand_dsml_arguments(recovered_args)
            if recovered_args:
                resolved = _resolve_parsed_tool_name("llm_recovered", text, recovered_args)
                if not resolved:
                    if recovered_args.get("path"):
                        resolved = "filesystem.read_file"
                    elif recovered_args.get("command") or recovered_args.get("cmd"):
                        resolved = "system.run_session_job"
                if resolved:
                    drop = {"func", "function"}
                    args = {k: v for k, v in recovered_args.items() if k not in drop}
                    tc_log.warning(
                        "[_parse_dsml] Lenient fallback: recovered %d orphan tokens as %s",
                        len(args),
                        resolved,
                    )
                    results.append((resolved, args))

        # ---- Pass 4: <func>name</func> + cross-style <param>... (legacy XML
        # wrapper content embedded inside a DSML or <tool_call> container) ----
        # Parse EACH container block separately: parsing the combined text would
        # merge parallel <tool_call> blocks into one call (args from the last
        # block win, e.g. parallel read+write collapsing into a single call).
        if not results:
            for block in inner_blocks or [text]:
                func_match = re.search(
                    r'<func\s*>([^<]+)</func\s*>|<function\s*=\s*"?([a-zA-Z_][\w.]*)"?\s*/?>',
                    block,
                    re.IGNORECASE,
                )
                if not func_match:
                    continue
                name = (func_match.group(1) or func_match.group(2) or "").strip()
                if not name:
                    continue
                args = ResponseParser.parse_xml_arguments(block)
                if args:
                    norm_name = _normalize_tool_name(name)
                    tc_log.info(
                        "[_parse_dsml] legacy-func name=%r -> %r, args=%r",
                        name,
                        norm_name,
                        args,
                    )
                    results.append((norm_name, args))

        return results

    @staticmethod
    def _parse_minimax_tool_calls(text: str) -> list[tuple[str, dict[str, Any]]]:
        """
        Parse Minimax/namespace format:
          <minimax:tool_call>
            <invoke name="filesystem.read_file">
              <parameter name="path">/tmp/test.txt</parameter>
            </invoke>
          </minimax:tool_call>

        Also supports standalone <invoke> (no outer namespace wrapper).
        """
        tc_log = get_tool_call_debug_logger()

        # Strategy A: <namespace:tool_call><invoke name="xxx">...</invoke></namespace:tool_call>
        ns_match = ResponseParser._TOOL_CALL_NS_PATTERN.search(text)
        if ns_match:
            inner = ns_match.group(2)
            inv_match = ResponseParser._INVOKE_PATTERN.search(inner)
            if inv_match:
                name = inv_match.group(1).strip()
                param_body = inv_match.group(2)
                args = {}
                for pm in ResponseParser._PARAM_PATTERN.finditer(param_body):
                    key = pm.group(1)
                    value_raw = pm.group(2).strip()
                    args[key] = ResponseParser.parse_param_value(value_raw)
                resolved = _resolve_parsed_tool_name(name, param_body, args)
                if not resolved:
                    return []
                args.pop("func", None)
                args.pop("function", None)
                tc_log.info("[_parse_minimax] Namespace format: name=%r -> %r, args=%r", name, resolved, args)
                return [(resolved, args)]

        # Strategy B: standalone <invoke name="xxx">...</invoke>
        inv_match = ResponseParser._INVOKE_PATTERN.search(text)
        if inv_match:
            name = inv_match.group(1).strip()
            param_body = inv_match.group(2)
            args = {}
            for pm in ResponseParser._PARAM_PATTERN.finditer(param_body):
                key = pm.group(1)
                value_raw = pm.group(2).strip()
                args[key] = ResponseParser.parse_param_value(value_raw)
            resolved = _resolve_parsed_tool_name(name, param_body, args)
            if not resolved:
                return []
            args.pop("func", None)
            args.pop("function", None)
            tc_log.info("[_parse_minimax] Standalone invoke: name=%r -> %r, args=%r", name, resolved, args)
            return [(resolved, args)]

        return []

    @staticmethod
    def _parse_dots_function_calls(text: str) -> list[tuple[str, dict[str, Any]]]:
        """Parse Dots Studio ``<dots_function_call>`` bodies (OpenRouter free models).

        Accepts the canonical ``<invoke name="...">`` form and the truncated
        ``invoke="...">`` / ``<invoke="...">`` variants that still close with
        ``</invoke>``. Must run *before* DSML orphan-parameter recovery, which
        would otherwise mis-attribute leftover ``<parameter name="path">`` as
        ``filesystem.read_file``.
        """
        if not text or "dots_function_call" not in text.lower():
            return []
        wrapper = re.compile(
            r"<dots_function_call\b[^>]*>(.*?)</dots_function_call\s*>",
            re.DOTALL | re.IGNORECASE,
        )
        bodies = [m.group(1) for m in wrapper.finditer(text)]
        if not bodies:
            bodies = [text]
        invoke_pat = re.compile(
            r'(?:<invoke\s+name\s*=\s*"([^"]+)"\s*>'
            r'|<invoke\s*=\s*"([^"]+)"\s*>'
            r'|invoke\s*=\s*"([^"]+)"\s*>)'
            r"(.*?)</invoke>",
            re.DOTALL | re.IGNORECASE,
        )
        results: list[tuple[str, dict[str, Any]]] = []
        tc_log = get_tool_call_debug_logger()
        for body in bodies:
            for inv in invoke_pat.finditer(body):
                name = (inv.group(1) or inv.group(2) or inv.group(3) or "").strip()
                param_body = inv.group(4) or ""
                args: dict[str, Any] = {}
                for pm in ResponseParser._PARAM_PATTERN.finditer(param_body):
                    args[pm.group(1)] = ResponseParser.parse_param_value(pm.group(2).strip())
                xml_args = ResponseParser.parse_xml_arguments(param_body)
                for k, v in xml_args.items():
                    args.setdefault(k, v)
                resolved = _resolve_parsed_tool_name(name, param_body, args)
                if not resolved:
                    continue
                args.pop("func", None)
                args.pop("function", None)
                tc_log.info("[_parse_dots] name=%r -> %r args=%r", name, resolved, args)
                results.append((resolved, args))
        return results

    @staticmethod
    def parse_tool_calls(text: str) -> list[tuple[str, dict[str, Any]]]:
        """
        Parse ALL tool_call blocks from text (supports parallel tool calls).

        Tries in order:
        1. Dots ``<dots_function_call>`` (OpenRouter / Dots Studio template)
        2. DSML format: <｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name="xxx">...</｜｜DSML｜｜invoke></｜｜DSML｜｜tool_calls>
        3. Minimax/namespace format: <namespace:tool_call><invoke name="xxx">...</invoke></namespace:tool_call>
        4. XML format: <tool_call>...</tool_call>
        5. Attribute-style: <tool_call name="xxx">...</tool_call>
        6. Native FC JSON format embedded in text

        Returns: List of (tool_name, arguments_dict) tuples. Empty list if none found.
        """
        tc_log = get_tool_call_debug_logger()

        has_dots = "dots_function_call" in (text or "").lower()
        if has_dots:
            dots_results = ResponseParser._parse_dots_function_calls(text)
            if dots_results:
                tc_log.info("[parse_tool_calls] Found %d dots_function_call(s)", len(dots_results))
                return ResponseParser._normalize_results(dots_results)

        # Strategy 1: DSML format (skip when a dots wrapper is present so
        # orphan <parameter> recovery cannot steal the call as read_file).
        if not has_dots:
            dsml_results = ResponseParser._parse_dsml_tool_calls(text)
            if dsml_results:
                tc_log.info("[parse_tool_calls] Found %d DSML tool call(s)", len(dsml_results))
                return ResponseParser._normalize_results(dsml_results)

        # Strategy 2: Minimax/namespace format
        minimax_results = ResponseParser._parse_minimax_tool_calls(text)
        if minimax_results:
            return ResponseParser._normalize_results(minimax_results)

        # Strategy 3: XML format <tool_call>...</tool_call> (closing tag may be
        # halfwidth </tool_call> OR fullwidth </｜｜DSML｜｜tool_calls> when the
        # model drifts between the two styles within a single response).
        delim = ResponseParser._DSML_DELIM
        dsml_tool_calls = rf"{delim}DSML{delim}\s*(?:tool_)?calls"
        xml_open_close_pat = re.compile(
            rf"<tool_call(?:\s*)>(.*?)</(?:tool_call|{dsml_tool_calls})(?:\s*)>",
            re.DOTALL | re.IGNORECASE,
        )
        matches = list(xml_open_close_pat.finditer(text))
        if matches:
            results = []
            for m in matches:
                parsed = ResponseParser.parse_single_tool_call(m.group(1).strip())
                if parsed:
                    results.append(parsed)
            if results:
                tc_log.info("[parse_tool_calls] Found %d XML tool call(s)", len(results))
                return ResponseParser._normalize_results(results)

        # Strategy 4: attribute-style <tool_call name="xxx"><arguments>JSON</arguments></tool_call>
        attr_match = re.search(r'<tool_call\s+name\s*=\s*"([^"]+)"\s*>', text, re.IGNORECASE)
        if attr_match:
            results = ResponseParser._parse_attr_tool_calls(text)
            if results:
                tc_log.info("[parse_tool_calls] Found %d attribute-style tool call(s)", len(results))
                return ResponseParser._normalize_results(results)

        # Strategy 5: Native FC JSON format in text
        json_results = ResponseParser._parse_json_tool_calls(text)
        if json_results:
            return ResponseParser._normalize_results(json_results)

        # Strategy 6: truncated / unclosed <tool_call> (cheap models hang mid-tag)
        unclosed = ResponseParser.parse_unclosed_tool_calls(text)
        with_args = [p for p in unclosed if p[1]]
        if with_args:
            tc_log.info("[parse_tool_calls] Found %d unclosed tool call(s)", len(with_args))
            return ResponseParser._normalize_results(with_args)

        tc_log.debug("[parse_tool_calls] No tool call found in response (len=%d)", len(text))
        return []

    @staticmethod
    def parse_partial_tool_preview(
        tag: str,
        buf: str,
        attrs: dict[str, str] | None = None,
    ) -> tuple[str, dict[str, Any]] | None:
        """Best-effort name+args from an in-flight (possibly unclosed) tool tag.

        Used for live Agent Web ``tool_call_delta`` while ``</tool_call>`` has
        not arrived yet. Returns None until a tool name is recognizable.
        """
        del tag  # tag is the commit-buffer kind; name lives in attrs / inner XML
        attrs = attrs or {}
        name = (attrs.get("name") or "").strip()
        blob = buf or ""

        if not name or name.lower() in _INVOKE_PLACEHOLDERS:
            m = re.search(
                r"<func\s*>([^<]{1,200}?)(?:</func\s*>|$)",
                blob,
                re.IGNORECASE | re.DOTALL,
            )
            if m:
                cand = m.group(1).strip()
                if cand and "\n" not in cand and len(cand) < 120:
                    name = cand
        if not name or name.lower() in _INVOKE_PLACEHOLDERS:
            m = re.search(
                r'<(?:invoke|[^\s<>]*invoke)\b[^>]*\bname\s*=\s*"([^"]+)"',
                blob,
                re.IGNORECASE,
            )
            if m:
                name = m.group(1).strip()
        if not name or name.lower() in _INVOKE_PLACEHOLDERS:
            m = re.search(r'<function\s*=\s*"?([a-zA-Z0-9_.]+)', blob, re.IGNORECASE)
            if m:
                name = m.group(1).strip()
        if not name or name.lower() in _INVOKE_PLACEHOLDERS:
            name = _first_line_tool_name(blob)
        func_closed = bool(re.search(r"</func\s*>", blob, re.IGNORECASE))
        attr_complete = bool(attrs.get("name")) and name.lower() not in _INVOKE_PLACEHOLDERS
        closed = func_closed or attr_complete
        if not name or not _is_preview_tool_name_ready(name, closed=closed):
            return None

        args: dict[str, Any] = {}
        if re.search(r"</func\s*>", blob, re.IGNORECASE) or "<arguments" in blob.lower() or "<arg_key" in blob.lower():
            parsed = ResponseParser.parse_single_tool_call(blob)
            if parsed:
                name = parsed[0] or name
                args = parsed[1] or {}
        if not args:
            args = ResponseParser.parse_xml_arguments(blob)
        if not args:
            m = re.search(r"<([a-zA-Z_][a-zA-Z0-9_]*)\s*>([^<]*)$", blob)
            if m and m.group(1).lower() not in ("func", "function", "tool_call", "invoke"):
                val = m.group(2).strip().strip('"').strip("'")
                if val:
                    args = {m.group(1): val}
        if not args:
            m = re.search(r"<arguments\s*>([\s\S]*)$", blob, re.IGNORECASE)
            if m:
                raw = re.sub(r"</arguments\s*>", "", m.group(1), flags=re.IGNORECASE).strip()
                obj = _parse_jsonish_object(raw) if raw.endswith("}") else None
                if obj:
                    args = obj
                elif raw:
                    qm = re.search(r'"(query|q|url|path|command)"\s*:\s*"([^"]*)', raw)
                    if qm:
                        args = {qm.group(1): qm.group(2)}
        return name, args

    @staticmethod
    def parse_unclosed_tool_calls(text: str) -> list[tuple[str, dict[str, Any]]]:
        """Parse a truncated ``<tool_call>`` / invoke block (no closing tag)."""
        if not text or not isinstance(text, str):
            return []
        stripped = re.sub(r"<thought\b[^>]*>.*?</thought>", " ", text, flags=re.IGNORECASE | re.DOTALL)
        stripped = re.sub(r"<think\b[^>]*>.*?</think>", " ", stripped, flags=re.IGNORECASE | re.DOTALL)
        if not re.search(r"<tool_call\b|invoke\b", stripped, re.IGNORECASE):
            return []

        results: list[tuple[str, dict[str, Any]]] = []

        attr = None
        for m in re.finditer(r'<tool_call\s+name\s*=\s*"([^"]+)"\s*>', stripped, re.IGNORECASE):
            attr = m
        if attr and "</tool_call>" not in stripped[attr.start() :].lower():
            inner = re.split(r"</tool_call>", stripped[attr.end() :], flags=re.IGNORECASE)[0]
            parsed = ResponseParser.parse_partial_tool_preview("tool_call", inner, {"name": attr.group(1)})
            if parsed:
                results.append(parsed)

        if not results:
            open_m = None
            for m in re.finditer(r"<tool_call\b[^>]*>", stripped, re.IGNORECASE):
                open_m = m
            if open_m and "</tool_call>" not in stripped[open_m.start() :].lower():
                parsed = ResponseParser.parse_partial_tool_preview("tool_call", stripped[open_m.end() :], {})
                if parsed and parsed[1]:
                    results.append(parsed)

        if not results:
            inv = None
            for m in re.finditer(r'<invoke\b[^>]*\bname\s*=\s*"([^"]+)"\s*>', stripped, re.IGNORECASE):
                inv = m
            if inv:
                parsed = ResponseParser.parse_partial_tool_preview(
                    "invoke", stripped[inv.end() :], {"name": inv.group(1)}
                )
                if parsed:
                    results.append(parsed)

        return results

    @staticmethod
    def _normalize_results(results: list[tuple[str, dict[str, Any]]]) -> list[tuple[str, dict[str, Any]]]:
        """
        Apply consistent normalization to all parsed tool call results.

        Operations:
          1. Normalize tool name to canonical snake_case.
          2. Normalize each argument key to canonical snake_case.
          3. De-duplicate argument keys: if a result has both 'startLine' and
             'start_line' (which can happen if one came from camelCase and one
             from snake_case in the same text), keep the first one encountered.
          4. Drop empty / None-valued args to avoid spurious downstream errors.

        This is the single chokepoint that gives ALL parser strategies
        (DSML, Minimax, XML, attribute, JSON-FC) the same tolerance.
        """
        normalized: list[tuple[str, dict[str, Any]]] = []
        for name, args in results:
            norm_name = _normalize_tool_name(name) if name else name
            norm_args: dict[str, Any] = {}
            for k, v in args.items():
                if v is None:
                    continue
                nk = _normalize_arg_key(k)
                if nk in norm_args:
                    # First-occurrence wins for duplicate keys across naming styles
                    continue
                norm_args[nk] = v
            normalized.append((norm_name, norm_args))
        return normalized

    @staticmethod
    def _parse_attr_tool_calls(text: str) -> list[tuple[str, dict[str, Any]]]:
        """Parse <tool_call name="xxx">...content...</tool_call> attribute-style blocks."""
        results = []
        # Match name attribute + optional <arguments>JSON</arguments>
        pattern = r'<tool_call\s+name\s*=\s*"([^"]+)"\s*>(.*?)</tool_call>'
        for match in re.finditer(pattern, text, re.DOTALL | re.IGNORECASE):
            name = match.group(1).strip()
            content = match.group(2).strip()
            if not name:
                continue
            # Try <arguments>JSON</arguments> child tag first
            args_match = re.search(r"<arguments>(.*?)</arguments>", content, re.DOTALL)
            if args_match:
                args_raw = args_match.group(1).strip()
                try:
                    args_dict = json.loads(args_raw)
                    if isinstance(args_dict, dict):
                        results.append((name, args_dict))
                        continue
                except (json.JSONDecodeError, Exception):
                    pass
            # Fallback: parse child tags as XML parameters
            args_dict = ResponseParser.parse_xml_arguments(content)
            if args_dict:
                results.append((name, args_dict))
            else:
                # Last resort: try parsing whole content as JSON
                try:
                    args_dict = json.loads(content)
                    if isinstance(args_dict, dict):
                        results.append((name, args_dict))
                except (json.JSONDecodeError, Exception):
                    results.append((name, {}))
        return results
