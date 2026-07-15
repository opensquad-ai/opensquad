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


def provider_card_name(provider_id: str) -> str:
    """Canonical model-card name for a connected provider (one card, many models)."""
    pid = (provider_id or "provider").strip().lower()
    pid = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in pid)[:48]
    return f"prov-{pid}"


def _norm(s: Any) -> str:
    return str(s or "").strip().lower()


def _provider_aliases(preset: dict[str, Any]) -> set[str]:
    pid = _norm(preset.get("id"))
    labels = {
        pid,
        _norm(preset.get("label")),
        _norm(preset.get("provider")),
    }
    return {x for x in labels if x}


def _card_matches_provider(card: dict[str, Any], preset: dict[str, Any]) -> bool:
    """True if this model-card belongs to the preset provider."""
    pid = _norm(preset.get("id"))
    name = _norm(card.get("name"))
    if not pid:
        return False
    if name == _norm(provider_card_name(pid)) or name.startswith(f"prov-{pid}"):
        return True
    if name.startswith(f"{pid}-") or name.startswith(f"{pid}_"):
        return True
    aliases = _provider_aliases(preset)
    return _norm(card.get("provider")) in aliases


def _sort_providers(providers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    popular = [p for p in providers if _norm(p.get("id")) in _POPULAR_PROVIDER_IDS]
    other = [p for p in providers if _norm(p.get("id")) not in _POPULAR_PROVIDER_IDS]
    popular.sort(
        key=lambda p: (
            _POPULAR_PROVIDER_IDS.index(_norm(p.get("id"))) if _norm(p.get("id")) in _POPULAR_PROVIDER_IDS else 99
        )
    )
    other.sort(key=lambda p: _norm(p.get("label") or p.get("id")))
    return popular + other


def build_model_menu(
    client: Any,
    agent: str | None,
    *,
    current_card: str | None = None,
    current_model: str | None = None,
) -> tuple[str, list[NavItem]]:
    """Provider-grouped model picker.

    Layout:
      Connect a provider…
      — DeepSeek —
        deepseek-v4-flash *
        deepseek-v4-pro
      — OpenCode —
        …

    Only providers with at least one saved model-card (API key) are expanded.
    Selecting a model switches via the provider's shared card ``prov-{id}``.
    """
    cards = list((client.admin_get("model-cards") or {}).get("cards") or [])
    try:
        presets = list((client.ai_web_get("model-presets") or {}).get("providers") or [])
    except Exception:
        presets = []

    cur_card = _norm(current_card)
    cur_model = _norm(current_model)
    # Infer current model_name from cards list when only card name is known
    if cur_card and not cur_model:
        for c in cards:
            if _norm(c.get("name")) == cur_card:
                cur_model = _norm(c.get("model_name"))
                break

    items: list[NavItem] = [
        NavItem(
            id="__connect__",
            label="Connect a provider…",
            detail="paste API key · then pick models",
            action="model.connect_providers",
        )
    ]

    connected: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    matched_names: set[str] = set()
    for p in _sort_providers(presets):
        matched = [c for c in cards if _card_matches_provider(c, p)]
        if matched:
            connected.append((p, matched))
            matched_names.update(_norm(c.get("name")) for c in matched)

    for preset, matched in connected:
        pid = str(preset.get("id") or "")
        label = str(preset.get("label") or preset.get("provider") or pid)
        card_slug = provider_card_name(pid)
        # Prefer canonical card for key reuse; else any matched card name
        key_card = next((c for c in matched if _norm(c.get("name")) == _norm(card_slug)), matched[0])
        key_card_name = str(key_card.get("name") or card_slug)

        items.append(
            NavItem(
                id=f"__h_{pid}__",
                label=f"— {label} —",
                detail=f"{len(preset.get('models') or [])} models",
                action="nav.noop",
            )
        )
        models = list(preset.get("models") or [])
        if not models:
            items.append(NavItem(id=f"__empty_{pid}__", label="  (no models in preset)", action="nav.noop"))
            continue
        for m in models:
            mn = str(m.get("model_name") or m.get("id") or "")
            title = str(m.get("title") or mn)
            think = "think" if m.get("is_think") else ""
            tok = m.get("token_max") or ""
            detail = " · ".join(str(x) for x in (think, f"ctx {tok}" if tok else "") if x)
            owns_current = bool(cur_card) and (
                cur_card == _norm(card_slug) or any(_norm(c.get("name")) == cur_card for c in matched)
            )
            mark = "*" if owns_current and cur_model == _norm(mn) else " "
            items.append(
                NavItem(
                    id=f"{pid}::{mn}",
                    label=f"  {title[:42]}",
                    detail=detail,
                    mark=mark,
                    data={
                        "provider": preset,
                        "model": m,
                        "card_name": card_slug,
                        "key_card_name": key_card_name,
                    },
                    children=_provider_model_actions(preset, m, card_slug, key_card_name, agent),
                )
            )

    # Orphan / custom cards not mapped to a preset
    orphans = [c for c in cards if _norm(c.get("name")) not in matched_names]
    if orphans:
        items.append(NavItem(id="__h_custom__", label="— Custom cards —", detail="", action="nav.noop"))
        for c in orphans:
            name = str(c.get("name") or "")
            title = str(c.get("title") or "")
            model = str(c.get("model_name") or "")
            provider = str(c.get("provider") or "")
            detail = " · ".join(x for x in (title or model, provider) if x)
            mark = "*" if cur_card and _norm(name) == cur_card else " "
            items.append(
                NavItem(
                    id=name,
                    label=f"  {name}",
                    detail=detail,
                    mark=mark,
                    data={"card": c, "name": name},
                    children=_model_card_actions(name, agent),
                )
            )

    if len(items) == 1:
        items.append(
            NavItem(
                id="__empty__",
                label="(no providers yet — Connect a provider…)",
                detail="",
                action="nav.noop",
            )
        )
    title = f"Models · {agent or '—'}"
    return title, items


def build_provider_menu(client: Any, *, connected_ids: set[str] | None = None) -> tuple[str, list[NavItem]]:
    """Connect-a-provider list from model-presets."""
    data = client.ai_web_get("model-presets")
    providers = list(data.get("providers") or [])
    connected_ids = {_norm(x) for x in (connected_ids or set())}
    if not connected_ids:
        # Infer from existing cards
        try:
            cards = list((client.admin_get("model-cards") or {}).get("cards") or [])
            for p in providers:
                if any(_card_matches_provider(c, p) for c in cards):
                    connected_ids.add(_norm(p.get("id")))
        except Exception:
            pass

    popular: list[NavItem] = []
    other: list[NavItem] = []
    for p in providers:
        pid = str(p.get("id") or "")
        label = str(p.get("label") or p.get("provider") or pid)
        n_models = len(p.get("models") or [])
        already = _norm(pid) in connected_ids
        detail = (
            f"connected · update key · {n_models} models"
            if already
            else f"{n_models} models · {p.get('api_protocol') or ''}"
        )
        item = NavItem(
            id=pid,
            label=label,
            detail=detail,
            mark="*" if already else " ",
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


def build_provider_model_menu(
    provider: dict[str, Any],
    card_name: str | None = None,
    *,
    current_model: str | None = None,
) -> tuple[str, list[NavItem]]:
    """Pick a model from a connected provider (realtime switch)."""
    label = str(provider.get("label") or provider.get("provider") or provider.get("id") or "provider")
    pid = str(provider.get("id") or "")
    slug = card_name or provider_card_name(pid)
    models = list(provider.get("models") or [])
    cur = _norm(current_model)
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
                mark="*" if cur and _norm(mn) == cur else " ",
                data={"provider": provider, "model": m, "card_name": slug, "key_card_name": slug},
                children=_provider_model_actions(provider, m, slug, slug, None),
            )
        )
    if not items:
        items.append(NavItem(id="__empty__", label="(no models listed)", action="nav.noop"))
    items.append(_back_item())
    return f"Models · {label}", items


