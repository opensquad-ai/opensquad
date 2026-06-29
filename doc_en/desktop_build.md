# Building the OpenSquad Desktop App

> The OpenSquad gateway ships as a desktop application (codename **NexusChat
> Pro**) built with **Electron + Vite + PyInstaller**. This document is the
> full how-to: from a one-command dev mode to multi-platform production
> installers and how the CI build pipeline fits in.
>
> For installing/running the **already-built** app, see
> [deployment_guide.md](deployment_guide.md). This file is about **building
> the app from source**.

---

## TL;DR

```bash
# Dev mode (frontend + Electron, expects opensquad start in another terminal)
cd src/opensquad/gateway/nexuschat-pro
npm install
npm run electron:dev

# Build an installer for the current platform
npm run electron:build
# → build/release/   (Windows .exe, macOS .dmg, Linux .AppImage / .deb)

# Build a specific platform
npm run electron:win     # Windows .exe (NSIS + portable)
npm run electron:mac     # macOS .dmg + .zip (x64 + arm64)
npm run electron:linux   # Linux .AppImage + .deb
```

The build pipeline has **two stages**: a Python backend (PyInstaller) and the
Electron wrapper. Both need to succeed; the Electron stage glues the right
backend into the right installer per OS.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  src/opensquad/gateway/nexuschat-pro/   (Electron + React UI)   │
│  ────────────────────────────────────────────────────────────  │
│   electron/         ← main process, preload, tray menus (.ts)  │
│   src/              ← React UI (Vite, TypeScript)              │
│   scripts/          ← compile-electron.mjs, dev-electron-live  │
│   assets/           ← icon.png / .ico / .icns / tray.png       │
│   package.json      ← npm scripts + electron-builder config    │
└─────────────────────────────────────────────────────────────────┘
                              │ bundles
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  opensquad/gateway/backend/   (Python backend → binary)        │
│  ────────────────────────────────────────────────────────────  │
│   opensquad_backend.spec   ← PyInstaller spec                   │
│   app/, routes/, …         ← FastAPI + the rest                │
└─────────────────────────────────────────────────────────────────┘
                              │ pyinstaller
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  build/   (NOT in git; created by build scripts)                │
│  ────────────────────────────────────────────────────────────  │
│   backend-win/run/run.exe + dependencies                        │
│   backend-mac/run/run   + dependencies                          │
│   backend-linux/run/run + dependencies                          │
│   release/  ← electron-builder output (installers)              │
│     *.exe  *.dmg  *.AppImage  *.deb  *.zip                      │
└─────────────────────────────────────────────────────────────────┘
```

The Electron app spawns the bundled backend binary as a child process and
loads the UI in a `BrowserWindow` that points at `http://127.0.0.1:<port>/`.
The port comes from `OPENSQUAD_PORT` (default `9510`) for the bundled
backend, or from `OPENSQUAD_FRONTEND_PORT` / `VITE_DEV_PORT` (default `5173`)
in dev mode. See `electron/main.ts` for the resolution rules.

---

## Prerequisites

| Tool       | Version            | Why |
|------------|--------------------|-----|
| **Node.js** | 20.x or 22.x       | Vite + Electron + TypeScript |
| **npm**    | bundled with Node  | `npm ci`, scripts |
| **Python** | 3.10+ (3.11 used in CI) | `pip install -e .`, PyInstaller, build_icons.py |
| **PyInstaller** | latest from pip | Backend → standalone binary |
| **Pillow + Playwright** | latest | `build_icons.py` (rasterize the SVG master into platform icons) |
| **Platform SDKs** | varies | macOS: Xcode CLT for `.icns`/notarization. Windows: optional, only for code signing. |

The `npm install` step is heavy (Electron is ~200 MB). The first run can take
a few minutes.

---

## Dev mode (three flavours)

All dev modes require a **separate backend** running on port `9510` (or the
value of `OPENSQUAD_PORT`). The most common setup:

```bash
# Terminal 1 — Python backend (any way you like)
uv run opensquad start         # or: python -m opensquad.cli start

# Terminal 2 — Electron in dev mode
cd src/opensquad/gateway/nexuschat-pro
npm install
npm run electron:dev
```

