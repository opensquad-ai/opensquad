# Playwright UI smoke (nexuschat-pro)

Critical-path browser tests for OpenSquad Web UI. **Not** the same as Python
`playwright` used by websearch / MCP.

## Prerequisites

1. Gateway is running and serving the built (or packaged) UI:

   ```bash
   # from repo root
   uv run opensquad start
   ```

   Default UI: `http://127.0.0.1:9555`

2. A login account exists. Defaults match local smoke scripts:

   | Env | Default |
   |-----|---------|
   | `E2E_EMAIL` | `ss@ss` |
   | `E2E_PASSWORD` | `ssssss` |
   | `E2E_BASE_URL` | `http://127.0.0.1:9555` |

3. Install browser once:

   ```bash
   cd src/opensquad/gateway/nexuschat-pro
   npm install
   npx playwright install chromium
   ```

## Run

```bash
cd src/opensquad/gateway/nexuschat-pro
npm run e2e:smoke          # @smoke tagged tests only
npm run e2e                # all e2e specs
```

## What is covered

1. **Login** — AuthScreen → chat list visible
2. **Enter group** — open first group (or create one if empty) → chat window visible

LLM agent replies are **not** asserted here (use `scripts/smoke_chat.py`).

## CI note

These tests are **not** wired into GitHub Actions yet — they require a live
Gateway. Run locally before release when touching auth / group chat UI.
