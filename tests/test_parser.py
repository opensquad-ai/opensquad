"""Unit tests for parser.py utility functions.

Tests cover: ResponseParser.parse_param_value type inference, _normalize_key,
_normalize_arg_key, _normalize_tool_name, ResponseParser.parse_xml_arguments
boundary cases.
"""


class TestParseParamValue:
    """Test ResponseParser.parse_param_value — type inference for XML parameter values."""

    @staticmethod
    def _target(value_str: str):
        from opensquad.parser import ResponseParser

        return ResponseParser.parse_param_value(value_str)

    def test_plain_string(self):
        assert self._target("hello") == "hello"

    def test_integer(self):
        assert self._target("42") == 42
        assert self._target("-1") == -1

    def test_float(self):
        assert self._target("3.14") == 3.14
        assert self._target("-0.5") == -0.5

    def test_boolean(self):
        # Python-style booleans (True/False) are parsed via ast.literal_eval
        assert self._target("True") is True
        assert self._target("False") is False
        # JSON-style booleans (true/false) are not valid Python; returned as strings
        assert self._target("true") == "true"
        assert self._target("false") == "false"

    def test_none_values(self):
        # Python None is parsed via ast.literal_eval
        assert self._target("None") is None
        # JSON-style null is not valid Python; returned as string
        assert self._target("null") == "null"

    def test_empty_string(self):
        assert self._target("") == ""
        assert self._target("   ").strip() == ""

    def test_quoted_windows_path_tab_restored(self):
        # ast.literal_eval turns \t into TAB; paths like docs\tool_x must survive.
        assert self._target(r'"docs\tool_result.md"') == r"docs\tool_result.md"

    def test_quoted_windows_path_bell_restored(self):
        assert "app" in str(self._target(r'"C:\app\file.txt"'))

    def test_list_literal(self):
        result = self._target("[1, 2, 3]")
        assert isinstance(result, list)
        assert result == [1, 2, 3]

    def test_dict_literal(self):
        result = self._target('{"a": 1}')
        assert isinstance(result, dict)


class TestNormalizeKey:
    """Test _normalize_key — key normalization (lowercase + separator collapsing)."""

    @staticmethod
    def _target(key: str):
        from opensquad.parser import _normalize_key

        return _normalize_key(key)

    def test_camel_case_becomes_lowercase(self):
        # _normalize_key does NOT split camelCase; it only lowercases
        assert self._target("maxRetries") == "maxretries"

    def test_already_snake(self):
        assert self._target("max_retries") == "max_retries"

    def test_with_dots(self):
        # dots are not separators, so they remain
        assert self._target("file.path") == "file.path"

    def test_hyphen_to_underscore(self):
        assert self._target("start-line") == "start_line"

    def test_empty(self):
        assert self._target("") == ""


class TestNormalizeArgKey:
    """Test _normalize_arg_key — key normalization with camelCase awareness."""

    @staticmethod
    def _target(key: str):
        from opensquad.parser import _normalize_arg_key

        return _normalize_arg_key(key)

    def test_camel_to_snake(self):
        assert self._target("maxRetries") == "max_retries"

    def test_already_snake(self):
        assert self._target("max_retries") == "max_retries"


class TestNormalizeToolName:
    """Test _normalize_tool_name — tool name normalization."""

    @staticmethod
    def _target(name: str):
        from opensquad.parser import _normalize_tool_name

        return _normalize_tool_name(name)

    def test_system_tool(self):
        assert self._target("system.echo") == "system.echo"

    def test_mcp_tool_preserves_double_underscore(self):
        # MCP / Native FC separators must stay as __ (registry routes on mcp__)
        name = "mcp__filesystem__read_file"
        assert self._target(name) == "mcp__filesystem__read_file"

    def test_native_fc_preserves_double_underscore(self):
        assert self._target("Filesystem__Read_File") == "filesystem__read_file"

    def test_mcp_tool_case_normalized_per_segment(self):
        assert self._target("mcp__Playwright__Browser_Navigate") == "mcp__playwright__browser_navigate"

    def test_colon_unchanged(self):
        # No dot -> _normalize_key does not replace colon
        assert self._target("system:echo") == "system:echo"

    def test_dots_converted(self):
        # Has dot -> each part normalized
        assert self._target("Filesystem.Read_File") == "filesystem.read_file"


class TestParseXmlArguments:
    """Test ResponseParser.parse_xml_arguments — XML arguments parsing."""

    @staticmethod
    def _target(xml_content: str):
        from opensquad.parser import ResponseParser

        return ResponseParser.parse_xml_arguments(xml_content)

    def test_simple_params(self):
        # Note: JSON-style `true`/`false` are parsed as strings since
        # ast.literal_eval only recognizes Python-style True/False
        result = self._target("<path>/tmp</path><recursive>true</recursive>")
        assert result == {"path": "/tmp", "recursive": "true"}

    def test_empty_xml(self):
        assert self._target("") == {}

    def test_no_valid_tags(self):
        assert self._target("plain text") == {}

    def test_multiline_values(self):
        result = self._target("<content>line1\nline2\nline3</content>")
        assert "line1" in result["content"]