### `npm run electron:dev` — full build, then run

1. `vite build` — bundles the React UI to `dist/`.
2. `node scripts/compile-electron.mjs` — compiles `electron/*.ts` to
   `dist-electron/*.cjs` (TypeScript → CJS, plus a small require-path patch
   so Node can resolve `./foo` → `foo.cjs`).
3. `electron dist-electron/main.cjs` — launches the Electron shell.

The Electron window points at the **bundled** UI (no Vite dev server) and
talks to the externally running backend. Frontend changes still need a
`vite build` (or just rerun the script). Useful when you want to test the
exact production build behaviour.

### `npm run electron:dev:fast` — skip the Vite rebuild

Same as above but **skips** `vite build`. Use this when you're iterating on
`electron/*.ts` (main / preload / tray menus) only and you don't want to wait
for Vite.

### `npm run electron:dev:live` — HMR via Vite dev server

`node scripts/dev-electron-live.mjs` sets `ELECTRON_DEV=1`, which makes
the main process:

- **Skip spawning the bundled backend** (assumes the backend is already
  running on its own port).
- **Load the UI from the Vite dev server** at `http://127.0.0.1:5173/`
  (or `OPENSQUAD_FRONTEND_PORT`), giving you React HMR.

Use this when you're iterating on UI/UX and want instant feedback. The
backend still needs to be running in another terminal.

---

## Production build (one command, one platform)

```bash
cd src/opensquad/gateway/nexuschat-pro
npm run electron:build
# or
npm run electron:win       # Windows-only
npm run electron:mac       # macOS-only
npm run electron:linux     # Linux-only
```

Each of these runs:

1. `npm run icons:build` — runs `python ../../../scripts/build_icons.py`,
   which rasterizes `assets/logo-source.svg` into `icon.png` / `icon@2x.png`
   / `icon.ico` / `icon.icns` / `tray.png`. Requires Pillow + Playwright +
   Chromium.
2. `npm run build` — `vite build`, bundles the React UI to `dist/`.
3. `node scripts/compile-electron.mjs` — TypeScript → CJS (see above).
4. `electron-builder` — packages everything into the platform installer(s).

### Build outputs

| Platform | Command | Outputs (in `build/release/`) |
|----------|---------|------------------------------|
| Windows  | `electron:win`  | `*-setup.exe` (NSIS installer), `*portable.exe` (no install) |
| macOS    | `electron:mac`  | `*.dmg` and `*.zip` for **both** x64 and arm64 |
| Linux    | `electron:linux`| `*.AppImage`, `*.deb` |
| All three (current OS only) | `electron:build` | whatever the current platform builds |

The output directory is `build/release/` at the **project root**, **not**
inside the frontend folder (see `package.json` → `build.directories.output`
= `../../../build/release`).

### Prerequisites for the **PyInstaller backend step**

The `electron-builder` step expects `build/backend-<os>/run/` to already
contain the bundled Python backend for the target OS. The frontend build
**does not** produce it — you need a separate PyInstaller step first.

Two ways to populate `build/backend-<os>/`:

#### Option A — per-OS build script (local)

```bash
# Windows
scripts\build_backend.bat
# macOS / Linux
bash scripts/build_backend.sh
```

Both scripts call PyInstaller with `opensquad/gateway/backend/opensquad_backend.spec`
and write to `build/backend-<win|mac|linux}/run/`.

#### Option B — let CI do it (recommended for releases)

