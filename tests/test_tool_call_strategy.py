"""
Unit tests for Tool Call Strategy Pattern

Tests both XML and Native Function Calling strategies.
"""

import pytest
import json
from unittest.mock import Mock, MagicMock
from opensquad.tool_call_strategy import (
    XMLToolCallStrategy,
    NativeToolCallStrategy,
    ToolCallStrategySelector
)


class TestXMLToolCallStrategy:
    """Test XML-based tool call strategy"""
    
    def test_prepare_llm_call_injects_tool_descriptions(self):
        """Test that prepare_llm_call injects tool descriptions into prompt"""
        # Mock tool registry
        mock_registry = Mock()
        mock_registry.generate_tool_descriptions.return_value = "Tool List: foo, bar"
        
        strategy = XMLToolCallStrategy(mock_registry)
        
        # Prepare LLM call with placeholder
        system_prompt = "You are an AI.\n\n{{TOOL_DESCRIPTIONS}}\n\nRespond wisely."
        result = strategy.prepare_llm_call(system_prompt)
        
        # Verify tool descriptions are injected
        assert "Tool List: foo, bar" in result["system_prompt"]
        assert "{{TOOL_DESCRIPTIONS}}" not in result["system_prompt"]
        assert result["tools"] is None  # XML mode doesn't use tools parameter
        
    def test_parse_response_delegates_to_parser(self):
        """Test that parse_response uses ResponseParser.parse_tool_call"""
        mock_registry = Mock()
        strategy = XMLToolCallStrategy(mock_registry)
        
        # Mock response with tool call
        response_text = """
        <tool_call>
          <func>websearch.search</func>
          <query>"test"</query>
        </tool_call>
        """
        
        result = strategy.parse_response(response_text)
        
        # Should return parsed tool call
        assert result is not None
        assert result[0] == "websearch.search"
        assert "query" in result[1]
    
    def test_get_strategy_name(self):
        """Test strategy name is XML"""
        mock_registry = Mock()
        strategy = XMLToolCallStrategy(mock_registry)
        assert strategy.get_strategy_name() == "XML"


class TestNativeToolCallStrategy:
    """Test Native Function Calling strategy"""
    
    def test_prepare_llm_call_generates_tools_schema(self):
        """Test that prepare_llm_call generates OpenAI Tools schema"""
        # Mock tool registry
        mock_registry = Mock()
        mock_registry.generate_openai_tools.return_value = [
            {
                "type": "function",
                "function": {
                    "name": "websearch.search",
                    "description": "Search the web",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"}
                        },
                        "required": ["query"]
                    }
                }
            }
        ]
        
        strategy = NativeToolCallStrategy(mock_registry)
        
        # Prepare LLM call
        system_prompt = "You are an AI.\n\n## 2. Tool Call Format\n...\n\n{{TOOL_DESCRIPTIONS}}"
        result = strategy.prepare_llm_call(system_prompt)
        
        # Verify tools are generated
        assert result["tools"] is not None
        assert len(result["tools"]) == 1
        assert result["tools"][0]["function"]["name"] == "websearch.search"
        assert result["tool_choice"] == "auto"
        
        # Verify old tool format section is removed, but Native FC notice is added
        assert "## 2. Tool Call Format" not in result["system_prompt"]  # 旧的标题被移除
        assert "Native Function Calling" in result["system_prompt"]  # 新的说明被添加
        assert "{{TOOL_DESCRIPTIONS}}" not in result["system_prompt"]
    
    def test_remove_tool_format_section(self):
        """Test that tool format instructions are removed from prompt"""
        mock_registry = Mock()
        strategy = NativeToolCallStrategy(mock_registry)
        
        prompt = """
        You are an AI assistant.
        
        ## 2. Tool Call Format
        
        Use XML format:
        <tool_call>...</tool_call>
        
        ## Available Tools
        {{TOOL_DESCRIPTIONS}}
        
        ## Other Instructions
        Be helpful.
        """
        
        result = strategy._remove_tool_format_section(prompt)
        
        # Tool format section should be removed
        assert "Tool Call Format" not in result
        assert "{{TOOL_DESCRIPTIONS}}" not in result
        assert "Available Tools" not in result
        
        # Other content should remain
        assert "You are an AI assistant" in result
        assert "Other Instructions" in result
        assert "Be helpful" in result
    
    def test_get_strategy_name(self):
        """Test strategy name is Native-FC"""
        mock_registry = Mock()
        strategy = NativeToolCallStrategy(mock_registry)
        assert strategy.get_strategy_name() == "Native-FC"


