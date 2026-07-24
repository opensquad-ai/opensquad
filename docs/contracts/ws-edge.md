# Contract: Gateway / Launcher WebSocket Edge

> **Status:** Skeleton — fill during **P0** of [`../rust-hybrid-refactor.md`](../rust-hybrid-refactor.md).  
> **Sources:** `gateway/backend/app/ai_web/websocket.py`, `launcher_main.py` (`_start_launcher_ws_tunnel`), `gateway_adapter.py`  
> **Rule:** Frame type / `action` string names are frozen. Edge implementations may only **forward** opaque business payloads unless a field is listed as edge-owned.

## Endpoints (verify exact paths in P0)

| Role | URL (typical) | Direction |
|------|---------------|-----------|
| Agent register / duplex | Gateway WS under `/ai-web/…` (confirm path) | Agent ↔ Gateway |
| User AI-Web chat | Gateway WS `/ai-web/ws/{agentId}?token=…` (confirm) | Browser ↔ Gateway |
| Launcher admin tunnel | `ws://{gateway}/ai-ws/launcher` | Launcher → Gateway (outbound) |
| Group chat WS | `/ws` (existing ChatPro) | **Out of Rust Edge P2 scope** unless explicitly added |

---

## A. Agent ↔ Gateway

### A.1 Register (first frame)

**Client → Server**

```json
{
  "action": "register",
  "agent_id": "coder",
  "agent_name": "Coder",
  "agent_type": "general",
  "capabilities": [],
  "description": "",
  "node_id": "",
  "node_label": "",
  "node_secret": "<secret>"
}
```

| Rule | Behavior |
|------|----------|
| First message `action` ≠ `register` | error + close |
| Invalid `node_secret` | Unauthorized + close |
| Duplicate `agent_id` | Replace connection; old WS closed with code `4000` |

**Server → Client (success)**

```json
{
  "status": "registered",
  "message": "Agent coder registered successfully",
  "assigned_route": "/ai-web/chat/coder"
}
```

### A.2 Heartbeat

**Client → Server:** `{ "action": "heartbeat", "stats": { } }`  
**Server → Client:** `{ "action": "pong" }`

### A.3 Status

**Client → Server:**

```json
{
  "action": "status",
  "status": "busy",
  "session_id": "optional-session-id"
}
```

Edge owns busy flags in the connection registry; payload details beyond `status` / `session_id` are forwarded as today.

### A.4 Business frames (opaque to Edge)

List **type / action names** observed in production (fill in P0 from `gateway_adapter` + websocket handlers). Edge must not rename them.

| Name | Direction | Edge role |
|------|-----------|-----------|
| _(fill)_ e.g. stream / to_user / token_stats / … | Agent→User or User→Agent | forward |
| | | |

---

## B. User AI-Web chat WS

| Concern | Owner in P2 |
|---------|-------------|
| Accept + JWT/token check | Edge (or call Python auth helper) |
| Multi-tab connection map | Edge |
| History on connect | May call Python HTTP / existing session APIs |
| Chat commands / uploads related | Forward / HTTP to Python as today |

**P0 task:** Attach 3–5 sample frames (connect hello, user message, stream chunk, end task) with field names locked.

---

## C. Launcher admin tunnel (`/ai-ws/launcher`)

### C.1 Register

**Launcher → Gateway**

```json
{
  "type": "launcher_register",
  "node_id": "<id>",
  "node_label": "<label>",
  "node_secret": "<secret>"
}
```

### C.2 Keepalive

Either side may send: `{ "type": "keepalive" }`  
Current client interval ≈ **12s** (preserve).

### C.3 Admin RPC

**Gateway → Launcher**

```json
{
  "type": "admin_request",
  "req_id": "<uuid>",
  "method": "GET",
  "path": "/api/ping",
  "body": null
}
```

**Launcher → Gateway**

```json
{
  "type": "admin_response",
  "req_id": "<uuid>",
  "status": 200,
  "body": { }
}
```

| Rule | Behavior |
|------|----------|
| Launcher relays to **local** management HTTP | `http://127.0.0.1:{mgmt_port}{path}` |
| Under Rust hybrid | Relay to Rust `:9600` (never create proxy loops via sidecar) |
| Timeout | Align with current ~15s upstream |

### C.4 Non-RPC types

Ignore / no-op unknown `type` values without closing the socket (unless auth failed at register).

---

## D. Node HTTP (related)

| Method | Path | Role |
|--------|------|------|
| POST | `/api/ai-web/nodes/register` | Launcher → Gateway |
| _(heartbeat path)_ | confirm in P0 | Launcher → Gateway |
| GET | `/api/ai-web/nodes` | UI / admin |

Auth: `node_secret` header or body as implemented today — **record exact field**.

---

## E. Auth summary

| Channel | Secret |
|---------|--------|
| Agent WS register | `node_secret` in first JSON |
| Launcher tunnel register | `node_secret` in `launcher_register` |
| User WS | user token query/header (record) |
| Node HTTP | `X-Node-Secret` / query `node_secret` |

---

## P0 fill-in checklist

- [ ] Confirm exact WS URL paths from `main.py` route bindings  
- [ ] Golden JSON under `tests/contracts/goldens/ws/`  
- [ ] Complete business frame name table (A.4)  
- [ ] Document close codes used (`4000`, `4003`, `1013`, …)  
- [ ] Contract tests: bad secret, replace connection, admin_request roundtrip  

## Revision

| Date | Note |
|------|------|
| 2026-07-23 | Skeleton from websocket.py + launcher tunnel |
