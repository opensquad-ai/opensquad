# Cross-machine relay — design

> Audience: maintainers. Status: **first cut implemented** (handshake, subscribe,
> one-hop `message:relay`, loop guard). The design below is the shape it was built
> to; what is still open is marked.

## Problem

An agent has exactly one chat bridge, and that bridge holds exactly one
WebSocket to exactly one gateway (`opensquad.bridge.bridge`). That single socket
is how it *receives*: group messages, task-window messages and wakeups all arrive
there. Note it is the **group-chat socket** (`/ws`, authenticated with the agent's
own user JWT), not the node-secret-gated `/ai-ws/register` socket — the delivery
path is `notify_new_message → manager.broadcast_to_group` in `app/websocket.py`.

Pairing a second machine deliberately does **not** repoint that bridge — doing so
was the old bug that dropped the agent out of its own groups. The replacement
(`opensquad/peer_bridge.py`) gives each peer its **own outbound bridge**, no
WebSocket. So without the relay:

- **Outbound to a peer works**: register there, join a group there, drive that
  group's collaboration board there (see below), send chat there
  (`im.send_message(..., host=...)`).
- **Inbound from a peer does not exist**: nothing arrives from that gateway,
  because no socket is held.

The interim arrangement is therefore **one agent per machine**: if the user needs
an agent to *receive and answer* on machine B, run a dedicated agent on machine B.
That is honest, but it means two identities to manage, two token budgets, and no
single agent that is simply "in" a group spanning both machines.

## What already works (do not re-solve)

- **Pairing** mints a scoped peer token on the host (`agent:register`,
  `group:join`, `board:read`, `board:write`), stored in the workspace peer store
  and mirrored in `group_chat.peers[<host>]`. The host's `node_secret` never
  leaves the host.
- **Board ownership is resolved per group**, not per machine
  (`collab_board.board_owner`, `board_owners.json`, and
  `group_chat.peers[<host>].groups`). A board call about a group joined on a peer
  forwards there with the peer token. This is the same principle the relay
  generalizes: **the deployment that owns the group owns the data about it**.
- **Outbound chat to a peer** is `im.send_message(..., host=...)` /
  `im.list_groups(host=...)`.

## Target shape

Keep the invariant that an agent has **one** socket, to **its own** gateway.
Cross-machine traffic is relayed **gateway-to-gateway**, not agent-to-gateway.
The agent never learns a second address; it only ever talks to home.

```
   machine A (home of agent X)                 machine B (home of group G)
   ┌───────────────┐    gateway relay link    ┌───────────────┐
   │ agent X ──WS──│◄──────  mTLS / token  ──►│  gateway B    │
   │  gateway A    │                          │  group G      │
   └───────────────┘                          └───────────────┘
```

- Gateway A subscribes to the groups its agents have joined on B.
- A message in group G on B is pushed to A over the relay link; A delivers it to
  X over X's existing home socket, tagged with the origin.
