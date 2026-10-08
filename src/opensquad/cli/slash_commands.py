"""Slash / plus command registry for the interactive OpenSquad shell."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SlashCommand:
    name: str
    help: str
    usage: str = ""
    subcommands: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    category: str = "general"


COMMANDS: tuple[SlashCommand, ...] = (
    # Session / chat
    SlashCommand("help", "Show commands (optionally filter)", "/help [filter]", category="session"),
    SlashCommand("quit", "Exit the shell", "/quit", aliases=("exit", "q"), category="session"),
    SlashCommand("clear", "Clear the screen", "/clear", category="session"),
    SlashCommand(
        "theme",
        "Switch TUI color theme",
        "/theme [name]",
        aliases=("themes",),
        category="session",
    ),
    SlashCommand(
        "language",
        "Switch TUI language (en/zh)",
        "/language [en|zh]",
        aliases=("lang", "locale"),
        category="session",
    ),
    SlashCommand(
        "start",
        "Start agent process and connect (then /new to chat)",
        "/start [name]",
        aliases=("boot",),
        category="session",
    ),
    SlashCommand("new", "Start a new chat session (auto-starts agent if needed)", "/new", category="session"),
    SlashCommand("stop", "Stop the current agent task", "/stop", category="session"),
    SlashCommand("compress", "Compress conversation context", "/compress", category="session"),
    SlashCommand(
        "sessions",
        "List / switch agent sessions",
        "/session [n|id]",
        aliases=("session",),
        category="session",
    ),
    SlashCommand("history", "Show recent messages (solo session / group)", "/history [n]", category="session"),
    SlashCommand(
        "agent",
        "Show or switch current agent (leaves group)",
        "/agent [name|list]",
        aliases=("agents",),
        category="session",
    ),
    SlashCommand(
        "autostart",
        "Show/set default boot agent (synced with Web)",
        "/autostart [name|off|list]",
        aliases=("boot-default",),
        category="session",
    ),
    SlashCommand("status", "Show gateway / mode / login", "/status", category="session"),
    SlashCommand(
        "debug",
        "Toggle verbose boot/system logs (default off)",
        "/debug [on|off]",
        category="session",
    ),
    SlashCommand("whoami", "Show logged-in user", "/whoami", category="session"),
    SlashCommand("login", "Log in to Gateway", "/login [email]", category="session"),
    SlashCommand("logout", "Clear CLI credentials", "/logout", category="session"),
    SlashCommand("leave", "Leave group mode → solo", "/leave", category="session"),
    SlashCommand("mute", "Mute background group approval alerts", "/mute", category="session"),
    SlashCommand("unmute", "Unmute background group alerts", "/unmute", category="session"),
    SlashCommand(
        "approve",
        "Approve latest (or id) collab card — Web button [1]",
        "/approve [id]",
        category="session",
    ),
    SlashCommand(
        "reject",
        "Reject latest (or id) collab card — Web button [2]",
        "/reject [id]",
        category="session",
    ),
    SlashCommand(
        "choose",
        "Choose propose-options (id + value or index)",
        "/choose [id] <value|index>",
        category="session",
    ),
    SlashCommand(
        "image",
        "Attach image from path or clipboard (no preview)",
        "/image [path]",
        aliases=("attach", "img"),
        category="session",
    ),
    SlashCommand("detach", "Clear pending attachments / skill chip", "/detach", category="session"),
    # Resources
    SlashCommand(
        "skill",
        "Manage skills",
        "/skill list|show|install|rm …",
        subcommands=("list", "show", "install", "rm"),
        category="manage",
    ),
    SlashCommand(
        "mcp",
        "Manage MCP servers",
        "/mcp list|show|enable|disable|add|remove|set …",
        subcommands=("list", "show", "enable", "disable", "add", "remove", "set"),
        category="manage",
    ),
    SlashCommand(
        "plugin",
        "Manage plugins",
        "/plugin list|enable|disable|status|config|install|uninstall …",
        subcommands=("list", "enable", "disable", "status", "config", "install", "uninstall"),
        category="manage",
    ),
    SlashCommand(
        "role",
        "Manage role cards",
        "/role list|show|edit|assign|unassign|rm …",
        subcommands=("list", "show", "edit", "assign", "unassign", "rm"),
        category="manage",
    ),
    SlashCommand(
        "model",
        "Manage model cards",
        "/model list|show|edit|assign|unassign|rm …",
        subcommands=("list", "show", "edit", "assign", "unassign", "rm"),
        category="manage",
    ),
    SlashCommand(
        "collab",
        "Manage collab cards / board",
        "/collab list|show|edit|rm|board …",
        subcommands=("list", "show", "edit", "rm", "board"),
        category="manage",
    ),
    SlashCommand(
        "group",
        "Group chat (join/list/members/search/more) — same as Web groups",
        "/group list|join|members|search|history|more …",
        subcommands=(
            "list",
            "join",
            "switch",
            "members",
            "who",
            "search",
            "find",
            "history",
            "more",
            "send",
            "approve",
            "choose",
        ),
        category="manage",
    ),
    SlashCommand(
        "agentctl",
        "Agent process control (list/start/stop/…)",
        "/agentctl list|show|start|stop|restart|config|logs …",
        subcommands=("list", "show", "start", "stop", "restart", "config", "logs"),
        aliases=("agents-ctl",),
        category="manage",
    ),
)


# ── Runtime commands (Claude Code mods) ────────────────────────────────────
#
# A mod contributes commands with `$.command.register`. They live here, not in
# `COMMANDS`, because they appear and disappear with the mods while the static
# tuple stays the reviewable in-repo list. `source` matters to mods: quick-buttons
# filters `c.source !== 'builtin'` when it builds its panel.

_RUNTIME: list[SlashCommand] = []
_RUNTIME_SOURCE: dict[str, str] = {}


def register_runtime_command(
    name: str,
    *,
    help: str = "",
    usage: str = "",
    subcommands: object = (),
    aliases: object = (),
    source: str = "",
) -> SlashCommand | None:
    """Add or replace one mod-contributed command. Returns None on a bad name."""
    cleaned = str(name or "").strip().lstrip("/+").lower()
    if not cleaned or any(ch.isspace() for ch in cleaned):
        return None
    # A mod may not claim a builtin's name. Registering one would also poison
    # `command_source`, which the CLI uses to decide to hand the text to the
    # agent — a hijacked `/help` would fall through and never be handled.
    if any(cleaned == c.name or cleaned in c.aliases for c in COMMANDS):
        return None
    command = SlashCommand(
        name=cleaned,
        help=str(help or ""),
        usage=str(usage or "") or f"/{cleaned}",
        subcommands=tuple(subcommands or ()),  # type: ignore[arg-type]
        aliases=tuple(aliases or ()),  # type: ignore[arg-type]
        category="mods",
    )
    for index, existing in enumerate(_RUNTIME):
        if existing.name == cleaned:
            _RUNTIME[index] = command
            break
    else:
        _RUNTIME.append(command)
    _RUNTIME_SOURCE[cleaned] = str(source or "")
    return command


def clear_runtime_commands(source: str | None = None) -> None:
    """Drop mod commands — every one, or only a single mod's (on unload)."""
    if source is None:
        _RUNTIME.clear()
        _RUNTIME_SOURCE.clear()
        return
    for name in [n for n, s in _RUNTIME_SOURCE.items() if s == str(source)]:
        _RUNTIME_SOURCE.pop(name, None)
    _RUNTIME[:] = [c for c in _RUNTIME if c.name in _RUNTIME_SOURCE]


