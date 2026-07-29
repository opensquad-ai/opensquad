# Desktop App — Known Issues & Architecture Notes

This document captures the known limitations of the OpenSquad **desktop app**
(the Electron + PyInstaller bundle) and how its backend services are launched.
It is intended for users running the installer and for maintainers packaging
future releases.

**Supported desktop OS (packaged app):** Windows 10+, macOS **12 Monterey+**,
modern Linux (AppImage/deb). macOS builds pin `MACOSX_DEPLOYMENT_TARGET=12.0`
in CI; see [desktop_build.md](../doc_en/desktop_build.md) and
[RELEASING.md](../RELEASING.md).

## How the desktop app starts its services

The desktop app is a thin Electron shell around a single PyInstaller binary
(`run.exe` on Windows). OpenSquad normally runs **four** services
(`opensquad start`):

| Service | Port | What it does |
|---------|------|--------------|
| Gateway (FastAPI) | 9555 | Chat API, WebSocket, serves the frontend, file uploads |
| Plugin Registry | 9720 | Plugin store catalog |
| Frontend (Vite) | 5173 | Dev server (not needed in the packaged app — the gateway serves the built frontend) |
| Launcher | 9600 | Agent process manager + management API for the Agent Workstation |

The packaged app only needs **Gateway + Launcher** (the frontend is built into
the gateway, and the Plugin Registry is not yet wired into the desktop bundle).
Electron's `main.ts` spawns **two** instances of the same `run.exe`:

1. `run.exe` — the Gateway (default mode, no `--service` flag).
2. `run.exe --service launcher --mgmt-port 9600` — the Launcher.
   Agents with `ui.auto_start_on_boot: true` start automatically via
   `run.exe --service agent`. Plugin services still need the Agent Python
   runtime from the setup wizard (see issue #1 below).

`run.py` dispatches on `--service`:
- `gateway` (default) runs the FastAPI app.
- `launcher` loads the standalone `opensquad/launcher_main.py` by file path
  (the file was renamed from `launcher.py` to avoid shadowing the
  `opensquad/launcher/` package, which made PyInstaller mark
  `opensquad.launcher.process_manager` as invalid).
- `agent` runs `opensquad.agents_boot.main()` inside the frozen binary — this is
  how agents are spawned in packaged mode (an external Python cannot
  `import opensquad` because PyInstaller compiles `.py` into the PYZ archive).

## Where the desktop app stores data (workspace)

The desktop app separates two directories:

| Directory | Purpose | Example (Windows) |
|-----------|---------|-------------------|
| **App data** (`OPENSQUAD_APP_DATA`) | Fixed Electron userData; stores app prefs such as `desktop-workspace.json` | `%APPDATA%\OpenSquad\` (older builds used `nexuschat-pro\`); macOS: `~/Library/Application Support/OpenSquad/` |
| **Workspace** (`OPENSQUAD_USER_DATA`) | Chat DB, uploads, agents, logs — user data | Defaults to app data on first run; can be changed |

On first launch the workspace defaults to the app data dir. The gateway/launcher
init it: create `data/uploads`, `data/logs`, `agents/`, copy
`system_config.json` from the bundled template, and seed default model cards
and the pm/coder/qa agents.

**Mac: “register again every launch”.** The first-run wizard appears when
`chat.db` has no web user. Common causes: (1) Electron userData flipped between
`nexuschat-pro` and `OpenSquad` (fixed by top-level `productName` + legacy
recovery); (2) workspace pointed at a DMG/`/Volumes/…` path that disappears
after eject — pick a permanent path under **System Settings → Workspace**;
(3) registering with an `*@ai` email (agent-reserved) so registration never
closes. After a good register, confirm
`~/Library/Application Support/OpenSquad/gateway/backend/chat.db` exists
(or the legacy `nexuschat-pro` folder if recovered).

**Changing the workspace:** open **System Settings → Workspace**. You can create
a workspace at a custom path, switch to an existing one, or use **Migrate
Workspace Data** to copy/move data between directories. After switching, click
**Restart app** (or restart the desktop app manually) — the choice is persisted
in `<app-data>/desktop-workspace.json` and picked up on the next launch.

Builtin resources (plugins, skills, role/model/collab cards, agents, pymcp)
ship inside the bundle at `_internal/<name>/` and are read-only. User data
(chat.db, uploads, agent configs the user edits) lives in the writable
workspace directory. Uploads go to `<workspace>/data/uploads/` and are served
at `/uploads/…`.

## Known issues

### 1. Agents started via `run.exe --service agent` (packaged mode)

**Resolved.** The launcher starts agents with `run.exe --service agent
--agent-dir <dir> --port <n>` — the frozen binary runs
`opensquad.agents_boot.main()` directly, using the full `opensquad` package
from its PYZ archive. This avoids the previous issue where an external Python
could not `import opensquad`.

Plugin services (e.g. websearch, whisper) still require an external Python
interpreter. The desktop setup wizard downloads Python 3.11 embeddable to
`%LOCALAPPDATA%\OpenSquad\runtime\python311\` and writes a manifest at
`<app-data>/agent-runtime.json`. The launcher reads this to run plugin
`service/main.py` scripts. If the runtime is not installed, plugin services
fail to start (non-fatal — agents still work).

### 3. `Backend did not start in time` after NSIS install (Windows)

**Resolved in v0.4.6+.** When the app is installed under `Program Files`, the
PyInstaller bundle's `_internal/` directory is read-only. Import-time
`os.makedirs()` in multiple modules (`session_manager`, `bot_api.uploads`, …)
crashed the gateway before Electron's health check succeeded.

Fix: `_syscfg/_workspace.py` honours `OPENSQUAD_USER_DATA` at import time;
`bot_api.py` uses `syscfg.workspace_uploads_dir()` like `api.py` and
`main.py`. Run `scripts/smoke_frozen_gateway.py` before tagging a release.


The sidebar uses the [Lucide](https://lucide.dev) icon set (an open-source
Feather-style icon library). The OpenSquad logo is used for the app icon
(`.ico`/`.icns`) and branding assets — not for every sidebar entry. This is
the intended design, not a missing-asset bug.

## Not yet bundled

- **Plugin Registry (port 9720)** is not started by the desktop app. The
  Plugin Store page will not be populated. This service has a separate
  `plugins_db.json` bootstrapping issue (read-before-create) that is being
  tracked independently and will be addressed together with bundling.
