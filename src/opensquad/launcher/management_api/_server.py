"""Exclusive-bind HTTP server for the Launcher management API.

Extracted verbatim from ``launcher_main._start_management_server``; the import
aliases used by the original were resolved (``_ThreadingHTTPServer`` ->
``ThreadingHTTPServer``, ``_socket_mod`` -> ``socket``). The class was renamed
from ``_ExclusiveHTTPServer`` to ``ExclusiveHTTPServer``: it is no longer
function-local, so the leading underscore (which marked "private to this
scope") would wrongly suggest a module-private name while ``__init__`` exports
it in ``__all__``.

Kept in launcher_main's process-exit path: a losing bind must terminate the
process, which stays a launcher_main policy decision.
"""

from __future__ import annotations

import os
import socket
from http.server import ThreadingHTTPServer


class ExclusiveHTTPServer(ThreadingHTTPServer):
    # Windows: SO_REUSEADDR allows a second bind; keep it exclusive so a
    # loser launcher fails with EADDRINUSE and exits. POSIX: reuse is
    # harmless (no dual-bind) and avoids TIME_WAIT restart stalls.
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        # Windows default does NOT make bind exclusive: two processes can
        # bind 0.0.0.0:9600 unless the owner set SO_EXCLUSIVEADDRUSE.
        # Without it the second launcher "succeeds" and both stay alive.
        if os.name == "nt":
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except (AttributeError, OSError):
                pass
        super().server_bind()
