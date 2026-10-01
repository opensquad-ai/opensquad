"""Join a group on another machine from one pasted invite string."""

from __future__ import annotations

from typing import Any


def pair_with_node(invite: str, name: str = "", wait_seconds: int = 20) -> dict[str, Any]:
    """
    [All agents] Pair this machine with the gateway an invite string points at.

    Uses the ``?code=`` in the invite: the host shows a 6-digit code, this asks to
    be paired with it, and once the owner approves the machine receives its own
    scoped token (it never gets the host's node_secret). The gateway address and
    the token are written into this agent's config, so ``join_by_invite`` and the
    collaboration board work against that machine from then on.

    Args:
        invite: the invite string, including ``?code=XXXXXX``.
        name: how this machine should appear in the owner's pairing queue.
        wait_seconds: how long to wait for the owner to approve (polled).

    Returns:
        dict with status, and on success the host plus where it was written.
    """
    try:
        import time

        import requests

        from ..invite import parse_invite

        parsed = parse_invite(invite)
        if not parsed:
            return {"status": "error", "message": "Not an invite string. Expected '<host>[:port]#<group-id>'."}
        if not parsed["code"]:
            return {
                "status": "error",
                "code": "no_pairing_code",
                "message": (
                    "This invite has no pairing code. Ask the host owner for the code "
                    "(it is shown as ?code=XXXXXX in the invite) — or, if this machine was paired "
                    "before, just run join_by_invite."
                ),
            }

        base = parsed["base_url"]
        asked = requests.post(
            f"{base}/api/node/pair/request",
            json={"code": parsed["code"], "name": name or ""},
            timeout=10,
        )
        if asked.status_code not in (200, 201):
            detail = ""
            try:
                detail = str((asked.json() or {}).get("detail") or "")
            except Exception:
                detail = asked.text[:200]
            return {
                "status": "error",
                "code": "pairing_refused",
                "message": f"{base} refused the pairing request: {detail or f'HTTP {asked.status_code}'}",
            }
        request_id = str((asked.json() or {}).get("request_id") or "")

        deadline = time.time() + max(0, int(wait_seconds))
        while True:
            polled = requests.get(f"{base}/api/node/pair/{request_id}", timeout=10)
            body = polled.json() if polled.content else {}
            status = str((body or {}).get("status") or "")
            if status == "approved":
                token = str((body or {}).get("token") or "")
                if not token:
                    return {
                        "status": "error",
                        "message": "Approved without a token; ask the owner to unpair and retry.",
                    }
                from ..node_peers import save_local_peer

                save_local_peer(base, token, name or parsed["host"])
                wrote = _point_agent_at(base, token)
                return {
                    "status": "success",
                    "peer": parsed["host"],
                    "base_url": base,
                    "scopes": list((body or {}).get("scopes") or []),
                    "config_updated": wrote,
                    "message": (
                        "Paired. This machine now has its own scoped token for that gateway — restart "
                        "the agent to use it, then join_by_invite again."
                    ),
                }
            if status in ("rejected", "unknown"):
                return {"status": "error", "code": f"pairing_{status}", "message": f"The host answered: {status}."}
            if time.time() >= deadline:
                return {
                    "status": "pending",
                    "host": parsed["host"],
                    "request_id": request_id,
                    "message": "Waiting for the owner to approve. Ask them to do it, then retry.",
                }
            time.sleep(2)
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _point_agent_at(base_url: str, token: str) -> bool:
    """Write the gateway address + peer token into this agent's config."""
    try:
        import json
        import os

        from ..input_hub import input_hub

        agent_dir = input_hub.agent_dir or ""
        if not agent_dir:
            return False
        path = os.path.join(agent_dir, "config.json")
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        if not isinstance(cfg, dict):
            return False
        chat = cfg.get("group_chat") if isinstance(cfg.get("group_chat"), dict) else {}
        chat["base_url"] = base_url
        cfg["group_chat"] = chat
        gateway = cfg.get("gateway") if isinstance(cfg.get("gateway"), dict) else {}
        gateway["url"] = f"{base_url.replace('https://', 'wss://').replace('http://', 'ws://')}/ai-ws/register"
        gateway["peer_token"] = token
        cfg["gateway"] = gateway
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


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
