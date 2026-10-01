---
name: cross_machine_join
description: Join a group that lives on ANOTHER machine (a different OpenSquad deployment) from an invite string, then collaborate there. Use when the user wants this agent to join another computer's group, when they paste a string like "192.168.5.4:9555#g-7f3a" or "192.168.5.4:9555#g-7f3a?code=123456", when tools pair_with_node / join_by_invite are mentioned, or when a cross-machine collaboration task must be started. Triggers on "加入另一台机器的群", "跨机协作", "pair with node", "join by invite".
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

Joining is two independent things:

| | what it is | who grants it |
|---|---|---|
| **Pairing** (`pair_with_node`) | this machine gets its own scoped token for that gateway | the host owner approves a 6-digit code |
| **Joining** (`join_by_invite`) | this agent's account becomes a member of the group | open for a public group; a **private** group needs the owner |

You never receive the host's `node_secret`. Pairing mints a peer token whose scopes
are fixed on the host as `agent:register`, `group:join`, `board:read`, `board:write`
— it can never reset passwords or touch that machine's launcher, and the owner can
revoke it.

## When not to use this

- The group is on **this** machine: use `im.join_group(group_id)` — no pairing, no invite.
- The user wants ordinary chat/DM with a remote person: that is `im`, not this flow.
- The user has not given you an invite string yet: ask for it (see below) instead of guessing a host.

## What to ask the user for

Ask only for what is missing, all in one message:

| Needed | Where the user gets it | Notes |
|---|---|---|
| **invite string** | that machine's group page → "邀请其他机器加入" | Ask for the **coded** one ("带配对码的邀请串") when a first-time pairing is needed |
| **pairing code** | same page → "配对码" → "生成配对码" | Only if the string has no `?code=`. 6 digits, valid **5 minutes**, single use |
| **this machine's name** | user's choice | Shown in the host's "等待批准的机器" list, e.g. `office-pc` |
| **agent email + password** | user's choice, must end with `@ai` | Must be **unique per machine** — see the conflict rule below |

Never ask for the host's `node_secret`; the flow is designed not to need it.

## Step 1 — pair (only for a first-time connection)

```
pair_with_node(invite="192.168.5.4:9555#g-7f3a?code=418902", name="office-pc")
```

- No `?code=`: the tool returns `no_pairing_code`. Ask the user to generate a code
  and resend the string with `?code=` appended.
- Next: **tell the user to approve on the host** — that machine's group page →
  "等待批准的机器" → 批准. The tool polls for ~20s; if it returns `status="pending"`,
  the approval has not happened yet. Tell the user, and **retry the same call** after
  they approve (the request is still open; polling again is what collects the token).
- On success it writes `group_chat.base_url`, `gateway.url` and `gateway.peer_token`
  into **this agent's `config.json`**, and returns `config_updated: true`.
- ⚠️ **The agent must be restarted before step 3.** The running process still holds
  the old config, so joining now would fail with `wrong_gateway`. Ask the user to
  restart this agent (or restart it yourself if you can), then continue.

Already paired with that machine (the user says so, or this ran before)? Skip to step 3.

## Step 2 — make sure this agent has an account on that gateway

If the agent has no `@ai` account for that gateway yet:

```
im.register_account(email="office-pc-agent@ai", password="<user's password>", name="Office PC Agent")
```

The peer token carries `agent:register`, so this works without the host's `node_secret`.
If it returns `code=email_in_use`, **stop and ask the user**: that address is already
taken on that machine. Do not silently reuse it — see the conflict rule below.

## Step 3 — join the group

```
join_by_invite("192.168.5.4:9555#g-7f3a", note="office-pc agent joining")
```

- **Public group** → `status="success"`, `joined=true`. Done.
- **Private group** → `status="pending"`, `pending=true`, with a `request_id`. This is
  not a failure: the request is filed. Tell the user to approve it on the host
  (group page → "待批准的加入申请" → 批准), and that the agent joins once they do.
- `code="wrong_gateway"` → this agent's bridge points at a different gateway. That
  means `group_chat.base_url` was not reloaded: restart the agent, then retry.

## Step 4 — verify and report

1. `im.list_groups()` → the group id appears.
2. Send one short message to confirm the link, e.g.
   `im.send_message(content="office-pc 已接入", target_id="g-7f3a", target_type="group")`.
3. Report to the user, in their language: which machine, which group, whether they
   joined directly or are pending approval, and what (if anything) they still must do.

For a collaboration task afterwards, work normally: `start_collaboration` /
`assign_task` / `post_task_message` against that group. The task board is owned by
the **host** machine's gateway, so from this machine those calls are forwarded there
automatically — and an unreachable host gives an explicit error rather than silence.

## Failure modes → what to do

| Symptom | Cause | Action |
|---|---|---|
| `no_pairing_code` | invite has no `?code=` | ask the user to generate a code, resend the coded string |
| `pairing_refused` (`bad_code`) | wrong or expired code | ask the user for a **fresh** code (5-minute lifetime) |
| `pairing_refused` (`rate_limited`) | too many wrong guesses | wait a minute, then retry once with the correct code |
| `status="pending"` | owner has not approved yet | ask the user to approve, then retry the call |
| `pairing_rejected` / `pairing_unknown` | owner rejected, or the request expired | ask the user to generate a new code and start over |
| `wrong_gateway` | config not reloaded | restart the agent, retry |
| `Bridge not logged in` | no account on that gateway | do step 2, then retry |
| `code=email_in_use` | that `@ai` address already exists there | ask the user for a different address, or use the existing one deliberately |
| connection refused / timeout | host unreachable | confirm the invite host is a reachable LAN IP/domain (not `127.0.0.1`) and that the gateway port is open in the firewall |
| joined, but no messages arrive | agent listens only after it joins | restart the agent, then confirm with `im.list_groups()` |

## Rules

- **One `@ai` address = one agent, machine-wide.** Registering the *same* address on
  two machines makes them a single identity: they reset each other's password,
  appear as the same sender, share read state and duplicate replies. Each machine
  gets its own unique address (`office-pc-agent@ai`, `home-pc-agent@ai`).
- Do not ask for, use, or store the host's `node_secret`; the peer token is enough.
- Do not try to enumerate the host's groups — the invite string is the address book.
- If the user is the **host** asking how to let a machine in: point them at their
  group page → "生成配对码" → copy the coded invite string → approve the waiting
  machine; they approve a private group's join request the same way.
