# Releasing OpenSquad

> Step-by-step for the release captain. **Why** we use this branching model,
> the version-bump policy, and worked examples (release flow + hotfix from an
> old tag) live in [BRANCHING.md](BRANCHING.md). Read that first if you
> haven't recently — this file is the **how**, that one is the **why**.

## TL;DR

1. `git checkout dev && git checkout -b release/0.X.Y && git push -u origin release/0.X.Y`
2. Bump **`pyproject.toml` only** (`0.X.0.dev0` → `0.X.0`), then run `python scripts/sync_version.py` to update `__init__.py` and `package.json`.
3. Move the `[Unreleased]` section in `CHANGELOG.md` into `## [0.X.0] — YYYY-MM-DD`.
4. `chore(release): prepare v0.X.0` on `release/0.X.Y`, **PR to `main`**.
5. Wait for `ci.yml` (the full gate) to go green. Merge.
6. `git checkout main && git tag -a v0.X.0 -m "v0.X.0" && git push origin v0.X.0`.
7. `release.yml` runs: Docker image → `ghcr.io/opensquad-ai/opensquad:0.X.0` + `:latest`, package → PyPI, GitHub Release. **Verify all three.**
8. **Absorb main back into dev**, bump dev to next `.dev0` (see [BRANCHING.md](BRANCHING.md) for "which bump?").
9. **Delete the `release/0.X.Y` branch** locally and on remote. Tags are the long-lived record.

The whole flow normally takes 30–60 minutes if the gates are green.

## Versioning

