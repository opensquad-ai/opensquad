# -*- coding: utf-8 -*-
import json
import re
import ast
from typing import Dict, Any, List, Optional, Tuple, Union
from .log_setup import get_tool_call_debug_logger

# ---------------------------------------------------------------------------
# Parameter / tool-name normalization helpers (DSML tolerance)
# ---------------------------------------------------------------------------
# LLMs frequently switch between naming conventions for the same conceptual
# argument, e.g. "startLine" vs "start_line" vs "start-line". Downstream tool
# schemas usually expect one canonical form (snake_case). These helpers make
# the parser forgiving: arguments and tool names are normalized so that
# downstream matching against a tool schema succeeds more often.
import re as _re_norm
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
        - else: just lower-case + collapse separators.
    """
    if not name:
        return name
    n = name.strip()
    if "." in n:
        parts = n.split(".")
        parts = [_normalize_key(p) for p in parts if p]
        return ".".join(parts)
    return _normalize_key(n)


# Mapping of Python escape sequences that must be restored to literal backslash + char
# after ast.literal_eval() or string fallback. These are common Windows path characters
# that LLMs output as single-backslash paths (e.g., "C:\Users\...") which Python
# interprets as escape sequences.
_ESCAPE_RESTORE_MAP = {
    '\a': r'\a',   # bell (0x07)  -> \a  (e.g., C:\app)
    '\b': r'\b',   # backspace    -> \b  (e.g., C:\bin)
    '\f': r'\f',   # form feed    -> \f
    '\n': r'\n',   # newline      -> \n  (only for paths, not intentional newlines)
    '\r': r'\r',   # carriage ret -> \r
    '\t': r'\t',   # tab          -> \t
    '\v': r'\v',   # vertical tab -> \v
    # Note: '\0' (null) is rare in paths, skip
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
    return result


class ResponseParser:
    """Parses XML-structured responses from AI models."""
    
    @staticmethod
    def extract_tag(text: str, tag: str) -> str:
        """Extract tag content from text; supports opening tags with attributes."""
        match = re.search(rf"<{tag}(?:\s+[^>]*)?>(.*?)</{tag}>", text, re.DOTALL)
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
    def parse_xml_arguments(xml_content: str) -> Dict[str, Any]:
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
        # Match all <key>value</key> tags (standard format)
        # CDATA support: <key><![CDATA[value]]></key>
        pattern = r'<([a-zA-Z_][a-zA-Z0-9_]*)\s*>(.*?)</\1\s*>'
        
        for match in re.finditer(pattern, xml_content, re.DOTALL):
            key = match.group(1)
            value_raw = match.group(2)
            
            # Skip the <func> tag (this is the tool name, not a parameter)
            if key == 'func':
                continue
            
            # Handle CDATA
            cdata_match = re.match(r'^\s*<!\[CDATA\[(.*?)\]\]>\s*$', value_raw, re.DOTALL)
            if cdata_match:
                value = cdata_match.group(1)
            else:
                value = value_raw.strip()
            
            # Parse parameter value using literal_eval()
            parsed_value = ResponseParser.parse_param_value(value)
            result[key] = parsed_value
            
            tc_log.debug("[parse_xml_arguments] Parsed %s: %r -> %r (type=%s)", 
                        key, value[:50] if len(value) > 50 else value, 
                        parsed_value, type(parsed_value).__name__)

        # Lenient format: <parameter=key>value</parameter> (some LLMs output this)
        # Also handle unquoted key like <parameter=url>...</parameter>
        param_pattern = r'<parameter\s*=\s*(?:")?([a-zA-Z_][a-zA-Z0-9_]*)(?:")?\s*>(.*?)</parameter\s*>'
        for match in re.finditer(param_pattern, xml_content, re.DOTALL | re.IGNORECASE):
            key = match.group(1)
            value_raw = match.group(2)
            if key.lower() == 'func':
                continue
            
            cdata_match = re.match(r'^\s*<!\[CDATA\[(.*?)\]\]>\s*$', value_raw, re.DOTALL)
            if cdata_match:
                value = cdata_match.group(1)
            else:
                value = value_raw.strip()
            
            parsed_value = ResponseParser.parse_param_value(value)
            if key not in result:
                result[key] = parsed_value
                tc_log.debug("[parse_xml_arguments] Lenient-param %s: %r -> %r (type=%s)",
                            key, value[:50] if len(value) > 50 else value,
                            parsed_value, type(parsed_value).__name__)

        # ---- Cross-style fallback: <name>value</...DSML...parameter> ----
        # When the model opened a parameter with the halfwidth form <path> and
        # closed it with the DSML fullwidth form </｜｜DSML｜｜parameter>, the
        # standard <key>...</key> pattern (which uses a backreference) cannot
        # match. Recover by matching the OPEN tag against the halfwidth form
        # only and the CLOSE tag against either style.
        delim = ResponseParser._DSML_DELIM
        dsml_close_param = r"</" + delim + r"DSML" + delim + r"parameter(?:\s*)>"
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
            cdata_match = re.match(r'^\s*<!\[CDATA\[(.*?)\]\]>\s*$', value_raw, re.DOTALL)
            if cdata_match:
                value = cdata_match.group(1)
            else:
                value = value_raw.strip()
            parsed_value = ResponseParser.parse_param_value(value)
            result[key] = parsed_value
            tc_log.debug("[parse_xml_arguments] Cross-style %s: %r -> %r", key, value, parsed_value)

        return result

    @staticmethod
    def parse_single_tool_call(xml_content: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Parse a single <tool_call> block's inner content into (tool_name, args).

        Supports multiple non-standard LLM output formats:
        1. Standard: <func>tool_name</func> <param>value</param>
        2. Lenient: <function=name> <parameter=key>value</parameter> (with closing tags)
        3. JSON-inside: {"name": "...", "arguments": {...}} inside <tool_call>
        """
        tc_log = get_tool_call_debug_logger()

        # GLM-5 patch: convert literal \\n to real newline characters.
        xml_content = xml_content.replace('\\n', '\n').replace('\\t', '\t').replace('\\r', '\r')

        tool_name = None
        strip_for_args = xml_content  # Content after stripping function wrapper

        # --- Strategy A: Standard <func>tool_name</func> ---
        func_match = re.search(r'<func\s*>(.*?)</func\s*>', xml_content, re.DOTALL | re.IGNORECASE)
        if func_match:
            tool_name = func_match.group(1).strip()

        # --- Strategy B: <function="name"> or <function=name> (lenient LLM format) ---
        if not tool_name:
            # Quoted: <function="namespace.tool_name">
            func_match = re.search(r'<function\s*=\s*"([^"]+)"\s*/?\s*>', xml_content, re.IGNORECASE)
            if not func_match:
                # Unquoted: <function=namespace.tool_name>  or  <function=name/>
                func_match = re.search(r'<function\s*=\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*/?\s*>', xml_content, re.IGNORECASE)
            if func_match:
                tool_name = func_match.group(1).strip()
                # Strip the <function=...>...</function> wrapper to extract inner params
                strip_for_args = re.sub(r'<function\s*=[^>]*/?\s*>', '', xml_content, flags=re.IGNORECASE)
                strip_for_args = re.sub(r'</function\s*>', '', strip_for_args, flags=re.IGNORECASE)

        # --- Strategy C: JSON format inside tool_call {"name": "...", "arguments": {...}} ---
        if not tool_name:
            json_stripped = xml_content.strip()
            if json_stripped.startswith('{'):
                try:
                    data = json.loads(json_stripped)
                    if isinstance(data, dict) and 'name' in data:
                        tool_name = data['name']
                        raw_args = data.get('arguments', {})
                        if isinstance(raw_args, str):
                            try:
                                raw_args = json.loads(raw_args)
                            except json.JSONDecodeError:
                                raw_args = {}
                        if isinstance(raw_args, dict):
                            tc_log.info("[parse_single_tool_call] JSON-inside format: name=%r, args=%r", tool_name, raw_args)
                            return tool_name, raw_args
                except json.JSONDecodeError:
                    pass

        if not tool_name:
            tc_log.error("[parse_single_tool_call] No func/function tag found. Content: %s", xml_content[:200])
            return None

        args_dict = ResponseParser.parse_xml_arguments(strip_for_args)
        tc_log.info("[parse_single_tool_call] name=%r, args=%r", tool_name, args_dict)
        return tool_name, args_dict

    @staticmethod
    def parse_tool_call(text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        Parse a single tool call (backward compatible).

        Returns: (tool_name, arguments_dict) or None
        """
        results = ResponseParser.parse_tool_calls(text)
        return results[0] if results else None

    @staticmethod
    def _parse_json_tool_calls(text: str) -> List[Tuple[str, Dict[str, Any]]]:
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
        array_match = re.search(
            r'\[\s*\{[^]]*"name"\s*:\s*"[^"]*"[^]]*"arguments"\s*:',
            text, re.DOTALL
        )
        if array_match:
            # Extract the full array starting from the match position
            brace_depth = 0
            start = array_match.start()
            for i in range(start, len(text)):
                if text[i] == '[':
                    brace_depth += 1
                elif text[i] == ']':
                    brace_depth -= 1
                    if brace_depth == 0:
                        json_str = text[start:i + 1]
                        break
            else:
                json_str = text[array_match.start():]

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
        obj_match = re.search(
            r'\{\s*"[nN]ame"\s*:\s*"[^"]*"\s*,\s*"[aA]rguments"\s*:',
            text, re.DOTALL
        )
        if obj_match:
            brace_depth = 0
            start = obj_match.start()
            for i in range(start, len(text)):
                if text[i] == '{':
                    brace_depth += 1
                elif text[i] == '}':
                    brace_depth -= 1
                    if brace_depth == 0:
                        json_str = text[start:i + 1]
                        break
            else:
                json_str = text[obj_match.start():]

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
            r'\{\s*"[fF]unction"\s*:\s*\{[^}]*"[nN]ame"\s*:\s*"[^"]*"[^}]*\}\s*,?\s*"[aA]rguments"\s*:',
            text, re.DOTALL
        )
        if fc_match:
            brace_depth = 0
            start = fc_match.start()
            for i in range(start, len(text)):
                if text[i] == '{':
                    brace_depth += 1
                elif text[i] == '}':
                    brace_depth -= 1
                    if brace_depth == 0:
                        json_str = text[start:i + 1]
                        break
            else:
                json_str = text[fc_match.start():]

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
        r'<([a-zA-Z_][a-zA-Z0-9_-]*):tool_call\b[^>]*>(.*?)</\1:tool_call>',
        re.DOTALL | re.IGNORECASE
    )
    _INVOKE_PATTERN = re.compile(
        r'<invoke\s+name="([^"]+)"\s*>(.*?)</invoke>',
        re.DOTALL | re.IGNORECASE
    )
    _PARAM_PATTERN = re.compile(
        r'<(?:param|parameter)\s+name="([^"]+)"\s*>(.*?)</(?:param|parameter)>',
        re.DOTALL | re.IGNORECASE
    )

    # DSML delimiter: supports both fullwidth ｜｜ (U+FF5C x2) and halfwidth || (U+007C x2)
    _DSML_DELIM = r'(?:｜｜|\|\|)'

    # Compiled patterns for DSML format
    # Each pattern matches EITHER <invoke name="...">...<something-invoke>
    # where the <something-invoke> closing tag can be halfwidth </invoke> OR
    # fullwidth </｜｜DSML｜｜invoke> (and likewise for the opening tag).
    # This way mixed halfwidth / fullwidth combinations are tolerated.
    _DSML_INVOKE_PATTERN = re.compile(
        r'<(?:invoke|{d}DSML{d}invoke)\s+name="([^"]+)"\s*>(.*?)</(?:invoke|{d}DSML{d}invoke)>'.format(
            d=_DSML_DELIM
        ),
        re.DOTALL | re.IGNORECASE
    )
    _DSML_PARAM_PATTERN = re.compile(
        r'<(?:parameter|{d}DSML{d}parameter)\s+name="([^"]+)"(?:\s+\w+="[^"]*")*\s*>(.*?)</(?:parameter|{d}DSML{d}parameter)>'.format(
            d=_DSML_DELIM
        ),
        re.DOTALL | re.IGNORECASE
    )
    # Plain XML form for invoke / parameter (so an opening <parameter> paired
    # with a DSML-style closing tag is also caught, and vice versa).
    _PLAIN_INVOKE_PATTERN = _DSML_INVOKE_PATTERN
    _PLAIN_PARAM_PATTERN = _DSML_PARAM_PATTERN

    @staticmethod
    def _parse_dsml_tool_calls(text: str) -> List[Tuple[str, Dict[str, Any]]]:
        """
        Parse DSML (DeepSeek Markup Language) format:
          <｜｜DSML｜｜tool_calls>
            <｜｜DSML｜｜invoke name="filesystem.list_directory">
              <｜｜DSML｜｜parameter name="path" string="true">C:\\...</｜｜DSML｜｜parameter>
            </｜｜DSML｜｜invoke>
          </｜｜DSML｜｜tool_calls>

        Also supports standalone <｜｜DSML｜｜invoke> without outer wrapper.

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
        delim = ResponseParser._DSML_DELIM
        # Wrapper accepts BOTH <｜｜DSML｜｜tool_calls>...</｜｜DSML｜｜tool_calls>
        # and <tool_calls>...</tool_calls> as outer container. The opening tag
        # and closing tag are matched INDEPENDENTLY so halfwidth / fullwidth
        # mixing across the two is also tolerated.
        dsml_tool_calls = "{}DSML{}tool_calls".format(delim, delim)
        # Plain (halfwidth) form: <tool_call>...</tool_call> (note: NO trailing 's')
        plain_open = r"<tool_call(?:\s*)>"
        plain_close = r"</tool_call(?:\s*)>"
        wrapper_pat = re.compile(
            r"(?:{plain_open}|<{dsml}>)(.*?)(?:{plain_close}|</{dsml}>)".format(
                plain_open=plain_open,
                plain_close=plain_close,
                dsml=dsml_tool_calls,
            ),
            re.DOTALL | re.IGNORECASE
        )

        # Collect all wrapped inner blocks (each <tool_calls>...</tool_calls> is one batch).
        inner_blocks = [m.group(1) for m in wrapper_pat.finditer(text)]
        if inner_blocks:
            combined = "\n".join(inner_blocks)
        else:
            combined = text

        results: List[Tuple[str, Dict[str, Any]]] = []

        # ---- Pass 1: DSML-style invoke (halfwidth or fullwidth) ----
        for inv_match in ResponseParser._DSML_INVOKE_PATTERN.finditer(combined):
            name = inv_match.group(1).strip()
            param_body = inv_match.group(2)
            args: Dict[str, Any] = {}
            for pm in ResponseParser._DSML_PARAM_PATTERN.finditer(param_body):
                key = pm.group(1)
                value_raw = pm.group(2).strip()
                norm_key = _normalize_arg_key(key)
                args[norm_key] = ResponseParser.parse_param_value(value_raw)
            if name:
                norm_name = _normalize_tool_name(name)
                tc_log.info("[_parse_dsml] DSML-invoke name=%r -> %r, args=%r", name, norm_name, args)
                results.append((norm_name, args))

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
                if name:
                    norm_name = _normalize_tool_name(name)
                    tc_log.info("[_parse_dsml] plain-invoke name=%r -> %r, args=%r", name, norm_name, args)
                    results.append((norm_name, args))

        # ---- Pass 3: recover orphan <parameter> tokens (no <invoke> wrapper) ----
        if not results:
            # Both DSML and plain halfwidth variants, value runs up to next '<' or newline.
            orphan_pat = re.compile(
                r'<(?:%s|%s)parameter\s+name="([^"]+)"(?:\s+\w+="[^"]*")*\s*([^<\r\n]*)'.format(
                    "parameter",
                    "{}DSML{}parameter".format(delim, delim),
                )
            )
            recovered_args: Dict[str, Any] = {}
            for m in orphan_pat.finditer(text):
                key = m.group(1)
                value_raw = m.group(2).strip().rstrip(",").strip()
                if not value_raw:
                    continue
                norm_key = _normalize_arg_key(key)
                recovered_args[norm_key] = ResponseParser.parse_param_value(value_raw)
            if recovered_args:
                tc_log.warning(
                    "[_parse_dsml] Lenient fallback: recovered %d orphan <parameter> tokens",
                    len(recovered_args),
                )
                results.append(("llm_recovered", recovered_args))

        # ---- Pass 4: <func>name</func> + cross-style <param>... (legacy XML
        # wrapper content embedded inside a DSML or <tool_call> container) ----
        if not results:
            # Match <func>name</func>  OR  <function=name>
            func_match = re.search(
                r'<func\s*>([^<]+)</func\s*>|<function\s*=\s*"?([a-zA-Z_][\w.]*)"?\s*/?>',
                combined, re.IGNORECASE,
            )
            if func_match:
                name = (func_match.group(1) or func_match.group(2) or "").strip()
                if name:
                    # Reuse the same param extraction used by XML parser
                    args = ResponseParser.parse_xml_arguments(combined)
                    if args:
                        norm_name = _normalize_tool_name(name)
                        tc_log.info(
                            "[_parse_dsml] legacy-func name=%r -> %r, args=%r",
                            name, norm_name, args,
                        )
                        results.append((norm_name, args))

        return results

    @staticmethod
    def _parse_minimax_tool_calls(text: str) -> List[Tuple[str, Dict[str, Any]]]:
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
                tc_log.info("[_parse_minimax] Namespace format: name=%r, args=%r", name, args)
                return [(name, args)]

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
            tc_log.info("[_parse_minimax] Standalone invoke: name=%r, args=%r", name, args)
            return [(name, args)]

        return []

    @staticmethod
    def parse_tool_calls(text: str) -> List[Tuple[str, Dict[str, Any]]]:
        """
        Parse ALL tool_call blocks from text (supports parallel tool calls).

        Tries in order:
        1. DSML format: <｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name="xxx">...</｜｜DSML｜｜invoke></｜｜DSML｜｜tool_calls>
        2. Minimax/namespace format: <namespace:tool_call><invoke name="xxx">...</invoke></namespace:tool_call>
        3. XML format: <tool_call>...</tool_call>
        4. Attribute-style: <tool_call name="xxx">...</tool_call>
        5. Native FC JSON format embedded in text

        Returns: List of (tool_name, arguments_dict) tuples. Empty list if none found.
        """
        tc_log = get_tool_call_debug_logger()

        # Strategy 1: DSML format
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
        dsml_tool_calls = "{}DSML{}tool_calls".format(delim, delim)
        xml_open_close_pat = re.compile(
            r'<tool_call(?:\s*)>(.*?)</(?:tool_call|{dsml})(?:\s*)>'.format(
                dsml=dsml_tool_calls,
            ),
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

        tc_log.debug("[parse_tool_calls] No tool call found in response (len=%d)", len(text))
        return []

    @staticmethod
    def _normalize_results(
        results: List[Tuple[str, Dict[str, Any]]]
    ) -> List[Tuple[str, Dict[str, Any]]]:
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
        normalized: List[Tuple[str, Dict[str, Any]]] = []
        for name, args in results:
            norm_name = _normalize_tool_name(name) if name else name
            norm_args: Dict[str, Any] = {}
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
    def _parse_attr_tool_calls(text: str) -> List[Tuple[str, Dict[str, Any]]]:
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
            args_match = re.search(r'<arguments>(.*?)</arguments>', content, re.DOTALL)
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
