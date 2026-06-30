"""
Unit tests for Claude and Google Native Function Calling implementation

Tests tool format conversion, response parsing, and auto mode strategy selection
for ClaudeAPI and GoogleAPI providers.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest

from opensquad.claude_api import ClaudeAPI
from opensquad.google_api import GoogleAPI
from opensquad.tool_call_strategy import ToolCallStrategySelector


class TestClaudeAPIToolConversion:
    """Test ClaudeAPI tool format conversion (OpenAI → Claude)"""

    def test_convert_single_tool(self):
        """Test converting single OpenAI tool to Claude format"""
        claude_api = ClaudeAPI(api_key="test-key", model="claude-3-sonnet-20240229")

        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": "websearch.search",
                    "description": "Search the web for information",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string", "description": "Search query"}},
                        "required": ["query"],
                    },
                },
            }
        ]

        result = claude_api._convert_tools_to_claude_format(openai_tools)

        # Verify conversion
        assert len(result) == 1
        assert result[0]["name"] == "websearch.search"
        assert result[0]["description"] == "Search the web for information"
        assert "input_schema" in result[0]
        assert result[0]["input_schema"]["type"] == "object"
        assert "query" in result[0]["input_schema"]["properties"]

        # Verify no 'function' wrapper
        assert "function" not in result[0]
        assert "type" not in result[0] or result[0].get("type") != "function"

    def test_convert_multiple_tools(self):
        """Test converting multiple tools"""
        claude_api = ClaudeAPI(api_key="test-key", model="claude-3-sonnet-20240229")

        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": "tool1",
                    "description": "Tool 1",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "tool2",
                    "description": "Tool 2",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]

        result = claude_api._convert_tools_to_claude_format(openai_tools)

        assert len(result) == 2
        assert result[0]["name"] == "tool1"
        assert result[1]["name"] == "tool2"

    def test_convert_empty_tools(self):
        """Test handling empty tools list"""
        claude_api = ClaudeAPI(api_key="test-key", model="claude-3-sonnet-20240229")

        result = claude_api._convert_tools_to_claude_format([])
        assert result == []


class TestClaudeAPIResponseParsing:
    """Test ClaudeAPI response parsing (extract tool_use from content)"""

    def test_parse_tool_use_from_response(self):
        """Test parsing tool_use block from Claude response (dict format)"""
        claude_api = ClaudeAPI(api_key="test-key", model="claude-3-sonnet-20240229")

        # Use dict format instead of MagicMock for content blocks
        content = [
            {"type": "text", "text": "I'll search for that."},
            {"type": "tool_use", "name": "websearch.search", "input": {"query": "test query"}},
        ]

        result = claude_api._parse_claude_tool_use(content)

        assert result is not None
        assert result[0] == "websearch.search"
        assert result[1] == {"query": "test query"}

    def test_parse_tool_use_only(self):
        """Test parsing response with only tool_use (no text)"""
        claude_api = ClaudeAPI(api_key="test-key", model="claude-3-sonnet-20240229")

        content = [{"type": "tool_use", "name": "calculator.add", "input": {"a": 1, "b": 2}}]

        result = claude_api._parse_claude_tool_use(content)

        assert result is not None
        assert result[0] == "calculator.add"
        assert result[1] == {"a": 1, "b": 2}

    def test_parse_no_tool_use(self):
        """Test parsing response with no tool_use (only text)"""
        claude_api = ClaudeAPI(api_key="test-key", model="claude-3-sonnet-20240229")

        content = [{"type": "text", "text": "This is a normal response."}]

        result = claude_api._parse_claude_tool_use(content)

        assert result is None

    def test_parse_multiple_tool_uses(self):
        """Test parsing returns first tool_use when multiple exist"""
        claude_api = ClaudeAPI(api_key="test-key", model="claude-3-sonnet-20240229")

        content = [
            {"type": "tool_use", "name": "tool1", "input": {"arg": "value1"}},
            {"type": "tool_use", "name": "tool2", "input": {"arg": "value2"}},
        ]

        result = claude_api._parse_claude_tool_use(content)

        # Should return the first tool_use
        assert result is not None
        assert result[0] == "tool1"
        assert result[1] == {"arg": "value1"}


class TestGoogleAPIToolConversion:
    """Test GoogleAPI tool format conversion (OpenAI → Gemini protobuf)"""

    @patch("opensquad.google_api._genai_mod")
    def test_convert_single_tool(self, mock_genai_mod):
        """Test converting single OpenAI tool to Gemini format"""
        # Mock protobuf classes
        mock_function_declaration = MagicMock()
        mock_tool = MagicMock()
        mock_genai_mod.protos.FunctionDeclaration.return_value = mock_function_declaration
        mock_genai_mod.protos.Tool.return_value = mock_tool
        mock_genai_mod.configure = MagicMock()

        google_api = GoogleAPI(api_key="test-key", model="gemini-1.5-pro")

        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": "websearch.search",
                    "description": "Search the web",
                    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                },
            }
        ]

        result = google_api._convert_tools_to_google_format(openai_tools)

        # Verify FunctionDeclaration was created
        mock_genai_mod.protos.FunctionDeclaration.assert_called_once()
        call_kwargs = mock_genai_mod.protos.FunctionDeclaration.call_args[1]
        assert call_kwargs["name"] == "websearch.search"
        assert call_kwargs["description"] == "Search the web"

        # Verify Tool wrapper was created
        mock_genai_mod.protos.Tool.assert_called_once()

        # Verify return value
        assert result == [mock_tool]

    @patch("opensquad.google_api._genai_mod")
    def test_convert_multiple_tools(self, mock_genai_mod):
        """Test converting multiple tools"""
        mock_genai_mod.protos.FunctionDeclaration.return_value = MagicMock()
        mock_genai_mod.protos.Tool.return_value = MagicMock()
        mock_genai_mod.configure = MagicMock()

        google_api = GoogleAPI(api_key="test-key", model="gemini-1.5-pro")

        openai_tools = [
            {
                "type": "function",
                "function": {"name": "tool1", "description": "Tool 1", "parameters": {"type": "object"}},
            },
            {
                "type": "function",
                "function": {"name": "tool2", "description": "Tool 2", "parameters": {"type": "object"}},
            },
        ]

        result = google_api._convert_tools_to_google_format(openai_tools)

        # Should call FunctionDeclaration twice
        assert mock_genai_mod.protos.FunctionDeclaration.call_count == 2

        # Should create one Tool wrapper
        assert mock_genai_mod.protos.Tool.call_count == 1

        # Should return single Tool object with all declarations
        assert len(result) == 1


class TestGoogleAPIResponseParsing:
    """Test GoogleAPI response parsing (extract functionCall from protobuf)"""

    @patch("opensquad.google_api._genai_mod")
    def test_parse_function_call(self, mock_genai_mod):
        """Test parsing functionCall from Gemini response"""
        mock_genai_mod.configure = MagicMock()

        google_api = GoogleAPI(api_key="test-key", model="gemini-1.5-pro")

        # Create mock part with function_call attribute
        mock_part = MagicMock()
        mock_function_call = MagicMock()
        mock_function_call.name = "websearch.search"
        mock_function_call.args = {"query": "test query"}
        mock_part.function_call = mock_function_call

        parts = [mock_part]

        result = google_api._parse_google_function_call(parts)

        assert result is not None
        assert result[0] == "websearch.search"
        assert result[1] == {"query": "test query"}

    @patch("opensquad.google_api._genai_mod")
    def test_parse_no_function_call(self, mock_genai_mod):
        """Test parsing response with no functionCall"""
        mock_genai_mod.configure = MagicMock()

        google_api = GoogleAPI(api_key="test-key", model="gemini-1.5-pro")

        # Part with only text (no function_call)
        mock_part = MagicMock(spec=[])  # spec=[] means it has no attributes
        parts = [mock_part]

        result = google_api._parse_google_function_call(parts)

        assert result is None

    @patch("opensquad.google_api._genai_mod")
    def test_parse_empty_parts(self, mock_genai_mod):
        """Test parsing empty parts list"""
        mock_genai_mod.configure = MagicMock()

        google_api = GoogleAPI(api_key="test-key", model="gemini-1.5-pro")

        result = google_api._parse_google_function_call([])

        assert result is None


class TestStrategyAutoModeClaudeGoogle:
    """Test strategy selector auto mode for Claude and Google"""

    def test_auto_mode_claude_selects_native(self):
        """Test auto mode selects Native FC for Claude"""
        config = {"model": {"provider": "claude", "model_name": "claude-3-sonnet-20240229", "tool_call_mode": "auto"}}
        mock_registry = Mock()
        mock_registry.generate_openai_tools.return_value = []

        strategy = ToolCallStrategySelector.select(config, mock_registry)

        # Claude supports Native FC, should select NativeToolCallStrategy
        from opensquad.tool_call_strategy import NativeToolCallStrategy

        assert isinstance(strategy, NativeToolCallStrategy)
        assert strategy.get_strategy_name() == "Native-FC"

    def test_auto_mode_google_selects_native(self):
        """Test auto mode selects Native FC for Google/Gemini"""
        config = {"model": {"provider": "google", "model_name": "gemini-1.5-pro", "tool_call_mode": "auto"}}
        mock_registry = Mock()
        mock_registry.generate_openai_tools.return_value = []

        strategy = ToolCallStrategySelector.select(config, mock_registry)

        # Google/Gemini supports Native FC, should select NativeToolCallStrategy
        from opensquad.tool_call_strategy import NativeToolCallStrategy

        assert isinstance(strategy, NativeToolCallStrategy)
        assert strategy.get_strategy_name() == "Native-FC"

    def test_is_native_fc_implemented_claude(self):
        """Test _is_native_fc_implemented returns True for Claude"""
        assert ToolCallStrategySelector._is_native_fc_implemented("claude") is True

    def test_is_native_fc_implemented_google(self):
        """Test _is_native_fc_implemented returns True for Google"""
        assert ToolCallStrategySelector._is_native_fc_implemented("google") is True

    def test_is_native_fc_implemented_openai(self):
        """Test _is_native_fc_implemented returns True for OpenAI"""
        assert ToolCallStrategySelector._is_native_fc_implemented("openai") is True
        assert ToolCallStrategySelector._is_native_fc_implemented("openai_compat") is True


class TestClaudeAPIIntegration:
    """Integration test for ClaudeAPI Native FC workflow"""

    def test_tools_conversion_and_parsing_workflow(self):
        """Test the workflow: tools conversion → parsing"""
        claude_api = ClaudeAPI(api_key="test-key", model="claude-3-sonnet-20240229")

        # Test tools conversion
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": "websearch.search",
                    "description": "Search the web",
                    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                },
            }
        ]

        claude_tools = claude_api._convert_tools_to_claude_format(openai_tools)

        # Verify conversion
        assert len(claude_tools) == 1
        assert claude_tools[0]["name"] == "websearch.search"
        assert "input_schema" in claude_tools[0]

        # Test response parsing
        mock_response_content = [
            {"type": "text", "text": "Let me search for that."},
            {"type": "tool_use", "name": "websearch.search", "input": {"query": "test"}},
        ]

        result = claude_api._parse_claude_tool_use(mock_response_content)

        # Verify parsing
        assert result is not None
        assert result[0] == "websearch.search"
        assert result[1] == {"query": "test"}


class TestGoogleAPIIntegration:
    """Integration test for GoogleAPI Native FC workflow"""

    @patch("opensquad.google_api._genai_mod")
    def test_tools_conversion_and_parsing_workflow(self, mock_genai_mod):
        """Test the workflow: tools conversion → parsing"""
        # Mock protobuf classes
        mock_function_declaration = MagicMock()
        mock_tool = MagicMock()
        mock_genai_mod.protos.FunctionDeclaration.return_value = mock_function_declaration
        mock_genai_mod.protos.Tool.return_value = mock_tool
        mock_genai_mod.configure = MagicMock()

        google_api = GoogleAPI(api_key="test-key", model="gemini-1.5-pro")

        # Test tools conversion
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": "websearch.search",
                    "description": "Search the web",
                    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                },
            }
        ]

        google_tools = google_api._convert_tools_to_google_format(openai_tools)

        # Verify conversion
        assert len(google_tools) == 1
        mock_genai_mod.protos.FunctionDeclaration.assert_called_once()

        # Test response parsing
        mock_part = MagicMock()
        mock_function_call = MagicMock()
        mock_function_call.name = "websearch.search"
        mock_function_call.args = {"query": "test"}
        mock_part.function_call = mock_function_call

        mock_parts = [mock_part]

        result = google_api._parse_google_function_call(mock_parts)

        # Verify parsing
        assert result is not None
        assert result[0] == "websearch.search"
        assert result[1] == {"query": "test"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
