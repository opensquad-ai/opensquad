"""Invite strings: one line that says where a group lives.

    <host>[:port]#<group_id>[?code=XXXXXX]

An operator copies it from the group's page on the machine that hosts the group
and pastes it on the agent's machine, instead of transcribing an IP, a port and a
group id by hand. The optional ``code`` is a pairing code a later version will
consume to open the connection (see node pairing); parsing it now means the
string does not have to change shape when that lands.
"""

from __future__ import annotations

import re
from typing import Any

DEFAULT_PORT = 9555

_HOST_RE = re.compile(r"^[A-Za-z0-9._\-]+$")  # hostname or IPv4
_GROUP_RE = re.compile(r"^[A-Za-z0-9._\-]+$")


def parse_invite(text: str) -> dict[str, Any] | None:
    """Parse an invite string; ``None`` when it is not one.

    ``host#group``, ``host:port#group``, ``host:port#group?code=abc123`` and the
    ``http://``/``https://`` prefixed forms are all accepted (the prefix is
    reported, since it decides whether the connection is secure).
    """
    raw = str(text or "").strip()
    if not raw or "#" not in raw:
        return None

    scheme = "http"
    for prefix, name in (("https://", "https"), ("http://", "http"), ("ws://", "http"), ("wss://", "https")):
        if raw.lower().startswith(prefix):
            scheme = name
            raw = raw[len(prefix) :]
            break

    authority, _, rest = raw.partition("#")
    group_id, _, query = rest.partition("?")
    host_part, _, port_part = authority.partition(":")
    host = host_part.strip()
    group_id = group_id.strip()
    if not host or not _HOST_RE.match(host) or not group_id or not _GROUP_RE.match(group_id):
        return None

    port = DEFAULT_PORT
    if port_part.strip():
        try:
            port = int(port_part.strip())
        except ValueError:
            return None
        if not (1 <= port <= 65535):
            return None

    code = ""
    for pair in query.split("&"):
        key, _, value = pair.partition("=")
        if key.strip().lower() == "code":
            code = value.strip()

    return {
        "host": host,
        "port": port,
        "group_id": group_id,
        "code": code,
        "scheme": scheme,
        "base_url": f"{scheme}://{host}:{port}",
    }


def build_invite(host: str, group_id: str, *, port: int = DEFAULT_PORT, code: str = "", secure: bool = False) -> str:
    """Build the string the other machine pastes."""
    scheme = "https://" if secure else ""
    query = f"?code={code}" if code else ""
    return f"{scheme}{host}:{port}#{group_id}{query}"
