"""
opensquad/security/sandbox.py — Weak sandbox for agent-driven shell commands.

Two layers of protection on top of the existing path whitelist
(``opensquad.utils.path_utils.is_path_safe``, already enforced for shell
working directories and filesystem tool paths):

1. **Dangerous command interception** — blocklist of shell patterns that are
   catastrophic when run unattended (disk formatting, recursive root delete,
   registry edits, shutdown, fork bombs, ...). Agent-shell commands are
   non-interactive; a human never gets a confirmation prompt, so these must
   be rejected outright.

2. **Config surface** — ``system_config.json`` section::

       "sandbox": {
         "enabled": true,
         "blocked_patterns": ["extra regex", ...]
       }

   ``enabled: false`` disables interception (NOT recommended for unattended
   / goal-mode operation). User patterns are appended to the built-in list.

The check is deliberately conservative: it only blocks commands whose
*intent* is unambiguous destruction. Ambiguous strings (e.g. ``del`` on a
project file) are left to the path whitelist layer.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Built-in blocklist (case-insensitive). Each entry: (regex, human reason).
# Patterns target *unambiguous* catastrophic intent only.
# ---------------------------------------------------------------------------

_BUILTIN_RULES: list[tuple[re.Pattern[str], str]] = [
    # --- disk / filesystem destruction ---
    (re.compile(r"\bformat\s+[a-z]:", re.I), "disk format"),
    (re.compile(r"\bmkfs(\.\w+)?\b", re.I), "filesystem creation (mkfs)"),
    (re.compile(r"\bdiskpart\b", re.I), "disk partitioning (diskpart)"),
    (re.compile(r"\bdd\s+if=.*\bof=/dev/", re.I), "raw disk write (dd)"),
    (re.compile(r"\brm\s+(-\w*[rf]\w*\s+)+/(\s|$|\*)", re.I), "recursive delete from filesystem root"),
    (re.compile(r"\brm\s+(-\w*[rf]\w*\s+)+\~(/|\s|$)", re.I), "recursive delete of home directory"),
    (re.compile(r"\brmdir\s+/s\s+/q\s+[a-z]:\\\s*($|\")", re.I), "recursive delete of drive root"),
    (re.compile(r"\bdel\s+/[fsq].*\s+[a-z]:\\\*\s*$", re.I), "mass delete of drive root"),
    (re.compile(r"\btakeown\b.*\b/r\b", re.I), "recursive ownership takeover"),
    (re.compile(r"\bicacls\s+[a-z]:\\\s*/reset\b", re.I), "ACL reset of drive root"),
    # --- OS control ---
    (re.compile(r"\bshutdown(\.exe)?\s+/[sr]", re.I), "system shutdown/restart"),
    (re.compile(r"\bbcdedit\b", re.I), "boot configuration edit"),
    (re.compile(r"\breagentc\b", re.I), "recovery environment modification"),
    (re.compile(r"\b(reg|reg\.exe)\s+(add|delete|import)\b", re.I), "registry modification"),
    (re.compile(r"\bnet\s+(user|localgroup)\b", re.I), "local account management"),
    (re.compile(r"\bwevtutil\s+cl\b", re.I), "event log clearing"),
    (re.compile(r"\bvssadmin\s+delete\s+shadows\b", re.I), "shadow copy deletion"),
    (re.compile(r"\bwmic\s+process\b.*\b(delete|terminate)\b", re.I), "wmic process kill"),
    (
        re.compile(r"\btaskkill\s+/f\s+/im\s+(svchost|lsass|csrss|winlogon|explorer)\.exe", re.I),
        "killing a critical system process",
    ),
    (re.compile(r"\bstop-computer\b|\brestart-computer\b", re.I), "system shutdown/restart (PowerShell)"),
    (re.compile(r"\bset-executionpolicy\s+bypass\b", re.I), "PowerShell execution policy bypass"),
    (
        re.compile(r"\bdisable-windowsoptionalfeature\b|\bdisable-computerrestore\b", re.I),
        "disabling OS protection features",
    ),
    # --- fork bombs / resource exhaustion ---
    (re.compile(r":\(\)\s*\{\s*:\|:&\s*\}\s*;:", re.I), "fork bomb"),
    (re.compile(r"%0\|%0", re.I), "batch fork bomb"),
    # --- credential / security tampering ---
    (re.compile(r"\bnetsh\s+advfirewall\s+set\s+allprofiles\s+state\s+off\b", re.I), "disabling the firewall"),
    (
        re.compile(r"\bsc\s+(stop|delete|config)\s+(windefend|wscsvc|sense)\b", re.I),
        "disabling Windows security services",
    ),
]

_config_cache: dict | None = None


def _load_config() -> dict:
    """Read the ``sandbox`` section of system_config.json (cached per call —
    config reads are cheap and this keeps tests hermetic)."""
    try:
        from opensquad._syscfg import raw as syscfg_raw

        cfg = syscfg_raw().get("sandbox")
        if isinstance(cfg, dict):
            return cfg
    except Exception:
        pass
    return {}


def is_enabled() -> bool:
    return bool(_load_config().get("enabled", True))


def check_shell_command(command: str) -> str | None:
    """Return a human-readable rejection reason if *command* is blocked, else None.

    Empty/None commands are not blocked here (callers validate separately).
    """
    if not command or not str(command).strip():
        return None
    cfg = _load_config()
    if not cfg.get("enabled", True):
        return None

    cmd = str(command)
    for pattern, reason in _BUILTIN_RULES:
        if pattern.search(cmd):
            logger.warning("[sandbox] blocked command (%s): %r", reason, cmd[:200])
            return f"Blocked by sandbox policy: {reason}"

    for raw in cfg.get("blocked_patterns") or []:
        try:
            if re.search(str(raw), cmd, re.I):
                logger.warning("[sandbox] blocked by user pattern %r: %r", raw, cmd[:200])
                return f"Blocked by sandbox policy (custom rule): {raw}"
        except re.error:
            logger.warning("[sandbox] invalid user blocked_pattern ignored: %r", raw)
    return None
