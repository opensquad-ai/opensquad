# Changelog

All notable changes to OpenSquad will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Releases


| Version                                                                | Date       | Compare to previous                                                                    | Release page                                                                     |
| ---------------------------------------------------------------------- | ---------- | -------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| [0.8.41]                                                               | 2026-08-10 | [0.8.40 → 0.8.41](https://github.com/opensquad-ai/opensquad/compare/v0.8.40...v0.8.41) | [GitHub Release](https://github.com/opensquad-ai/opensquad/releases/tag/v0.8.41) |
| [0.8.40]                                                               | 2026-08-10 | [0.8.30 → 0.8.40](https://github.com/opensquad-ai/opensquad/compare/v0.8.30...v0.8.40) | [GitHub Release](https://github.com/opensquad-ai/opensquad/releases/tag/v0.8.40) |
| [0.8.8]                                                                | 2026-08-04 | [0.8.7 → 0.8.8](https://github.com/opensquad-ai/opensquad/compare/v0.8.7...v0.8.8)     | [GitHub Release](https://github.com/opensquad-ai/opensquad/releases/tag/v0.8.8)  |
| [0.8.7]                                                                | 2026-08-03 | [0.8.6 → 0.8.7](https://github.com/opensquad-ai/opensquad/compare/v0.8.6...v0.8.7)     | [GitHub Release](https://github.com/opensquad-ai/opensquad/releases/tag/v0.8.7)  |
| [0.8.6]                                                                | 2026-08-03 | [0.8.5 → 0.8.6](https://github.com/opensquad-ai/opensquad/compare/v0.8.5...v0.8.6)     | [GitHub Release](https://github.com/opensquad-ai/opensquad/releases/tag/v0.8.6)  |
| [0.8.5]                                                                | 2026-07-29 | [0.8.2 → 0.8.5](https://github.com/opensquad-ai/opensquad/compare/v0.8.2...v0.8.5)     | [GitHub Release](https://github.com/opensquad-ai/opensquad/releases/tag/v0.8.5)  |
| [0.8.0]                                                                | 2026-07-22 | [0.6.0 → 0.8.0](https://github.com/opensquad-ai/opensquad/compare/v0.6.0...v0.8.0)     | [GitHub Release](https://github.com/opensquad-ai/opensquad/releases/tag/v0.8.0)  |
| [0.6.0]                                                                | 2026-07-15 | [0.5.1 → 0.6.0](https://github.com/opensquad-ai/opensquad/compare/v0.5.1...v0.6.0)     | [GitHub Release](https://github.com/opensquad-ai/opensquad/releases/tag/v0.6.0)  |
| [0.5.1]                                                                | 2026-07-14 | [0.5.0 → 0.5.1](https://github.com/opensquad-ai/opensquad/compare/v0.5.0...v0.5.1)     | [GitHub Release](https://github.com/opensquad-ai/opensquad/releases/tag/v0.5.1)  |
| [0.4.12]                                                               | 2026-07-09 | [0.4.11 → 0.4.12](https://github.com/opensquad-ai/opensquad/compare/v0.4.11...v0.4.12) | [GitHub Release](https://github.com/opensquad-ai/opensquad/releases/tag/v0.4.12) |
| [0.4.11]                                                               | 2026-07-08 | [0.4.10 → 0.4.11](https://github.com/opensquad-ai/opensquad/compare/v0.4.10...v0.4.11) | [GitHub Release](https://github.com/opensquad-ai/opensquad/releases/tag/v0.4.11) |
| [0.4.3](https://github.com/opensquad-ai/opensquad/releases/tag/v0.4.3) | 2026-07-01 | [0.4.2 → 0.4.3](https://github.com/opensquad-ai/opensquad/compare/v0.4.2...v0.4.3)     | [GitHub Release](https://github.com/opensquad-ai/opensquad/releases/tag/v0.4.3)  |
| [0.4.2](https://github.com/opensquad-ai/opensquad/releases/tag/v0.4.2) | 2026-06-30 | [0.4.1 → 0.4.2](https://github.com/opensquad-ai/opensquad/compare/v0.4.1...v0.4.2)     | [GitHub Release](https://github.com/opensquad-ai/opensquad/releases/tag/v0.4.2)  |
| [0.4.1](https://github.com/opensquad-ai/opensquad/releases/tag/v0.4.1) | 2026-06-30 | [0.4.0 → 0.4.1](https://github.com/opensquad-ai/opensquad/compare/v0.4.0...v0.4.1)     | [GitHub Release](https://github.com/opensquad-ai/opensquad/releases/tag/v0.4.1)  |
| [0.4.0](https://github.com/opensquad-ai/opensquad/releases/tag/v0.4.0) | 2026-06-29 | (initial release)                                                                      | [GitHub Release](https://github.com/opensquad-ai/opensquad/releases/tag/v0.4.0)  |


> This is the first public release of OpenSquad on the recreated
> `opensquad-ai/opensquad` repository. The repository was re-published
> fresh on 2026-06-29; no prior commit history is carried over.

---

## [Unreleased]

---

## [0.8.41] — 2026-08-10

> Patch release — fix the broken `v0.8.40` Docker image so the published
> artifact is actually runnable. v0.8.40 was tagged and the GitHub Release
> page was created, but `release.yml`'s Docker build failed at the final
> stage and PyPI trusted publishing was unconfigured, so no usable artifact
> was shipped. v0.8.41 re-publishes the same code with the Docker fix in
> place.

### Fixed

- **docker: `chmod +x` on `docker-entrypoint.sh` failed as the non-root
  `opensquad` user.** The Dockerfile switched to `USER opensquad` *before*
  copying the entrypoint, then tried to `chmod +x` as that user, which is
  denied. Moved the `COPY` + `chmod` + `chown` sequence to before
  `USER opensquad` so `chmod` runs as root, then `chown` hands the
  executable bit back to the `opensquad` user. Restores a green
  `release.yml` Docker job.

---

## [0.8.40] — 2026-08-10

> Theme overhaul (pure-white hue drift, dark-mode white borders), models
> page: add-more-models + detail drawer, OpenSquad pulse-dot / boot SVG /
> session search, dispatcher __STOP__ session scope.

### Added

- **web: OpenSquad pulse-dot indicator.** Replaces the previous plain
  status dot with the four-quadrant OpenSquad loader so the agent's
  working / idle / sleeping states read at a glance, and matches the
  boot loader visual language.
- **web: redesigned boot loader SVG.** Static purple gradient + four
  pulsing quadrants with constant halo layer (no more "shrinking" when
  a quadrant lights up). Background switches with a 260ms transition
  so the logo doesn't visibly resize on theme apply.
- **web: in-app session search modal.** ⌘/Ctrl-K opens a search modal
  over the active workspace, lets you fuzzy-search session titles and
  jump straight to a session without scrolling the sidebar. Also
  exposes a "new session in this workspace" affordance.
- **web (models): add more models to an existing provider.** Provider
  detail drawer now exposes an "add model" flow (id, label, capabilities,
  context window) that appends to the same provider instead of forcing
  a new provider card.

### Fixed

- **theme: pure-white preset no longer renders rose/pink.** The
  HSL-derived rail/nest wash of a deep neutral primary was forcing
  the surface hue to 0, which painted the whole palette pink on a true
  white background. The `pure-white` preset now skips the primary tint
  and uses a fixed white/black monochromatic pair; the dark variant is
  a true near-black with light text.
- **theme: dark mode no longer paints white frames around borders,
  code blocks, slider tracks, and active rails.** The theme CSS
  variables were stored as raw hex, so Tailwind opacity modifiers like
  `bg-primary/40` produced invalid CSS and fell back to `currentColor`
  (which is the light text color in dark mode — that's the "white
  border" the user kept seeing). All theme vars are now space-separated
  RGB triplets; `tailwind.config.cjs` and the `color-mix` calls in
  `index.css` were migrated to `rgb(var(--color-x) / <alpha-value>)`.
- **theme: every popover / modal / panel that used to hardcode
  `bg-white` / `bg-gray-*` now follows the active appearance.** The
  AI chat composer, slash menu, attach / model / context pickers, plan
  / diff / thought / task / shell folds, modals (close-workspace,
  create-workspace, session-search), workspace-tab dropdown, content
  tab dropdown, and the scroll-to-bottom button all switched to
  `bg-bgLight` / `bg-panel` / `border-border` theme tokens.
- **dispatcher: `__STOP__` is now scoped to its own session.** Sending
  stop in one parallel session no longer cancels the other two. The
  stop signal is routed per-session instead of broadcast across the
  dispatcher's shared channel.
- **web (models): models page header is centered and model names
  left-aligned.** Polishes the detail drawer layout so the table reads
  consistently with the rest of the page chrome.

### Maintenance

- Absorbed `main` back into `dev` (the v0.8.30 release had been
  tagged without the post-release dev sync, so the next release
  branch would have started on a stale base).

---

## [0.8.8] — 2026-08-04

> 400 context-overflow fix (reasoning_content injection amplification),
> tool-bubble render fix, and three latency optimizations.

### Fixed

- **400 context overflow (root cause).** chat() copied the latest
  reasoning_content into EVERY historical tool_calls message (121 copies on
  session 151735_a7s7), multiplying requests ~2.75x (362K -> 998K tokens)
  past the 1M context limit. Now injected once into the most recent
  assistant message (DeepSeek's actual "subsequent turns" semantics).
- **tool results rendered as chat bubbles.** buildTimelineFromSession showed
  role='tool' messages (LLM context) as dialogue on history replay;
  MessageBubble defensively skips them too.
- **reasoning_content token undercount.** _count_tokens counted content but
  skipped reasoning_content (~2.5% undercount); both counters now include it.

### Changed

- **tool-schema prewarm starts earlier.** Built-in schemas prewarm right
  after the early runner (chat-ready), so a first message sent before
  extensions finish no longer pays 1-3s lazy-import on the event loop;
  plugin schemas rebuild after extensions complete.
- **startup token stats backgrounded.** _broadcast_token_stats_sync moved out
  of AgentRunner.__init__ (0.5-1s full-history tiktoken encoding) into a
  background worker once the event loop runs.
- **agents list TTL cache.** _handle_list_agents cached 5s (was reading
  token_stats.json + profile.json per agent on every poll).

---

## [0.8.7] — 2026-08-03

> Boot-to-first-turn optimization round 2: static-UI web startup, MCP connect
> timeouts, tool-schema prewarm, graded readiness, plus two launcher fixes
> (process-table wiring, single-instance guard) that stop the stale-kill loop.

### Added

- **web: static-UI default.** `opensquad web` serves the built frontend from
  Gateway :9555 and skips the Vite cold start (5-15s saved); `--dev` keeps
  Vite for frontend work.
- **lite whitelist extended.** `/api/groups` and `/api/ai-web/agents` are
  available as soon as `ready_lite` is set (login first paint -0.5~1.5s).
- **MCP connect hard timeout.** Per-server `asyncio.wait_for` (max(5, cfg))
  with `finally` AsyncExitStack cleanup prevents hung spawns from stalling
  full_ready and leaking subprocesses.
- **Tool-schema background prewarm.** `generate_openai_tools` +
  `generate_tool_descriptions` run in a thread after agent_ready, moving the
  one-time lazy-import cost (1-3s) out of first-turn TTFT.
- **prompt-cache opt-in (DeepSeek).** `model.prompt_cache: true` injects
  `chat_template_kwargs.cache.use` for deepseek endpoints (default off).
- **single-launcher guard.** A second launcher instance (uv vs anaconda
  python installs) probes the management port and exits instead of sharing
  the runtime registry and killing the first instance's agents.

### Changed

- **httpx graded timeouts.** `Timeout(connect=10, read=120, write=30,
  pool=10)` + `max_retries=0`: dead endpoints now fail in ~12-22s instead of
  blind-waiting 120s (x6 retries).
- **`_setup_local_mode` idempotent.** Skips config rewrite, workspace-config
  subprocess and .env.local writes when local mode is already active.
- **connections setup parallel.** Web-server setup and gateway adapter run
  concurrently (gather); config dict reused across AgentRunner/model_switch.
- **`init_db` backgrounded.** TCP port + `ready_lite` come up immediately;
  DB-backed lite endpoints wait on `_db_ready` (15s cap).
- **plugin.json write dedup.** Manifests are only written when content
  changed (12 plugins x redundant IO removed).
- **cloned ChatAPI shares read-only resources.** httpx client and tiktoken
  encoding reused from the root instance.

### Fixed

- **launcher stale-kill loop (root cause).** `set_process_tables()` was never
  wired: process_manager's process table stayed empty, so cleanup treated
  every live agent/plugin as stale and killed it every ~60s. Now injected
  after registration.
- **plugin to_thread regression reverted.** `asyncio.to_thread` broke plugins
  whose `on_load` calls `get_running_loop()` (e.g. reminder); loading stays
  on the event loop.
- **async session archive reverted.** The threaded archive introduced a disk
  visibility race (stale reads right after New Chat); back to synchronous.

---

## [0.8.6] — 2026-08-03

> Boot-to-first-turn latency rework + web interaction performance: MCP fully
> backgrounded, lazy tool imports, TTL caches across launcher/gateway/agent,
> file-tree git acceleration, fast shutdown, and a canonical message model.

### Added

- **file tree: git-accelerated indexing.** `utils/fs_index.py` lists git repos
  via `git ls-files` (milliseconds, .gitignore respected) with a 10s TTL cache
  and a bounded scandir fallback (depth cap, symlink-loop immune); the web UI
  lazy-expands one level on demand and upgrades to a full listing on search.
- **ready stages.** Agent emits `agent_ready_stage` (`extensions_ready` /
  `full_ready`); web UI shows a "tools loading, you can chat now" strip.
- **canonical message model.** `opensquad/messages.py` (ToolCall / ToolResult /
  AssistantTurn) as the single tool-call parsing boundary; fixes parallel
  `<tool_call>` blocks merging in the DSML strategy.
- **turn-loop extraction.** `_runner/_turn_loop.py::TurnLoop` — deterministic
  fake-runner tests for tool execution / stop / plain-text paths.
- **commit guard.** Local pre-commit wrapper rejects commits with unstaged
  changes (pre-commit's stash/restore could silently drop work) plus a
  recovery tool (`scripts/recover_precommit_stash.py`).

### Changed

- **boot: MCP fully backgrounded.** The main coroutine no longer awaits MCP
  init (playwright npx cold start ~6.6s); group-chat bridge starts after
  plugins; per-server MCP timeout tightened (60s→8s config, 30s→10s default).
- **boot: lazy tool imports.** `ToolRegistry.register_lazy()` defers built-in
  tool module imports until first use (filesystem stays eager for configure).
- **boot: gateway module imports deferred.** `_admin` already lazy; `_main`
  now lazy-loads collab_board / agent_sessions / sessions via proxies.
- **prompt build: agent.md cached by mtime** (was a disk read every turn).
- **web latency: TTL caches.** Launcher `runtime/list` (5s), token stats
  (12s per session), gateway readonly proxy endpoints (5s); frontend polls
  reduced (12s→30s token, 3s→5s disk catch-up).
- **streaming: first chunk flushes immediately** (no 30ms debounce).
- **web boot: registration check runs in parallel** with token restore.
- **CLI start: `_find_python` cached; port owners probed before netstat.**
- **TUI: app.py split (6589→3898 lines) into 6 mixins.**
- **compaction: token estimation, file-operation tracking, head+tail
  truncation** in summary payloads.

### Fixed

- **stop command: 13.5s→0.8s tree kill.** `psutil.kill()` replaces serial
  taskkill; wmic snapshot kept (psutil attr walks can take 17s); parallel
  graceful shutdown in launcher; `TimeoutError` now reported.
- **gateway `_main` lazy imports:** PEP 562 `__getattr__` does not fire for
  function-body globals (LOAD_GLOBAL) — replaced with explicit lazy proxies
  (agent-sessions 500 regression fixed).
- **pre-commit stash rollback could silently drop unstaged work** — guard +
  recovery tooling; lint debt cleaned (ruff fully green).
- **session tool-call persistence** (chat_api) so history reloads do not lose
  tool results.
- **stale tests** referencing moved APIs repaired; pytest capture crash on
  Windows fixed.

---

## [0.8.5] — 2026-07-29

> Desktop macOS: target Monterey 12+, pin CI runner, optional signing/notarization.

### Fixed

- **desktop/macOS:** CI builds on pinned `macos-15` with
  `MACOSX_DEPLOYMENT_TARGET=12.0` and `minimumSystemVersion: 12.0` so packaged
  apps run on macOS 12 Monterey and newer (avoids `macos-latest` raising Mach-O
  minOS). Backend artifact minOS is gated with `otool` in CI.

### Changed

- **desktop/macOS:** Hardened Runtime + entitlements; `afterSign` notarization
  when Apple / Developer ID secrets are configured (unsigned fallback when not).

---

## [0.8.0] — 2026-07-22

> Agent Web performance + parallel sessions, Goal/Plan workflows, SenseVoice,
> and session/model isolation — desktop build via `v0.8.0`.

### Added

- **agent-web: fast session switch.** Timeline LRU with `complete` metadata,
paged first paint (80 messages), idle prefetch of recent sessions, and
scroll-up `loadMoreHistory` instead of full-history fetches on every tab change.
- **gateway: session list metadata cache.** History sidebar list uses per-file
mtime metadata so 6s refreshes skip re-parsing large `history/*.json` files.
- **agent-web: project files module cache.** Same `rootPath` reuses tree + file
content across remounts / session switches (no full-panel flash).
- **sessions: parallel turns.** Multi-pane / multi-session dispatch without
blocking other sessions on a single busy turn.
- **agent: Goal mode + Plan workflow.** New goal tools and plan workflow prompts
for structured multi-step work.
- **plugin: SenseVoice.** Local ASR plugin panel and service wiring.
- **audio: OpenAI ASR path.** Shared ASR helpers alongside StepFun / Whisper.
- **agent-web: Work/Code chrome.** Settings-hosted global nav, workspace panes,
model flyout polish, and silent refresh.

### Fixed

- **agent-web: restore replies on send / history view.** Full dialogue + tool
streams when returning to a session; cache-first paint without losing live turns.
- **session: model switch isolation.** Per-session model cards do not clobber
agent defaults or other panes.
- **paged history: archived payload.** `archived_*` only on `offset=0` so
scroll-up pages stay light.

### Changed

- **hydrate / switch:** default history window is the latest page (not ~full
`limit=10000`); older turns load on demand.
- **AgentSessionReader:** larger LRU, shallow cache copies, `asyncio.to_thread`
for disk reads on the Gateway event loop.

---

## [0.6.0] — 2026-07-15

> CLI TUI (OpenCode-style) + session/cwd/token UX; continues web/desktop hardening
> from the 0.5.x line.

### Added

- **cli: OpenCode-style Textual TUI.** Full-screen `opensquad code` chat with
queue, live thinking, Ctrl+X side stream, provider connect, Plan/Build, and
decision cards.
- **cli: live ↑↓ token + elapsed meter.** Context upload (current window),
animated per-turn output, and turn duration in the prompt footer.
- **cli: launch cwd → session working directory.** TUI binds agent
`.session_cwd` to the shell cwd at start; each TUI launch opens a new session.
- **agent-web: project files panel.** Right-side browse panel (breadcrumb,
search, syntax-highlighted preview, image/Markdown preview, dashed filename
links from tool stream); sandboxed Launcher FS list/read under session cwd.

### Fixed

- **cli: Ctrl+P palette vs history arrows.** Priority up/down no longer steal
keys from the command palette.
- **cli: httpx URL flicker under the prompt.** Quiet HTTP client logs during TUI;
wait banner no longer mirrors stream/API paths.
- **chat: context compression archive UI.** Chronological message/event archive,
tool_call/result atomic pairs, frontend hydration merge + tool-id dedup so
「已归档」 appears without refresh and without duplicated tool streams.
- **chat: avatar local fallback.** Dicebear CDN defaults replaced with local SVG
initials; `AvatarImg` + `onError` across chat surfaces.
- **session: working-directory signal.** Atomic `.session_cwd` write (`version: 1`,
`.tmp` + `os.replace`); shared read helper tolerates legacy files.
- **desktop: Linux AppImage update asset match.** Updater regex aligned to
`-linux-x64.AppImage` (electron-builder `${arch}`).

### Changed

- **compress: auto/manual archive path unified.** Auto-compression uses the same
`compress_current_session` cut as manual (`keep_from_timestamp_ms` / keep
fraction from system config); dead `handle_auto_compression` removed.
- **release: version sync.** `scripts/sync_version.py` also updates
`nexuschat-pro/package.json`; release flow is `pyproject` → sync → `uv lock`.

---

## [0.4.12] — 2026-07-09

> Session working directory + desktop reload fixes.

### Added

- **session working directory.** Per-agent folder picker; tools/shell respect
session cwd for the current conversation.

### Fixed

- **desktop:** stop chat UI auto-reload loops after gateway restart; working-directory
API 405 on packaged Electron builds.

---

## [0.4.11] — 2026-07-08

> Plugin uninstall robustness, websearch Playwright fixes, installer process kill.

### Fixed

- **plugins:** uninstall handles locked `.git` files; allow dots in plugin names.
- **websearch:** Playwright driver/chromium install in frozen builds; Windows GBK
print crash; auto-start service.
- **installer:** kill `run.exe` children before NSIS upgrade.
- **config:** infer `model.api_protocol` in defaults; preserve protocol on save.

---

## [0.4.4]–[0.4.10] — 2026-07

> Incremental desktop, gateway, and CI hardening between 0.4.3 and 0.4.11
> (beta tags `v0.4.10beta.*` / `v0.4.11beta.*` included). Highlights: in-app
> update polish, token analytics units, launcher health grace, frontend smoke
> without `package-lock`, and assorted agent/runtime stability fixes. See
> GitHub compare links on the release tags for full commit lists.

---

## [0.4.3](https://github.com/opensquad-ai/opensquad/releases/tag/v0.4.3) — 2026-07-01

> Desktop UX: clearer Release asset names and visible update progress.

### Fixed

- **desktop: blank window on startup.** Wait for backend `ready` before
loading the UI; disable Vite reverse-proxy in frozen/desktop builds; bundle
tray/window icons; start Launcher after Gateway init to avoid workspace
bootstrap races.

### Added

- **desktop: full-screen update progress overlay.** In-app updates now show
phased feedback (downloading → verifying → launching installer → closing
app) so the long pause after the `.exe` download no longer feels like a
freeze.
- **desktop: OS/arch suffixes on installer filenames.** Release assets are
named like `OpenSquad-0.4.3-win-x64-Setup.exe` and
`OpenSquad-0.4.3-mac-arm64.dmg` so users can pick the right platform at a
glance. Auto-update selects the matching asset (including Mac CPU arch).

---

## [0.4.2](https://github.com/opensquad-ai/opensquad/releases/tag/v0.4.2) — 2026-06-30

> Desktop polish: OpenSquad branding, first-launch system settings, plugin
> auto-start, and in-app updates from GitHub Releases.

### Added

- **desktop: in-app auto-update.** Stable desktop builds can download the
platform installer from GitHub Releases and run it after quitting the
current app (silent NSIS upgrade on Windows).
- **desktop: OpenSquad product branding.** Installers, window title, tray,
and About menu now show **OpenSquad** instead of the legacy NexusChat Pro
codename (`appId`: `com.opensquad.desktop`).

### Fixed

- **desktop: System Settings 500 on first launch.** Bootstrap
`system_config.json` in the workspace before the admin API reads it.
- **desktop: plugin services auto-start in frozen builds.** Spawn plugin
`service/main.py` with the system Python interpreter; remove
`--no-services` from the Electron launcher.
- **CI (fast):** pytest dev extras, ruff pin/format, frontend types, and
gitleaks allowlist fixes so the daily gate stays green.

### Changed

- **chore:** ignore local `.coverage`, `htmlcov/`, and generated audit
report artifacts in `.gitignore`.

---

## [0.4.1](https://github.com/opensquad-ai/opensquad/releases/tag/v0.4.1) — 2026-06-30

> Desktop-focused patch: Launcher spawn, bundled resources, workspace paths,
> and configurable workspace directory in the packaged app.

### Fixed

- **desktop: Launcher not running in the packaged app.** The Electron app
only spawned the Gateway (`run.exe`, port 9555); the Launcher (port 9600)
was a separate process the desktop bundle never started, so the Agent
Workstation showed "Launcher is not running (cannot connect to
[http://127.0.0.1:9600](http://127.0.0.1:9600))". `run.py` now dispatches on `--service` and the
Electron `main.ts` spawns a second `run.exe --service launcher` instance
(management-port-only, `--no-auto-start --no-services` to stay safe inside
a frozen bundle). The PyInstaller spec now ships the standalone
`opensquad/launcher.py` as a data file so the launcher's `main()` is
reachable — it was previously shadowed by the `opensquad/launcher/`
package and dropped from the bundle.
- **desktop: builtin resources (plugins, skills, role/model/collab cards,
agents, pymcp) were missing from the bundle.** They live at `src/` top
level (not inside the `opensquad` package), so `collect_submodules` never
reached them and the desktop app showed empty plugin/skill/card pages.
The spec now collects `plugins`/`skills` (with node_modules + UI build
dirs filtered out) and bundles the card/agent/pymcp directories plus the
`system_config.example.json` template under `_internal/`, where frozen
mode resolves builtin resources.
- **desktop: frozen workspace now uses Electron's userData dir.** Previously
the packaged app reused the dev workspace from `last_workspace.json`,
which only exists on a developer's machine — on any other PC it fell back
to the read-only install dir. `bootstrap_workspace()` and the gateway
startup now branch on frozen mode: use `OPENSQUAD_USER_DATA` as an
independent workspace, init it on first run (structure + config + seed
resources), and reuse it afterwards.
- **desktop: uploads path aligned with the workspace.** Frozen mode stored
uploads at `<userData>/uploads/`, diverging from the workspace layout
(`<workspace>/data/uploads/`) used in dev, so images appeared torn. Both
modes now use `workspace_uploads_dir()`; since the desktop workspace IS
userData, uploads persist per-user and are served consistently.
- **desktop: configurable workspace path in System Settings.** The desktop app
now separates Electron app data (`OPENSQUAD_APP_DATA`, fixed userData dir)
from the active workspace (`OPENSQUAD_USER_DATA`). Users can create, switch,
or migrate workspace data to a custom directory; the choice is persisted in
`desktop-workspace.json` and applied after restarting the app.

### Known limitations (desktop)

- Agent **start** from the Agent Workstation UI is not yet supported in the
packaged app (a frozen EXE cannot `sys.executable -m` an agent). Listing
and configuring agents works. See
[docs/desktop-known-issues.md](docs/desktop-known-issues.md).

### Changed

- **CI: desktop Release Assets can be refreshed via workflow_dispatch.**
`build-desktop.yml` accepts a `release_tag` input to rebuild installers
from any branch and overwrite Assets on an existing GitHub Release.

---

## [0.4.0](https://github.com/opensquad-ai/opensquad/releases/tag/v0.4.0) — 2026-06-29

> **First public release on the recreated repository.**

### Highlights

- **Local-first multi-agent collaboration** — multiple autonomous agents
(PM, Coder, QA, and custom roles) communicate via a group chat to
coordinate and complete complex tasks. The PM agent breaks down user
goals, dispatches work to Coder / QA, and reconciles their output.
- **Bring-your-own LLM** — connect any OpenAI-compatible endpoint
(OpenAI, Anthropic via proxy, local Ollama, etc.) per agent card.
Each agent has its own model, system prompt, tool set, and memory.
- **Group-chat gateway** — a FastAPI backend (`src/opensquad/gateway/backend`)
paired with a React + Vite + react-i18next frontend
(`src/opensquad/gateway/nexuschat-pro`) provides a real-time web UI
for orchestrating agents, inspecting token usage, and steering the
collaboration.
- **Plugin system** — drop-in plugins under `src/plugins/` extend agent
capabilities (Telegram bridge, Feishu bridge, web search, agent
factory, chat account, task watch, token analytics, quick note).
Each plugin is a self-contained Python package with a `plugin.json`
manifest.
- **Skill system** — packaged skills under `src/skills/` provide
task-specific prompts and tool definitions. Skills are versioned,
installable, and shipped alongside the project.
- **First-launch init wizard** — a one-time language picker (中文 / English)
on a fresh deployment, with the first web registration creating the
admin account. Subsequent registrations are closed by default.
- **Documentation** — bilingual READMEs, full `doc_en/` and `doc_cn/`
guides, `BRANCHING.md` (English) and `BRANCHING_ZH.md` (Chinese)
release-flow reference, `RELEASING.md` maintainer guide,
`CONTRIBUTING.md` for new contributors, `SECURITY.md` for
vulnerability disclosure, and `CODE_OF_CONDUCT.md` (Contributor
Covenant 2.1).
- **CI** — GitHub Actions workflows for fast PR checks, full CI,
desktop build (Electron), npm release, stale bot, and PR title
linting.

### Changed

- **Version bumped to 0.4.0** in `pyproject.toml`,
`src/opensquad/__init__.py`, and `package.json` to mark the
post-cleanup state of the recreated repository.
- **Dependabot disabled by default** in `.github/dependabot.yml`
(each update entry is `enabled: false`). To re-enable, flip the
flag to `true` on the entries you want.
- **Repository recreated** as a clean single-commit history. The
project retains all working code but does not carry over the prior
325-commit history or any prior release tags.

### Notes for early adopters

- The Python package is `opensquad`; the npm wrapper is
`@opensquad-ai/opensquad`. Both share the same version.
- The web UI ships bilingual; the language is set on first launch and
is per-user (not per-deployment).
- For self-hosted LLM providers, see `doc_en/agent_management.md` for
the `tool_filter`, `model_id`, and `base_url` model-card fields.
- The pre-commit hook chain (ruff, ruff-format, commitlint,
detect-secrets) is recommended for contributors.

&nbsp;
