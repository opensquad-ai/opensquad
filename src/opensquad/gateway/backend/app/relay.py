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
    clear_outbox,
    drop_outbox,
    due_outbox,
    enqueue_outbox,
    forget_outbound,
    new_secret,
    note_seen,
    outbox_entries,
    outbox_size,
    prune_outbox,
    record_outbox_attempt,
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


async def _push_envelope(client, sub: dict, envelope: dict, timeout: float) -> tuple[bool, str]:
    """One subscriber's push. ``(delivered, error)``; never raises.

    The caller builds the envelope so a failed push can be queued **exactly as it was
    attempted**: a retry must send the identical frame, or the receiving gateway
    cannot recognise it as the same message and would deliver it twice.
    """
    group_id = str(envelope.get("group_id") or "")
    url = _deliver_url(sub.get("callback_url", ""))
    try:
        resp = await client.post(
            url,
            json=envelope,
            headers={
                "X-Relay-Secret": sub.get("secret", ""),
                "X-Relay-Origin": str(envelope.get("origin_host") or ""),
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
            # anyone ("delivered": false) — that is the agent being offline, which is
            # precisely what the queue is for.
            if bool(body.get("delivered", True)):
                return True, ""
            return False, str(body.get("reason") or "receiver delivered to nobody")
        logger.warning("[Relay] push to %s returned %s for group %s", url, resp.status_code, group_id)
        return False, f"HTTP {resp.status_code}"
    except Exception as exc:
        logger.warning("[Relay] push to %s failed for group %s: %s", url, group_id, exc)
        return False, str(exc)


def _envelope_for(sub: dict, group_id: str, payload: dict, origin_host: str, kind: str) -> dict:
    """The frame one subscriber receives — carrying its own user (and agent) id."""
    return build_envelope(
        group_id,
        payload,
        origin_host,
        user_id=sub.get("user_id", ""),
        kind=kind,
        # A task event is delivered to the agent's control channel, so the receiving
        # gateway needs to know which agent it is for.
        target_agent_id=str(sub.get("agent_id") or "") if kind.startswith("task") else "",
    )


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
    counted failed.

    **A failure is queued, not dropped.** The group lives here, so a subscriber that
    did not hear a message has no way to fetch it (its own history is this machine's
    history) — a task event is not in any history at all. Queued frames are retried by
    :func:`_retry_loop` and flushed the moment the subscriber comes back.
    """
    subs = subscribers(group_id)
    if not subs:
        return {"ok": True, "delivered": 0, "failed": 0, "queued": 0}
    dedupe_id = str((payload or {}).get("id") or (payload or {}).get("event_id") or "")
    if dedupe_id and already_seen(origin_host, dedupe_id):
        return {"ok": True, "delivered": 0, "failed": 0, "queued": 0, "skipped": "duplicate"}

    from app.http_clients import get_local_http_client

    client = get_local_http_client()
    pairs = [(sub, _envelope_for(sub, group_id, payload, origin_host, kind)) for sub in subs]
    tasks = [asyncio.create_task(_push_envelope(client, sub, envelope, timeout)) for sub, envelope in pairs]
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
    failed = 0
    queued = 0
    for (sub, envelope), task in zip(pairs, tasks, strict=True):
        if task in pending:
            ok, error = False, "peer did not answer within the fan-out budget"
        else:
            try:
                ok, error = task.result()
            except Exception as exc:
                ok, error = False, str(exc)
        if ok:
            delivered += 1
            continue
        failed += 1
        if enqueue_outbox(
            group_id=group_id,
            envelope=envelope,
            callback_url=str(sub.get("callback_url") or ""),
            user_id=str(sub.get("user_id") or ""),
            error=error,
        ).get("ok"):
            queued += 1
    if queued:
        logger.warning(
            "[Relay] %d push(es) for group %s could not be delivered and are queued for retry",
            queued,
            group_id,
        )
    return {"ok": failed == 0, "delivered": delivered, "failed": failed, "queued": queued}


async def flush_outbox(
    *,
    group_id: str = "",
    callback_url: str = "",
    user_id: str = "",
    now: float | None = None,
    timeout: float = 8.0,
    budget: float = 5.0,
    limit: int = 50,
) -> dict:
    """Retry what is queued: on a tick, and the moment a subscriber (re)subscribes.

    An entry whose subscriber is gone — unsubscribed, or its peer was revoked — is
    dropped without a push: nobody is listening, and retrying would keep a dead
    peer's traffic forever.

    Bounded like the first attempt, so a slow peer costs the budget and not the
    caller (a subscribe request flushes the backlog before it answers).
    """
    from opensquad import relay_link

    # Expired entries are dropped here, not merely skipped: this is what stops a peer
    # that never comes back from leaving its stale frames in the file forever.
    relay_link.prune_outbox(now=now)
    entries = relay_link.due_outbox(now=now, limit=limit)
    if group_id:
        entries = [e for e in entries if str(e.get("group_id") or "") == str(group_id)]
    if callback_url:
        wanted = str(callback_url).rstrip("/")
        entries = [e for e in entries if str(e.get("callback_url") or "") == wanted]
    if user_id:
        entries = [e for e in entries if str(e.get("user_id") or "") == str(user_id)]
    if not entries:
        return {"ok": True, "delivered": 0, "failed": 0, "dropped": 0}

    from app.http_clients import get_local_http_client

    client = get_local_http_client()

    async def _retry_one(entry: dict) -> str:
        key = str(entry.get("key") or "")
        live = [
            s
            for s in subscribers(str(entry.get("group_id") or ""))
            if str(s.get("callback_url") or "").rstrip("/") == str(entry.get("callback_url") or "")
            and str(s.get("user_id") or "") == str(entry.get("user_id") or "")
        ]
        if not live:
            relay_link.drop_outbox(key)
            return "dropped"
        ok, error = await _push_envelope(client, live[0], entry.get("envelope") or {}, timeout)
        if ok:
            relay_link.drop_outbox(key)
            return "delivered"
        relay_link.record_outbox_attempt(key, error, now=now)
        return "failed"

    # One task per subscriber, its entries in arrival order inside it. A machine that
    # comes back should receive what it missed in the order it was sent — pushing a
    # subscriber's backlog concurrently would shuffle it. Different subscribers still
    # run together, so one slow peer delays neither another peer nor the caller.
    series: dict[tuple[str, str], list[dict]] = {}
    for entry in entries:
        series.setdefault((str(entry.get("callback_url") or ""), str(entry.get("user_id") or "")), []).append(entry)

    async def _retry_series(queue: list[dict]) -> list[str]:
        outcomes = []
        for entry in queue:
            outcomes.append(await _retry_one(entry))
        return outcomes

    tasks = [asyncio.create_task(_retry_series(queue)) for queue in series.values()]
    done, pending = await asyncio.wait(tasks, timeout=max(0.01, float(budget)))
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    counts = {"delivered": 0, "failed": 0, "dropped": 0}
    for task in done:
        try:
            outcomes = list(task.result())
        except Exception:
            outcomes = ["failed"]
        for outcome in outcomes:
            counts[outcome] = counts.get(outcome, 0) + 1
    # A cancelled series leaves its remaining entries queued and untouched: they are
    # retried on the next pass, which is the point of a queue.
    counts["failed"] += len(pending)
    return {"ok": counts["failed"] == 0, **counts}


# How often the queue is retried. Long enough to be cheap, short enough that a peer
# that comes back is served without a human doing anything.
RETRY_INTERVAL_S = 20.0

_retry_task: asyncio.Task | None = None


async def _retry_loop(interval: float = RETRY_INTERVAL_S, first_delay: float = 3.0) -> None:
    """Retry the queue forever, once the app has finished starting."""
    delay = first_delay
    while True:
        try:
            await asyncio.sleep(delay)
            res = await flush_outbox()
            if res.get("delivered") or res.get("failed") or res.get("dropped"):
                logger.info("[Relay] outbox retry: %s", res)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # one bad pass must not end the loop
            logger.debug("[Relay] outbox retry pass failed: %s", exc)
        delay = interval


def ensure_retry_loop(loop: asyncio.AbstractEventLoop | None = None) -> dict:
    """Start the retry loop once (idempotent, like the agent liveness sweeper)."""
    global _retry_task
    if _retry_task is not None and not _retry_task.done():
        return {"started": False, "running": True}
    try:
        running = loop or asyncio.get_running_loop()
    except RuntimeError:
        return {"started": False, "running": False}
    _retry_task = running.create_task(_retry_loop(), name="relay-outbox-retry")
    logger.info("[Relay] outbox retry loop started (every %.0fs)", RETRY_INTERVAL_S)
    return {"started": True, "running": True}


def retry_loop_running() -> bool:
    """Diagnostics/tests: is the retry loop alive?"""
    return _retry_task is not None and not _retry_task.done()


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
