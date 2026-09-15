"""HTTP helpers for loopback (127.0.0.1 / ``localhost`` / ``::1``) calls.

Why this module exists
----------------------
``urllib.request.urlopen`` resolves proxies from the environment
(``HTTP_PROXY`` / ``HTTPS_PROXY`` / ``ALL_PROXY``) and, on Windows, from the
WinINET registry settings (via ``getproxies_registry()``).  ``proxy_bypass()``
does *not* treat a bare ``127.0.0.1`` as a bypass target on every platform, so a
loopback request can silently be routed **through the proxy**.

For a local process that turns a healthy target into an apparent failure:

* ``LauncherProcess._check_agent_health`` -- the watchdog counts consecutive
  probe failures and *restarts a perfectly healthy agent*;
* ``PluginServiceProcess._check_health`` -- a running service is reported as
  unhealthy;
* ``opensquad stop`` / ``start`` -- the graceful ``POST /api/shutdown`` never
  reaches the launcher, so it falls through to a force-kill and in-flight work
  is lost instead of drained.

Routing loopback traffic through a proxy is never intentional, so every
localhost call in this codebase goes through :func:`open_local`, which uses an
opener with all proxies explicitly disabled.
"""

from __future__ import annotations

import urllib.request
from typing import Any

# Built once (module import) so the hot health-check path does not reallocate an
# opener on every probe.  An empty ProxyHandler dict disables proxy detection
# entirely for this opener.
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def open_local(
    url: str,
    *,
    timeout: float,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """Open ``url`` while bypassing any configured HTTP(S) proxy.

    Only use this for loopback endpoints.  For outbound traffic the ambient
    proxy configuration is usually desirable, so keep plain
    ``urllib.request.urlopen`` there.

    Args:
        url: Absolute URL, normally ``http://127.0.0.1:<port>/...``.
        timeout: Socket timeout in seconds.
        method: HTTP method (defaults to GET).
        data: Optional request body bytes.
        headers: Optional extra headers.

    Returns:
        The open response object; the caller owns closing it.
    """
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    return _NO_PROXY_OPENER.open(request, timeout=timeout)