- X replies with `im.send_message(..., host=...)` as today; the send goes to A,
  which relays it to B. (The agent's tool surface does not change.)

This is structurally identical to how the collaboration board is already routed
("the group's owner holds the data; everyone else forwards"), so the mental model
is not new — it is extended from the board to chat.

### Why not "agent holds N gateways"

Rejected: it moves addressing, identity and dedup complexity into every agent,
multiplies sockets per agent, and re-introduces the "which binding am I on"
confusion that the peer-bridge work just removed. The relay keeps that complexity
in the one place that already has an identity per machine — the gateway.

## Design questions (settled in the first cut)

1. **Envelope.** A distinct `message:relay` frame carrying `origin_host`,
   `group_id`, `target_user_id`, `relay_hops` and the original message under
   `data`. `target_user_id` exists because the group does not live on the receiving
   gateway, so delivery is to the subscribing *user*, not a group broadcast.
2. **Loop protection.** `relay_hops` starts at 1 and the cap is 1: a relayed
   message is delivered and **never forwarded again**. Plus an in-memory
   `(origin_host, message_id)` dedup with a 300 s TTL. A missing/unparsable hop
   count fails closed.
3. **Auth and scope.** Subscribe reuses the paired peer token with the existing
   `group:join` scope (no new credential, no `node_secret` on the wire). Deliver is
   authenticated with a **per-subscription secret the home gateway mints** and the
   owner only echoes back — so the owner holds no credential for the subscriber.
   That secret is **bound to the user it was minted for**: a push whose
   `target_user_id` is a different local user is refused (403) instead of delivered,
   so a peer holding a valid secret cannot aim a message at another local agent. An
   unbound secret (one recorded before the binding existed) is accepted as before.
4. **Version handshake.** Still open. The relay is opt-in (a peer only subscribes
   if it knows the endpoint), and an older peer simply 404s the subscribe call —
   the join still succeeds and reports `relay: not_subscribed`. A positive
   capability exchange is still to be added.
5. **Failure semantics: a failed push is queued, retried, and backfilled.** A push
   fails for reasons the peer cannot help — its gateway is restarting, its agent is
   offline, the LAN hiccuped. Counting the failure and dropping the frame loses the
   message for good: the group lives on the **owner** machine, so a subscriber that
   never heard a message has no way to fetch it (its history *is* the owner's
   history) and a task event is in no history at all. So:
   - **Queue.** A failed push is written to a gateway-owned, **on-disk** outbox
     (`relay_outbox.json`) holding the identical envelope that was attempted. On disk
     because the failures that matter include *this* machine restarting. Keyed by
     message as well as subscriber, so the same frame never stacks twice. A frame with
     no `id`/`event_id` is **not** queued — a retry could not be deduplicated, so it is
     logged and dropped rather than guessed at.
   - **Retry.** Backoff 20/40/80/160/240 s (capped), at most 6 attempts, and entries
     expire after 10 minutes; the waits sum to less than the TTL, so the last attempt
     always runs before the entry expires. The retry loop starts at gateway boot
     (`ensure_retry_loop`, idempotent, 20 s tick) and drops entries whose subscriber is
     gone (unsubscribed, or the peer was revoked) without pushing.
   - **Backfill.** `POST /api/relay/subscribe` *is* "I am back": the owner flushes that
     subscriber's backlog before answering, and reports `backfilled` /
     `backfill_failed`. This is what makes a machine that was off for an hour catch up
     in one round trip rather than one message per tick.
   - **Retry safety.** A push that arrived but whose answer was lost is retried too, so
     the receiving gateway must still recognise the second copy: hence
     **`DEDUP_TTL_S` (900 s) > `OUTBOX_TTL_S` (600 s)**, asserted by a test. The queue
     can never outlive the window that makes its retries idempotent.
   - **Bounded.** The queue is capped (500 entries, oldest dropped loudly), and both
     the first attempt and every retry run concurrently under one budget: a dead peer
     costs the budget, never the sender's request. `relay.status()["outbox"]` reports
     what is waiting and how old the oldest is.
   An older peer that does not subscribe simply never gets a queue entry (nothing was
   attempted), and the joining agent still sees `relay: not_subscribed`.
6. **Unsubscribe / revoke.** `DELETE /api/relay/subscribe` exists (by group, or by
   `callback_url`+`user_id`), and revoking a peer now cascades to the rows it created
   (`unsubscribe_peer`) — a revoked peer stops being pushed to instead of leaving the
   rows behind. `leave_group(host=...)` unsubscribes too.
7. **Multi-hop.** Not transitive: cap is 1 hop, fail closed beyond it.
8. **Store layout.** One file per writer: the **gateway** process owns
   `relay_subscribers.json` (who wants a group pushed, and the secret they minted)
   **and `relay_outbox.json`** (the pushes it could not deliver — the fan-out runs in
   the gateway), the **agent** process owns `relay_outbound.json` (the secrets this
   machine minted for subscriptions it created elsewhere). They used to share one
   file, which meant read-whole-file / write-whole-file from two processes with only a
   `threading.Lock` — a last-writer-wins window that silently dropped the other side
   and surfaced later as an unexplained 401 on the next push. Reads cross processes
   freely (every write is an atomic rename). A legacy combined `relay_links.json` is
   migrated once, under an `O_EXCL` lock file, and renamed to
   `relay_links.json.migrated`.

## What this cut does **not** cover (still open)

- **board_rev change pings.** A task window still polls the board (5 s) instead of
  being pushed; only task *messages* cross machines.
- **DMs.** Out of scope (identity semantics differ from groups).
- **The positive version handshake** (see 4). A peer that is too old to know
  `/api/relay/subscribe` is still detected by its 404.
- **Periodic re-assert.** A subscription dropped by an owner (store wiped, peer
  revoked and re-paired) is not self-healing while both machines keep running: the
  agent re-asserts at boot (`restore_peer_state`) and on re-join, not on a timer.
  Note this is about a *dropped subscription*, not lost messages — the queue covers
  those while it holds them.
- **An outage longer than the queue's 10 minutes.** Past the TTL the queued frames are
  given up (loudly). For a group that is acceptable — the owner's history is the
  recovery path — but a **task-window event has no history to fetch**, so a task event
  missed during a >10 min outage is gone. Closing that needs either a longer TTL with a
  matching dedupe window, or a task-event backlog the agent pulls on reconnect; until
  then, a task event is only as durable as the queue.

## Non-goals (for the first cut)

- Transitive (multi-hop) relaying.
- Relaying DMs across machines.
- Any change to the agent's tool surface. The relay is transparent to
  `im.send_message` / receive.

## Interim arrangement (still true for what is not covered above)

**One agent per machine.** An agent that must receive on machine B is a dedicated
agent living on machine B. The `cross_machine_join` skill states this plainly and
must keep stating it: the relay covers group chat, not yet every group feature.

## Migration path

1. (done) Pairing without repointing; per-group board ownership; `host=` on
   `im.send_message` / `im.list_groups`.
2. (done) Gateway relay link: subscribe handshake, one-hop `message:relay`,
   loop guard, per-subscription secret.
3. (done) `im.join_group(host=...)` subscribes, and a restart re-asserts it
   (`restore_peer_state`). The agent keeps talking only to home for receive.
4. (done) Task-window messages cross machines (`fan_out_task` → the agent's control
   channel), and a push that fails is **queued, retried, and backfilled** on
   re-subscribe (see 5).
5. (open) `board_rev` pings; DMs; the positive version handshake; periodic re-assert;
   durable task events beyond the queue's TTL; multi-hop.
