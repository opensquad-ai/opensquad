# Cross-machine relay — design (proposal, not implemented)

> Audience: maintainers. Status: **design only, no code**. This is the target
> shape for cross-machine collaboration, written so the interim arrangement is
> not mistaken for the end state.

## Problem

An agent has exactly one chat bridge, and that bridge holds exactly one
WebSocket to exactly one gateway (`opensquad.bridge.bridge`, registered on
`/ai-ws/register`). That single socket is how it *receives*: group messages,
task-window messages and wakeups all arrive there.

Pairing a second machine deliberately does **not** repoint that bridge — doing so
was the old bug that dropped the agent out of its own groups. The replacement
(`opensquad/peer_bridge.py`) gives each peer its **own outbound bridge**, no
WebSocket. So today:

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

## Design questions to settle before coding

1. **Envelope.** Every relayed message needs `origin_host` (which gateway first
   received it), `group_id`, the original sender identity, and a relay hop count
   or message id for loop protection. Decide: reuse the existing group-message
   frame plus an envelope, or a distinct `message:relay` frame.
2. **Loop protection.** Two gateways that both relay could echo. A monotonic
   `relay_hops` cap (drop at 1) plus a dedup on `(origin_host, message_id)` at
   each hop is the minimum. Decide the cap and the dedup store (in-memory TTL vs
   persisted).
3. **Auth and scope.** The relay link is gateway-to-gateway. Reuse the peer token
   (add a `relay` scope, or reuse `group:join` + `board:*`), or mint a distinct
   gateway relay credential. Do **not** ship `node_secret` across the link.
4. **Version handshake.** Gateways must agree they both speak relay before
   forwarding, and fail closed (no relay, keep today's behaviour) when the peer
   is older. Decide the capability flag exchanged at link setup.
5. **Failure semantics.** A relayed send that cannot reach B must surface to the
   sender the way `BoardRemoteError` does for the board — loud, never silently
   written locally. Decide retry/queue policy (probably: no queue, report).
6. **Unsubscribe / revoke.** When a peer token is revoked or a group is left, A
   must stop subscribing to B for that group. Tie subscription lifetime to the
   peer token and the group membership.
7. **Multi-hop.** If X is in a group on B and B is itself paired with C, is
   relaying transitive? Recommended first cut: **no** — one hop only, fail closed
   beyond it.

## Non-goals (for the first cut)

- Transitive (multi-hop) relaying.
- Relaying DMs across machines (group messages first; DMs are a later question
  because they carry identity semantics the group path does not).
- Any change to the agent's tool surface. The relay is transparent to
  `im.send_message` / receive.

## Interim arrangement (until the relay ships)

**One agent per machine.** An agent that must receive on machine B is a dedicated
agent living on machine B. The `cross_machine_join` skill states this plainly and
must keep stating it: pairing gives outbound, not inbound.

## Migration path

1. (done) Pairing without repointing; per-group board ownership; `host=` on
   `im.send_message` / `im.list_groups`.
2. Gateway relay link: handshake, subscribe, one-hop `message:relay`, loop guard.
3. Route the existing `host=` sends through the relay (agent keeps talking only to
   home; the relay carries it).
4. Only then consider DMs and multi-hop.
