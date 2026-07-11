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

    def test_quoted_string(self):
        result = self._target('"hello"')
        assert isinstance(result, str)

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

    def test_mcp_tool_doublescore_to_single(self):
        # No dot -> _normalize_key collapses __ to _
        name = "mcp__filesystem__read_file"
        assert self._target(name) == "mcp_filesystem_read_file"

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
