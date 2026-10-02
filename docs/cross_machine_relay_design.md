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
4. **Version handshake.** Still open. The relay is opt-in (a peer only subscribes
   if it knows the endpoint), and an older peer simply 404s the subscribe call —
   the join still succeeds and reports `relay: not_subscribed`. A positive
   capability exchange is still to be added.
5. **Failure semantics.** No queue, no retry: `fan_out` reports `{delivered,
   failed}` and logs; a failed push never becomes a local write. The joining agent
   sees `relay: not_subscribed` in the `join_group` result when the peer refused.
6. **Unsubscribe / revoke.** `DELETE /api/relay/subscribe` exists (by group, or by
   `callback_url`+`user_id`). Not yet wired to peer-token revocation or to
   `leave_group` — a revoke currently stops auth but leaves the stale subscription
   row until an explicit unsubscribe.
7. **Multi-hop.** Not transitive: cap is 1 hop, fail closed beyond it.

## What the first cut does **not** cover (still open)

- **Task-window messages and board-change notifications.** The relay carries
  group-chat `new_message` only. Task-window messages (`POST
  /collab-board/tasks/{id}/messages`) and `board_rev` change pings still travel
  over the home socket and do not cross machines yet.
- **DMs.** Out of scope (identity semantics differ from groups).
- **Revoke-driven unsubscribe** (see 6) and the **positive version handshake**
  (see 4).
- **Reconnect reconciliation.** If a gateway restarts, subscriptions persist on
  disk and are honoured again; but there is no periodic re-assert from the agent,
  so a subscription dropped by an owner (e.g. store wiped) is not self-healing.

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
3. (partial) `im.join_group(host=...)` now subscribes. The agent keeps talking only
   to home for receive; its sends still go direct via the peer bridge, which is
   fine (outbound already worked).
4. (open) Task-window messages and board-change pings over the relay; then DMs and
   multi-hop.
