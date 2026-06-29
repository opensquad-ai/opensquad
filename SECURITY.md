# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.4.x (current) | Yes — active development, security fixes backported to the latest 0.4.x |
| ≤ 0.3.x | No — repository history was reset at v0.4.0; older versions are not in this tree |

OpenSquad v0.4.0 is the first public release. There is no prior release
line in this repository.

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

To report a vulnerability, open a [GitHub Security Advisory](https://github.com/opensquad-ai/opensquad/security/advisories/new) on this repository (requires a GitHub account). This creates a private discussion visible only to you and the maintainers.

Alternatively, you may contact the maintainers by opening a regular issue with the title prefixed `[SECURITY]` — but note that this is public. Only use this channel for low-severity findings that do not expose sensitive information.

### What to include

- A description of the vulnerability and its potential impact.
- The affected component(s) and version(s).
- Step-by-step reproduction instructions.
- Any proof-of-concept code (if applicable).

### Response timeline

| Event | Target time |
|-------|-------------|
| Acknowledgement | Within 3 business days |
| Initial assessment | Within 7 business days |
| Patch / mitigation plan | Within 30 days (severity-dependent) |

We will coordinate a disclosure timeline with you once the fix is ready.

## Scope

**In scope** — code in this repository and the artifacts built from it
(PyPI `opensquad`, npm `@opensquad-ai/opensquad`, `ghcr.io/opensquad-ai/opensquad` images, GitHub Releases), the plugin loading and sandbox boundary, authn/authz in the Gateway / Launcher, and the 20 bundled plugins under `src/plugins/`.

**Out of scope** — the separate plugin / skill / role / collab registries
(`opensquad-ai/opensquad-plugins`, `opensquad-ai/opensquad-skills`,
`opensquad-ai/opensquad-roles`, `opensquad-ai/opensquad-collabs`) — report
there instead. Also out of scope: self-hosted instances where the operator
modified core code, vulnerabilities in pinned dependencies with no upstream
fix, and social engineering / physical-access attacks.

## Safe Harbor

We will not pursue legal action against researchers who make a good-faith
effort to avoid privacy violations and data destruction, only interact with
accounts they own or have explicit permission to access, stop testing
immediately if they encounter user data, and do not exploit a vulnerability
beyond what is necessary to demonstrate it.

## Security Best Practices for Deployers

- **Never commit** `system_config.json` or `system_config.gateway.json` to version control — they contain `node_secret` which authenticates agents to the gateway.  
  Use the provided `*.example.json` templates and keep real configs in `.gitignore`.
- Rotate `node_secret` regularly and use a strong random value (e.g., `openssl rand -hex 32`).
- Run the gateway behind a firewall; do not expose ports 9555/9600/9720 to the public internet unless intentional.
- Keep Python and Node.js dependencies up to date — Dependabot is enabled
  on this repo for `pip`, `npm`, and `github-actions`.

## Acknowledgments

_(no reports yet — be the first!)_

---

*OpenSquad Contributors — MIT*
