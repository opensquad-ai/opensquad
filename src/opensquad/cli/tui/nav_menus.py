"""Interactive nested nav menus for OpenSquad TUI slash commands.

Used by /model /skill /role /collab /mcp /plugin /agent /group /session
so lists are ↑↓ selectable with Enter drilling into actions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class NavItem:
    """One row in a nav menu."""

    id: str
    label: str
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    action: str | None = None
    """Action key handled by TUI (_run_nav_action), or None if children only."""
    children: list[NavItem] | None = None
    """If set, Enter pushes this submenu instead of running action."""
    mark: str = " "


def _truncate(text: str, n: int = 40) -> str:
    t = (text or "").replace("\n", " ").strip()
    return (t[: n - 1] + "…") if len(t) > n else t


def _back_item() -> NavItem:
    return NavItem(id="__back__", label="← Back", detail="return", action="nav.back")


# ── builders (sync; call from worker threads) ─────────────────────────────


def build_model_menu(client: Any, agent: str | None) -> tuple[str, list[NavItem]]:
    data = client.admin_get("model-cards")
    cards = data.get("cards") or []
    items: list[NavItem] = [
        NavItem(
            id="__connect__",
            label="Connect a provider…",
            detail="API key only · auto defaults",
            action="model.connect_providers",
        )
    ]
    for c in cards:
        name = str(c.get("name") or "")
        title = str(c.get("title") or "")
        model = str(c.get("model_name") or "")
        provider = str(c.get("provider") or "")
        detail = " · ".join(x for x in (title or model, provider) if x)
        items.append(
            NavItem(
                id=name,
                label=name,
                detail=detail,
                data={"card": c, "name": name},
                children=_model_card_actions(name, agent),
            )
        )
    if len(items) == 1:
        items.append(NavItem(id="__empty__", label="(no model cards yet)", detail="", action="nav.noop"))
    title = f"Model cards · {agent or '—'}"
    return title, items


_POPULAR_PROVIDER_IDS = (
    "deepseek",
    "openai",
    "anthropic",
    "google",
    "qwen",
    "moonshot",
    "openrouter",
    "opencode",
)


def build_provider_menu(client: Any) -> tuple[str, list[NavItem]]:
    """Connect-a-provider list from model-presets."""
    data = client.ai_web_get("model-presets")
    providers = list(data.get("providers") or [])
    popular: list[NavItem] = []
    other: list[NavItem] = []
    for p in providers:
        pid = str(p.get("id") or "")
        label = str(p.get("label") or p.get("provider") or pid)
        n_models = len(p.get("models") or [])
        item = NavItem(
            id=pid,
            label=label,
            detail=f"{n_models} models · {p.get('api_protocol') or ''}",
            action="model.provider_pick",
            data={"provider": p},
        )
        if pid in _POPULAR_PROVIDER_IDS:
            popular.append(item)
        else:
            other.append(item)
    items: list[NavItem] = []
    if popular:
        items.append(NavItem(id="__h_pop__", label="— Popular —", detail="", action="nav.noop"))
        items.extend(popular)
    if other:
        items.append(NavItem(id="__h_oth__", label="— Other —", detail="", action="nav.noop"))
        items.extend(other)
    if not items:
        items.append(NavItem(id="__empty__", label="(no presets — try refresh)", action="nav.noop"))
    items.append(_back_item())
    return "Connect a provider", items


def build_provider_model_menu(provider: dict[str, Any], card_name: str | None = None) -> tuple[str, list[NavItem]]:
    """Pick a model from a connected provider (realtime switch)."""
    label = str(provider.get("label") or provider.get("provider") or provider.get("id") or "provider")
    models = list(provider.get("models") or [])
    items: list[NavItem] = []
    for m in models:
        mn = str(m.get("model_name") or m.get("id") or "")
        title = str(m.get("title") or mn)
        think = "think" if m.get("is_think") else ""
        tok = m.get("token_max") or ""
        detail = " · ".join(str(x) for x in (think, f"ctx {tok}" if tok else "") if x)
        items.append(
            NavItem(
                id=mn,
                label=title[:40],
                detail=detail,
                action="model.provider_use_model",
                data={"provider": provider, "model": m, "card_name": card_name or ""},
            )
        )
    if not items:
        items.append(NavItem(id="__empty__", label="(no models listed)", action="nav.noop"))
    items.append(_back_item())
    return f"Models · {label}", items


def _model_card_actions(name: str, agent: str | None) -> list[NavItem]:
    items = [
        NavItem(
            id=f"use:{name}",
            label="Use for current agent",
            detail="runtime switch + assign" if agent else "need /agent first",
            action="model.use",
            data={"name": name},
        ),
        NavItem(
            id=f"assign:{name}",
            label="Assign to agent…",
            detail="pick agent",
            action="model.assign_pick",
            data={"name": name},
        ),
        NavItem(
            id=f"show:{name}",
            label="Show card JSON",
            detail="view config",
            action="model.show",
            data={"name": name},
        ),
        _back_item(),
    ]
    return items


def build_skill_menu(client: Any) -> tuple[str, list[NavItem]]:
    data = client.admin_get("skills")
    skills = data.get("skills") or []
    items: list[NavItem] = []
    for s in skills:
        name = str(s.get("name") or s.get("dir") or "")
        disp = str(s.get("display_name") or "")
        ver = str(s.get("version") or "")
        desc = _truncate(str(s.get("description") or ""), 36)
        detail = " · ".join(x for x in (disp, ver, desc) if x)
        items.append(
            NavItem(
                id=name,
                label=name,
                detail=detail,
                data={"skill": s, "name": name},
                children=[
                    NavItem(
                        id=f"show:{name}",
                        label="Show SKILL.md",
                        action="skill.show",
                        data={"name": name},
                    ),
                    NavItem(
                        id=f"rm:{name}",
                        label="Delete skill",
                        detail="destructive",
                        action="skill.rm",
                        data={"name": name},
                    ),
                    _back_item(),
                ],
            )
        )
    if not items:
        items.append(NavItem(id="__empty__", label="(no skills)", action="nav.noop"))
    return "Skills", items


def build_role_menu(client: Any, agent: str | None) -> tuple[str, list[NavItem]]:
    data = client.admin_get("role-cards")
    cards = data.get("cards") or []
    items: list[NavItem] = []
    for c in cards:
        name = str(c.get("name") or "")
        title = str(c.get("title") or "")
        items.append(
            NavItem(
                id=name,
                label=name,
                detail=title,
                data={"name": name},
                children=[
                    NavItem(
                        id=f"assign:{name}",
                        label="Assign to current agent",
                        detail=agent or "need /agent",
                        action="role.assign",
                        data={"name": name},
                    ),
                    NavItem(
                        id=f"show:{name}",
                        label="Show role content",
                        action="role.show",
                        data={"name": name},
                    ),
                    _back_item(),
                ],
            )
        )
    if not items:
        items.append(NavItem(id="__empty__", label="(no role cards)", action="nav.noop"))
    return f"Role cards · {agent or '—'}", items


def build_collab_menu(client: Any) -> tuple[str, list[NavItem]]:
    data = client.admin_get("collab-cards")
    cards = data.get("cards") or []
    items: list[NavItem] = [
        NavItem(
            id="__board__",
            label="Board tasks…",
            detail="collab board",
            action="collab.board",
            data={},
        )
    ]
    for c in cards:
        name = str(c.get("name") or "")
        title = str(c.get("title") or "")
        items.append(
            NavItem(
                id=name,
                label=name,
                detail=title,
                data={"name": name},
                children=[
                    NavItem(
                        id=f"show:{name}",
                        label="Show card",
                        action="collab.show",
                        data={"name": name},
                    ),
                    _back_item(),
                ],
            )
        )
    return "Collab cards", items


def build_mcp_menu(client: Any) -> tuple[str, list[NavItem]]:
    data = client.admin_get("mcp/config")
    servers = data.get("mcpServers") or {}
    items: list[NavItem] = []
    for name, cfg in servers.items():
        enabled = not (isinstance(cfg, dict) and cfg.get("disabled"))
        cmd = ""
        if isinstance(cfg, dict):
            cmd = str(cfg.get("command") or "")
        items.append(
            NavItem(
                id=name,
                label=name,
                detail=("on" if enabled else "off") + (f" · {cmd}" if cmd else ""),
                mark="*" if enabled else " ",
                data={"name": name, "enabled": enabled},
                children=[
                    NavItem(
                        id=f"toggle:{name}",
                        label="Disable" if enabled else "Enable",
                        action="mcp.toggle",
                        data={"name": name, "enable": not enabled},
                    ),
                    NavItem(
                        id=f"show:{name}",
                        label="Show config",
                        action="mcp.show",
                        data={"name": name},
                    ),
                    _back_item(),
                ],
            )
        )
    if len(items) <= 0:
        items.append(NavItem(id="__empty__", label="(no MCP servers)", action="nav.noop"))
    return "MCP servers", items


def build_plugin_menu(client: Any) -> tuple[str, list[NavItem]]:
    items: list[NavItem] = []
    try:
        data = client.admin_get("plugins")
        plugins = data.get("plugins") or data.get("items") or []
        if isinstance(plugins, dict):
            plugins = [{"id": k, **(v if isinstance(v, dict) else {})} for k, v in plugins.items()]
    except Exception:
        plugins = []
    for p in plugins:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("id") or p.get("name") or "")
        name = str(p.get("name") or p.get("display_name") or pid)
        enabled = p.get("enabled", True)
        ver = str(p.get("version") or "")
        items.append(
            NavItem(
                id=pid,
                label=name,
                detail=("on" if enabled else "off") + (f" · {ver}" if ver else ""),
                mark="*" if enabled else " ",
                data={"id": pid, "enabled": bool(enabled)},
                children=[
                    NavItem(
                        id=f"toggle:{pid}",
                        label="Disable" if enabled else "Enable",
                        action="plugin.toggle",
                        data={"id": pid, "enable": not enabled},
                    ),
                    NavItem(
                        id=f"status:{pid}",
                        label="Status",
                        action="plugin.status",
                        data={"id": pid},
                    ),
                    _back_item(),
                ],
            )
        )
    if not items:
        # fall back note
        items.append(
            NavItem(
                id="__hint__",
                label="(no plugins from API — try /plugin list)",
                action="nav.noop",
            )
        )
    return "Plugins", items


def build_agent_menu(client: Any, current: str | None) -> tuple[str, list[NavItem]]:
    data = client.admin_get("agents")
    agents = data.get("agents") or []
    items: list[NavItem] = []
    for a in agents:
        dir_name = str(a.get("dir_name") or a.get("agent_name") or "")
        aid = str(a.get("agent_id") or "")
        ready = bool(a.get("ready") or a.get("registry_online"))
        model = str(a.get("model_card") or "")
        mark = "*" if current and current in (dir_name, aid) else " "
        detail = ("ready" if ready else "offline") + (f" · {model}" if model else "")
        items.append(
            NavItem(
                id=dir_name or aid,
                label=dir_name or aid,
                detail=detail,
                mark=mark,
                data={"dir_name": dir_name, "agent_id": aid},
                children=[
                    NavItem(
                        id=f"switch:{dir_name}",
                        label="Switch to this agent",
                        action="agent.switch",
                        data={"name": dir_name or aid},
                    ),
                    NavItem(
                        id=f"start:{dir_name}",
                        label="Start / connect",
                        action="agent.start",
                        data={"name": dir_name or aid},
                    ),
                    _back_item(),
                ],
            )
        )
    if not items:
        items.append(NavItem(id="__empty__", label="(no agents)", action="nav.noop"))
    return "Agents", items


def build_agent_pick_menu(
    client: Any,
    *,
    action: str,
    payload: dict[str, Any],
    title: str = "Pick agent",
) -> tuple[str, list[NavItem]]:
    """Agent list that runs `action` with payload + chosen agent name."""
    data = client.admin_get("agents")
    agents = data.get("agents") or []
    items: list[NavItem] = []
    for a in agents:
        dir_name = str(a.get("dir_name") or a.get("agent_name") or "")
        aid = str(a.get("agent_id") or "")
        name = dir_name or aid
        items.append(
            NavItem(
                id=name,
                label=name,
                detail=aid,
                action=action,
                data={**payload, "agent": name},
            )
        )
    items.append(_back_item())
    return title, items


def build_group_menu(client: Any) -> tuple[str, list[NavItem]]:
    groups = client.get("/api/groups")
    if not isinstance(groups, list):
        groups = (groups or {}).get("groups") or []
    items: list[NavItem] = []
    for g in groups:
        if not isinstance(g, dict):
            continue
        gid = str(g.get("id") or "")
        name = str(g.get("name") or gid)
        items.append(
            NavItem(
                id=gid,
                label=name,
                detail=gid,
                data={"id": gid, "name": name},
                children=[
                    NavItem(
                        id=f"join:{gid}",
                        label="Join group",
                        action="group.join",
                        data={"ref": gid},
                    ),
                    _back_item(),
                ],
            )
        )
    if not items:
        items.append(NavItem(id="__empty__", label="(no groups)", action="nav.noop"))
    return "Groups", items


def build_session_menu(
    sessions: list[dict[str, Any]],
    current_id: str | None,
    agent: str | None,
) -> tuple[str, list[NavItem]]:
    items: list[NavItem] = []
    for s in sessions:
        sid = str(s.get("id") or "")
        title = _truncate(str(s.get("title") or "(untitled)"), 36)
        mark = "*" if (s.get("current") or sid == current_id) else " "
        items.append(
            NavItem(
                id=sid,
                label=sid[:16],
                detail=title,
                mark=mark,
                action="session.switch",
                data={"id": sid, "title": str(s.get("title") or sid)},
            )
        )
    if not items:
        items.append(NavItem(id="__empty__", label="(no sessions)", action="nav.noop"))
    return f"Sessions · {agent or '—'}", items


def build_theme_menu(app: Any, current: str | None = None) -> tuple[str, list[NavItem]]:
    """Interactive theme picker (OpenCode-style Themes list)."""
    from opensquad.cli.tui.themes import list_theme_names

    cur = (current or getattr(app, "theme", None) or "").strip()
    items: list[NavItem] = []
    for name in list_theme_names(app):
        mark = "●" if name == cur else " "
        items.append(
            NavItem(
                id=name,
                label=name,
                detail="active" if name == cur else "",
                mark=mark,
                action="theme.apply",
                data={"name": name},
            )
        )
    if not items:
        items.append(NavItem(id="__empty__", label="(no themes)", action="nav.noop"))
    return "Themes · esc to close", items


# kind → builder used by TUI open_nav(kind)
NavBuilder = Callable[[Any, str | None], tuple[str, list[NavItem]]]

ROOT_KINDS = frozenset(
    {
        "model",
        "skill",
        "role",
        "collab",
        "mcp",
        "plugin",
        "agent",
        "agentctl",
        "group",
        "agents",
        "theme",
    }
)
