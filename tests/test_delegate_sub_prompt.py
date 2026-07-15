"""Sub-agent prompt construction must keep tool injection placeholders."""

from opensquad.tools.delegate import _build_sub_prompt


def test_build_sub_prompt_keeps_tool_descriptions_placeholder():
    parent = "You are an agent.\n{{TOOL_DESCRIPTIONS}}\nWorkspace: {{AGENT_WORKSPACE}}\nSummary: {{CONTEXT_SUMMARY}}\n"
    result = _build_sub_prompt(parent)
    assert "{{TOOL_DESCRIPTIONS}}" in result
    assert "{{AGENT_WORKSPACE}}" not in result
    assert "{{CONTEXT_SUMMARY}}" not in result
    assert "Sub-Agent Mode" in result
    assert "same tools as the parent" in result


def test_build_sub_prompt_empty_parent():
    result = _build_sub_prompt("")
    assert "Sub-Agent Mode" in result
    assert "same tools as the parent" in result
