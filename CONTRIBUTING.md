# Contributing to OpenSquad

Thank you for your interest in contributing to OpenSquad!  
Please read this guide before opening an issue or submitting a pull request.

A Chinese version is available: [CONTRIBUTING_ZH.md](CONTRIBUTING_ZH.md)

**Branching & release process:** see [BRANCHING.md](BRANCHING.md) (中文：[BRANCHING_ZH.md](BRANCHING_ZH.md)) and [RELEASING.md](RELEASING.md).

---

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [How to Contribute](#how-to-contribute)
  - [Reporting Bugs](#reporting-bugs)
  - [Suggesting Features](#suggesting-features)
  - [Submitting Code](#submitting-code)
- [Sub-projects: where to contribute what](#sub-projects-where-to-contribute-what)
- [Development Setup](#development-setup)
- [Coding Standards](#coding-standards)
- [Commit Messages](#commit-messages)
- [Pull Request Process](#pull-request-process)

---

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md).  
By participating you agree to abide by its terms.

---

## Getting Started

1. Fork the repository: `https://github.com/opensquad-ai/opensquad`
2. Clone your fork locally.
3. Read [BRANCHING.md](BRANCHING.md) — the project uses a `main` + `dev` + `release/*` + `hotfix/*` model, not a single-branch GitHub Flow.
4. Pick the right base branch:
   - **New feature, bug fix, docs, chore, refactor** → base off `dev`
   - **Urgent production fix** → base off `main` as `hotfix/*`
5. Cut a branch with the right name: `git checkout -b feature/<module>-<desc> dev`
6. Make your changes, add tests, and commit (follow Conventional Commits — see below).
7. Push your branch and open a pull request **to `dev`** (or to `main` for hotfixes).

> Default base is `dev`. The `main` branch only receives merges from `release/*`
> (release tags) and `hotfix/*` (emergency patches). See [BRANCHING.md](BRANCHING.md)
> for the full strategy, naming convention, worked examples, and the
> release/hotflow process.

---

## How to Contribute

### Reporting Bugs

- Search existing issues first to avoid duplicates.
- Use the **Bug Report** issue template.
- Include: OS, Python version, reproduction steps, expected vs. actual behavior, and any relevant logs.

### Suggesting Features

- Use the **Feature Request** issue template.
- Describe the problem you are solving and why the proposed approach is the best fit.

### Submitting Code

- Keep pull requests focused on a single concern.
- Link the relevant issue in the PR description (e.g., `Closes #42`).
- All CI checks must pass before a PR can be merged.

---

## Development Setup

Recommended: [uv](https://github.com/astral-sh/uv)

```bash
uv sync
cd src/opensquad/gateway/nexuschat-pro && npm install && cd ../../../..
uv run pytest tests/
```

Legacy pip path:

```bash
python -m pip install --upgrade pip
pip install -e .
pytest tests/
```

### Workspace vs install directory

- **Install directory**: this git clone (`src/opensquad`, `src/plugins`, …).
- **Workspace**: runtime data (`~/.opensquad/workspace` by default) — agents, `data/plugins/*/config.json`, logs. **Never commit workspace contents.**

Copy example config before first run:

```bash
cp system_config.example.json system_config.json
# Edit node_secret and ports; keep the file out of git (see .gitignore)
```

### Pre-commit hooks

```bash
pip install pre-commit
pre-commit install
pre-commit run --all-files
```

Hooks: `ruff` lint/format, YAML/JSON checks, `detect-private-key`. See `.pre-commit-config.yaml`.

### Good first contributions

| Area | Where to start |
|------|----------------|
| Docs | `doc_en/`, `doc_cn/`, `docs/README.md` — see [Documentation folder structure](#documentation-folder-structure) below |
| Plugin | `src/plugins/`, [PLUGIN_DEVELOPMENT](doc_en/PLUGIN_DEVELOPMENT.md) |
| Skill | `src/skills/` |
| Tests | `tests/` |
| Gateway UI | `src/opensquad/gateway/nexuschat-pro/` |

Use the **Good First Contribution** issue template or ask in a issue before large changes.

## Coding Standards

| Area | Standard |
|------|----------|
| Python | PEP 8; type hints encouraged |
| TypeScript / React | ESLint + Prettier (config in `nexuschat-pro/`) |
| Imports | `isort` for Python; absolute imports preferred |
| Tests | `pytest`; new features should ship with tests in `tests/` |
| Docstrings | Google style for public functions/classes |

---

## Commit Messages

Follow the [Conventional Commits](https://www.conventionalcommits.org/) specification:

```
<type>(<scope>): <short summary>

[optional body]

[optional footer]
```

Common types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`.

Examples:
```
feat(plugins): add vcs_remote user_info tool
fix(gateway): handle missing node_secret gracefully
docs: update CONTRIBUTING with dev setup steps
```

---

## Pull Request Process

1. Ensure all tests pass (`pytest tests/`).
2. Update relevant documentation if behavior changes.
3. A maintainer will review within a few business days.
4. Squash-and-merge is the default merge strategy.

### Critical safety rules

- Do not use `git add -A` in regular workflow.
- Stage explicit files only.
- Ensure local/runtime files are never tracked (CI guard enforces this).

---

## Sub-projects: where to contribute what

OpenSquad is split across **multiple repositories**. Open your PR in the
right one — submitting a new plugin here, or a core runtime change in
the skills repo, will be closed and redirected.

| What you're changing                                                          | Repository                                                                              | Branching model                                |
|-------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------|------------------------------------------------|
| **Core framework** (gateway, agent runtime, runner, tools, CLI, launcher)     | **[opensquad-ai/opensquad](https://github.com/opensquad-ai/opensquad)** *(this repo)*   | `main` + `dev` + `release/*` + `hotfix/*`     |
| **Roles** (agent role cards / personas / character definitions)               | [opensquad-ai/opensquad-roles](https://github.com/opensquad-ai/opensquad-roles)         | simpler (PR → `main`, see below)               |
| **Collaboration cards** (group-chat templates, multi-agent scenarios)         | [opensquad-ai/opensquad-collab-cards](https://github.com/opensquad-ai/opensquad-collab-cards) | simpler (PR → `main`)                       |
| **Skills** (agent capabilities, workflow recipes)                             | [opensquad-ai/opensquad-skills](https://github.com/opensquad-ai/opensquad-skills)       | simpler (PR → `main`)                          |
| **Plugins** (channel adapters, tool integrations, non-core plugins)           | [opensquad-ai/opensquad-plugins](https://github.com/opensquad-ai/opensquad-plugins)     | simpler (PR → `main`)                          |

> The Plugin Market UI in the desktop client already points contributors
> at `opensquad-ai/opensquad-plugins` for new plugin submissions.

### Built-in vs. contributed content (this repo)

This repository (`opensquad-ai/opensquad`) only ships the **built-in
core plugins** — the system-level tools every OpenSquad install needs
to function. They are listed in [`src/plugins/builtin_plugins.json`](src/plugins/builtin_plugins.json),
default-enabled, and cannot be uninstalled.

All other content (roles, collaboration cards, skills, non-core plugins)
lives in the corresponding sub-project repo above. To submit one, open
the PR **there**, not here.

### Branching model for sub-projects

Sub-projects (roles / collab-cards / skills / plugins) ship **declarative
content** (Markdown / JSON / Python with no runtime coupling), so they
use a simpler workflow:

- Single `main` branch, protected.
- Feature branches: `feature/<name>`, `fix/<name>`, `docs/<name>`.
- PRs target `main` directly; squash-merge; branch auto-deleted.
- `main` only receives merges that have been CI-green + reviewed.
- No `dev` / `release/*` overhead — content is small, reviewable in
  minutes, and any regression is reverted with a follow-up PR.

The full `main` + `dev` + `release/*` + `hotfix/*` model is reserved
for **this core framework repo**, where a bad merge breaks everyone's
runtime. See [BRANCHING.md](BRANCHING.md) for that model.

### Cross-repo changes

If your change spans the core framework **and** a sub-project:

1. Open the sub-project PR first (cheaper to merge, faster feedback).
2. Then open the core framework PR and reference the sub-project PR
   number in the body (e.g. `Depends on opensquad-ai/opensquad-skills#42`).
3. The maintainer will sequence the merges; do **not** force-merge the
   core PR before the sub-project side is in.

## Documentation folder structure

This repo has three documentation folders, each with a different purpose.
Putting a doc in the wrong folder (or using the wrong naming convention)
is one of the easiest ways to make the docs confusing for future readers.

| Folder | Purpose | Naming |
|--------|---------|--------|
| `doc_en/` | English user guides (getting started, architecture, deployment, plugin development, troubleshooting, …) | `FOO.md` — no suffix needed |
| `doc_cn/` | Chinese user guides — mirror of `doc_en/` | `FOO.md` — no suffix needed; the folder already implies the language |
| `docs/` | Cross-language, supplementary, and **maintainer-facing** docs (security baseline, GitHub settings, this repo's own meta-README) | `FOO.md` — language-neutral or maintainer-only content; no user-facing EN or ZH content |
| Root | Project-level files (`README.md`, `BRANCHING.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`, `LICENSE`) | `FOO.md` (EN) / `FOO_ZH.md` (CN) — suffix disambiguates because both live at root |

### Rules

- **Bilingual user-facing doc** → add the same `FOO.md` filename to both
  `doc_en/` and `doc_cn/`. If only one language exists, put the translated
  version in its language folder; the other language is a follow-up —
  don't leave a single-language user doc in `docs/`.
- **Maintainer-only doc** (security baseline, repo settings, internal
  development reports) → `docs/`.
- **Project-level doc** (applies to the whole project, not a subsystem) →
  root, with `_ZH.md` suffix for Chinese.
- **New doc that has both `_EN.md` or `_ZH.md` suffix inside `doc_en/` or
  `doc_cn/`** → the suffix is redundant, rename to plain `FOO.md`. The
  folder already implies the language.

### Don'ts

- ❌ Put user-facing English content in `docs/` — use `doc_en/`.
- ❌ Put user-facing Chinese content in `docs/` — use `doc_cn/`.
- ❌ Use `_EN.md` / `_ZH.md` suffix inside `doc_en/` or `doc_cn/` —
  the folder already implies the language.
- ❌ Use `_ZH.md` suffix in `docs/` (it's not a Chinese folder; if the
  doc is only relevant to Chinese-speaking maintainers, leave it in
  `docs/` without a suffix and just have the content be Chinese).
- ❌ Use `_EN.md` suffix in `docs/` for the same reason.

### Examples

| Adding this doc | Goes in | Filename |
|-----------------|---------|----------|
| English guide to deploying on Kubernetes | `doc_en/` | `kubernetes_deployment.md` |
| Chinese mirror of the same guide | `doc_cn/` | `kubernetes_deployment.md` |
| Internal report on Q2 perf measurements | `docs/` | `perf-q2-2026.md` |
| Chinese-only detailed guide on the `agent_factory` plugin | `doc_cn/` | `agent_factory_guide.md` (no `_ZH` suffix) |
| English + Chinese agent-management reference | `doc_en/` + `doc_cn/` | `agent_management.md` (mirror pair) |
| Project-level Chinese release notes draft | root | `RELEASE_NOTES_ZH.md` |

For the current contents of each folder, see the corresponding README:

- [doc_en/README.md](doc_en/README.md) — index of English user guides
- [doc_cn/README.md](doc_cn/README.md) — index of Chinese user guides
- [docs/README.md](docs/README.md) — what's in `docs/` and why

---

*OpenSquad Contributors — Licensed under MIT*
