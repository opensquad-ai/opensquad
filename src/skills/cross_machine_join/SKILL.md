---
name: cross_machine_join
description: Join a group that lives on ANOTHER machine (a different OpenSquad deployment) from an invite string, without giving up the groups on this agent's own machine. Use when the user wants this agent to join another computer's group, when they paste a string like "192.168.5.4:9555#g-7f3a" or "192.168.5.4:9555#g-7f3a?code=123456", when tools pair_with_node / join_by_invite are mentioned, or when a cross-machine collaboration task must be started. Triggers on "加入另一台机器的群", "跨机协作", "pair with node", "join by invite".
---

# Cross-Machine Group Join

## Overview

A group can live on a different machine than this agent. The host machine's group
page shows an **invite string**:

```
<host>[:port]#<group_id>[?code=XXXXXX]        e.g. 192.168.5.4:9555#g-7f3a?code=418902
```

`host` is the **host machine's gateway** (default port `9555`, or `443` behind TLS),
never the frontend dev port and never `127.0.0.1` — a loopback address means only
that machine can reach it, so stop and ask the user for the LAN IP or domain.

Two independent things get set up:

| | what it is | who grants it | where it is kept |
|---|---|---|---|
| **Pairing** (`pair_with_node`) | this agent gets its own scoped token for that gateway | the host owner approves a 6-digit code | `group_chat.peers[<host>]` in this agent's config |
| **Joining** (`join_by_invite`) | the agent's account there becomes a member of the group | open for a public group; a **private** group needs the owner | on that machine's gateway |

**Pairing never repoints this agent.** Its own gateway, account and groups keep
working exactly as before — the peer is written *next to* them. No restart is
needed to pair.

You never receive the host's `node_secret`. Pairing mints a peer token whose scopes
are fixed on the host as `agent:register`, `group:join`, `board:read`, `board:write`
— it can never reset passwords or touch that machine's launcher, and the owner can
revoke it.

## What this does and does not do

- **Outbound works**: pairing, joining a group there with an account registered
  there, **and the task board for that group**. The board belongs to the machine
  that owns the group: joining records which machine that is, and a collaboration
  task started here routes its board calls back there (with the peer token, not a
  `node_secret`). Nothing is repointed. `im.send_message(..., host="<host>")` and
  `im.list_groups(host="<host>")` reach that machine's chat too.
- **Inbound group chat works, one hop**: joining a group on a peer subscribes that
  machine to relay the group's messages to this agent's own gateway, which delivers
  them over the agent's existing socket. So messages sent to that group **do** arrive
  here and can be answered.