class TestExtractTagIgnoresReasoningMentions:
    """Plan extraction must not latch onto `<plan>` mentioned inside <think>."""

    def test_plan_inside_think_does_not_pollute(self):
        from opensquad.parser import ResponseParser

        text = (
            "<think>\n"
            "Just create a plan with the `<plan>` tag for an outdoor checklist.\n"
            "Let me create a practical going-out checklist as a plan.\n"
            "</think>\n"
            "<plan>\n"
            "出门计划清单\n"
            "查看天气预报和预警\n"
            "准备雨具（伞/雨衣）\n"
            "</plan>\n"
            "<to_user>出门计划清单已准备好。</to_user>"
        )
        plan = ResponseParser.extract_tag(text, "plan")
        assert "出门计划清单" in plan
        assert "查看天气预报和预警" in plan
        assert "</think>" not in plan
        assert "outdoor checklist" not in plan
        assert "` tag" not in plan

    def test_extract_think_still_works(self):
        from opensquad.parser import ResponseParser

        text = "<think>reasoning here</think>\n<plan>\nstep1\n</plan>"
        assert ResponseParser.extract_tag(text, "think") == "reasoning here"
        assert ResponseParser.extract_tag(text, "plan") == "step1"


class TestParseDsmlToolCalls:
    """DSML tool-call variants must be executed, not shown as plain text."""

    @staticmethod
    def _parse(text: str):
        from opensquad.parser import ResponseParser

        return ResponseParser.parse_tool_calls(text)

    def test_spaced_calls_wrapper_and_arguments_json(self):
        # Exact shape the agent sometimes emits (space after delimiter, wrapper `calls`).
        fw = "\uff5c\uff5c"
        text = (
            f"<{fw}DSML{fw} calls>\n"
            f'<{fw}DSML{fw} invoke name="shell">\n'
            f'<{fw}DSML{fw} parameter name="arguments" string="true">'
            '{"command": "dir foo /b"}'
            f"</{fw}DSML{fw} parameter>\n"
            f"</{fw}DSML{fw} invoke>\n"
            f"</{fw}DSML{fw} calls>"
        )
        result = self._parse(text)
        assert len(result) == 1
        name, args = result[0]
        assert name == "shell"
        assert args.get("command") == "dir foo /b"

    def test_user_sample_unescaped_quotes_in_command(self):
        fw = "\uff5c\uff5c"
        text = (
            f"<{fw}DSML{fw} calls>\n"
            f'<{fw}DSML{fw} invoke name="shell">\n'
            f'<{fw}DSML{fw} parameter name="arguments" string="true">'
            '{"command": "dir "c:\\\\users\\\\adminuser\\\\desktop\\\\aigame2" /b"}'
            f"</{fw}DSML{fw} parameter>\n"
            f"</{fw}DSML{fw} invoke>\n"
            f"</{fw}DSML{fw} calls>"
        )
        result = self._parse(text)
        assert len(result) == 1
        name, args = result[0]
        assert name == "shell"
        assert "command" in args
        assert "aigame2" in str(args["command"])

    def test_canonical_dsml_tool_calls_no_space(self):
        fw = "\uff5c\uff5c"
        text = (
            f"<{fw}DSML{fw}tool_calls>"
            f'<{fw}DSML{fw}invoke name="filesystem.list_directory">'
            f'<{fw}DSML{fw}parameter name="path" string="true">C:\\\\tmp</{fw}DSML{fw}parameter>'
            f"</{fw}DSML{fw}invoke>"
            f"</{fw}DSML{fw}tool_calls>"
        )
        result = self._parse(text)
        assert len(result) == 1
        name, args = result[0]
        assert name == "filesystem.list_directory"
        assert "tmp" in str(args.get("path", ""))

    def test_delimiter_only_invoke_without_dsml_token(self):
        fw = "\uff5c\uff5c"
        text = f'<{fw}invoke name="web.search">\n<{fw}parameter name="query">福州天气</{fw}parameter>\n</{fw}invoke>'
        result = self._parse(text)
        assert len(result) == 1
        name, args = result[0]
        assert name == "web.search"
        assert args.get("query") == "福州天气"

    def test_halfwidth_dsml_still_parses(self):
        text = (
            "<||DSML||tool_calls>"
            '<||DSML||invoke name="system.echo">'
            '<||DSML||parameter name="text">hi</||DSML||parameter>'
            "</||DSML||invoke>"
            "</||DSML||tool_calls>"
        )
        result = self._parse(text)
        assert result == [("system.echo", {"text": "hi"})]

    def test_xml_tool_call_still_works(self):
        text = "<tool_call>\n<func>im.send</func>\n<to>user@ai.com</to>\n</tool_call>"
        result = self._parse(text)
        assert len(result) == 1
        name, args = result[0]
        assert name == "im.send"
        assert args.get("to") == "user@ai.com"

    def test_invoke_name_tool_call_uses_inner_func(self):
        fw = "\uff5c\uff5c"
        text = (
            f"<{fw}DSML{fw}tool_calls>"
            f'<{fw}DSML{fw}invoke name="tool_call">'
            "<func>filesystem.read_file</func>"
            "<path>README.md</path>"
            f"</{fw}DSML{fw}invoke>"
            f"</{fw}DSML{fw}tool_calls>"
        )
        result = self._parse(text)
        assert len(result) == 1
        name, args = result[0]
        assert name == "filesystem.read_file"
        assert args.get("path") == "README.md"
        assert "func" not in args

    def test_orphan_path_parameter_maps_to_read_file(self):
        text = '<parameter name="path">README.md</parameter>'
        result = self._parse(text)
        assert len(result) == 1
        name, args = result[0]
        assert name == "filesystem.read_file"
        assert args.get("path") == "README.md"
        assert name != "llm_recovered"

    def test_placeholder_invoke_without_func_is_skipped(self):
        fw = "\uff5c\uff5c"
        text = (
            f"<{fw}DSML{fw}tool_calls>"
            f'<{fw}DSML{fw}invoke name="tool_call">'
            f'<{fw}DSML{fw}parameter name="unknown_key" string="true">x</{fw}DSML{fw}parameter>'
            f"</{fw}DSML{fw}invoke>"
            f"</{fw}DSML{fw}tool_calls>"
        )
        result = self._parse(text)
        assert result == []

    def test_strip_dsml_removes_inner_json(self):
        from opensquad.parser import strip_dsml_tool_markup

        fw = "\uff5c\uff5c"
        text = (
            f"hello <{fw}DSML{fw} calls>"
            f'<{fw}DSML{fw} invoke name="shell">'
            f'<{fw}DSML{fw} parameter name="arguments">{{"command": "dir foo"}}'
            f"</{fw}DSML{fw} parameter>"
            f"</{fw}DSML{fw} invoke>"
            f"</{fw}DSML{fw} calls> world"
        )
        out = strip_dsml_tool_markup(text)
        assert "dir foo" not in out
        assert "invoke" not in out.lower()
        assert "hello" in out
        assert "world" in out