def _provider_model_actions(
    provider: dict[str, Any],
    model: dict[str, Any],
    card_name: str,
    key_card_name: str,
    agent: str | None,
) -> list[NavItem]:
    """L2: actions for one model under a provider (not immediate switch)."""
    payload = {
        "provider": provider,
        "model": model,
        "card_name": card_name,
        "key_card_name": key_card_name,
    }
    mn = str(model.get("model_name") or model.get("id") or "")
    title = str(model.get("title") or mn)
    return [
        NavItem(
            id=f"use:{card_name}:{mn}",
            label="Use for current agent",
            detail="runtime switch + assign" if agent else "need /agent first",
            action="model.provider_use_model",
            data=payload,
        ),
        NavItem(
            id=f"info:{card_name}:{mn}",
            label="Show model info",
            detail=title[:36],
            action="model.provider_show",
            data=payload,
        ),
        NavItem(
            id=f"edit:{card_name}:{mn}",
            label="Modify parameters…",
            detail="temperature · ctx · flags",
            data=payload,
            children=_provider_model_edit_menu(provider, model, card_name, key_card_name),
        ),
        _back_item(),
    ]


def _provider_model_edit_menu(
    provider: dict[str, Any],
    model: dict[str, Any],
    card_name: str,
    key_card_name: str,
) -> list[NavItem]:
    """L3: editable fields for this model (saved onto provider card)."""
    base = {
        "provider": provider,
        "model": model,
        "card_name": card_name,
        "key_card_name": key_card_name,
    }
    temp = model.get("temperature", 0)
    tok = model.get("token_max") or ""
    think = bool(model.get("is_think"))
    image = bool(model.get("is_image"))
    title = str(model.get("title") or model.get("model_name") or "")
    return [
        NavItem(
            id="edit:title",
            label="title",
            detail=str(title)[:40] or "(empty)",
            action="model.provider_edit_field",
            data={**base, "field": "title"},
        ),
        NavItem(
            id="edit:temperature",
            label="temperature",
            detail=str(temp),
            action="model.provider_edit_field",
            data={**base, "field": "temperature"},
        ),
        NavItem(
            id="edit:token_max",
            label="token_max",
            detail=str(tok) if tok != "" else "(default)",
            action="model.provider_edit_field",
            data={**base, "field": "token_max"},
        ),
        NavItem(
            id="edit:is_think",
            label="is_think",
            detail="on" if think else "off",
            action="model.provider_toggle_field",
            data={**base, "field": "is_think"},
        ),
        NavItem(
            id="edit:is_image",
            label="is_image",
            detail="on" if image else "off",
            action="model.provider_toggle_field",
            data={**base, "field": "is_image"},
        ),
        _back_item(),
    ]


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
        NavItem(
            id=f"editcard:{name}",
            label="Modify parameters…",
            detail="temperature · ctx · flags",
            data={"name": name},
            children=_legacy_card_edit_menu(name),
        ),
        _back_item(),
    ]
    return items


def _legacy_card_edit_menu(name: str) -> list[NavItem]:
    base = {"name": name, "card_name": name}
    return [
        NavItem(
            id=f"c:title:{name}",
            label="title",
            detail="edit",
            action="model.card_edit_field",
            data={**base, "field": "title"},
        ),
        NavItem(
            id=f"c:temp:{name}",
            label="temperature",
            detail="edit",
            action="model.card_edit_field",
            data={**base, "field": "temperature"},
        ),
        NavItem(
            id=f"c:tok:{name}",
            label="token_max",
            detail="edit",
            action="model.card_edit_field",
            data={**base, "field": "token_max"},
        ),
        NavItem(
            id=f"c:think:{name}",
            label="is_think",
            detail="toggle",
            action="model.card_toggle_field",
            data={**base, "field": "is_think"},
        ),
        NavItem(
            id=f"c:img:{name}",
            label="is_image",
            detail="toggle",
            action="model.card_toggle_field",
            data={**base, "field": "is_image"},
        ),
        _back_item(),
    ]


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
