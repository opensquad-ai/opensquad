"""HTTP management API for the Launcher.

``launcher_main._start_management_server`` used to be a single 3.6k-line
function holding a ~120-method ``ManagementHandler``.  The handler is now
composed here from per-domain mixins.  The routers (``do_GET`` / ``do_POST`` /
... and the ``_do_*_impl`` dispatch tables) stayed byte-identical in
:mod:`._base`, so the route surface is unchanged -- verified by a route-table
diff against the pre-split source.

Import this package **lazily** (from inside ``launcher_main``), never at module
level: the mixins import the launcher registries from ``launcher_main`` itself,
so a module-level import would be circular.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler

from ._agents import AgentsMixin
from ._base import BaseHandlerMixin
from ._cards import CardsMixin
from ._filesystem import FilesystemMixin
from ._mcp import McpMixin
from ._plugin_services import PluginServicesMixin
from ._plugins import PluginsMixin
from ._server import ExclusiveHTTPServer
from ._sessions import SessionsMixin
from ._skills import SkillsMixin
from ._workspace import WorkspaceMixin

__all__ = ["ExclusiveHTTPServer", "ManagementHandler"]


class ManagementHandler(
    BaseHandlerMixin,
    AgentsMixin,
    FilesystemMixin,
    PluginsMixin,
    PluginServicesMixin,
    SessionsMixin,
    McpMixin,
    SkillsMixin,
    CardsMixin,
    WorkspaceMixin,
    BaseHTTPRequestHandler,
):
    """Lightweight HTTP handler — no FastAPI/uvicorn dependency, minimal external deps"""
