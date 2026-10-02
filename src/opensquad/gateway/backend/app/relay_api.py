"""HTTP surface for the gateway-to-gateway relay (see ``app/relay.py``).

Two endpoints, two directions:

* ``POST /api/relay/subscribe`` — a **home** gateway tells an **owner** gateway
  "push this group's messages to me". Authenticated with the scoped peer token
  (``group:join``), the same token pairing already mints; no new credential and
  no ``node_secret`` crosses the link.
* ``POST /api/relay/deliver`` — the **owner** gateway pushes a relayed message to
  the **home** gateway. Authenticated with the per-subscription secret the home
  gateway minted, so the owner needs no credential of its own.

Delivery re-enters the local broadcast path, which is exactly how a locally
authored message reaches the agent's socket — so the agent side is unchanged.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter()


def _origin_host() -> str:
    try:
        from opensquad.system_config import syscfg

        return str(syscfg.node_id() or "")
    except Exception:
        return ""


def _peer_from_token(request: Request):
    """The paired peer this request carries, or None."""
    token = request.headers.get("X-Node-Token", "")
    if not token:
        return None
    from opensquad import node_peers

    return node_peers.verify(token)


@router.post("/relay/subscribe")
async def relay_subscribe(request: Request, body: dict = Body(default={})):
    """Owner side: register a subscriber for a group's messages."""
    from app import relay
    from opensquad import node_peers

    peer = _peer_from_token(request)
    if not node_peers.has_scope(peer, "group:join"):
        raise HTTPException(status_code=401, detail="A paired token with group:join is required to subscribe")

    group_id = str((body or {}).get("group_id") or "").strip()
    callback_url = str((body or {}).get("callback_url") or "").strip()
    secret = str((body or {}).get("secret") or "")
    user_id = str((body or {}).get("user_id") or "").strip()
    agent_id = str((body or {}).get("agent_id") or "").strip()
    if not group_id or not callback_url:
        raise HTTPException(status_code=400, detail="group_id and callback_url are required")

    result = relay.subscribe(
        group_id,
        callback_url,
        secret,
        user_id=user_id,
        host=str(peer.get("name") or ""),
        # Stored so revoking this peer can drop the rows it created (the token check
        # alone leaves them behind, still being pushed to).
        peer_id=str(peer.get("id") or ""),
        # …and so a task-window event can be handed to that agent's control channel.
        agent_id=agent_id,
    )
    return {**result, "origin_host": _origin_host()}


@router.delete("/relay/subscribe")
async def relay_unsubscribe(request: Request, body: dict = Body(default={})):
    """Owner side: stop pushing a group (optionally to one subscriber)."""
    from app import relay
    from opensquad import node_peers

    peer = _peer_from_token(request)
    if not node_peers.has_scope(peer, "group:join"):
        raise HTTPException(status_code=401, detail="A paired token with group:join is required to unsubscribe")

    group_id = str((body or {}).get("group_id") or "").strip()
    callback_url = str((body or {}).get("callback_url") or "").strip()
    user_id = str((body or {}).get("user_id") or "").strip()
    if not group_id:
        raise HTTPException(status_code=400, detail="group_id is required")
    removed = relay.unsubscribe(group_id, callback_url, user_id)
    return {"ok": True, "removed": removed}


@router.post("/relay/deliver")
async def relay_deliver(request: Request, body: dict = Body(default={})):
    """Home side: receive a relayed group message and deliver it to the agent.

    The group lives on the *other* machine, so it is not in this gateway's group
    subscriptions; the message is delivered to the subscribed user directly, the
    same way a personal message reaches their socket. One hop only: a relayed
    message is delivered and stops here — never forwarded onward — so two
    gateways cannot echo.
    """
    from app import relay
    from app.websocket import manager

    envelope = body or {}
    group_id = str(envelope.get("group_id") or "").strip()
    if not group_id:
        raise HTTPException(status_code=400, detail="group_id is required")

    secret = request.headers.get("X-Relay-Secret", "")
    if not relay.verify_inbound(group_id, secret):
        raise HTTPException(status_code=401, detail="Unknown relay subscription")

    # The secret was minted for one user on this machine, so it may only deliver to
    # that user. Without this, any peer holding a valid secret could aim a push at
    # another local agent's socket. An unbound secret (an older subscription) is
    # accepted as before, so a rollout cannot reject legitimate pushes.
    target_user = str(envelope.get("target_user_id") or "")
    bound_user = relay.verify_inbound_user(group_id, secret)
    if bound_user and bound_user != target_user:
        raise HTTPException(status_code=403, detail="Relay secret is bound to a different user")

    if not relay.within_hop_limit(envelope):
        # Already relayed as far as allowed: deliver, do not forward.
        return {"ok": True, "delivered": False, "reason": "hop_limit"}

    origin_host = str(envelope.get("origin_host") or request.headers.get("X-Relay-Origin") or "")
    message = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    kind = str(envelope.get("type") or "message:relay")

    if kind == "task:relay":
        # A task-window event. It belongs on the agent's control channel — the very
        # place the owning gateway's local dispatch puts it — so the remote agent
        # behaves exactly as it does when the user talks in a local task window.
        event_id = str(message.get("event_id") or "")
        if event_id and relay.already_seen(origin_host, event_id):
            return {"ok": True, "delivered": False, "reason": "duplicate"}
        target_agent = str(envelope.get("target_agent_id") or "")
        if not target_agent:
            return {"ok": True, "delivered": False, "reason": "no_agent"}
        try:
            from app.ai_web.registry import registry as agent_registry

            delivered = await agent_registry.send_to_agent(
                target_agent, {**message, "relayed": True, "origin_host": origin_host}
            )
        except Exception as exc:  # noqa: BLE001 - a failed notification must not break the push
            logger.warning("[Relay] task delivery to agent %s failed: %s", target_agent, exc)
            delivered = False
        return {"ok": True, "delivered": bool(delivered)}

    message_id = str(message.get("id") or "")
    if message_id and relay.already_seen(origin_host, message_id):
        return {"ok": True, "delivered": False, "reason": "duplicate"}

    payload = {
        "type": "new_message",
        "data": message,
        "origin_host": origin_host,
        "relayed": True,
    }
    if target_user:
        # Deliver to the subscribing user directly: the agent's socket is here,
        # but the group itself is not (it belongs to the origin gateway).
        await manager.send_personal_message(target_user, payload)
        delivered = target_user in manager.active_connections
    else:
        # Fallback: a subscriber that did not pin a user gets the group broadcast,
        # which reaches anyone already subscribed to that group id locally.
        await manager.broadcast_to_group(group_id, payload)
        delivered = True
    return {"ok": True, "delivered": delivered}


@router.get("/relay/status")
async def relay_status():
    """Diagnostics: who we push to, and which subscriptions we accept inbound."""
    from app import relay

    return {**relay.status(), "origin_host": _origin_host()}
