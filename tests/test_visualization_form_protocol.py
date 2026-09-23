"""The Agent Web FORM protocol must reach the model, not just the source file.

Regression this locks (2026-09-22 audit). ``plugin.py`` carried a 964-char
``@tool(description=...)`` spelling out the whole ``os_form_submit`` protocol,
and a source-scan test grep'd those strings out of the file and stayed green.
Nothing ever shipped them:

* ``registry.generate_openai_tools()`` renders an extended tool from
  ``inspect.getdoc(func).splitlines()[0]`` clipped to 96 chars, so the native-FC
  schema the model receives said only "Create a visualization from HTML for
  Agent Web to embed." — the ``@tool`` description was never read on that path;
* the XML prompt lists one line per namespace, and ``help.get_tool_help`` was
  the only door — which an agent has no reason to open for a capability it does
  not know exists.

So the agent could not learn that forms were supported at all. These tests
assert the protocol is present in what the **model** receives.

Run with ``PYTHONPATH=src`` (see ci.yml), like the rest of the suite.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PROMPTS = REPO / "src" / "prompts"
PART = PROMPTS / "parts" / "common_2.26_agent_web_interactive_html.md"
TEMPLATES = ("base_fc.md", "thought_fc.md", "base_xml.md", "thought_xml.md")

CREATE = "visualization__create"


def _registry_with_visualization():
    """Register the plugin through the public plugin_api path.

    ``ToolModuleWrapper`` documents exactly this usage for hosts (PluginManager
    does the same in ``_prepare_new_style``); going through PluginManager itself
    would regenerate ``plugin.json`` on disk as a test side effect.
    """
    from opensquad.plugin_api import Context, ToolModuleWrapper, get_tool_methods
    from opensquad.registry import ToolRegistry
    from plugins.visualization.plugin import VisualizationPlugin

    instance = VisualizationPlugin(Context(agent_id="form-protocol-test"))
    registry = ToolRegistry()

    grouped = {}
    for tm in get_tool_methods(instance):
        grouped.setdefault(tm["meta"]["name"], []).append(tm)

    for namespace, methods in grouped.items():
        wrapper = ToolModuleWrapper(instance, namespace=namespace)
        desc = methods[0]["meta"].get("description", "")
        if desc:
            wrapper.__doc__ = desc
        for tm in methods:
            wrapper._add_method(
                method_name=tm["method_name"],
                bound_method=tm["bound_method"],
                doc=tm["bound_method"].__doc__ or "",
            )
        registry.register(wrapper, namespace, level=methods[0]["meta"]["level"])
    return registry


def test_fc_schema_carries_the_form_protocol_under_the_default_filter():
    """Native FC is the default mode; the tool must be present AND say how."""
    registry = _registry_with_visualization()
    schema = next(
        (t["function"] for t in registry.generate_openai_tools("high") if t["function"]["name"] == CREATE),
        None,
    )
    assert schema is not None, (
        f"{CREATE} is missing from the default 'high' filter — agents cannot call a tool that is not in the schema"
    )
    assert "os_form_submit" in schema["description"], (
        "the FC description is the only prose the model gets for this tool; "
        f"it must name the form protocol. Got: {schema['description']!r}"
    )


def test_extended_budget_still_fits_the_docstring_first_line():
    """The FC description is the first docstring line, clipped to 96 chars."""
    from opensquad.registry import _FN_DESC_MAX_EXTENDED
    from plugins.visualization.plugin import VisualizationPlugin

    first_line = inspect.getdoc(VisualizationPlugin.create).strip().splitlines()[0]
    assert len(first_line) <= _FN_DESC_MAX_EXTENDED, (
        f"{len(first_line)} chars exceeds the extended budget of "
        f"{_FN_DESC_MAX_EXTENDED}; the protocol would be clipped away: {first_line!r}"
    )
    assert "os_form_submit" in first_line


def test_xml_tool_list_mentions_the_form_mechanism():
    """XML mode gets the wrapper __doc__, so it must name the contract too."""
    text = _registry_with_visualization().generate_tool_descriptions()
    assert "os_form_submit" in text


@pytest.mark.parametrize("template", TEMPLATES)
def test_system_prompt_carries_the_form_protocol(template: str):
    """The prompt part is the carrier that works in every mode — and it is the
    only place the agent learns how to recognise the reply message."""
    from opensquad.prompt_includes import read_prompt_with_includes

    text = read_prompt_with_includes(str(PROMPTS / template), str(PROMPTS))
    assert "2.26 Interactive HTML & Forms" in text, f"{template} does not include the part"
    assert "os_form_submit" in text
    assert "[Form submission]" in text
    assert "[表单提交]" in text, "Chinese UI users get a Chinese-titled message"


def test_part_documents_how_to_recognise_the_returned_message():
    body = PART.read_text(encoding="utf-8")
    assert "window.parent.postMessage" in body
    assert "os_form_submit" in body
    assert "payload" in body