class TestToolCallStrategySelector:
    """Test strategy selector"""
    
    def test_select_xml_mode_explicit(self):
        """Test explicit XML mode selection"""
        config = {
            "model": {
                "provider": "openai_compat",
                "model_name": "glm-5",
                "tool_call_mode": "xml"
            }
        }
        mock_registry = Mock()
        
        strategy = ToolCallStrategySelector.select(config, mock_registry)
        
        assert isinstance(strategy, XMLToolCallStrategy)
        assert strategy.get_strategy_name() == "XML"
    
    def test_select_native_mode_explicit(self):
        """Test explicit native mode selection"""
        config = {
            "model": {
                "provider": "openai_compat",
                "model_name": "glm-5",
                "tool_call_mode": "native"
            }
        }
        mock_registry = Mock()
        mock_registry.generate_openai_tools.return_value = []
        
        strategy = ToolCallStrategySelector.select(config, mock_registry)
        
        assert isinstance(strategy, NativeToolCallStrategy)
        assert strategy.get_strategy_name() == "Native-FC"
    
    def test_select_auto_mode_glm5(self):
        """Test auto mode selects Native FC for GLM-5"""
        config = {
            "model": {
                "provider": "openai_compat",
                "model_name": "glm-5",
                "tool_call_mode": "auto"
            }
        }
        mock_registry = Mock()
        mock_registry.generate_openai_tools.return_value = []
        
        strategy = ToolCallStrategySelector.select(config, mock_registry)
        
        # GLM-5 supports Function Calling, should select Native
        assert isinstance(strategy, NativeToolCallStrategy)
    
    def test_select_auto_mode_unknown_model(self):
        """Test auto mode falls back to XML for unknown models"""
        config = {
            "model": {
                "provider": "unknown",
                "model_name": "unknown-model",
                "tool_call_mode": "auto"
            }
        }
        mock_registry = Mock()
        
        strategy = ToolCallStrategySelector.select(config, mock_registry)
        
        # Unknown model should fall back to XML
        assert isinstance(strategy, XMLToolCallStrategy)
    
    def test_select_default_auto_mode(self):
        """Test default mode is auto when not specified"""
        config = {
            "model": {
                "provider": "openai_compat",
                "model_name": "gpt-4"
            }
        }
        mock_registry = Mock()
        mock_registry.generate_openai_tools.return_value = []
        
        strategy = ToolCallStrategySelector.select(config, mock_registry)
        
        # GPT-4 supports FC, should select Native
        assert isinstance(strategy, NativeToolCallStrategy)
    
    def test_supports_function_calling_openai_models(self):
        """Test FC detection for OpenAI models"""
        assert ToolCallStrategySelector._supports_function_calling("openai_compat", "gpt-4")
        assert ToolCallStrategySelector._supports_function_calling("openai_compat", "gpt-3.5-turbo")
        assert ToolCallStrategySelector._supports_function_calling("openai", "gpt-4o")
    
    def test_supports_function_calling_glm_models(self):
        """Test FC detection for GLM models"""
        assert ToolCallStrategySelector._supports_function_calling("openai_compat", "glm-4")
        assert ToolCallStrategySelector._supports_function_calling("openai_compat", "glm-5")
    
    def test_supports_function_calling_claude(self):
        """Test FC detection for Claude"""
        assert ToolCallStrategySelector._supports_function_calling("claude", "claude-3-sonnet")
    
    def test_supports_function_calling_gemini(self):
        """Test FC detection for Gemini"""
        assert ToolCallStrategySelector._supports_function_calling("google", "gemini-1.5-pro")
    
    def test_does_not_support_function_calling_unknown(self):
        """Test FC detection returns False for unknown models"""
        assert not ToolCallStrategySelector._supports_function_calling("unknown", "unknown-model")


