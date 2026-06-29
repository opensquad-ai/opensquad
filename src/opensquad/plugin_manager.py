# -*- coding: utf-8 -*-
"""OpenSquad Plugin Manager — Unified namespace (P1-2).

This module re-exports PluginManager from the canonical location,
so that both import styles work:

    from opensquad.plugin_manager import PluginManager   # preferred
    from plugins.plugin_manager import PluginManager     # backward-compat

All new code should use `opensquad.plugin_manager`.
"""
# Re-export the canonical implementation from plugins/
from plugins.plugin_manager import (
    PluginManager as _PluginManager,
)

# Re-export for convenience
PluginManager = _PluginManager

__all__ = ["PluginManager"]
