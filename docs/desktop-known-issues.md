# Desktop App — Known Issues & Architecture Notes

This document captures the known limitations of the OpenSquad **desktop app**
(the Electron + PyInstaller bundle) and how its backend services are launched.
It is intended for users running the installer and for maintainers packaging
future releases.

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
2. `run.exe --service launcher --mgmt-port 9600 --no-auto-start --no-services` —
   the Launcher.

`run.py` dispatches on `--service`: `gateway` (the default) runs the FastAPI
app as before; `launcher` loads the standalone `opensquad/launcher.py` by file
path (it is shadowed by the `opensquad/launcher/` package, so a normal
`import opensquad.launcher` cannot reach `main()`) and calls its `main()`.

## Known issues

### 1. Images uploaded in dev mode don't appear in the desktop app

**Symptom:** chat messages sent during `opensquad start` (dev mode) show a
broken/torn image placeholder in the desktop app.

**Cause:** the two modes store uploads in different directories:
- Dev mode: `<workspace>/data/uploads/`
- Packaged mode: `<userData>/uploads/` (e.g.
  `%APPDATA%\NexusChat Pro\uploads\` on Windows)

The packaged app reads only from its `userData/uploads/` directory, so files
uploaded during dev runs are not visible.

**Workaround:** re-upload the image inside the desktop app. New uploads work
correctly — the directory is created on startup and served at `/uploads/`.
There is no data-loss bug; only the path differs between modes.

### 2. Agents cannot be started from the Agent Workstation UI (packaged mode)

**Symptom:** the Agent Workstation page now loads and **lists** agents
configurations correctly (the "Launcher is not running" error is gone), but
clicking *Start* on an agent does not launch an agent process.

**Cause:** the launcher starts agents with `sys.executable -m
opensquad.agents_boot …`. In a PyInstaller bundle `sys.executable` **is** the
frozen `run.exe`, which does not honor `-m <module>`. The same applies to
plugin services (e.g. `external_api`, `feishu`), which is why the desktop app
launches the launcher with `--no-services` to suppress their auto-start.

**What works today:** listing agents, reading/writing agent configs, role
cards, model cards, and MCP configs — everything the management API exposes
without spawning a child process.

**What doesn't:** starting an agent process or a plugin service from inside
the packaged app. To run agents, use `opensquad start` from a Python
environment (the dev/source layout). A frozen agent entry-point is planned for
a future release.

### 3. Sidebar icons are not OpenSquad-specific (by design)

The sidebar uses the [Lucide](https://lucide.dev) icon set (an open-source
Feather-style icon library). The OpenSquad logo is used for the app icon
(`.ico`/`.icns`) and branding assets — not for every sidebar entry. This is
the intended design, not a missing-asset bug.

## Not yet bundled

- **Plugin Registry (port 9720)** is not started by the desktop app. The
  Plugin Store page will not be populated. This service has a separate
  `plugins_db.json` bootstrapping issue (read-before-create) that is being
  tracked independently and will be addressed together with bundling.
