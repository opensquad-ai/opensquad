# Branching Strategy — Working on a Module

This guide answers one question:

> "I'm responsible for the `<X>` module. Which branch do I cut, from where,
> and how does it get back to `main`?"

It complements the high-level [CONTRIBUTING.md](CONTRIBUTING.md) and the
maintainer-focused [RELEASING.md](RELEASING.md). Read this when you are
about to start coding.

中文版：[BRANCHING_ZH.md](BRANCHING_ZH.md)

> **Scope:** This model applies to **this repository** (`opensquad-ai/opensquad`,
> the core framework). The four sub-projects — [opensquad-ai/opensquad-roles](https://github.com/opensquad-ai/opensquad-roles),
> [opensquad-ai/opensquad-collab-cards](https://github.com/opensquad-ai/opensquad-collab-cards),
> [opensquad-ai/opensquad-skills](https://github.com/opensquad-ai/opensquad-skills),
> [opensquad-ai/opensquad-plugins](https://github.com/opensquad-ai/opensquad-plugins) —
> ship declarative content and use a **simpler** workflow (PR → `main`).
> See [CONTRIBUTING.md → Sub-projects](CONTRIBUTING.md#sub-projects-where-to-contribute-what)
> for the split and the rationale.

---

## 1. The branch map at a glance

```
                           ┌──────────────┐
                           │   main       │  ◄── stable, only vX.Y.Z tags
                           └──────▲───────┘
                                  │ (merge commit from release/* or
                                  │  hotfix/*, or back-merge from dev)
                                  │
              ┌───────────────────┴───────────────────┐
              │                                       │
        ┌─────┴─────┐                          ┌──────┴──────┐
        │ release/  │ ◄── cut from dev         │  hotfix/*   │
        │ x.y.z     │  (or from vX.Y.Z tag     │  (urgent,   │
        │           │   for a patch)           │   cut from  │
        └─────▲─────┘                          │   main)     │
              │ (PR to main, then DELETE)     └──────▲──────┘
              │                                       │ (PR to main
              │                                       │  + cherry-pick
              │                                       │  back to dev)
              │     ┌──────────────────────────────┐  │
              └─────┤             dev              ├──┘
                    │ (long-lived integration)     │
                    └────────────▲─────────────────┘
                                 │ (squash-merge from)
                                 │
              ┌──────────────────┴──────────────────┐
              │                                     │
        ┌─────┴─────┐   ┌─────────┐   ┌─────┐   ┌────┴────┐
        │ feature/  │   │  fix/   │   │docs/ │   │ chore/ │
        │ <module>  │   │<module> │   │      │   │        │
        └───────────┘   └─────────┘   └──────┘   └────────┘
        (your work, branched off dev)
```

### Release lines vs. integration

- `release/x.y.z` is a **short-lived working branch**. It exists only for
  the duration of one release cycle: cut from `dev` (for the next minor
  or major) or from the existing `vX.Y.Z` tag (for a patch), used for
  the version bump + changelog + QA, then PR'd to `main` and **deleted**
  once the tag is in place. **Tags, not branches, are the long-lived
  record of what shipped.**
- `hotfix/*` follows the same shape for an urgent production fix that
  cannot wait for the next planned release: branched from `main`, minimal
  fix, PR to `main` with a tag, then deleted.
- `dev` is the **integration line**: every `feature/*`, `fix/*`, `docs/*`,
  `chore/*` PR lands here first.
- `main` receives three kinds of merge: from `release/x.y.z` (planned
  release graduation), from `hotfix/*` (urgent fix), and from `dev` (the
  periodic "absorb dev" sync to keep main in step with what dev has
  accumulated between releases).

> **Don't keep a `release/*` or `hotfix/*` branch around "just in case".**
> The `vX.Y.Z` tag is the source of truth. If a future patch is needed,
> cut a fresh `release/x.y.(z+1)` from the tag at that time.

---

## 2. Pick the right base branch

Ask yourself three questions:

| Question                                                | Base branch                                  |
|---------------------------------------------------------|----------------------------------------------|
| Am I adding new behavior or a new module?               | `dev`                                        |
| Am I fixing a non-urgent bug?                           | `dev`                                        |
| Am I editing docs (not a typo / broken link)?           | `dev`                                        |
| Am I cutting a new release line for `x.y.z`?            | `dev` (next minor/major) or the existing `vX.Y.Z` tag (patch) |
| Am I fixing a critical production bug right now?        | `main` → `hotfix/*`                          |

> Default to `dev`. `release/x.y.z` and `hotfix/*` are maintainer
> workflows; never branch a regular feature off them.

---

## 3. Branch naming convention

All work-in-progress branches follow:

```
<type>/<module>-<short-kebab-description>
```

| Type       | When to use                                        | Example                                  |
|------------|----------------------------------------------------|------------------------------------------|
| `feature/` | New functionality, new module, new API surface     | `feature/plugin-store-rating-filter`     |
| `fix/`     | Non-urgent bug fix                                 | `fix/gateway-node-secret-missing`        |
| `release/` | Short-lived release-prep branch (cut by maintainer)| `release/0.3.0`                          |
| `hotfix/`  | Urgent production fix (branched from `main`)       | `hotfix/rotate-leaked-secret`            |
| `docs/`    | Docs-only change (typos / broken links can skip)   | `docs/branching-typo`                    |
| `chore/`   | Refactor, CI, tooling, no behavior change          | `chore/ruff-pin-0.6`                     |
| `refactor/`| Code restructure with same behavior                | `refactor/agent-message-bus`             |
| `test/`    | Adding tests only, no production code change       | `test/plugin-store-coverage`             |

> `release/x.y.z` and `hotfix/*` are short-lived. They will be deleted
> after they graduate to `main`; the `vX.Y.Z` tag is what remains.

### `<module>` cheat sheet

The `<module>` token should match a top-level area in the repo so the
branch is easy to triage:

| Module area in repo                          | `<module>` token |
|----------------------------------------------|------------------|
| `src/opensquad/gateway/`                     | `gateway`        |
| `src/opensquad/gateway/nexuschat-pro/`       | `gateway-ui`     |
| `src/plugins/`                               | `plugins`        |
| `src/skills/`                                | `skills`         |
| `src/opensquad/agent/`                       | `agent`          |
| `src/opensquad/tools/`                       | `tools`          |
| `doc_en/` / `doc_cn/` / `docs/`              | `docs`           |
| `tests/`                                     | `tests`          |
| `.github/workflows/`, `scripts/`             | `ci`             |

If your change spans multiple modules, either split the work into stacked
PRs or use the dominant module in the branch name. Mention the rest in
the PR body.

---

## 4. Worked examples

### Example A — "I'm building the user-auth module"

```bash
# 1. Sync dev
git fetch upstream
git checkout dev
git rebase upstream/dev
git push origin dev

# 2. Cut the branch
git checkout -b feature/agent-user-auth

# 3. Develop
git add src/opensquad/agent/auth.py tests/test_agent_auth.py
git commit -m "feat(agent): add user authentication module

- JWT-based session token
- login / logout / refresh endpoints
- 100% coverage on new code"

# 4. Push & open PR
git push -u origin feature/agent-user-auth
# Open PR: feature/agent-user-auth -> dev
```

### Example B — "I found a bug in the plugin loader"

```bash
git fetch upstream
git checkout dev
git rebase upstream/dev
git checkout -b fix/plugin-loader-import-error

# ... fix ...
git commit -m "fix(plugins): handle missing __init__ in plugin discovery

The plugin loader crashed with ImportError when a plugin directory was
missing __init__.py. Now we skip with a clear log line.

Closes #312"

git push -u origin fix/plugin-loader-import-error
# Open PR: fix/plugin-loader-import-error -> dev
```

### Example C — "There's a security issue in production right now"

```bash
# Branch from main, not dev — we want a minimal, urgent fix.
git fetch upstream
git checkout main
git rebase upstream/main
git checkout -b hotfix/disable-leaky-log

# ... minimal fix + test ...
git commit -m "fix(gateway): redact Authorization header from logs

A regression in 0.4.2 caused the Authorization header to be written to
gateway logs at DEBUG level. This change redacts the header globally.

CVE: pending
Closes #987"

git push -u origin hotfix/disable-leaky-log
# Open PR: hotfix/disable-leaky-log -> main (NOT dev)

# After merge: tag the merge commit as v0.4.3, cherry-pick the fix
# to dev, and delete the hotfix branch. See the post-release
# checklist in section 6.
```

### Example D — "I want to update the docs for the API"

```bash
git fetch upstream
git checkout dev
git rebase upstream/dev
git checkout -b docs/agent-api-reference

# ... edit doc_en/, doc_cn/ ...
git commit -m "docs(agent): clarify on_message hook signature

Adds an example for the v2 hook and a deprecation note for v1."

git push -u origin docs/agent-api-reference
# Open PR -> dev
```

For a single typo or broken link, you can commit directly on a branch
off `main` — but for any non-trivial doc change, target `dev` so it
ships in the next release.

### Example E — "I'm cutting a new release `v0.3.0`" (maintainer)

```bash
# 1. Sync dev
git fetch upstream
git checkout dev
git rebase upstream/dev
git push origin dev

# 2. Cut the short-lived release branch from dev
git checkout -b release/0.3.0

# 3. Version bump + changelog (see RELEASING.md for the full checklist)
#    - pyproject.toml: version = "0.3.0"
#    - CHANGELOG.md:   move [Unreleased] items into "## [0.3.0] - YYYY-MM-DD"
git add pyproject.toml CHANGELOG.md
git commit -m "chore(release): bump version to 0.3.0"

# 4. QA: install from this branch, run smoke tests, fix anything broken.
#    Each fix is a regular commit on the release branch.

# 5. Push and open a PR to main (merge commit, NOT squash)
git push -u origin release/0.3.0
gh pr create --base main --head release/0.3.0 \
    --title "chore(release): 0.3.0" --body "See RELEASING.md"
gh pr merge <PR#> --merge --delete-branch
# (--delete-branch removes the remote; we'll also clean the local copy below.)

# 6. Tag the release on the merge commit
git checkout main && git pull origin main
git tag -a v0.3.0 -m "v0.3.0"
git push origin v0.3.0

# 7. Absorb the release into dev so dev doesn't drift
git checkout dev
git merge --no-ff main -m "Merge branch 'main' into dev (absorb v0.3.0)"
git push origin dev

# 8. Bump dev to the next dev version
#    pyproject.toml: version = "0.4.0.dev0"
git add pyproject.toml
git commit -m "chore(dev): bump to 0.4.0.dev0 after v0.3.0 release"
git push origin dev

# 9. Clean up the local release branch — the v0.3.0 tag is the record
git branch -D release/0.3.0
```

#### When to bump minor vs patch (and the `.devN` / `.postN` markers)

The project follows [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html)
(`MAJOR.MINOR.PATCH`), with a key caveat from spec item 4:

> Major version zero (0.y.z) is for initial development. Anything may
> change at any time. The public API should not be considered stable.

So for `0.x.y` releases, **MINOR bumps are the meaningful unit** and can
contain breaking changes; **PATCH bumps** are for pure bug fixes within
an existing minor line. `1.0.0` is the lock-in milestone.

| Bump                                | When                                                                                                                                                                  |
|-------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `0.x.y` → `0.x.(y+1)` (PATCH)       | Pure bug fixes shipped together. No new external surface, no new required config, no first-run UX change. Existing deployments need to do nothing on upgrade.          |
| `0.x.y` → `0.(x+1).0` (MINOR)       | New user-facing flow (e.g. first-launch wizard), new external API / endpoint, new required config, new auth mechanism, or an accumulated batch of features from dev. |
| `0.x.y` → `0.x.y.postN` (POST)      | Urgent post-release fix to a **shipped** version that does not warrant a new minor/patch. Keeps the existing minor line alive with minimal disruption.                |
| `0.x.y` → `1.0.0` (MAJOR)           | Public API is now stable. Any further breaking change requires a major bump.                                                                                          |

PEP 440 dev / post markers combine with the above for development lines:

| Marker                | Meaning                                                                | Used on          |
|-----------------------|------------------------------------------------------------------------|------------------|
| `0.X.0.dev0`          | "we're starting work on the 0.X.0 release"                             | `dev` branch tip |
| `0.X.0.dev5`          | "iterating; this is the 5th dev snapshot"                              | long dev cycles  |
| `0.X.0a1` / `b1` / `rc1` | alpha / beta / release candidate                                    | pre-release tags |
| `0.X.0`               | "0.X.0 is shipped"                                                     | `vX.Y.Z` tag     |
| `0.X.0.postN`         | "post-release fix #N, not a new release"                               | hotfix tags      |

**Heuristic for maintainers:** ask "what does a deployer have to *do*
on upgrade?" If the answer is "nothing, it just works", it's PATCH. If
the answer is "they need to do a one-time thing (walk through a
wizard, generate a secret, configure a new endpoint)", it's MINOR. If
the answer is "their existing data/workflows break and need migration",
it's a MAJOR candidate (or a MINOR bump during `0.x`).

### Example F — "I need to patch the already-released v0.2.0"

The shipped v0.2.0 has a regression and we can't wait for v0.3.0. Cut a
patch release off the existing tag — same short-lived release-branch
flow, but the base is the old tag instead of dev.

```bash
# 1. Branch from the existing v0.2.0 tag, NOT from dev or main
git fetch upstream --tags
git checkout v0.2.0
git checkout -b release/0.2.1

# 2. Apply the minimal fix (cherry-pick or direct edit)
# ... add a regression test ...
git add src/.../buggy.py tests/...
git commit -m "fix(gateway): correct X regression in 0.2.0

Backport of the fix for #NNN, scoped to the v0.2.x line.

Fixes #NNN"

# 3. Bump version
#    pyproject.toml: version = "0.2.1"
git add pyproject.toml
git commit -m "chore(release): bump to 0.2.1"

# 4. PR to main, tag, absorb, bump dev — same as Example E
git push -u origin release/0.2.1
gh pr create --base main --head release/0.2.1 \
    --title "chore(release): 0.2.1" --body "Backport fix for #NNN"
gh pr merge <PR#> --merge --delete-branch
git checkout main && git pull
git tag -a v0.2.1 -m "v0.2.1" && git push origin v0.2.1
git checkout dev && git cherry-pick <fix-commit-sha> && git push origin dev

# 9. Clean up the local release branch
git branch -D release/0.2.1
```

---

## 5. Keeping your branch healthy

While your branch is open:

- **Sync daily.** Rebase on the base branch before pushing new commits:
  ```bash
  git fetch upstream
  git rebase upstream/dev     # or upstream/main for hotfixes
  git push --force-with-lease origin <your-branch>
  ```
- **Avoid `git merge`.** Rebase keeps history linear; merges create
  noisy "Merge branch 'dev' into feature/x" commits that get squashed
  away anyway.
- **Run the local quality gate** before requesting review
  (see [CONTRIBUTING.md → Coding Standards](CONTRIBUTING.md#coding-standards)).
- **Stay focused.** If scope grows, open a new branch / PR instead of
  piling unrelated changes into one.
- **For release branches:** keep the QA loop tight. Every fix commit on
  the release branch becomes part of the released history; squash
  noise locally before pushing, and never push `wip` or `try again`
  commits to a release branch.

---

## 6. What "done" looks like

Your branch is ready to merge when **all** of the following are true:

- [ ] Branch name follows the `<type>/<module>-<desc>` convention.
- [ ] Base branch is correct (`dev` by default, `main` only for hotfixes).
- [ ] Commits follow [Conventional Commits](https://www.conventionalcommits.org/).
- [ ] PR description links the tracking issue (`Closes #N` or `Refs #N`).
- [ ] PR template is fully filled in (no sections deleted).
- [ ] All CI checks are green (lint, pytest, frontend smoke, doc links,
      secret scan, SAST, SCA).
- [ ] CODEOWNER for the touched directories has approved.
- [ ] No `system_config*.json`, no workspace data, no secrets.
- [ ] Branch is rebased on the target branch (no merge commits).
- [ ] At least one approving review.

Once approved, a maintainer will squash-merge (`feature/*`, `fix/*`,
`docs/*`, etc.). Your branch will be deleted automatically by the merge
action.

### Post-merge checklist for release captains

If the PR was a **`release/x.y.z` graduation** or a **`hotfix/*` merge
to `main`**, the work is not done when the PR merges. The release
captain must also:

- [ ] Tag the merge commit on `main` as `vX.Y.Z` and push the tag.
      `git tag -a vX.Y.Z <merge-sha> -m "vX.Y.Z" && git push origin vX.Y.Z`
- [ ] Delete the `release/x.y.z` / `hotfix/*` branch both locally and
      remotely. The `vX.Y.Z` tag is the long-lived record; the branch
      is scratch.
- [ ] Back-merge `main` into `dev` (for releases) or cherry-pick the
      fix commit onto `dev` (for hotfixes) so dev doesn't drift.
- [ ] Bump dev's `pyproject.toml` to the next `*.dev0` version and
      push the dev bump commit.

If any of these steps is skipped, dev will silently fall out of sync
with main, and the next release cycle will start from a stale base.

---

## 7. Common pitfalls

| Pitfall                                              | Fix                                                            |
|------------------------------------------------------|----------------------------------------------------------------|
| Branched from `main` for a normal feature           | Re-cut from `dev`; close the old PR and open a new one.        |
| Branched from `dev` for a hotfix                     | Re-cut from `main`; do not back-merge `dev` into a hotfix.     |
| Kept a `release/*` branch around "for the next patch"| Delete it. Future patches are cut fresh from the `vX.Y.Z` tag. |
| Forgot to tag the merge commit                       | `git tag -a vX.Y.Z <merge-sha>` then `git push origin vX.Y.Z`. |
| Skipped the `main → dev` absorb step                 | `git checkout dev && git merge --no-ff main` after the release PR merges. |
| Tagged the merge commit but forgot to delete the branch | `git push origin --delete release/x.y.z` and `git branch -D release/x.y.z`. |
| Branch name `my-branch` or `test`                    | Rename: `git branch -m feature/<module>-<desc>`.               |
| Commits like "wip", "fix typo", "asdf"               | Squash them locally before pushing.                            |
| Forced `git push` without `--force-with-lease`       | Use `--force-with-lease` to avoid clobbering teammate work.    |
| PR with 30+ files across 8 modules                  | Split into stacked PRs along module lines.                     |
| `git add -A` to "just stage everything"             | Stage explicit files. CI guard will fail on leaked workspace.   |
| Unsure if a change is PATCH or MINOR                 | Use the heuristic in [Example E § When to bump](BRANCHING.md#example-e--im-cutting-a-new-release-v030-maintainer): "what does a deployer have to *do* on upgrade?" If anything, it's MINOR. |

---

*Questions? Open a discussion in the
[OpenSquad discussions](https://github.com/opensquad-ai/opensquad/discussions)
or ping `@maintainers` in your PR.*
