"""
SSRF (Server-Side Request Forgery) protection helpers.

OpenSquad exposes HTTP-fetching tools (websearch, external_api, MCP fetch) whose
URLs are partially controllable by LLM output / user input.  A malicious prompt
must not be able to point the agent at internal-only addresses (cloud metadata
``169.254.169.254``, localhost admin panels, private RFC1918 ranges).

Usage:
    from opensquad.utils.ssrf import assert_public_http_url
    assert_public_http_url(url)  # raises ValueError on private/loopback targets
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# RFC 6890 special-purpose ranges that must never be reachable from a
# server-side fetch.  Loopback + link-local + private + CGNAT + reserved.
_BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network, ...] = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
)

_ALLOWED_SCHEMES = {"http", "https"}


def _ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        # Link-local (fe80::/10), unique-local (fc00::/7), loopback (::1).
        return ip.is_link_local or ip.is_private or ip.is_loopback or ip.is_multicast or ip.is_unspecified
    return any(ip in net for net in _BLOCKED_NETWORKS)


def assert_public_http_url(url: str, *, resolve: bool = True) -> None:
    """Reject URLs that would hit loopback/private/link-local addresses.

    Args:
        url: The URL to validate (http/https only).
        resolve: Also resolve the hostname and reject DNS answers that map to
            blocked ranges (guards against DNS-rebinding style attacks).

    Raises:
        ValueError: scheme unsupported, or the target is a blocked address.
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"Only http/https URLs are allowed (got scheme {parsed.scheme!r})")
    host = parsed.hostname
    if not host:
        raise ValueError("URL has no host")

    # 1. Literal IP address.
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        addr = None
    if addr is not None and _ip_is_blocked(addr):
        raise ValueError(f"Blocked SSRF target (internal address): {host}")

    # 2. Hostname — optionally resolve and check every answer.
    if resolve:
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            infos = []
        for info in infos:
            try:
                sockaddr_ip = info[4][0]
                resolved = ipaddress.ip_address(sockaddr_ip)
            except (ValueError, IndexError):
                continue
            if _ip_is_blocked(resolved):
                raise ValueError(f"Blocked SSRF target: host {host!r} resolves to internal address {resolved}")


def is_public_http_url(url: str) -> bool:
    """Non-raising variant of :func:`assert_public_http_url`."""
    try:
        assert_public_http_url(url)
        return True
    except ValueError:
        return False
