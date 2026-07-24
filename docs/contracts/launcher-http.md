# Contract: Launcher HTTP API (`:9600`)

> **Status:** Skeleton — fill during **P0** of [`../rust-hybrid-refactor.md`](../rust-hybrid-refactor.md).  
> **Source of truth (today):** `src/opensquad/launcher_main.py`  
> **Compatibility rule:** Path, status codes, and JSON field **names** must stay stable across Python / Rust implementations. Unknown fields are ignored by clients.

## Auth

| Mechanism | Where |
|-----------|--------|
| `Authorization: Bearer <launcher_token>` | Most management routes |
| None / local-only | Document per-route if applicable |

`launcher_token` comes from `system_config.json` (see `syscfg`).

## Legend

| Tag | Meaning |
|-----|---------|
| **P1-must** | Rust Launcher must implement (not proxy) by end of P1 |
| **sidecar** | May stay on Python sidecar (`:9601`) via reverse proxy in P1 |
| **spike** | Required for P1-spike |

---

## Core process API

| Method | Path | Tag | Request | Response (shape) | Notes |
|--------|------|-----|---------|------------------|-------|
| GET | `/api/ping` | spike, P1-must | — | `{ "status": "ok", "service": "launcher" }` | Health |
| GET | `/api/agents` | spike, P1-must | — | `{ "agents": [ ... ] }` or array — **record actual** | List + runtime status |
| POST | `/api/agents/{id}/start` | spike, P1-must | body? | `{ ... }` | Start agent process |
| POST | `/api/agents/{id}/stop` | spike, P1-must | body? | `{ ... }` | Stop agent process |
| POST | `/api/agents/{id}/restart` | P1-must | body? | `{ ... }` | |
| GET | `/api/agents/{id}/logs` | P1-must | `?lines=200` | | Ring-buffer tail |
| GET | `/api/agents/{id}/stats` | P1-must | — | | |
| POST | `/api/agents/create` | sidecar | | | |
| POST | `/api/agents/rescan` | sidecar | | | |
| POST | `/api/shutdown` | P1-must | | | Stop launcher |
| GET | `/api/runtime/list` | P1-must | | | |
| POST | `/api/runtime/cleanup` | P1-must | | | |

### Agents list — golden checklist (fill in P0)

Record one live response and lock these fields (mark required):

- [ ] Top-level type: object vs array  
- [ ] Per-agent: `name` / `agent_id` / `status` / `pid` / `config` keys  
- [ ] Error body shape on unknown id  

---

## Plugin services

| Method | Path | Tag | Notes |
|--------|------|-----|-------|
| GET | `/api/plugin-services` | P1-must | |
| GET | `/api/plugin-services/{name}/logs` | sidecar or P1-must | `?lines=` |
| POST | `/api/plugin-services/{name}/start` | P1-must | |
| POST | `/api/plugin-services/{name}/stop` | P1-must | |
| POST | `/api/plugin-services/{name}/restart` | P1-must | |
| PUT | `/api/plugin-services/{name}/auto-start` | sidecar | |

---

## Agent config / role / MCP (sidecar in P1)

| Method | Path | Tag |
|--------|------|-----|
| GET/PUT | `/api/agents/{id}/config` | sidecar |
| GET/PUT | `/api/agents/{id}/role` | sidecar |
| GET/PUT | `/api/agents/{id}/mcp` | sidecar |
| GET/PUT | `/api/agents/{id}/working-directory` | sidecar |
| PUT | `/api/agents/{id}/model-card` | sidecar |
| PUT | `/api/agents/{id}/role-prompt` | sidecar |

---

## Filesystem / session-changes (sidecar in P1)

| Method | Path | Tag |
|--------|------|-----|
| GET | `/api/agents/{id}/fs/list\|tree\|read\|changed` | sidecar |
| GET | `/api/agents/{id}/fs/session-changes` | sidecar |
| GET | `/api/agents/{id}/fs/session-diff` | sidecar |
| POST | `/api/agents/{id}/fs/write\|mkdir\|delete\|rename\|reveal\|open-terminal` | sidecar |
| POST | `/api/agents/{id}/fs/session-diffs` | sidecar |
| POST | `/api/agents/{id}/fs/session-changes/commit\|keep\|keep-all\|checkpoint\|revert` | sidecar |

---

## Plugins / skills / cards / MCP global (sidecar)

| Method | Path | Tag |
|--------|------|-----|
| GET | `/api/plugins` | sidecar |
| GET/PUT | `/api/plugins/{name}/config` | sidecar |
| GET | `/api/plugins/{name}/data` | sidecar |
| POST | `/api/plugins/{name}/action` | sidecar |
| POST | `/api/plugins/{name}/enable\|disable` | sidecar |
| POST | `/api/plugins/install-zip` | sidecar |
| GET | `/api/skills` | sidecar |
| GET | `/api/skills/{name}/source` | sidecar |
| GET/PUT | `/api/role-cards`… | sidecar |
| GET/PUT | `/api/collab-cards`… | sidecar |
| GET/PUT | `/api/model-cards`… | sidecar |
| GET/PUT | `/api/mcp/config` | sidecar |
| GET | `/api/mcp/global` | sidecar |
| POST | `/api/mcp/global/servers/{id}/enable\|disable` | sidecar |

---

## Workspace / system / sessions (sidecar unless noted)

| Method | Path | Tag |
|--------|------|-----|
| GET | `/api/workspace` | sidecar |
| GET | `/api/workspace/list` | sidecar |
| GET | `/api/workspace/detect-legacy` | sidecar |
| POST | `/api/workspace/create\|switch\|migrate` | sidecar |
| GET | `/api/workspace/migrate/status/{task_id}` | sidecar |
| GET/POST | `/api/system/pick-directory` | sidecar |
| GET | `/api/sessions/{agent}/list\|current` | sidecar |
| GET | `/api/task_watch_status` | sidecar |
| GET | `/api/services/manage` | sidecar |
| POST | `/api/resources/upload` | sidecar |
| POST | `/api/plugin-view-error` | sidecar |

---

## Gateway-facing proxy paths (not Launcher itself)

These are served by **Gateway** but must keep forwarding semantics when Edge moves to Rust (P2):

| Gateway path | Upstream |
|--------------|----------|
| `/api/launcher/{path}` | `{launcher_url}/{path}` |
| `/api/ai-web/admin/…` | Launcher via `_proxy_*` or WS tunnel |
| `/api/workspace/…` (Gateway) | Launcher `/api/workspace/…` |

---

## P0 fill-in checklist

- [ ] Capture real JSON for every **spike** and **P1-must** row  
- [ ] Store goldens under `tests/contracts/goldens/launcher/` (sanitize user paths)  
- [ ] Document error envelopes (`error` vs `detail`)  
- [ ] Document `Content-Type` and charset expectations  
- [ ] List which routes ignore auth in local-dev (if any)  

## Revision

| Date | Note |
|------|------|
| 2026-07-23 | Skeleton created from `launcher_main.py` route table |
