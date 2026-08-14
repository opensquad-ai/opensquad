"""Proxy-pattern tool import. Keep this module dependency-free.

Frozen builds may ship an older opensquad.plugin_api without these helpers.
Plugins live on disk (or under _internal/plugins), so they import from here.
"""

from __future__ import annotations

import importlib
from typing import Any


class PluginToolAttachError(RuntimeError):
    """Proxy tool module could not be imported — plugin shell loaded, tools missing."""

    def __init__(self, tool_name: str, import_name: str, cause: BaseException):
        self.tool_name = tool_name
        self.import_name = import_name
        self.cause = cause
        super().__init__(f"{tool_name}: cannot import {import_name} ({type(cause).__name__}: {cause})")


def proxy_tool_module(
    import_name: str,
    *,
    name: str,
    level: str = "extended",
    auto_register: bool = False,
    requires_agent_id: bool = False,
    module: Any = None,
) -> dict[str, Any]:
    """Import a proxy tool module or raise PluginToolAttachError.

    Do not catch ImportError and return []. That hides missing deps / syntax
    errors as "plugin loaded, 0 tools".
    """
    if module is None:
        try:
            module = importlib.import_module(import_name)
        except Exception as e:
            raise PluginToolAttachError(name, import_name, e) from e
    return {
        "name": name,
        "module": module,
        "level": level,
        "auto_register": auto_register,
        "requires_agent_id": requires_agent_id,
    }
