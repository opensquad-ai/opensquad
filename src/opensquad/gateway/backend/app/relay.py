"""Gateway side of the group relay: push a group's messages to subscribers.

The store and the loop-protection rules live in ``opensquad.relay_link`` (shared
with the agent process, which writes the other half). This module is only the
gateway's async half — the HTTP push itself — plus re-exports so callers can say
``from app import relay`` and reach everything.

See ``docs/cross_machine_relay_design.md`` for the shape and the one-hop rule.
"""

from __future__ import annotations

import asyncio
import logging

from opensquad.relay_link import (  # noqa: F401  (re-exported for gateway callers)
    RELAY_MAX_HOPS,
    already_seen,
    build_envelope,
    forget_outbound,
    new_secret,
    remember_outbound,
    reset_seen,
    status,
    subscribe,
    subscribers,
    unsubscribe,
    verify_inbound,
    verify_inbound_user,
    within_hop_limit,
)

logger = logging.getLogger(__name__)


def _deliver_url(callback_url: str) -> str:
    base = str(callback_url or "").rstrip("/")
    if base.endswith("/api/relay/deliver"):
        return base
    return f"{base}/api/relay/deliver"


async def _push_one(
    client,
    sub: dict,
    group_id: str,
    payload: dict,
    origin_host: str,
    timeout: float,
    kind: str = "message:relay",
) -> bool:
    """One subscriber's push. True on 200; never raises."""
    url = _deliver_url(sub.get("callback_url", ""))
    envelope = build_envelope(
        group_id,
        payload,
        origin_host,
        user_id=sub.get("user_id", ""),
        kind=kind,
        # A task event is delivered to the agent's control channel, so the receiving
        # gateway needs to know which agent it is for.
        target_agent_id=str(sub.get("agent_id") or "") if kind.startswith("task") else "",
    )
    try:
        resp = await client.post(
            url,
            json=envelope,
            headers={
                "X-Relay-Secret": sub.get("secret", ""),
                "X-Relay-Origin": str(origin_host or ""),
            },
            timeout=timeout,
        )
        if resp.status_code == 200:
            body = {}
            try:
                body = resp.json() or {}
            except Exception:
                body = {}
            # The home gateway answers 200 even when it could not hand the frame to
            # anyone ("delivered": false) — reporting that as a success would hide an
            # agent that is simply offline.
            return bool(body.get("delivered", True))
        logger.warning("[Relay] push to %s returned %s for group %s", url, resp.status_code, group_id)
        return False
    except Exception as exc:
        logger.warning("[Relay] push to %s failed for group %s: %s", url, group_id, exc)
        return False


async def _fan_out_kind(
    group_id: str,
    payload: dict,
    origin_host: str,
    kind: str,
    *,
    timeout: float,
    budget: float,
) -> dict:
    """Push ``payload`` to every subscriber of ``group_id``, concurrently and bounded.

    ``kind`` decides what the subscriber is asked to do with it: ``message:relay``
    goes to the chat socket, ``task:relay`` to the agent's control channel (the same
    place the owning gateway dispatches a task message locally). Dedup uses the
    payload's ``id`` (a message) or ``event_id`` (a task event).

    The pushes are independent, so they run together: doing them one by one added a
    dead peer's whole timeout to the *sender's* request, N subscribers worst case.
    ``budget`` caps the whole fan-out; whatever is still in flight is cancelled and
    counted failed. Never raises, and a failure is reported, not retried and never
    written locally — a relayed event that cannot be delivered must surface, not
    silently become a local one.
    """
    subs = subscribers(group_id)
    if not subs:
        return {"ok": True, "delivered": 0, "failed": 0}
    dedupe_id = str((payload or {}).get("id") or (payload or {}).get("event_id") or "")
    if dedupe_id and already_seen(origin_host, dedupe_id):
        return {"ok": True, "delivered": 0, "failed": 0, "skipped": "duplicate"}

    from app.http_clients import get_local_http_client

    client = get_local_http_client()
    tasks = [asyncio.create_task(_push_one(client, sub, group_id, payload, origin_host, timeout, kind)) for sub in subs]
    done, pending = await asyncio.wait(tasks, timeout=max(0.01, float(budget)))
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
        logger.warning(
            "[Relay] %d push(es) for group %s still running after %.1fs — cancelled",
            len(pending),
            group_id,
            budget,
        )
    delivered = 0
    for task in done:
        try:
            delivered += 1 if task.result() else 0
        except Exception:
            pass
    failed = len(done) - delivered + len(pending)
    return {"ok": failed == 0, "delivered": delivered, "failed": failed}


async def fan_out(
    group_id: str, message: dict, origin_host: str = "", timeout: float = 8.0, budget: float = 5.0
) -> dict:
    """Push a group message to every subscriber (see :func:`_fan_out_kind`)."""
    return await _fan_out_kind(group_id, message, origin_host, "message:relay", timeout=timeout, budget=budget)


async def fan_out_task(
    group_id: str, chat: dict, origin_host: str = "", timeout: float = 8.0, budget: float = 5.0
) -> dict:
    """Push a task-window event to the group's subscribers.

    A task's conversation belongs to the machine that owns its group, but a
    participant can be an agent whose socket lives on a paired machine: the local
    agent registry cannot reach it, so the very payload that registry would send
    travels over the relay and is handed to that agent's control channel instead.
    """
    return await _fan_out_kind(group_id, chat, origin_host, "task:relay", timeout=timeout, budget=budget)
