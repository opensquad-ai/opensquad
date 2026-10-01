"""Join a group on another machine from one pasted invite string."""

from __future__ import annotations

from typing import Any


def join_by_invite(invite: str, note: str = "") -> dict[str, Any]:
    """
    [All agents] Join a group on another machine from its invite string.

    The string looks like ``192.168.5.4:9555#g-7f3a`` (or
    ``https://chat.example.com#g-7f3a``); the group's page on the hosting machine
    shows it. This agent must already be bridged to that gateway (its
    ``group_chat.base_url`` points there) with an ``@ai`` account — the tool joins
    the group, and when the group is private it files a join request instead of
    failing, because only the owner can let you in there.

    Args:
        invite: the invite string copied from the group page.
        note: optional message attached to a private-group join request.

    Returns:
        dict with status and, for a private group, ``request_id`` + ``pending``.
    """
    try:
        from ..invite import parse_invite

        parsed = parse_invite(invite)
        if not parsed:
            return {
                "status": "error",
                "message": "Not an invite string. Expected '<host>[:port]#<group-id>'.",
            }

        from ..bridge import bridge

        if not bridge or not bridge.token:
            return {
                "status": "error",
                "message": (
                    "Bridge not logged in. Register an account first "
                    "(im.register_account(email='<agent>@ai', password='...')) and point this agent's "
                    f"group_chat.base_url at {parsed['base_url']}."
                ),
            }
        if not bridge.base_url.startswith(parsed["base_url"]):
            return {
                "status": "error",
                "code": "wrong_gateway",
                "message": (
                    f"This agent is bridged to {bridge.base_url}, but the invite is for "
                    f"{parsed['base_url']}. Point group_chat.base_url there and restart, then retry."
                ),
            }

        result = bridge.join_group_api(parsed["group_id"])
        if isinstance(result, dict) and result.get("ok"):
            return {
                "status": "success",
                "joined": True,
                "group_id": parsed["group_id"],
                "host": parsed["host"],
            }
        detail = str((result or {}).get("detail", "") if isinstance(result, dict) else result)

        # A private group refuses self-join by design — ask the owner instead.
        lowered = detail.lower()
        private = "private" in lowered or "cannot join" in lowered
        if not private:
            return {"status": "error", "message": f"Failed to join {parsed['group_id']}: {detail}"}

        import requests

        response = requests.post(
            f"{bridge.base_url}/api/groups/{parsed['group_id']}/join-request",
            params={"token": bridge.token},
            json={"message": note or "join by invite"},
            timeout=10,
        )
        if response.status_code not in (200, 201):
            return {
                "status": "error",
                "message": f"Group {parsed['group_id']} is private and the request failed: HTTP {response.status_code}",
            }
        body = response.json() if response.content else {}
        return {
            "status": "pending",
            "joined": False,
            "pending": True,
            "group_id": parsed["group_id"],
            "host": parsed["host"],
            "request_id": str((body or {}).get("request_id") or ""),
            "message": "This group is private: the owner has been asked. You join once they approve.",
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