class TestStripDsmlUnclosedSafety:
    """未闭合的 DSML 标签不应吞掉其后的正常正文。"""

    FW = "\uff5c\uff5c"

    @staticmethod
    def _strip(text: str) -> str:
        from opensquad.parser import strip_dsml_tool_markup

        return strip_dsml_tool_markup(text)

    def test_unclosed_tag_with_argument_tail_is_dropped(self):
        """流被截断时，残缺的 JSON 参数确实应该被丢弃。"""
        fw = self.FW
        text = f'<{fw}DSML{fw} invoke name="shell"><{fw}DSML{fw} parameter name="arguments">{{"command": "dir foo"'
        out = self._strip(text)
        assert "dir foo" not in out
        assert "invoke" not in out.lower()

    def test_unclosed_tag_keeps_surrounding_prose(self):
        """模型只是在解释 DSML 语法时，后面的正文必须保留。"""
        fw = self.FW
        text = f"要传参数就写成 <{fw}DSML{fw} parameter> 这种形式，然后按顺序给出即可，不要漏掉收尾标签。"
        out = self._strip(text)
        assert "要传参数就写成" in out
        assert "然后按顺序给出即可，不要漏掉收尾标签。" in out
        assert "parameter>" not in out


class TestParseDotsFunctionCalls:
    """Dots Studio <dots_function_call> must win over DSML orphan path recovery."""

    @staticmethod
    def _parse(text: str):
        from opensquad.parser import ResponseParser

        return ResponseParser.parse_tool_calls(text)

    def test_canonical_invoke_name(self):
        text = (
            "<dots_function_call>\n"
            '<invoke name="mcp__filesystem__directory_tree">\n'
            '<parameter name="path">.</parameter>\n'
            "</invoke>\n"
            "</dots_function_call>"
        )
        result = self._parse(text)
        assert len(result) == 1
        name, args = result[0]
        assert name == "mcp__filesystem__directory_tree"
        assert args.get("path") == "."
        assert name != "filesystem.read_file"

    def test_truncated_invoke_attr_is_not_read_file(self):
        """OpenRouter dots often emit `invoke="mcp__...">` without `<invoke name=`."""
        text = (
            "<dots_function_call>\n"
            'invoke="mcp__filesystem__directory_tree">\n'
            '<parameter name="path">.</parameter>\n'
            "</invoke>\n"
            "</dots_function_call>"
        )
        result = self._parse(text)
        assert len(result) == 1
        name, args = result[0]
        assert name == "mcp__filesystem__directory_tree"
        assert args.get("path") == "."
        assert name != "filesystem.read_file"

    def test_angle_invoke_equals_form(self):
        text = (
            "<dots_function_call>\n"
            '<invoke="mcp__filesystem__directory_tree">\n'
            '<parameter name="path">C:\\\\tmp</parameter>\n'
            "</invoke>\n"
            "</dots_function_call>"
        )
        result = self._parse(text)
        assert len(result) == 1
        name, args = result[0]
        assert name == "mcp__filesystem__directory_tree"
        assert "tmp" in str(args.get("path", ""))
