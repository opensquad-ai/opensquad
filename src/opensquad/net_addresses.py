"""This machine's own addresses, for telling another machine where to dial.

The invite panel used to fall back to ``127.0.0.1`` (the page it is served from), which no
other machine can reach. A browser cannot read its host's interfaces, so the address has to
come from the backend — here, without pulling in a dependency: ask the routing table for the
address a connection would leave from, and cross-check the hostname's own records.

Only addresses another machine could plausibly use are returned: loopback, link-local,
unspecified and multicast are dropped, and private ranges are listed first.
"""

from __future__ import annotations

import ipaddress
import socket


def _usable(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (addr.is_loopback or addr.is_link_local or addr.is_unspecified or addr.is_multicast)


def lan_addresses() -> list[str]:
    """Addresses other machines can reach, best candidate first. Never raises.

    The **route-probe** address leads: it is the interface this machine would actually
    leave from, so on a host with Docker / VPN / several NICs it is the reachable one, while
    the hostname's records may list virtual bridges (``172.17.0.1``) that no peer can dial.
    """
    probe_ip = ""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            probe_ip = str(probe.getsockname()[0])
    except Exception:
        probe_ip = ""

    resolved: list[str] = []
    try:
        _host, _aliases, ips = socket.gethostbyname_ex(socket.gethostname())
        resolved.extend(str(ip) for ip in ips)
    except Exception:
        pass

    def rank(ip: str) -> tuple[int, str]:
        try:
            private = 0 if ipaddress.ip_address(ip).is_private else 1
        except ValueError:
            private = 1
        return (private, ip)

    out: list[str] = []
    if _usable(probe_ip):
        out.append(probe_ip)
    for ip in sorted(set(resolved), key=rank):
        if _usable(ip) and ip not in out:
            out.append(ip)
    return out
