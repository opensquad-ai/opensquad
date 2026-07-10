# Security Baseline

OpenSquad runs two CI tracks. This document reflects what is actually enforced
today (not aspirational targets).

| Track | Workflow | When |
|-------|----------|------|
| Fast gate | [`ci-fast.yml`](../.github/workflows/ci-fast.yml) | Push to `main`/`dev`, PRs → `dev` |
| Full gate | [`ci.yml`](../.github/workflows/ci.yml) | PRs → `main` (release gate) |

---

## Static Analysis (SAST)

**Tool**: bandit (CI job `sast` in `ci.yml`)

**Current mode**: strict — `--severity-level high --confidence-level high --exit-non-zero-on-failure`

**Excluded checks**: B101 (assert statements — development use only)

**ci-fast**: not run (keeps the daily gate cheap).

---

## Dependency Vulnerability Scan (SCA)

**Tool**: pip-audit (CI job `sca` in `ci.yml`)

**Current mode**: `--strict --progress-spinner=off` (fails the full gate on known issues)

**ci-fast**: not run.

---

## CodeQL

**Tool**: GitHub CodeQL (CI job `codeql` in `ci.yml`)

**Languages**: Python

**Mode**: `security-extended` query suite

**Status**: Active, results visible in GitHub Security tab

**ci-fast**: not run.

---

## Secrets Detection

**Tool**: gitleaks

**Status**: Active in both `ci-fast.yml` (`secrets-scan`) and `ci.yml`

**Config**: [`.gitleaks.toml`](../.gitleaks.toml)

---

## Dependabot

**Status**: Active (`pip`, `npm`, `github-actions` ecosystems)

**Schedule**: Weekly (pip/npm), Monthly (github-actions)

---

## Container Security

- Base image: `python:3.11-slim` (no SHA256 pin — planned)
- User: Non-root `opensquad`
- Ports exposed: 9555 (gateway), 9600 (launcher), 9720 (plugin_registry)

---

## Key Contacts

- Report vulnerabilities via GitHub Security Advisory
- Response SLA: 3 days acknowledgement, 30 days fix