def all_commands() -> tuple[SlashCommand, ...]:
    """Static commands first; a mod may not shadow a builtin name."""
    static_names = {c.name for c in COMMANDS}
    return COMMANDS + tuple(c for c in _RUNTIME if c.name not in static_names)


def command_source(token: str) -> str:
    """'builtin', the contributing mod's name, or '' when unknown."""
    name = token.lstrip("/+").lower()
    if name in _RUNTIME_SOURCE:
        return _RUNTIME_SOURCE[name]
    return "builtin" if any(c.name == name or name in c.aliases for c in COMMANDS) else ""


def all_names() -> list[str]:
    names: list[str] = []
    for cmd in all_commands():
        names.append(cmd.name)
        names.extend(cmd.aliases)
    return names


def resolve_command(token: str) -> SlashCommand | None:
    name = token.lstrip("/+").lower()
    for cmd in all_commands():
        if cmd.name == name or name in cmd.aliases:
            return cmd
    return None


def match_commands(prefix: str, *, fuzzy: bool = True) -> list[SlashCommand]:
    raw = prefix.lstrip("/+").lower()
    seen: set[str] = set()
    out: list[SlashCommand] = []

    def _add(cmd: SlashCommand) -> None:
        if cmd.name in seen:
            return
        seen.add(cmd.name)
        out.append(cmd)

    if not raw:
        for cmd in all_commands():
            _add(cmd)
        return out

    for cmd in all_commands():
        if cmd.name.startswith(raw) or any(a.startswith(raw) for a in cmd.aliases):
            _add(cmd)

    if out and not fuzzy:
        return out

    for cmd in all_commands():
        if raw in cmd.name or any(raw in a for a in cmd.aliases):
            _add(cmd)

    if fuzzy:
        for cmd in all_commands():
            candidates = (cmd.name, *cmd.aliases)
            if any(_is_subsequence(raw, c) for c in candidates):
                _add(cmd)

    return out


def _is_subsequence(needle: str, haystack: str) -> bool:
    it = iter(haystack)
    return all(ch in it for ch in needle)


def format_help(filter_text: str = "") -> str:
    filt = filter_text.lstrip("/+").lower().strip()
    lines = [
        "OpenSquad CLI — same capabilities as Web, text interaction only",
        "(slash / and plus + both work; Web buttons → numbered [1] [2] …)",
        "",
    ]
    categories = ("session", "manage", "general", "mods")
    titles = {
        "session": "Session, chat & group",
        "manage": "Manage resources",
        "general": "Other",
        "mods": "From installed mods",
    }
    for cat in categories:
        cmds = [c for c in all_commands() if c.category == cat]
        if filt:
            cmds = [c for c in cmds if c in match_commands(filt)]
        if not cmds:
            continue
        lines.append(titles.get(cat, cat))
        for c in cmds:
            alias = f"  ({', '.join('/' + a for a in c.aliases)})" if c.aliases else ""
            usage = c.usage or f"/{c.name}"
            lines.append(f"  {usage:<44} {c.help}{alias}")
        lines.append("")
    lines.append("Tip: /sk + Tab → /skill …   Ctrl+V pastes clipboard image as [image:…] chip.")
    return "\n".join(lines).rstrip() + "\n"


def suggest_lines(prefix: str, limit: int = 12) -> list[str]:
    matched = match_commands(prefix)
    lines = []
    for c in matched[:limit]:
        lines.append(f"/{c.name:<14} {c.help}")
        if c.subcommands and prefix.lstrip("/+").lower() in (c.name, *c.aliases):
            for sub in c.subcommands:
                lines.append(f"  /{c.name} {sub}")
    return lines
