# Releasing OpenSquad

> Step-by-step for the release captain. **Why** we use this branching model,
> the version-bump policy, and worked examples (release flow + hotfix from an
> old tag) live in [BRANCHING.md](BRANCHING.md). Read that first if you
> haven't recently — this file is the **how**, that one is the **why**.

## TL;DR

1. `git checkout dev && git checkout -b release/0.X.Y && git push -u origin release/0.X.Y`
2. Bump `pyproject.toml` (`0.X.0.dev0` → `0.X.0`) and `__init__.py` together.
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
- Single source of truth: `pyproject.toml` → `[project].version`. Mirror it in `src/opensquad/__init__.py::__version__` (see the front-end build notes in `BRANCHING.md` for why the mirror is needed).
- See [BRANCHING.md](BRANCHING.md) → "When to bump minor vs patch" for the cheat sheet (SemVer rule 4 for `0.x.y`, PEP 440 markers, deployer-effort heuristic).
- Update `CHANGELOG.md` **before** each release. Move `[Unreleased]` items into a dated `## [X.Y.Z] — YYYY-MM-DD` section; open a fresh `[Unreleased]` afterwards.

| Component | Published to |
|-----------|-------------|
| Python package `opensquad` | Git tags + PyPI (via `release.yml`, OIDC trusted publishing) |
| Docker image | `ghcr.io/opensquad-ai/opensquad:X.Y.Z` (and `:latest` on final release) |
| Gateway frontend | Bundled in repo / Docker image (built inside the image, not a separate artifact) |
| Desktop (Electron) | `.github/workflows/build-desktop.yml` on `v*` tag |
| npm package `@opensquad-ai/opensquad` | `.github/workflows/release-npm.yml` on `v*` tag (bootstrap wrapper, see below) |

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

# 2. Bump version: 0.X.0.dev0  →  0.X.0
#    Edit pyproject.toml and src/opensquad/__init__.py together.
#    See the cheat sheet in BRANCHING.md for the right bump level.

# 3. CHANGELOG.md: rename the [Unreleased] section to [0.X.0] — YYYY-MM-DD.
#    Add a fresh [Unreleased] below it for the next cycle.

# 4. Commit + PR
git add pyproject.toml src/opensquad/__init__.py CHANGELOG.md
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
#    - pypi job: builds the wheel + sdist, publishes via OIDC trusted publishing
#    - release job: generates GitHub Release notes from commits since the previous tag
#    Verify all three in:
#      - https://github.com/opensquad-ai/opensquad/releases/tag/v0.X.0
#      - https://pypi.org/project/opensquad/#history
#      - https://github.com/opensquad-ai/opensquad/pkgs/container/opensquad

# 7. Absorb main back into dev
git checkout dev && git pull --ff-only
git merge --no-ff origin/main -m "Merge branch 'main' into dev (absorb v0.X.0 release)"

# 8. Bump dev to the next .dev0
#    See BRANCHING.md cheat sheet. PATCH next → 0.X.1.dev0.
#    MINOR next → 0.(X+1).0.dev0.
#    Edit pyproject.toml and src/opensquad/__init__.py together.
git add pyproject.toml src/opensquad/__init__.py
git commit -m "chore(dev): bump to 0.X.(Y+1).dev0 after v0.X.Y release"
git push origin dev

# 9. Delete the release branch — it's done its job
git push origin --delete release/0.X.Y
git branch -d release/0.X.Y
```

**Common gotchas** (full list in [BRANCHING.md](BRANCHING.md) → "Common pitfalls"):

- The `validate` job in `release.yml` will **fail loudly** if `pyproject.toml` version doesn't match the tag. Always bump `pyproject.toml` **and** `__init__.py` together (they're mirrored — see BRANCHING.md for the front-end build reason).
- Don't `git push --tags` indiscriminately. Push the **one** tag you just made.
- Don't merge the release PR with the dev version still in `__init__.py` — that's the bug that caused the original `v0.1.1`-stuck-in-the-frontend incident.

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
| npm name | `@opensquad-ai/opensquad` |
| bin name | `opensquad` |
| License | MIT (matches `LICENSE` at the repo root) |
| Source | `package.json` + `bin/opensquad.js` |
| Workflow | `.github/workflows/release-npm.yml` |
| Trigger | push of any `v*` tag |

The package name is **scoped** because the unscoped name `opensquad`
is already taken on the public registry by an unrelated project.

### How the bootstrap works

When a user runs `npx @opensquad-ai/opensquad`:

1. The Node.js script `bin/opensquad.js` runs.
2. It detects Python 3.10+ on `PATH` (`python3` or `python`).
3. It checks if the matching `opensquad==X.Y.Z` PyPI package is
   installed. If not, it `pip install --user opensquad==X.Y.Z`.
4. It `exec`s the real `opensquad` Python CLI with the user's
   arguments and exits with the same code.

So users get a familiar short command (`opensquad`) without
needing to know they crossed a language boundary:

```bash
npm install -g @opensquad-ai/opensquad
opensquad --version
opensquad run ...
```

### Cut a new npm release

The npm package is published **automatically** by `release-npm.yml`
on every `v*` tag push — no separate `npm publish` step is needed.
It runs in parallel with `release.yml` (Python package + Docker).

Requirements for the workflow to succeed:

1. The tag matches `package.json` version (the workflow's `validate` job
   enforces this; bump `version` in `package.json` when bumping
   `pyproject.toml`).
2. The npm account `@opensquad-ai` (https://www.npmjs.com/org/
   opensquad-ai) is configured for **trusted publishing** with
   this repository as the OIDC publisher. See the npm docs to set
   this up once.
3. The workflow has `id-token: write` permission (already set in
   the file).

If trusted publishing isn't set up yet, the workflow falls back to
`NPM_TOKEN` (a publish token stored in repo secrets). Set it via
`Settings → Secrets and variables → Actions → New repository secret`,
name `NPM_TOKEN`, value from `npm token create`.

### Manual backfill (one-time, e.g. for v0.1.0)

If a tag was pushed before trusted publishing / `NPM_TOKEN` was
configured, publish manually:

```bash
npm login --registry=https://registry.npmjs.org/   # as @opensquad-ai
npm publish --access public
```

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
package (`@opensquad-ai/sdk` or similar), not replace this
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
2. Bump `pyproject.toml` (and `__init__.py`) to the pre-release version
   (e.g. `0.X.Yb1`).
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

1. Bump `pyproject.toml` (and `__init__.py`) back to plain `0.X.Y`.
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
- [ ] **Docker image is on `ghcr.io/opensquad-ai/opensquad:0.X.Y` and `:latest`** (final release only).
- [ ] **PyPI shows the new version** at https://pypi.org/project/opensquad/#history.
- [ ] **npm package published** (`@opensquad-ai/opensquad` on the public registry).
- [ ] **`dev` is bumped** to the next `.dev0` (per [BRANCHING.md](BRANCHING.md) cheat sheet) and pushed.
- [ ] **`[Unreleased]` section in `CHANGELOG.md` is open on dev** for the next cycle.
- [ ] **Release branch deleted** locally and on remote.
- [ ] **Monitor Dependabot / GitHub Security Advisories** in the days after.

## What this file does NOT cover

- **Branch model design and diagrams** → [BRANCHING.md](BRANCHING.md)
- **Version bump policy / SemVer for `0.x.y`** → [BRANCHING.md](BRANCHING.md) → "When to bump minor vs patch"
- **Day-to-day PR / commit conventions** → [CONTRIBUTING.md](CONTRIBUTING.md)
- **Local dev setup** → [CONTRIBUTING.md](CONTRIBUTING.md) → "Development setup"
