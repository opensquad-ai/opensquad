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


async def _push_one(client, sub: dict, group_id: str, message: dict, origin_host: str, timeout: float) -> bool:
    """One subscriber's push. True on 200; never raises."""
    url = _deliver_url(sub.get("callback_url", ""))
    envelope = build_envelope(group_id, message, origin_host, user_id=sub.get("user_id", ""))
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
            return True
        logger.warning("[Relay] push to %s returned %s for group %s", url, resp.status_code, group_id)
        return False
    except Exception as exc:
        logger.warning("[Relay] push to %s failed for group %s: %s", url, group_id, exc)
        return False


async def fan_out(
    group_id: str, message: dict, origin_host: str = "", timeout: float = 8.0, budget: float = 5.0
) -> dict:
    """Push a group message to every subscriber, concurrently and under a budget.

    The pushes are independent, so they run together: doing them one by one added a
    dead peer's whole timeout to the *sender's* request (this is awaited from
    ``notify_new_message``, which sits in the send-message handler), N subscribers
    worst case. ``budget`` caps the entire fan-out; whatever is still in flight when
    it expires is cancelled and counted as failed.

    Never raises, and a failure to reach one subscriber is reported, not retried and
    never written locally — a relayed message that cannot be delivered must surface,
    not silently become a local one.
    """
    subs = subscribers(group_id)
    if not subs:
        return {"ok": True, "delivered": 0, "failed": 0}
    message_id = str((message or {}).get("id") or "")
    if message_id and already_seen(origin_host, message_id):
        return {"ok": True, "delivered": 0, "failed": 0, "skipped": "duplicate"}

    from app.http_clients import get_local_http_client

    client = get_local_http_client()
    tasks = [asyncio.create_task(_push_one(client, sub, group_id, message, origin_host, timeout)) for sub in subs]
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
