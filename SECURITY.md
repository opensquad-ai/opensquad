# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.3.x (latest) | Yes |
| 0.2.x | Yes |
| 0.1.x | Best-effort security fixes only |
| 0.0.x | No |
| < 0.0.1 | No |

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

## Security Best Practices for Deployers

- **Never commit** `system_config.json` or `system_config.gateway.json` to version control — they contain `node_secret` which authenticates agents to the gateway.  
  Use the provided `*.example.json` templates and keep real configs in `.gitignore`.
- Rotate `node_secret` regularly and use a strong random value (e.g., `openssl rand -hex 32`).
- Run the gateway behind a firewall; do not expose ports 9555/9600/9720 to the public internet unless intentional.
- Keep Python and Node.js dependencies up to date.

---

*OpenSquad Contributors — MIT*
