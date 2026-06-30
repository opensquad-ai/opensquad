# Changelog

All notable changes to OpenSquad will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Releases

| Version | Date | Compare to previous | Release page |
|---------|------|---------------------|--------------|
| [0.4.0] | 2026-06-29 | (initial release) | [GitHub Release](https://github.com/opensquad-ai/opensquad/releases/tag/v0.4.0) |

> This is the first public release of OpenSquad on the recreated
> `opensquad-ai/opensquad` repository. The repository was re-published
> fresh on 2026-06-29; no prior commit history is carried over.

---

## [Unreleased]

> Changes since [0.4.0]. Will be folded into the next release section when cut.

### Fixed

- **desktop: Launcher not running in the packaged app.** The Electron app
  only spawned the Gateway (`run.exe`, port 9555); the Launcher (port 9600)
  was a separate process the desktop bundle never started, so the Agent
  Workstation showed "Launcher is not running (cannot connect to
  http://127.0.0.1:9600)". `run.py` now dispatches on `--service` and the
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

### Known limitations (desktop)

- Agent **start** from the Agent Workstation UI is not yet supported in the
  packaged app (a frozen EXE cannot `sys.executable -m` an agent). Listing
  and configuring agents works. See
  [docs/desktop-known-issues.md](docs/desktop-known-issues.md).
- The desktop app uses a fresh, independent workspace (Electron userData).
  Data from a dev workspace (`opensquad start`) is **not** migrated — the
  desktop app starts clean. This is by design.

---

## [0.4.0] — 2026-06-29

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

[0.4.0]: https://github.com/opensquad-ai/opensquad/releases/tag/v0.4.0
