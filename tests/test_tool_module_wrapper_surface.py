"""A plugin namespace must expose its @tool methods and nothing else.

``ToolRegistry.generate_openai_tools`` discovers tools by scanning the
registered object for *public* members (``inspect.isfunction`` **plus**
``inspect.ismethod``) and only skips names starting with ``_``. ``PluginManager``
registers a ``ToolModuleWrapper`` instance, so any public method on that wrapper
shipped as a bogus tool — ``<namespace>__add_method`` appeared in every plugin
namespace (e.g. ``visualization__add_method -> 'Add a bound method as a plain
function attribute.'``), wasting schema tokens and offering the model a call it
must not make.

Run with ``PYTHONPATH=src`` (see ci.yml), like the rest of the suite.
"""

from __future__ import annotations

import inspect

from opensquad.plugin_api import Context, ToolModuleWrapper, get_tool_methods
from opensquad.registry import ToolRegistry
from plugins.visualization.plugin import VisualizationPlugin


def test_wrapper_exposes_no_public_methods():
    """Every public member of the wrapper becomes a tool — keep them private."""
    public = [name for name, _ in inspect.getmembers(ToolModuleWrapper, inspect.isfunction) if not name.startswith("_")]
    assert public == [], (
        f"public ToolModuleWrapper method(s) would ship as bogus tools in every plugin namespace: {sorted(public)}"
    )


def test_namespace_tools_are_exactly_the_decorated_methods():
    instance = VisualizationPlugin(Context(agent_id="wrapper-surface-test"))
    registry = ToolRegistry()

    grouped = {}
    for tm in get_tool_methods(instance):
        grouped.setdefault(tm["meta"]["name"], []).append(tm)

    emitted = set()
    for namespace, methods in grouped.items():
        wrapper = ToolModuleWrapper(instance, namespace=namespace)
        if methods[0]["meta"].get("description"):
            wrapper.__doc__ = methods[0]["meta"]["description"]
        for tm in methods:
            wrapper._add_method(
                method_name=tm["method_name"],
                bound_method=tm["bound_method"],
                doc=tm["bound_method"].__doc__ or "",
            )
        registry.register(wrapper, namespace, level=methods[0]["meta"]["level"])
        emitted.update(t["function"]["name"] for t in registry.generate_openai_tools("all"))

    expected = {f"{ns}__{tm['method_name']}" for ns, tms in grouped.items() for tm in tms}
    actual = {n for n in emitted if n.startswith(tuple(f"{ns}__" for ns in grouped))}
    assert actual == expected, f"unexpected tools leaked into the schema: {sorted(actual - expected)}"
