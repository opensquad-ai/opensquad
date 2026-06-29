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

_No unreleased changes yet._

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
