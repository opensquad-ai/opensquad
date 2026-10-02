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
                from ..peer_bridge import remember_peer

                save_local_peer(base, token, name or parsed["host"])
                # Recorded NEXT TO this agent's own gateway, never over it: pairing a
                # second machine must not cost the agent the groups it is in at home
                # (that hijack is why the old flow needed a restart to switch back).
                wrote = remember_peer(parsed["host"], base, token, name or parsed["host"])
                return {
                    "status": "success",
                    "peer": parsed["host"],
                    "base_url": base,
                    "scopes": list((body or {}).get("scopes") or []),
                    "config_updated": wrote,
                    "message": (
                        f"Paired with {base}. This agent keeps its own gateway and groups — the peer was "
                        "recorded alongside them, nothing was repointed and no restart is needed. Next: "
                        f"im.register_account(email='<unique>@ai', password='...', host='{parsed['host']}'), "
                        "then join_by_invite."
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


def _after_peer_join(host: str, group_id: str) -> dict:
    """Bookkeeping once this agent is a member of a group on a peer.

    Two things, both needed before the membership is usable:

    * the group's **owner** is recorded, so board calls about it route back (the board
      lives on the machine that owns the group);
    * a **relay subscription** is opened, so that machine pushes the group's messages
      to this one — without it the membership is send-only, and nothing arrives.

    ``im.join_group(host=...)`` did this; ``join_by_invite`` (the documented entry
    point) did not, which is how an agent ended up able to send but never receive.
    """
    from ..peer_bridge import remember_peer_group, subscribe_group

    remember_peer_group(host, group_id)
    relay = subscribe_group(host, group_id)
    if relay.get("ok"):
        return {"relay": "subscribed"}
    return {
        "relay": "not_subscribed",
        "relay_error": str(relay.get("error") or ""),
        "message": (
            " Joined, but that machine did not accept the message relay, so its group messages will "
            "NOT be delivered here yet — re-pair with pair_with_node if the peer token is stale."
        ),
    }


def join_by_invite(invite: str, note: str = "") -> dict[str, Any]:
    """
    [All agents] Join a group on another machine from its invite string.

    The string looks like ``192.168.5.4:9555#g-7f3a`` (or
    ``https://chat.example.com#g-7f3a``); the group's page on the hosting machine
    shows it. The invite says which gateway the call goes to:

    * this agent's **own** gateway — joined with the account it already has;
    * a **paired** machine — joined there with the account registered for it via
      ``im.register_account(..., host=...)``, through that peer's own bridge. This
      agent's gateway and its groups at home are untouched.

    A private group files a join request instead of failing, because only the owner
    can let you in there.

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
        from ..peer_bridge import find_peer, host_key, peer_bridge

        # Is this invite about the gateway this agent already lives on, or about a
        # machine it paired with? The second case must NOT go through the home
        # bridge (and must not repoint it): it gets its own bridge to that peer.
        home = host_key(getattr(bridge, "base_url", "") or "")
        invited = host_key(parsed["base_url"])
        is_home = bool(home) and (invited == home or host_key(parsed["host"]) == home)

        if is_home:
            if not bridge or not bridge.token:
                return {
                    "status": "error",
                    "message": (
                        "Bridge not logged in. Register an account first "
                        "(im.register_account(email='<agent>@ai', password='...'))."
                    ),
                }
            target = bridge
        else:
            target, why = peer_bridge(parsed["host"])
            if target is None:
                if find_peer(parsed["host"]):
                    return {"status": "error", "code": "peer_not_ready", "message": why}
                return {
                    "status": "error",
                    "code": "not_paired",
                    "message": (
                        f"{parsed['base_url']} is another machine and this agent is not paired with it. "
                        "Ask its owner for the coded invite string ('带配对码的邀请串') and run "
                        "pair_with_node(invite='<host>:<port>#<group>?code=XXXXXX', name='...') here first."
                    ),
                }

        result = target.join_group_api(parsed["group_id"])
        if isinstance(result, dict) and result.get("ok"):
            extra = {} if is_home else _after_peer_join(parsed["host"], parsed["group_id"])
            return {
                "status": "success",
                "joined": True,
                "group_id": parsed["group_id"],
                "host": parsed["host"],
                **extra,
            }
        detail = str((result or {}).get("detail", "") if isinstance(result, dict) else result)

        # Already in the group: do the peer bookkeeping anyway. A join that merely
        # looks like a failure is how a member ended up send-only in the field — the
        # relay was never subscribed and nothing ever arrived.
        if not is_home and ("already a member" in detail.lower() or "already joined" in detail.lower()):
            return {
                "status": "success",
                "joined": True,
                "already_member": True,
                "group_id": parsed["group_id"],
                "host": parsed["host"],
                **_after_peer_join(parsed["host"], parsed["group_id"]),
            }

        # A private group refuses self-join by design — ask the owner instead.
        lowered = detail.lower()
        private = "private" in lowered or "cannot join" in lowered
        if not private:
            return {"status": "error", "message": f"Failed to join {parsed['group_id']}: {detail}"}

        import requests

        response = requests.post(
            f"{target.base_url}/api/groups/{parsed['group_id']}/join-request",
            params={"token": target.token},
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