- Follow [Semantic Versioning](https://semver.org/).
- **Single source of truth:** `pyproject.toml` → `[project].version`.
- After changing `pyproject.toml`, run:

  ```bash
  python scripts/sync_version.py
  ```

  This updates `src/opensquad/__init__.py::__version__` (same PEP 440 string),
  root `package.json`, root `package-lock.json`, and
  `src/opensquad/gateway/nexuschat-pro/package.json`
  (Electron `app.getVersion()`, npm semver e.g. `0.4.2.dev0` → `0.4.2-dev.0`).
  Then refresh the lockfile so editable package metadata matches:

  ```bash
  uv lock
  ```

  CI runs `python scripts/sync_version.py --check` on every push to `dev`/`main`.
  Pre-commit auto-syncs when `pyproject.toml` is staged.
- See [BRANCHING.md](BRANCHING.md) → "When to bump minor vs patch" for the cheat sheet (SemVer rule 4 for `0.x.y`, PEP 440 markers, deployer-effort heuristic).
- Update `CHANGELOG.md` **before** each release. Move `[Unreleased]` items into a dated `## [X.Y.Z] — YYYY-MM-DD` section; open a fresh `[Unreleased]` afterwards.

| Component | Published to |
|-----------|-------------|
| Python package `opensquad` | Git tags + PyPI (via `release.yml`, OIDC trusted publishing) |
| Docker image | `ghcr.io/opensquad-ai/opensquad:X.Y.Z` (and `:latest` on final release) |
| Gateway frontend | Bundled in repo / Docker image (built inside the image, not a separate artifact) |
| Desktop (Electron) | `.github/workflows/build-desktop.yml` on `v*` tag |
| npm package `opensquad-ai` | `.github/workflows/release-npm.yml` on `v*` tag (bootstrap wrapper, see below) |

## Pre-flight checklist (before cutting the release branch)

- [ ] **dev CI is green** (`ci-fast.yml` is the daily gate — check the last run on `origin/dev`).
- [ ] **The full gate is reachable.** `ci.yml` runs on PRs to `main`; if your GitHub Actions budget is exhausted the heavy checks (multi-Python, mypy, bandit, pip-audit, CodeQL) will silently be skipped. Confirm before relying on the gate to catch things.
- [ ] **`CHANGELOG.md` `[Unreleased]` is accurate.** Every change since the last release should be there, grouped by `### Added` / `### Changed` / `### Fixed` / `### Docs` / `### Migration`. The release PR is the right place to fix omissions.
- [ ] **No half-finished work on dev.** A feature you don't want in this release should be on its own branch, not on `dev`.
- [ ] **You know what the next bump should be** — PATCH, MINOR, or POST? (See [BRANCHING.md](BRANCHING.md).)

## Cut a release (full flow)

The full flow, with commands:

```bash
# 1. Branch — from dev (most releases) or an old tag (hotfixes; see below)
git checkout dev && git pull --ff-only
git checkout -b release/0.X.Y
git push -u origin release/0.X.Y

# 2. Bump version in pyproject.toml only (0.X.0.dev0 → 0.X.0), then:
#    python scripts/sync_version.py
#    See the cheat sheet in BRANCHING.md for the right bump level.

# 3. CHANGELOG.md: rename the [Unreleased] section to [0.X.0] — YYYY-MM-DD.
#    Add a fresh [Unreleased] below it for the next cycle.

# 4. Commit + PR
git add pyproject.toml src/opensquad/__init__.py package.json CHANGELOG.md
git commit -m "chore(release): prepare v0.X.0"
git push -u origin release/0.X.Y
# Open PR: release/0.X.Y  →  main
```

Then wait for `ci.yml` to go green on the PR, merge, and continue:

```bash
# 5. Tag from main
git checkout main && git pull --ff-only
git tag -a v0.X.0 -m "v0.X.0"
git push origin v0.X.0

# 6. release.yml runs automatically:
#    - validate job: tag version == pyproject.toml version (else fail loudly)
#    - docker job: builds & pushes ghcr.io/opensquad-ai/opensquad:0.X.0 and :latest
#    - pypi job: runs scripts/verify_release_artifacts.py --tree, builds the
#      wheel + sdist, re-runs the verifier on them, and only then publishes via
#      OIDC trusted publishing
#    - release job: generates GitHub Release notes from commits since the previous tag
#    Verify all three in:
#      - https://github.com/opensquad-ai/opensquad/releases/tag/v0.X.0
#      - https://pypi.org/project/opensquad/#history
#      - https://github.com/opensquad-ai/opensquad/pkgs/container/opensquad
#
#    NEVER `twine upload` a locally built dist/. The repo is public and the
#    working tree carries private plugins/skills, local model cards with real
#    API keys, local agent dirs (agent301 holds a live model.api_key), plugin
#    UI node_modules and hand-made debug dumps. verify_release_artifacts.py
#    catches all of that, but only the tag → CI path runs it. To check a local
#    build before tagging:
#        python scripts/verify_release_artifacts.py --tree   # pre-build, ~1 min
#        python -m build && python scripts/verify_release_artifacts.py dist
#    Note for local builds: a stale `build/lib` from an earlier run is packed
#    into the wheel on top of the fresh file set, so `rm -rf build/lib` (or let
#    the artifact check fail) before trusting a local wheel.

# 7. Absorb main back into dev
git checkout dev && git pull --ff-only
git merge --no-ff origin/main -m "Merge branch 'main' into dev (absorb v0.X.0 release)"

# 8. Bump dev to the next .dev0
#    See BRANCHING.md cheat sheet. PATCH next → 0.X.1.dev0.
#    MINOR next → 0.(X+1).0.dev0.
#    Bump pyproject.toml only, then: python scripts/sync_version.py
git add pyproject.toml src/opensquad/__init__.py package.json
git commit -m "chore(dev): bump to 0.X.(Y+1).dev0 after v0.X.Y release"
git push origin dev

# 9. Delete the release branch — it's done its job
git push origin --delete release/0.X.Y
git branch -d release/0.X.Y
```

**Common gotchas** (full list in [BRANCHING.md](BRANCHING.md) → "Common pitfalls"):

- The `validate` job in `release.yml` will **fail loudly** if `pyproject.toml` version doesn't match the tag. Bump **`pyproject.toml` only**, then run `python scripts/sync_version.py` before committing. CI also runs `sync_version.py --check`.
- Don't `git push --tags` indiscriminately. Push the **one** tag you just made.
- Don't merge the release PR without running `sync_version.py` — stale `__init__.py` / `package.json` caused the original `v0.1.1`-stuck-in-the-frontend incident.

## Hotfix (patch from an old tag)

Sometimes you need to ship a fix against an already-released version without taking the latest dev work. The pattern:

```bash
# Cut from the OLD tag, not from dev
git checkout v0.X.Y
git checkout -b hotfix/0.X.(Y+1)
git push -u origin hotfix/0.X.(Y+1)

# Apply the fix, bump version, update CHANGELOG
# ...

# PR hotfix/0.X.(Y+1)  →  main
# Tag v0.X.(Y+1) from main, push, let release.yml do its thing

# IMPORTANT: cherry-pick or merge the fix into dev too, so dev doesn't regress.
git checkout dev
git cherry-pick <fix-commit-sha>      # or: git merge --no-ff origin/main
# ... then bump dev to the next .dev0 as usual.
```

See [BRANCHING.md](BRANCHING.md) → Example F for the full worked example.

## Docker

```bash
# Locally:
docker compose build
docker tag opensquad:latest opensquad:0.X.Y

# In CI: the `docker` job in release.yml does this automatically on every v* tag.
# Verify after a release:
docker pull ghcr.io/opensquad-ai/opensquad:0.X.Y
```

See [doc_en/deployment_guide.md](doc_en/deployment_guide.md) for production deployment.

## NPM packaging (npm bootstrap)

The repo ships a thin Node.js wrapper that lets JavaScript users install
OpenSquad via npm. The wrapper is a **bootstrap** — it doesn't replace
the Python CLI, it just installs it and forwards commands.

### Package metadata

| Field | Value |
|-------|-------|
| npm name | `opensquad-ai` |
| bin name | `opensquad` |
| License | MIT (matches `LICENSE` at the repo root) |
| Source | `package.json` + `bin/opensquad.js` |
| Workflow | `.github/workflows/release-npm.yml` |
| Trigger | push of any `v*` tag |

The package name is **unscoped** (`opensquad-ai`, not `@opensquad-ai/opensquad`)
because the npm org `opensquad-ai` does not exist. The unscoped name
`opensquad` is already taken on the public registry by an unrelated
project, so the `-ai` suffix is what keeps us distinct.

### How the bootstrap works

When a user runs `npx opensquad-ai`:

1. The Node.js script `bin/opensquad.js` runs.
2. It detects Python 3.11+ on `PATH` (`python3` or `python`).
3. It checks if the matching `opensquad==X.Y.Z` PyPI package is
   installed. If not, it `pip install --user opensquad==X.Y.Z`.
4. It `exec`s the real `opensquad` Python CLI with the user's
   arguments and exits with the same code.

So users get a familiar short command (`opensquad`) without
needing to know they crossed a language boundary:

```bash
npm install -g opensquad-ai
opensquad --version
opensquad run ...
```

### Cut a new npm release

The npm package is published by `release-npm.yml` on every `v*` tag push —
no separate `npm publish` step is needed. It runs in parallel with
`release.yml` (Python package + Docker).

Requirements for the workflow to succeed:

1. The tag matches `package.json` version (the workflow's `validate` job
   enforces this; `scripts/sync_version.py` keeps the two in sync).
2. The **trusted publisher is configured on npmjs.com** for the package
   (`https://www.npmjs.com/package/opensquad-ai/access` → *Trusted
   Publisher*): Organization/user `opensquad-ai`, Repository `opensquad`,
   Workflow filename `release-npm.yml`, Environment **empty**. There is no
   `NPM_TOKEN` any more — the job authenticates with the OIDC token from
   `id-token: write`. A wrong value in any of those four fields makes the
   token exchange fail, and npm reports it as a bare `ENEEDAUTH` in the
   CLI or a misleading `404`; a typo in the workflow name is the easy one
   to make (`releases-npm.yml` vs `release-npm.yml`).
3. npm CLI ≥ 11.5.1 in the job. `release-npm.yml` installs `npm@11`
   explicitly: `npm@latest` is now npm 12, which no longer performs the
   OIDC exchange.

The publish step runs with `--loglevel verbose` on purpose: when the
token exchange fails, npm logs the reason only at verbose/silly and then
degrades silently into that `ENEEDAUTH` / `404`.

### Approve the staged release

**A green `Release (npm)` job does not mean users can install the new
version.** The publish lands in npm's *staging* area: trusted-publisher
publishes may always stage, and npm routes dual-use packages (an AI
coding agent qualifies) through staging by policy. Until a maintainer
approves the staged version **with 2FA**, `npm install -g opensquad-ai`
keeps resolving to the previous version.

Approve either way — both prompt for 2FA:

- **npmjs.com** → the package page → **Staged Packages** tab → *Approve*.
- **CLI** → `npm login`, then `npm stage list opensquad-ai` and
  `npm stage approve <stage-id>`. `npm stage reject <stage-id>` discards
  a bad one.

The workflow's last step reads the registry's `dist-tags` and prints a
warning when the version is not public yet, so a staged release is never
mistaken for a published one.

### Re-publishing after a failed job

Do **not** re-tag: moving the tag re-runs `release.yml`, whose PyPI upload
of an already-published version fails. Dispatch the workflow from a ref
that still carries the released version instead:

```bash
gh workflow run release-npm.yml --ref <ref>
```

`workflow_dispatch` skips the tag-name comparison and publishes the
`package.json` version of that ref, so the ref must still carry the
released version — `dev` is bumped to the next `.dev0` right after every
release and will be refused as a prerelease, and the tagged commit carries
whatever workflow file it had when it was tagged (fixes made later are not
retroactive). A short-lived branch cut from the tag with the workflow fix
cherry-picked onto it is the reliable form.

### Why a thin wrapper, not a real npm package?

A real JS/TS SDK would need to either:

- Re-implement the Python runtime in JavaScript (huge), or
- Spawn the Python subprocess anyway (so the user pays the cost
  regardless).

A bootstrap that delegates to the existing Python CLI gives npm
presence, discoverability, and a friendly `npx` entry point for
JS developers, while keeping a single source of truth (the Python
code). It also means the npm package and PyPI package are always
the same version with the same source.

If a real JS SDK is needed later, it should live in a separate
package (`opensquad-ai-sdk` or similar), not replace this
bootstrap.

## Pre-release protocol (alpha / beta / rc)

When a release needs to soak with early users before going final — for
example, when a minor release has a risky new gateway feature — cut
intermediate tags **on the `release/0.X.Y` branch** (which is, per
[BRANCHING.md](BRANCHING.md), a short-lived working branch) before the
final `v0.X.Y` tag. The release branch is still deleted after the final
tag — the pre-release tags live in git history as the soak record.

### Version progression

```
v0.X.Y-alpha.1   ← internal, expect breakage
v0.X.Y-alpha.2
v0.X.Y-beta.1    ← feature-complete, may have bugs
v0.X.Y-beta.2
v0.X.Y-rc.1      ← frozen, bug fixes only
v0.X.Y-rc.2
v0.X.Y           ← final, immutable forever
```

The same suffix maps to a different PEP 440 number in `pyproject.toml`
and on PyPI:

| Tag | `pyproject.toml` | PyPI version |
|-----|------------------|--------------|
| `v0.X.Y-alpha.1` | `0.X.Ya1` | `0.X.Ya1` |
| `v0.X.Y-beta.1`  | `0.X.Yb1` | `0.X.Yb1` |
| `v0.X.Y-rc.1`    | `0.X.Yrc1` | `0.X.Yrc1` |
| `v0.X.Y`         | `0.X.Y`   | `0.X.Y` |

### Cut a pre-release

1. Be on the release branch (short-lived, per [BRANCHING.md](BRANCHING.md)):
   ```bash
   git checkout release/0.X.Y
   ```
2. Bump `pyproject.toml` to the pre-release version (e.g. `0.X.Yb1`), then `python scripts/sync_version.py`.
3. Commit: `chore(release): prepare v0.X.Y-beta.1`.
4. Tag and push:
   ```bash
   git tag -a v0.X.Y-beta.1 -m "v0.X.Y-beta.1"
   git push origin release/0.X.Y --tags
   ```
5. `release.yml` runs automatically. It detects the `-beta` suffix and:
   - Marks the GitHub Release as **Pre-release** (de-emphasized in the UI).
   - Publishes the package with PEP 440 numbering (`opensquad==0.X.Yb1`).
   - Pushes a Docker image tagged `0.X.Y-beta.1` (not `latest`).

### Promote to the next pre-release

```bash
# Bump pyproject.toml: 0.X.Yb1 → 0.X.Yb2
git commit -am "chore(release): prepare v0.X.Y-beta.2"
git tag -a v0.X.Y-beta.2 -m "v0.X.Y-beta.2"
git push origin release/0.X.Y --tags
```

### Cut the final release

When pre-releases are stable:

1. Bump `pyproject.toml` back to plain `0.X.Y`, then `python scripts/sync_version.py`.
2. Commit: `chore(release): finalize v0.X.Y`.
3. PR to `main` (or merge the existing release branch), tag, push — same
   as the regular "Cut a release" flow above. The release.yml validate
   job detects no suffix → full Release, no Pre-release flag, `latest`
   Docker tag moves.
4. After the final tag, **delete the `release/0.X.Y` branch** as usual.

### Why pre-releases don't leak to users

- **PyPI**: `pip install opensquad` will not install a pre-release by
  default. Users have to write `pip install opensquad==0.X.Yb1` explicitly.
- **Docker**: pre-release images are tagged `0.X.Y-beta.1`, never
  `latest`. `docker pull opensquad` (or any `:latest` consumer) is safe.
- **GitHub Releases**: the Pre-release badge hides them from the main
  Releases feed; only the final `v0.X.Y` appears prominently.

### When NOT to cut a pre-release

- Trivial patches (typo, doc fix, dep bump) — go straight to `v0.X.Y`.
- Hotfixes that the maintainer controls end-to-end.
- v0.1.0 and v0.2.0 and v0.3.0 were all released without pre-release
  tags. That's fine for a `0.x` line; the pre-release protocol is
  opt-in per release, not mandatory.

### Abandoning a bad pre-release

If a beta turns out to be broken, just **don't tag the next one**. The
broken `v0.X.Y-beta.N` tag stays in git history but no one auto-upgrades
to it, so it's safe to leave in place. If you really need to yank it
from PyPI (e.g. it bricks installs), use `pip yank` (yanks but doesn't
delete — historical record preserved) and document the issue in the
GitHub Release.

## Post-release

After the final tag is pushed and `release.yml` completes:

- [ ] **GitHub Release looks right** — notes render, artifacts attached, pre-release flag correct.
- [ ] **Desktop installers attached** — `build-desktop.yml` finishes its
  `attach-to-release` job (~10–15 min after the tag). Check Actions →
  **Build Desktop App** → **Attach desktop artifacts to Release**. Five files
  expected: Windows `OpenSquad-X.Y.Z-win-x64-Setup.exe`, Linux
  `…-linux-x86_64.AppImage` + `…-linux-amd64.deb`, macOS `…-mac-x64.dmg` +
  `…-mac-arm64.dmg` (no Windows portable — `package.json`'s `win.target` is
  `nsis` only; see [desktop_build.md](doc_en/desktop_build.md)).
- [ ] **Docker image is on `ghcr.io/opensquad-ai/opensquad:0.X.Y` and `:latest`** (final release only).
- [ ] **PyPI shows the new version** at https://pypi.org/project/opensquad/#history.
- [ ] **npm package published *and approved*** (`opensquad-ai` on the public
  registry). A version that is only staged is not installable — see
  [Approve the staged release](#approve-the-staged-release).
- [ ] **`dev` is bumped** to the next `.dev0` (per [BRANCHING.md](BRANCHING.md) cheat sheet) and pushed.
- [ ] **`[Unreleased]` section in `CHANGELOG.md` is open on dev** for the next cycle.
- [ ] **Release branch deleted** locally and on remote.
- [ ] **Monitor Dependabot / GitHub Security Advisories** in the days after.

## Desktop installers on GitHub Releases

Desktop builds are **not** produced by `release.yml`. They come from
`.github/workflows/build-desktop.yml`, which runs in parallel on every `v*`
tag push:

1. **build-backend** — PyInstaller on Windows / macOS / Linux (matrix), then
   `scripts/check_backend_bundle.py` (rejects nested `opensquad/build` /
   Electron `*-unpacked` pollution inside `_internal`).
   macOS uses a **pinned `macos-15` runner** with
   `MACOSX_DEPLOYMENT_TARGET=12.0` so the frozen backend runs on
   **macOS 12 Monterey and newer** (do not use `macos-latest` for this job).
2. **build-electron** — electron-builder installers per OS (same macOS pin +
   deployment target; `minimumSystemVersion: 12.0`).
3. **attach-to-release** — uploads `.exe` / `.dmg` / `.AppImage` / `.deb` to
   the GitHub Release with the same tag name (`overwrite_files: true` replaces
   same-named assets).

**Local one-shot (Windows):** from repo root,
`powershell -ExecutionPolicy Bypass -File scripts\build_desktop.ps1`
(runs `build_backend.bat` → bundle check → `npm run electron:win`).
macOS/Linux: `bash scripts/build_desktop.sh`.

Official installer output is always repo-root `build/release/`. Do **not**
write Electron trees under `src/opensquad/build/` (gitignored; would be
pulled into `run.exe` by `collect_data_files`).

`release.yml` creates the Release page and notes first; `build-desktop.yml`
only **adds/replaces** binary assets (~10–15 minutes later). Do not panic if
the Release page appears before the installers show up.

### macOS code signing & notarization (optional, recommended)

Without Apple credentials, CI still produces **unsigned** `.dmg` / `.zip`
(Gatekeeper requires right-click → Open). To ship signed + notarized builds,
add these **repository secrets** and re-run `build-desktop.yml`:

| Secret | Purpose |
|--------|---------|
| `CSC_LINK` | Base64-encoded Developer ID Application `.p12` (or file path on self-hosted) |
| `CSC_KEY_PASSWORD` | Password for that `.p12` |
| `APPLE_ID` | Apple ID used for notarization |
| `APPLE_APP_SPECIFIC_PASSWORD` | App-specific password for that Apple ID |
| `APPLE_TEAM_ID` | 10-character Team ID |

Alternatively (App Store Connect API key instead of Apple ID password):

| Secret | Purpose |
|--------|---------|
| `APPLE_API_KEY` | Contents or path of the `.p8` key |
| `APPLE_API_KEY_ID` | Key ID |
| `APPLE_API_ISSUER` | Issuer UUID |

`scripts/notarize.cjs` (electron-builder `afterSign`) runs only when one of
those auth sets is present; missing secrets keep the unsigned path.

### Cut a release with fresh desktop Assets (normal flow)

Follow the usual tag flow in this file. After `git push origin v0.X.Y`:

```bash
# Watch the desktop pipeline
gh run list --workflow=build-desktop.yml --limit 3
gh run watch   # pick the run id for the tag you just pushed
```

When `attach-to-release` is green, verify:

```bash
gh release view v0.X.Y --json assets --jq '.assets[].name'
```

### Refresh Assets on an **existing** release tag (no new tag)

Use when desktop fixes landed on `dev`/`main` but you do not want a full
PyPI/Docker/npm bump yet, or when you need to replace broken installers on
`v0.4.0`:

1. GitHub → **Actions** → **Build Desktop App** → **Run workflow**.
2. Choose the **branch** to build from (usually `dev` or `main`).
3. Set **release_tag** to the existing tag, e.g. `v0.4.0`.
4. Run. When `attach-to-release` succeeds, same-named Assets on that Release
   are overwritten.

CLI equivalent:

```bash
gh workflow run build-desktop.yml \
  --ref dev \
  -f release_tag=v0.4.0
```

**Caveat:** the binaries are built from the branch you select, but the Release
tag name stays the same — document in the Release notes if the semver tag no
longer matches the exact commit. For user-facing semver, prefer cutting
`v0.X.(Y+1)` instead.

### Re-tag (last resort)

GitHub does not re-fire tag workflows on an existing tag. To rebuild from a
specific commit **and** move the tag:

```bash
git checkout main && git pull
git tag -d v0.X.Y && git push origin :refs/tags/v0.X.Y   # delete remote tag
git tag -a v0.X.Y -m "v0.X.Y" && git push origin v0.X.Y  # re-create on current HEAD
```

This re-triggers both `release.yml` and `build-desktop.yml`. Only do this if
no one has pinned the old tag hash; prefer a patch tag when in doubt.

## What this file does NOT cover

- **Branch model design and diagrams** → [BRANCHING.md](BRANCHING.md)
- **Version bump policy / SemVer for `0.x.y`** → [BRANCHING.md](BRANCHING.md) → "When to bump minor vs patch"
- **Day-to-day PR / commit conventions** → [CONTRIBUTING.md](CONTRIBUTING.md)
- **Local dev setup** → [CONTRIBUTING.md](CONTRIBUTING.md) → "Development setup"
