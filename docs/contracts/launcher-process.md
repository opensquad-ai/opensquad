# Contract: Launcher process behavior

> **Status:** Skeleton — complete in **P0.4** of [`../rust-hybrid-refactor.md`](../rust-hybrid-refactor.md).  
> **Source:** `src/opensquad/launcher/process_manager.py`, `launcher_main.py`, CLI start path.

Rust and Python launchers must match these behaviors for Agent and plugin-service children.

## Spawn

| Item | Record actual value in P0 |
|------|---------------------------|
| Executable / module entry | e.g. `python -m opensquad…` / frozen path |
| cwd | |
| Required env vars | `OPENSQUAD_*`, workspace, secrets, … |
| PATH sanitization | Strip PyInstaller `_internal` / `backend-*/run` markers |
| stdout/stderr | Piped → ring buffer |

## Stop / restart

| Item | Record |
|------|--------|
| Graceful signal (POSIX) | |
| Windows termination | Process tree policy |
| Grace timeout then force kill | seconds |
| Restart backoff | if any |

## Logging

| Item | Record |
|------|--------|
| Ring buffer max lines | |
| `/api/.../logs?lines=` clamp | |
| Log file locations under workspace | |

## Gateway registration

| Item | Record |
|------|--------|
| When `register_to_gateway` is true | HTTP register + WS tunnel |
| Heartbeat interval | |
| `launcher_url` construction | |

## Hybrid notes

- Under `OPENSQUAD_LAUNCHER_IMPL=rust`, **only Rust** may spawn/stop Agent processes.
- Sidecar must not start a second competing supervisor for the same agent id.

## P0 checklist

- [ ] Fill tables from code + one live spawn  
- [ ] Add integration test hooks for stop leaving zero orphan PIDs  
- [ ] Windows + Linux notes  

## Revision

| Date | Note |
|------|------|
| 2026-07-23 | Skeleton |
