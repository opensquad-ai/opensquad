"""Gateway side of the group relay: push a group's messages to subscribers.

The store and the loop-protection rules live in ``opensquad.relay_link`` (shared
with the agent process, which writes the other half). This module is only the
gateway's async half — the HTTP push itself — plus re-exports so callers can say
``from app import relay`` and reach everything.

See ``docs/cross_machine_relay_design.md`` for the shape and the one-hop rule.
"""

from __future__ import annotations

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
    within_hop_limit,
)

logger = logging.getLogger(__name__)


def _deliver_url(callback_url: str) -> str:
    base = str(callback_url or "").rstrip("/")
    if base.endswith("/api/relay/deliver"):
        return base
    return f"{base}/api/relay/deliver"


async def fan_out(group_id: str, message: dict, origin_host: str = "", timeout: float = 8.0) -> dict:
    """Push a group message to every subscriber. Never raises.

    A failure to reach one subscriber is reported, not retried and never written
    locally — a relayed message that cannot be delivered must surface, not
    silently become a local one.
    """
    subs = subscribers(group_id)
    if not subs:
        return {"ok": True, "delivered": 0, "failed": 0}
    message_id = str((message or {}).get("id") or "")
    if message_id and already_seen(origin_host, message_id):
        return {"ok": True, "delivered": 0, "failed": 0, "skipped": "duplicate"}

    from app.http_clients import get_local_http_client

    client = get_local_http_client()
    delivered = 0
    failed = 0
    for sub in subs:
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
                delivered += 1
            else:
                failed += 1
                logger.warning("[Relay] push to %s returned %s for group %s", url, resp.status_code, group_id)
        except Exception as exc:
            failed += 1
            logger.warning("[Relay] push to %s failed for group %s: %s", url, group_id, exc)
    return {"ok": failed == 0, "delivered": delivered, "failed": failed}
