# Security Baseline

## Static Analysis (SAST)

**Tool**: bandit (CI job `sast`)

**Current mode**: `--exit-zero` (informational, does not fail CI)

**Target**: After 1 month of data collection, switch to strict mode:
- High confidence + High severity → exit 1
- All others → warnings only

**Excluded checks**: B101 (assert statements — development use only)

---

## Dependency Vulnerability Scan (SCA)

**Tool**: pip-audit (CI job `sca`)

**Current mode**: `--strict --progress-spinner=off` (reports but does not fail CI)

**Target**: After 1 month of data collection:
- Known exploited CVEs → exit 1
- All others → warnings only

---

## CodeQL

**Tool**: GitHub CodeQL (CI job `codeql`)

**Languages**: Python

**Mode**: `security-extended` query suite

**Status**: Active, results visible in GitHub Security tab

---

## Secrets Detection

**Tool**: None (planned)

**Recommendation**: Add gitleaks or truffleHog to CI pipeline

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
