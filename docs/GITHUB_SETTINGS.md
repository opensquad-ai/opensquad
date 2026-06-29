# Recommended GitHub Repository Settings

These cannot be enforced from git; configure in the GitHub UI for `opensquad-ai/opensquad`.

## Security

- Enable **Private vulnerability reporting** (Security → Private vulnerability reporting).
- Enable **Dependabot alerts** and **Dependabot security updates**.
- Branch protection on `main`: require PR, require status checks (`CI` workflow).

## Community

- Display [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md) via Insights → Community standards.
- Display [SECURITY.md](../SECURITY.md) as the security policy.

## Labels (suggested)

| Label | Use |
|-------|-----|
| `good first issue` | Small, scoped tasks for new contributors |
| `help wanted` | Maintainer welcomes external PRs |
| `documentation` | Docs-only changes |
| `plugin` | Plugin or Registry related |

## Issue templates

Already in `.github/ISSUE_TEMPLATE/`. Encourage templates for bugs and features.