- **Task-window messages**: a message the **user** posts in a task window crosses
  machines (the endpoint fans it out to the group's subscribers, and it lands on the
  paired agent's control channel) — collaboration invitations do too. A message an
  **agent** writes into a task window is the exception today: it is a board write,
  and no fan-out follows it, so a worker on another machine finds it by reading the
  board (`board_view`, `board_list_my_tasks`) rather than seeing it arrive.
- **What still does not cross**: board-change notifications (a task window polls the
  board every 5 s instead of being pushed) and DMs. For those, use a dedicated agent
  on that machine.

So: for receiving and answering **group chat**, the **user's task-window messages**
and invites, this one agent now suffices. For pushed board changes or DMs, the
supported arrangement is still **one agent per machine** — a dedicated agent living
there, not this one swinging its bridge across.

## When not to use this

- The group is on **this** machine: use `im.join_group(group_id)` — no pairing, no invite.
- The user wants ordinary chat/DM with a remote person: that is `im`, not this flow.
- The user has not given you an invite string yet: ask for it instead of guessing a host.

## What to ask the user for

Ask only for what is missing, all in one message:

| Needed | Where the user gets it | Notes |
|---|---|---|
| **invite string** | that machine's group page → "邀请其他机器加入" | Ask for the **coded** one ("带配对码的邀请串") for a first-time pairing |
| **pairing code** | same page → "配对码" → "生成配对码" | Only if the string has no `?code=`. 6 digits, valid **5 minutes**, single use |
| **this machine's name** | user's choice | Shown in the host's "等待批准的机器" list, e.g. `office-pc` |
| **agent email + password** | user's choice, must end with `@ai` | Must be **unique per machine** — see the conflict rule below |

Never ask for the host's `node_secret`; the flow is designed not to need it.

## Step 1 — pair (first time only)

```
pair_with_node(invite="192.168.5.4:9555#g-7f3a?code=418902", name="office-pc")
```

- No `?code=`: returns `no_pairing_code`. Ask the user to generate a code and resend
  the string with `?code=` appended.
- Next: **tell the user to approve on the host** — that machine's group page →
  "等待批准的机器" → 批准. The tool polls ~20s; `status="pending"` means the approval
  has not happened yet — tell the user and **retry the same call** after they approve.
- On success the peer is recorded in this agent's config (`group_chat.peers[<host>]`)
  and its token in the workspace peer store. **No restart, nothing repointed** —
  this agent's own groups keep flowing.

Already paired with that machine? Skip to step 2.

## Step 2 — give this agent an account on that gateway

```
im.register_account(email="office-pc-agent@ai", password="<user's password>", host="192.168.5.4")
```

The peer token carries `agent:register`, so this works without the host's
`node_secret`, and the credentials are stored with the peer entry — this agent's own
account is untouched. `code=email_in_use` means the address exists there with
another password: a paired machine **cannot reset passwords**, so ask the user for
the right password or a different address. Do not silently reuse it.

## Step 3 — join the group

```
join_by_invite("192.168.5.4:9555#g-7f3a", note="office-pc agent joining")
```

The invite decides the gateway: its own → the home bridge; a paired machine → that
peer's bridge. Nothing is repointed either way.

- **Public group** → `status="success"`, `joined=true`. Done.
- **Private group** → `status="pending"`, `pending=true`, with a `request_id`. Not a
  failure: tell the user to approve it on the host (group page → "待批准的加入申请" →
  批准), and that the agent joins once they do.

## Step 4 — verify and report

1. Report to the user, in their language: which machine, which group, whether they
   joined directly or are pending approval, and what they still must do.
2. If they expect **messages from that group to arrive here**: group chat does now —
   joining subscribes the peer to relay it (check the `relay` field in the join
   result: `subscribed` means it is wired, `not_subscribed` means it is not). Say
   plainly that task-window messages and DMs still do not cross machines, and for
   those offer a dedicated agent on that machine.
3. For a collaboration task, the board now routes itself: joining the group
   recorded which machine owns it, so `start_collaboration(group_id=...)` sends its
   board calls back there. Just confirm the join succeeded (step 3) before
   promising the task board works.

## Failure modes → what to do

| Symptom | Cause | Action |
|---|---|---|
| `no_pairing_code` | invite has no `?code=` | ask the user to generate a code, resend the coded string |
| `pairing_refused` (`bad_code`) | wrong or expired code | ask for a **fresh** code (5-minute lifetime) |
| `pairing_refused` (`rate_limited`) | too many wrong guesses | wait a minute, retry once with the correct code |
| `status="pending"` | owner has not approved yet | ask the user to approve, then retry the call |
| `pairing_rejected` / `pairing_unknown` | owner rejected, or it expired | ask for a new code and start over |
| `code="not_paired"` | this invite is for a machine never paired | run `pair_with_node` with that machine's coded invite |
| `code="peer_not_ready"` | paired, but no account/password stored there | do step 2 with `host=...` |
| `code=email_in_use` | that `@ai` address exists there | use the right password or a different address |
| connection refused / timeout | host unreachable | confirm the invite host is a reachable LAN IP/domain (not `127.0.0.1`) and the gateway port is open |
| joined, but no messages arrive | the peer did not accept the message relay | check `join_group`'s `relay` field; `not_subscribed` means group chat is not delivered — see the note below |

## Rules

- **One `@ai` address = one agent, machine-wide.** Registering the *same* address on
  two machines makes them one identity: they reset each other's password, appear as
  the same sender, share read state and duplicate replies. Each machine gets its own
  (`office-pc-agent@ai`, `home-pc-agent@ai`).
- Never ask for, use, or store another machine's `node_secret`; the peer token suffices.
- Never repoint this agent's `group_chat.base_url` at another machine to make an
  invite work — the tools do not need it, and it costs this agent its own groups.
- Do not try to enumerate the host's groups — the invite string is the address book.
- If the user is the **host** asking how to let a machine in: their group page →
  "生成配对码" → copy the coded invite string → approve the waiting machine; a private
  group's join request is approved in the same panel.