class TestNativeFCToolCallParsing:
    """Test Native FC tool call parsing (streaming mode)"""
    
    def test_parse_streaming_tool_call_single_chunk(self):
        """Test parsing tool call from single streaming chunk"""
        mock_registry = Mock()
        strategy = NativeToolCallStrategy(mock_registry)
        
        # Mock streaming response (single chunk with complete tool call)
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].delta = MagicMock()
        
        # Simulate tool_calls in delta
        mock_tc = MagicMock()
        mock_tc.index = 0
        mock_tc.id = "call_123"
        mock_tc.type = "function"
        mock_tc.function = MagicMock()
        mock_tc.function.name = "websearch.search"
        mock_tc.function.arguments = '{"query": "test"}'
        
        mock_response.choices[0].delta.tool_calls = [mock_tc]
        mock_response.choices[0].finish_reason = "tool_calls"
        
        # Parse
        result = strategy.parse_response(mock_response)
        
        # Should return parsed tool call
        assert result is not None
        assert result[0] == "websearch.search"
        assert result[1] == {"query": "test"}
    
    def test_parse_streaming_tool_call_multiple_chunks(self):
        """Test parsing tool call from multiple streaming chunks"""
        mock_registry = Mock()
        strategy = NativeToolCallStrategy(mock_registry)
        
        # Chunk 1: Function name
        chunk1 = MagicMock()
        chunk1.choices = [MagicMock()]
        chunk1.choices[0].delta = MagicMock()
        tc1 = MagicMock()
        tc1.index = 0
        tc1.function = MagicMock()
        tc1.function.name = "websearch.search"
        tc1.function.arguments = ""
        chunk1.choices[0].delta.tool_calls = [tc1]
        chunk1.choices[0].finish_reason = None
        
        # Chunk 2: Arguments (part 1)
        chunk2 = MagicMock()
        chunk2.choices = [MagicMock()]
        chunk2.choices[0].delta = MagicMock()
        tc2 = MagicMock()
        tc2.index = 0
        tc2.function = MagicMock()
        tc2.function.name = None
        tc2.function.arguments = '{"query":'
        chunk2.choices[0].delta.tool_calls = [tc2]
        chunk2.choices[0].finish_reason = None
        
        # Chunk 3: Arguments (part 2) + finish
        chunk3 = MagicMock()
        chunk3.choices = [MagicMock()]
        chunk3.choices[0].delta = MagicMock()
        tc3 = MagicMock()
        tc3.index = 0
        tc3.function = MagicMock()
        tc3.function.name = None
        tc3.function.arguments = ' "test"}'
        chunk3.choices[0].delta.tool_calls = [tc3]
        chunk3.choices[0].finish_reason = "tool_calls"
        
        # Parse chunks sequentially
        result1 = strategy.parse_response(chunk1)
        assert result1 is None  # Not finished yet
        
        result2 = strategy.parse_response(chunk2)
        assert result2 is None  # Still not finished
        
        result3 = strategy.parse_response(chunk3)
        assert result3 is not None
        assert result3[0] == "websearch.search"
        assert result3[1] == {"query": "test"}
    
    def test_preserve_skills_and_mcp_sections(self):
        """Test that Skills and MCP sections are preserved in Native FC mode"""
        mock_registry = Mock()
        mock_registry.generate_openai_tools.return_value = []
        
        strategy = NativeToolCallStrategy(mock_registry)
        
        # Simulate base.md structure with all sections
        prompt = """
# AI Agent Core Instructions

## 1. Role Definition
{{EXPERT_ROLE_CARD}}

## 2. System Rules

### 2.1 Tool Call Format

You call tools using XML tags...
<tool_call>
  <func>tool_name</func>
</tool_call>

### 2.2 Thinking Tags
Use <title> for task summary.

## 3. Tools & Skills

### 3.1 Built-in Tools + MCP Tools
This list is dynamically generated.

{{TOOL_DESCRIPTIONS}}

**Additional Notes:**
- agent_setup: Manage Skills

### 3.2 MCP Service Usage Guide
{{MCP_GUIDE}}

### 3.3 Skill Packages (Skills)
{{SKILLS_INSTRUCTIONS}}

---

## 4. Context Summary
{{CONTEXT_SUMMARY}}
"""
        
        result = strategy._remove_tool_format_section(prompt)
        
        # XML format section should be removed
        assert "### 2.1 Tool Call Format" not in result
        assert "<tool_call>" not in result
        
        # 3.1 Built-in Tools section should be removed
        assert "### 3.1 Built-in Tools" not in result
        assert "{{TOOL_DESCRIPTIONS}}" not in result
        
        # BUT 3.2 MCP Guide and 3.3 Skills MUST be preserved
        assert "### 3.2 MCP Service Usage Guide" in result
        assert "{{MCP_GUIDE}}" in result
        assert "### 3.3 Skill Packages (Skills)" in result
        assert "{{SKILLS_INSTRUCTIONS}}" in result
        
        # Other sections should be preserved
        assert "## 1. Role Definition" in result
        assert "### 2.2 Thinking Tags" in result
        assert "## 4. Context Summary" in result
        assert "{{CONTEXT_SUMMARY}}" in result
    
    def test_skills_instructions_replacement_after_cleanup(self):
        """Test that {{SKILLS_INSTRUCTIONS}} can be replaced after cleanup"""
        mock_registry = Mock()
        mock_registry.generate_openai_tools.return_value = []
        
        strategy = NativeToolCallStrategy(mock_registry)
        
        prompt = """
## 3. Tools & Skills

### 3.1 Built-in Tools
{{TOOL_DESCRIPTIONS}}

### 3.3 Skill Packages (Skills)
{{SKILLS_INSTRUCTIONS}}

## 4. Context Summary
"""
        
        # Clean up the prompt
        result = strategy._remove_tool_format_section(prompt)
        
        # Verify {{SKILLS_INSTRUCTIONS}} is still present
        assert "{{SKILLS_INSTRUCTIONS}}" in result
        
        # Simulate replacement (as done in runner.py:1430)
        final = result.replace("{{SKILLS_INSTRUCTIONS}}", "Skill: code_review, task_planner")
        
        # Verify replacement succeeded
        assert "{{SKILLS_INSTRUCTIONS}}" not in final
        assert "Skill: code_review, task_planner" in final


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
