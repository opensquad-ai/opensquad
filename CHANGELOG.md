# Changelog

All notable changes to OpenSquad will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Releases

| Version | Date | Compare to previous | Release page |
|---------|------|---------------------|--------------|
| [0.3.0] | 2026-06-27 | [v0.2.0…v0.3.0](https://github.com/opensquad-ai/opensquad/compare/v0.2.0...v0.3.0) | [GitHub Release](https://github.com/opensquad-ai/opensquad/releases/tag/v0.3.0) |
| [0.2.0] | 2026-06-26 | [v0.1.0…v0.2.0](https://github.com/opensquad-ai/opensquad/compare/v0.1.0...v0.2.0) | [GitHub Release](https://github.com/opensquad-ai/opensquad/releases/tag/v0.2.0) |
| [0.1.0] | 2026-06-17 | [v0.0.1…v0.1.0](https://github.com/opensquad-ai/opensquad/compare/v0.0.1...v0.1.0) | [GitHub Release](https://github.com/opensquad-ai/opensquad/releases/tag/v0.1.0) |
| [0.0.1] | 2026-05-29 | (initial release) | [GitHub Release](https://github.com/opensquad-ai/opensquad/releases/tag/v0.0.1) |

> Want commit-level detail? Click "Compare to previous" for the full
> commit and file diff between any two tags.

---

## [Unreleased]

> Changes since [0.3.0]. Will be folded into the next release section when cut.

### Fixed
- **`src/opensquad/__init__.py` ruff violations** — dropped the unused `TurnLoop` import (F401) and four stale `__all__` entries (`system`, `filesystem`, `api_process`, `memory` — F822 × 4) that were never bound as top-level attributes. Plus standard ruff auto-format cleanup (UP009, I001, RUF022). Closes [#75](https://github.com/opensquad-ai/opensquad/issues/75).

### Docs
- **`BRANCHING.md` & `BRANCHING_ZH.md` refresh** — replayed the short-lived `release/*` policy (release branches are cut → PR'd to `main` → **deleted**; tags are the long-lived record) and added a new **"When to bump minor vs patch"** cheat sheet (SemVer rule 4 for 0.x.y, PEP 440 markers, deployer-effort heuristic). Closes the consistency gap left when the previous BRANCHING.md rewrite was folded but never opened as a PR.
- **Desktop build documentation** — added `doc_en/desktop_build.md` and `doc_cn/desktop_build.md` covering the full desktop-app build (Electron + Vite + PyInstaller) from source: dev modes (`electron:dev` / `electron:dev:fast` / `electron:dev:live`), production builds per platform (`electron:win` / `electron:mac` / `electron:linux`), the two-stage backend + electron pipeline, the `build-desktop.yml` CI flow on `v*` tag, prerequisites (Node 20+, Python 3.10+, PyInstaller, Pillow + Playwright for icon generation), and a pitfalls section. The 2-line "Desktop Application (Electron)" stubs in both `deployment_guide.md`s were replaced with a "see Desktop Build Guide" pointer.
- **Documentation folder consolidation** — the three doc folders (`doc_en/`, `doc_cn/`, `docs/`) had grown inconsistent: 7 files duplicated across `docs/` and `doc_en/`, ~25 files of misplaced Chinese content in `docs/`, `_EN.md` / `_ZH.md` suffix conventions contradicting each other between folders, and a `docs/README.md` whose role overlapped with the language-specific READMEs. This change (1) moves 19 misplaced Chinese files to `doc_cn/` (with `_ZH` suffix dropped because the folder already implies the language), (2) moves 5 misplaced English files to `doc_en/` (including the full 38 KB `PLUGIN_DEVELOPMENT_EN.md` which overwrites the 11 KB stub), (3) deletes 5 files whose authoritative version lives in another folder, (4) rewrites `docs/README.md` as the meta-README explaining the bucket's role and what does/doesn't belong there, (5) updates `doc_en/README.md` and `doc_cn/README.md` indexes to add the newly-arrived docs, and (6) adds an explicit "Documentation folder structure" section to both `CONTRIBUTING.md` and `CONTRIBUTING_ZH.md` with rules, don'ts, and worked examples. After this, `docs/` is down to 5 files (4 maintainer-facing + the meta-README) and matches its stated purpose.
- **Doc pruning, deployment-guide reorder, agent-config consolidation** — six stale docs removed (`doc_cn/faq.md`, `doc_cn/quick_start.md`, `doc_cn/multi_server_deployment.md`, `doc_cn/MULTI_NODE_ARCHITECTURE.md`, `doc_cn/migration_guide.md`, `doc_cn/multi_model_adaptation.md` — the project doesn't ship multi-server deployment, and the rest were superseded by `getting_started.md` + `agent_management.md`); the EN 62-line `agent_setup.md` stub removed and its 800-line CN sibling `agent_management_guide.md` translated into `doc_en/agent_management.md` (with the CN file renamed to `agent_management.md` for symmetry), eliminating the agent-config split; `doc_en/group_chat_agent.md` translated to `doc_cn/group_chat_agent.md` so the Chinese README no longer jumps to English on click; both `deployment_guide.md` files reordered from "Docker-first" to the README's "one-click script → uv → pip → Docker" order with the long-superseded "Multi-Node Distributed Deployment" section removed from both. All cross-references in `src/skills/opensquad_intro/SKILL.md`, `src/prompts/{base,thought}_{fc,xml}.md`, `src/skills/collaboration_management/README.md`, `src/plugins/agent_factory/README.md`, `CONTRIBUTING.md`/`_ZH.md`, and the doc READMEs updated to match.
- **Drop unused Whisper service docs** — `doc_cn/WHISPER_GUIDE.md` (Whisper usage guide) and `doc_cn/WHISPER_MODELS.md` (Whisper model selection guide) removed. The underlying `whisper` tool / plugin is still documented in `doc_cn/configuration_reference.md`, `doc_cn/PLUGIN_DEVELOPMENT.md`, and `doc_cn/ARCHITECTURE.md`; these two standalone usage guides were not referenced from anywhere except the `doc_cn/README.md` index, which has been updated.
- **Drop obsolete tool-filter / report docs** — `doc_cn/tool_filtering_optimization.md`, `doc_cn/report_analysis.md`, and `doc_cn/report_split_deployment.md` removed. The tool-filter knob is now documented inline in `doc_cn/agent_management.md` (the `tool_filter` model field) and `doc_en/agent_management.md`; the two report docs were one-off internal reports never referenced after the work landed. `doc_cn/troubleshooting.md` reference to `tool_filtering_optimization.md` updated.

---

## [0.3.0] — 2026-06-27

> **First-launch init wizard, single-source version display.**

### Added
- **First-launch init wizard** — on a fresh deployment, the web UI now shows a one-time language picker (中文 / English) and guides the user to register their own account. The default `admin@opensquad.ai` / `123456` auto-fill is gone; deployments bootstrap from a clean state and the first web registration creates the account.
- **One web account per deployment** — after the first web user registers, subsequent web `/auth/register` calls get a 403. Internal tools (agent comm accounts, `node_secret`-authenticated endpoints) are unaffected via the new `X-Node-Secret` bypass header.
- **`/auth/registration-status` endpoint** — lets the frontend ask the backend "is registration still open?" without a probe-and-fail dance. Drives the wizard state purely from server truth.
- **Localized default group** — the first registered user's chosen language flows into the auto-created collaboration group name and pinned welcome message.
- **`X-Node-Secret` header** — internal-tool authentication bypass for agent account creation, so plugins like `chat_account` can still register after the first web user is in place.

### Fixed
- **Default group not bootstrapped on register** — previously the welcome group + pinned message were only created on `/auth/login`. Since the wizard auto-logs the user in after registration, the group never appeared until a manual re-login. Now bootstrapped from `/auth/register` as well.
- **Wizard gated on localStorage was wrong** — the language screen used to consult `opensquad_lang` in localStorage, so a stale key from a previous deployment on the same browser would skip the wizard on a fresh deploy. Replaced with a per-session `langPicked` flag in `App.tsx`; backend `registrationStatus` is the source of truth.
- **Version display drift in the UI** — the web footer showed `v0.1.1` long after the project had shipped v0.2.0. Two halves of the fix:
  - Backend `_get_current_version()` now reads `importlib.metadata.version("opensquad")` (PEP 517 standard) instead of the hand-maintained `opensquad.__version__` literal.
  - Frontend `vite.config.ts::loadAppVersion()` now reads `pyproject.toml` first, with `__init__.py` only as a fallback. `pyproject.toml` is the single source of truth across the build and runtime.
- **`test_version_format` accepted only legacy `X.Y.Z`** — relaxed to any valid PEP 440 version via `packaging.version.Version`, so future dev / post / rc / local markers don't break the test.

### Docs
- **`BRANCHING.md` & `BRANCHING_ZH.md`** — release branches redefined as **short-lived working branches** (cut from `dev` or an old tag, PR'd to `main`, then deleted). Tags — not branches — are the long-lived record of what shipped. Old `release/0.1.0` and `release/0.2.0` branches removed; `v0.1.0` and `v0.2.0` tags remain as the source of truth.

---

## [0.2.0] — 2026-06-26

> **Release-channel detection, npm distribution, license migration.**

### Added
- **`@opensquad-ai/opensquad` npm package** — thin Node.js wrapper that installs the Python `opensquad` CLI on first run and proxies commands to it. JavaScript users can now `npm install -g @opensquad-ai/opensquad` and get the familiar `opensquad` command. The package name is scoped because the unscoped `opensquad` name on npm is already taken by an unrelated project.
- **Channel-aware version check** — the gateway's `/version` endpoint now derives a release channel from the PEP 440 version string (`stable` / `dev` / `pre-release` / `local` / `unknown`) and only queries GitHub for updates when on the `stable` channel. Dev / hotfix / pre-release users no longer see misleading "new version 0.1.0 available" prompts while running `0.2.0.dev0`. The frontend shows the channel as a colored badge and disables the check button with an explanatory message on non-stable channels.
- **Pre-release protocol docs** — `RELEASING.md` now documents the `vX.Y.Z-alpha.N` / `-beta.N` / `-rc.N` → `vX.Y.Z` progression, including the PEP 440 / PyPI mapping (`X.Y.Zb1`) and how `release.yml` auto-detects prerelease tags.
- **Sub-project routing in CONTRIBUTING** — clarifies that role cards / collab cards / skills / plugins live in separate repos (`opensquad-roles`, `opensquad-collab-cards`, `opensquad-skills`, `opensquad-plugins`) and use the simpler `feature/*` → `main` workflow.

### Changed
- **CI split into fast + full** — `ci-fast.yml` runs on every push to `main` / `dev` and on PRs to `dev` (5 jobs, ~1 min). The full `ci.yml` runs only on PRs to `main` (11 jobs, coverage gate, mypy, bandit, pip-audit, CodeQL). Estimated 85% reduction in CI minutes on dev pushes.
- **Release branch naming aligned with SemVer** — `release/YYYY.M.D` → `release/x.y.z` to match PEP 440 tag names exactly.

### Migration
- **License: Apache 2.0 → MIT** — `LICENSE`, `pyproject.toml`, `README_ZH.md`, and `package.json` updated. `NOTICE` retained as a historical record with an explicit note that v0.1.0 (the only release to date) remains under Apache 2.0 by the LICENSE file present in that commit; v0.2.0 and all subsequent releases are under MIT.

---


## [0.1.0] — 2026-06-17

> **First public open-source release of OpenSquad.**

### Highlights
- Stable framework for local-first multi-agent collaboration (PM, Coder, QA, …) over group chat.
- Collab Card driven workflows for software dev, code review, research, and more.
- Plugin system with 21 built-in plugins (tool, hook, platform, service).
- Skill system, long-term semantic memory, and interruptible sleep.
- Web UI (React + TypeScript + Vite) with real-time WebSocket streaming.
- Multi-platform access: Web, Telegram, Feishu/Lark, QQ.
- Docker deployment and one-click install scripts (Linux/macOS/Windows).
- Desktop application build pipeline (Electron).

### Added (since 0.0.1)
- `py.typed` marker — type information is now exposed to downstream packages.
- Documentation hub (`docs/README.md`) consolidating EN + ZH guides.
- `RELEASING.md` — maintainer release checklist.
- Plugin ecosystem guide, plugin store publishing guide.
- CI doc-link check, Dependabot, and pre-commit configuration.
- Gateway UI: **Save & Restart** for plugin services (port changes apply after restart).

### Fixed
- WebSearch / Whisper service URL defaults aligned with `ports.websearch` (9001) and `ports.whisper` (5001).
- `batch_writer.get_nowait()`: a mutation could be dropped without execution under contention (data-loss bug).
- WebSocket broadcast: per-connection exceptions no longer affect other listeners.
- Bare `except Exception:` paths tightened to specific exception types.
- Gateway port `9555` is now the canonical default across code, examples, Docker, and install scripts.

### Performance
- Incremental token-count cache for OpenAI/Claude/Gemini — repeated `_prepare_messages()` calls are 30–50% faster.
- `ContextBuilder` parallelises state and wake-mode fetches via `asyncio.gather`.
- Unified `load_json_cached()` for hot reload and tool-output reads.
- `SessionManager` gains a JSON fast-path for deep copies.
- Skill prompt assembly is cached module-wide.
- `chat_api` enforces a 5,000-message history cap to defend against runaway sessions.
- `CharPrinter` extracted to a shared utility (was duplicated in 3 places).

### Security
- Default CORS origin list in example configs is now `localhost` allowlist, not `["*"]`.
- `gitleaks`, `bandit`, `pip-audit`, and `CodeQL` integrated into CI for secrets / SAST / SCA.
- All hardcoded internal paths and `local_agent` references in docs/i18n replaced with placeholders or `opensquad/`.


## [0.0.1] — 2026-05-29

### Added
- Multi-agent collaboration framework: PM, Coder, QA agents communicate via group chat.
- Collab Card driven workflows (software dev, code review, research, etc.).
- Plugin system: 21 built-in plugins (tool, hook, platform, service types).
- Skill system: reusable task instructions via Markdown files.
- Long-term memory with semantic search and recall.
- Interruptible sleep: agents can sleep and auto-wake on messages.
- MCP support for external tool server integration.
- Web UI (React + TypeScript + Vite) with real-time WebSocket streaming.
- Platform integrations: Telegram, Feishu/Lark, QQ.
- Desktop application build pipeline (Electron).
- CLI tool: `opensquad init`, `opensquad start`, `opensquad status`, `opensquad plugin`.
- Docker deployment support.
- CI pipeline: pytest, frontend build smoke test, release workflow.
- `CONTRIBUTING.md` / `CONTRIBUTING_ZH.md` — contribution guidelines.
- `CODE_OF_CONDUCT.md` — Contributor Covenant v2.1.
- `SECURITY.md` — vulnerability reporting policy.
- `NOTICE` — Apache 2.0 attribution notice.
- `.github/ISSUE_TEMPLATE/` — Bug Report and Feature Request templates.
- `.github/PULL_REQUEST_TEMPLATE.md` — PR checklist template.
- `.github/CODEOWNERS` — code ownership declarations.
- Bilingual documentation (Chinese + English) for architecture, collaboration, agent setup, plugin development, troubleshooting, and context flow.

---

[0.3.0]: https://github.com/opensquad-ai/opensquad/releases/tag/v0.3.0
[0.2.0]: https://github.com/opensquad-ai/opensquad/releases/tag/v0.2.0
[0.1.0]: https://github.com/opensquad-ai/opensquad/releases/tag/v0.1.0
[0.0.1]: https://github.com/opensquad-ai/opensquad/releases/tag/v0.0.1
