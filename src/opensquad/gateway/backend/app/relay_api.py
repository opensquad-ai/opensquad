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

    # A callback on the subscriber's own loopback address can never work: the owner pushes to it
    # and reaches itself. One of these sat in a live install from a hand-made subscribe call and
    # filled the outbox with 401 retries (one per group message) until its TTL. Accept it — a local
    # test harness legitimately uses loopback — but say so, in the log and in the reply.
    loopback = callback_url
    loopback_note = ""
    if "127.0.0.1" in loopback or "localhost" in loopback or "[::1]" in loopback:
        loopback_note = (
            "callback_url is a loopback address, so pushes to it can only ever fail. "
            "Send the address this machine is reachable at from the owner instead."
        )
        logger.warning("[Relay] subscribe for group %s used a loopback callback (%s)", group_id, callback_url)

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
    # (Re)subscribing is also "I am back": anything the owner could not deliver while
    # this machine was away is pushed now, instead of waiting for the retry tick.
    backfilled = {"delivered": 0, "failed": 0, "dropped": 0}
    try:
        backfilled = await relay.flush_outbox(
            group_id=group_id, callback_url=callback_url, user_id=user_id, timeout=6.0, budget=4.0
        )
    except Exception as exc:  # a failed backfill must not fail the subscription
        logger.warning("[Relay] backfill after subscribe failed: %s", exc)
    return {
        **result,
        "origin_host": _origin_host(),
        "backfilled": int(backfilled.get("delivered") or 0),
        "backfill_failed": int(backfilled.get("failed") or 0),
        **({"note": loopback_note} if loopback_note else {}),
    }


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


def _origin_base_for(group_id: str) -> str:
    """The gateway that owns ``group_id``'s uploads, as recorded when we subscribed."""
    try:
        from opensquad import peer_bridge, relay_link

        host = relay_link.outbound_host(group_id)
        if not host:
            return ""
        peer = peer_bridge.find_peer(host) or {}
        return str(peer.get("base_url") or "").rstrip("/")
    except Exception:
        return ""


def _with_origin_uploads(message: dict, base: str) -> dict:
    """Point a relayed message's upload references at the machine that holds them.

    Attachments and images reference ``/uploads/...`` on the **origin** gateway. Left
    relative they resolve against this machine's gateway, where the file does not
    exist — so a picture or a document sent from the other machine arrives broken.
    """
    if not base or not isinstance(message, dict):
        return message

    def _fix(value: object) -> object:
        url = str(value or "")
        if url.startswith("/") and not url.startswith("//"):
            return f"{base}{url}"
        return value

    out = dict(message)
    attachments = out.get("attachments")
    if isinstance(attachments, list):
        out["attachments"] = [
            ({**att, "url": _fix(att.get("url"))} if isinstance(att, dict) else att) for att in attachments
        ]
    images = out.get("images")
    if isinstance(images, list):
        out["images"] = [_fix(img) for img in images]
    return out


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
        # Peek, do not record: the owner retries anything it was told was not delivered, and
        # recording here burned the dedup key *before* the agent could be reached — so a push
        # that missed once (the agent's control socket was briefly absent, e.g. just after a
        # restart) came back "duplicate" on every retry, the event never arrived, and the outbox
        # retried it forever. A live cross-machine run showed exactly that.
        if event_id and relay.already_seen(origin_host, event_id, record=False):
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
        if delivered:
            relay.note_seen(origin_host, event_id)
        else:
            # The agent is not on its control channel right now. Say so (delivered: False) and
            # leave the key unrecorded: the owner's retry is the only thing that can heal this,
            # and it needs the frame to still look new. `reason` names the real cause, which
            # "duplicate" used to hide.
            logger.info("[Relay] task event %s for agent %s was not delivered yet", event_id, target_agent)
        return {"ok": True, "delivered": bool(delivered), "reason": "" if delivered else "agent_offline"}

    message_id = str(message.get("id") or "")
    # The same rule as the task event above: peek, and record only once the message is on its way.
    # Recording here meant a group message that missed the user's socket once — a reconnect, a
    # gateway restart — came back "duplicate" on every retry the owner made: lost, while the outbox
    # kept retrying it until its TTL.
    if message_id and relay.already_seen(origin_host, message_id, record=False):
        return {"ok": True, "delivered": False, "reason": "duplicate"}

    # The files stay on the machine that owns the group: make the references absolute
    # so they resolve there rather than against this gateway.
    message = _with_origin_uploads(message, _origin_base_for(group_id))

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
    if delivered and message_id:
        relay.note_seen(origin_host, message_id)
    else:
        logger.info("[Relay] group message %s was not delivered yet (%s offline)", message_id, target_user or "group")
    return {"ok": True, "delivered": delivered, "reason": "" if delivered else "user_offline"}


@router.get("/relay/status")
async def relay_status():
    """Diagnostics: who we push to, and which subscriptions we accept inbound."""
    from app import relay

    return {**relay.status(), "origin_host": _origin_host()}