`build-desktop.yml` runs the PyInstaller step in parallel for all three
OSes on `push` of a `v*` tag, then assembles the Electron installers on
matching runners. See [CI / release pipeline](#ci--release-pipeline) below.

---

## CI / release pipeline

`.github/workflows/build-desktop.yml` is the canonical way to produce
release installers. Two paths trigger it:

- **Tag push**: `git tag -a v0.X.Y && git push origin v0.X.Y` — runs the
  full multi-platform build and creates a GitHub Release with the
  installers attached.
- **Manual**: `Actions → Build Desktop App → Run workflow` — useful for
  testing the build without a release.

Three stages:

1. **`build-backend`** (3-way matrix) — install Python deps, build icons,
   `npm ci && npm run build` (so PyInstaller can bundle the UI), run
   PyInstaller with `opensquad_backend.spec`, upload `backend-{win,mac,linux}`
   artifact.
2. **`build-electron`** (3-way matrix, `needs: build-backend`) — download
   the matching backend artifact, `npm ci`, run `npm run electron:{win,mac,linux}`,
   upload the resulting `build/release/*.{exe,dmg,AppImage,deb}` as
   `release-<os>` artifacts (retained 7 days).
3. **`create-release`** (only on tag push) — downloads the three
   `release-*` artifacts and attaches them to a GitHub Release with
   auto-generated notes.

The full pipeline takes **~25–35 minutes** for a clean run on a fresh
tag (3 backend builds in parallel + 3 electron builds in parallel + release).

### Verifying a desktop build

After the workflow finishes:

1. `https://github.com/opensquad-ai/opensquad/releases/tag/v0.X.Y` — the
   GitHub Release page should have platform-specific installers attached.
2. The **artifacts tab** of the workflow run also has them (if you ran
   via `workflow_dispatch` and didn't create a release).
3. Smoke test: download the installer for your platform, install, launch.
   The app should open to the gateway UI, spawn its bundled backend
   (visible in the system tray), and the backend port should be reachable
   on `127.0.0.1:9510/health`.

---

## Common pitfalls

### `electron:dev` white-screens on first run

- The Vite build succeeded but the bundled backend isn't running. Start
  it in another terminal (`uv run opensquad start`). Look at the
  Electron DevTools console — if you see `ECONNREFUSED 127.0.0.1:9510`,
  that's the cause.
- The dev variant you want is `electron:dev:live` for HMR, or
  `electron:dev` if you want production-bundle behaviour.

### `electron:build` complains about missing icons

- `npm run icons:build` failed (Pillow / Playwright not installed, or
  Chromium download blocked). Run it manually to see the error:
  `python scripts/build_icons.py`.
- Re-run the `electron:build` script — `icons:build` is its first step.

### `electron:build` for a non-native platform

- You **cannot** produce a Windows `.exe` from macOS or Linux (or vice
  versa) without Wine / a Windows VM. The matrix in `build-desktop.yml`
  exists exactly because of this. For local builds, stick to the variant
  matching your host OS.

### `electron-builder` complains about `extraResources`

- The `build/backend-<os>/run/` directory doesn't exist or is empty.
  Either run the matching `build_backend.{sh,bat}` first, or download the
  artifact from a `build-desktop.yml` run that completed the
  `build-backend` stage.

### `appimage` won't run on Linux

- `chmod +x <file>.AppImage` first, then run it.
- Some FUSE-less environments need `--appimage-extract-and-run` as a
  fallback. Or use the `.deb` instead.

### Code signing (macOS / Windows)

- **Not configured** in this repo. macOS builds will be unsigned (Gatekeeper
  warning on first launch, right-click → Open to bypass). Windows builds
  are similarly unsigned. Adding signing is a project-level decision;
  see [RELEASING.md](../RELEASING.md) for the implications.

### Output is in `build/release/` at the **project root**, not in `nexuschat-pro/`

- Confusing on first look — `package.json` →
  `build.directories.output = "../../../build/release"`. Three `..`s
  because `nexuschat-pro/` is three directories deep from the project
  root. The `build/` directory is in `.gitignore` and is **not** cleaned
  by the npm scripts; remove it manually with `rm -rf build/` if you
  want a fresh slate.

---

## What this file does NOT cover

- **Backend install / first-launch wizard / web UI** →
  [deployment_guide.md](deployment_guide.md) and
  [getting_started.md](getting_started.md).
- **Branch model and version policy** → [BRANCHING.md](../BRANCHING.md).
- **End-to-end release process (when does a tag get cut, what runs)** →
  [RELEASING.md](../RELEASING.md).
- **Plugin development** → [PLUGIN_DEVELOPMENT.md](PLUGIN_DEVELOPMENT.md)
  (covered in `doc_cn/` only at the moment; ask in an issue if you need
  an English version).
