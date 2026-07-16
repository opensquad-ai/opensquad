"""Dispatch /skill list … and shell-native group/media commands."""

from __future__ import annotations

import shlex
from argparse import Namespace
from typing import Any, Callable

from opensquad.cli.slash_commands import format_help, match_commands, resolve_command, suggest_lines


def dispatch_slash(line: str, ctx: dict[str, Any]) -> bool:
    """
    Returns False if the shell should exit, True to continue.
    """
    line = line.strip()
    if not line:
        return True

    prefix = line[0]
    if prefix not in ("/", "+"):
        return True

    try:
        parts = shlex.split(line[1:])
    except ValueError:
        parts = line[1:].split()

    if not parts:
        _print_suggestions("")
        return True

    name = parts[0].lower()
    args = parts[1:]
    cmd = resolve_command(name)

    if cmd is None:
        matches = match_commands(name)
        if len(matches) == 1 and (matches[0].name.startswith(name) or name in matches[0].aliases):
            cmd = matches[0]
        elif matches:
            print(f"Matches for '{prefix}{name}':")
            for i, m in enumerate(matches, 1):
                print(f"  [{i}] {prefix}{m.name:<14} {m.help}")
            print("  (type the full command, or Tab to complete)")
            return True
        else:
            print(f"Unknown command: {prefix}{name}  — try /help")
            return True

    if cmd.name in ("quit",) or name in ("exit", "q"):
        return False
    if cmd.name == "help":
        print(format_help(args[0] if args else ""))
        return True
    if cmd.name == "clear":
        clear_fn = ctx.get("clear_screen")
        if callable(clear_fn):
            clear_fn()
        else:
            print("\033[2J\033[H", end="")
        return True
    if cmd.name == "status":
        _status(ctx)
        return True
    if cmd.name == "whoami":
        _run_login_whoami(ctx)
        return True
    if cmd.name == "login":
        _run_login(ctx, args)
        return True
    if cmd.name == "logout":
        from opensquad.cli.commands.login_cmd import run_logout

        run_logout(Namespace())
        return True

    if cmd.name == "leave":
        leave = ctx.get("leave_group")
        if callable(leave):
            leave()
        else:
            print("/leave only in interactive shell")
        return True

    if cmd.name == "mute":
        sm = ctx.get("set_muted")
        if callable(sm):
            sm(True)
        return True
    if cmd.name == "unmute":
        sm = ctx.get("set_muted")
        if callable(sm):
            sm(False)
        return True

    if cmd.name == "approve":
        fn = ctx.get("approve")
        if callable(fn):
            fn(args[0] if args else None, " ".join(args[1:]) if len(args) > 1 else "")
        return True
    if cmd.name == "reject":
        fn = ctx.get("reject")
        if callable(fn):
            fn(args[0] if args else None, " ".join(args[1:]) if len(args) > 1 else "")
        return True
    if cmd.name == "choose":
        fn = ctx.get("choose")
        if not callable(fn):
            return True
        if not args:
            print("usage: /choose [id] <value|index>")
            return True
        if len(args) == 1:
            fn(None, args[0])
        else:
            fn(args[0], " ".join(args[1:]))
        return True

    if cmd.name == "image":
        fn = ctx.get("attach_image")
        if callable(fn):
            fn(args[0] if args else None)
        return True
    if cmd.name == "detach":
        fn = ctx.get("detach_media")
        if callable(fn):
            fn()
        return True

    if cmd.name == "history":
        fn = ctx.get("history")
        n = 20
        if args and args[0].isdigit():
            n = int(args[0])
        if callable(fn):
            fn(n)
        return True

    if cmd.name == "agent" or name == "agents":
        return _agent_session(ctx, args)

    if cmd.name in ("start", "boot"):
        handler = ctx.get("start_agent")
        name = args[0] if args else None
        if callable(handler):
            handler(name)
        else:
            print("[start] not available")
        return True

    if cmd.name in ("new", "stop", "compress", "sessions", "session"):
        handler = ctx.get("session_cmd")
        name = "sessions" if cmd.name == "session" else cmd.name
        if callable(handler):
            # Prefer (name, args) signature used by TUI interactive picker
            try:
                handler(name, args)
            except TypeError:
                handler(name)
        else:
            print(f"[{cmd.name}] chat session not connected")
        return True

    if cmd.name == "group":
        return _run_group_shell(ctx, args)

    runners: dict[str, Callable] = {
        "skill": _run_skill,
        "mcp": _run_mcp,
        "plugin": _run_plugin,
        "role": _run_role,
        "model": _run_model,
        "collab": _run_collab,
        "agentctl": _run_agentctl,
    }
    # Theme picker — TUI only (no non-interactive runner)
    if cmd.name == "theme":
        apply = ctx.get("apply_theme")
        open_nav = ctx.get("open_nav")
        if args:
            if callable(apply):
                apply(args[0])
            elif callable(open_nav):
                open_nav("theme")
            else:
                print("[theme] Use inside `opensquad code` TUI")
            return True
        if callable(open_nav):
            open_nav("theme")
            return True
        print("[theme] Use inside `opensquad code` TUI — /theme [name]")
        return True

    # Language picker — TUI only
    if cmd.name == "language":
        apply = ctx.get("apply_locale")
        open_nav = ctx.get("open_nav")
        if args:
            if callable(apply):
                apply(args[0])
            elif callable(open_nav):
                open_nav("language")
            else:
                print("[language] Use inside `opensquad code` TUI")
            return True
        if callable(open_nav):
            open_nav("language")
            return True
        print("[language] Use inside `opensquad code` TUI — /language [en|zh]")
        return True

    runner = runners.get(cmd.name)
    if runner:
        # TUI: bare /model|/skill|… opens interactive nav (↑↓ Enter)
        open_nav = ctx.get("open_nav")
        if callable(open_nav) and cmd.name in (
            "model",
            "skill",
            "role",
            "collab",
            "mcp",
            "plugin",
            "agentctl",
        ):
            if not args or args[0] in ("list", "ls"):
                open_nav(cmd.name)
                return True
        try:
            runner(ctx, args)
        except SystemExit as e:
            if e.code not in (0, None):
                print(f"[{cmd.name}] exited with {e.code}")
        except Exception as e:
            print(f"[{cmd.name}] {e}")
        return True

    print(f"Unhandled: /{cmd.name}")
    return True


def _print_suggestions(prefix: str) -> None:
    for line in suggest_lines(prefix):
        print(f"  {line}")


def _ns(**kwargs) -> Namespace:
    return Namespace(**kwargs)


def _gateway(ctx) -> str | None:
    return ctx.get("gateway")


def _status(ctx) -> None:
    from opensquad.cli.api_client import load_credentials

    creds = load_credentials()
    client = ctx.get("client")
    group = ctx.get("group")
    print(f"Gateway:  {ctx.get('gateway') or (client.gateway_url if client else '?')}")
    print(f"Logged in: {'yes — ' + str(creds.get('email') or '') if creds.get('token') else 'no'}")
    print(f"Mode:     {ctx.get('mode') or 'solo'}")
    print(f"Agent:    {ctx.get('agent') or '(none)'}")
    if group and getattr(group, "group_id", None):
        print(f"Group:    {group.group_name} ({group.group_id})")


def _run_login(ctx, args: list[str]) -> None:
    # TUI must collect email/password via Input — never call input()/getpass here
    # (those block stdin and freeze Textual).
    tui_login = ctx.get("tui_login")
    if callable(tui_login):
        tui_login(args[0] if args else None)
        return

    from opensquad.cli.commands.login_cmd import run_login

    email = args[0] if args else None
    run_login(_ns(gateway=_gateway(ctx), email=email, password=None, language="zh"))
    refresh = ctx.get("refresh_client")
    if callable(refresh):
        refresh()


def _run_login_whoami(ctx) -> None:
    from opensquad.cli.api_client import GatewayClient, load_credentials

    # Avoid login_cmd.run_whoami — it calls sys.exit on failure and kills the TUI.
    creds = load_credentials()
    if not creds.get("token"):
        print("[whoami] Not logged in. Run: /login")
        return
    client = ctx.get("client") or GatewayClient(gateway_url=_gateway(ctx))
    try:
        me = client.me()
    except Exception as e:
        print(f"[whoami] Failed: {e}")
        return
    print(f"Name:    {me.get('name')}")
    print(f"Email:   {me.get('email')}")
    print(f"ID:      {me.get('id')}")
    print(f"Status:  {me.get('status')}")
    print(f"Gateway: {getattr(client, 'gateway_url', None) or _gateway(ctx)}")


def _agent_session(ctx, args: list[str]) -> bool:
    if not args or args[0] in ("list", "ls"):
        open_nav = ctx.get("open_nav")
        if callable(open_nav):
            open_nav("agent")
            return True
        _run_agentctl(ctx, ["list"])
        print(f"current: {ctx.get('agent') or '(none)'}  mode={ctx.get('mode')}")
        return True
    name = args[0]
    switch = ctx.get("switch_agent")
    if callable(switch):
        switch(name)
    else:
        print("/agent switch not available")
    return True


def _run_group_shell(ctx, args: list[str]) -> bool:
    """Prefer shell join/leave; fall back to REST helpers for list/send."""
    action, rest = _need_action(args, "list")

    if action in ("join", "switch"):
        if not rest:
            print("usage: /group join <id|name>")
            return True
        join = ctx.get("join_group")
        if callable(join):
            join(rest[0])
        else:
            print("/group join only in interactive shell (opensquad chat)")
        return True

    if action == "list":
        open_nav = ctx.get("open_nav")
        if callable(open_nav):
            open_nav("group")
            return True
        from opensquad.cli.commands.group_cmd import run_group

        run_group(_ns(gateway=_gateway(ctx), group_action="list"))
        # also print as numbered list for Web-parity picking
        try:
            client = ctx.get("client")
            if client:
                groups = client.get("/api/groups")
                if isinstance(groups, list) and groups:
                    print("\nPick with: /group join <id>")
                    for i, g in enumerate(groups, 1):
                        print(f"  [{i}] {g.get('id')}  {g.get('name')}")
        except Exception:
            pass
        return True

    if action == "history":
        fn = ctx.get("history")
        n = (
            int(rest[1])
            if len(rest) > 1 and rest[1].isdigit()
            else (int(rest[0]) if rest and rest[0].isdigit() else 20)
        )
        if callable(fn):
            # if id given and not in group yet, join first? just history via REST
            if rest and not rest[0].isdigit() and ctx.get("mode") != "group":
                from opensquad.cli.commands.group_cmd import run_group

                run_group(
                    _ns(
                        gateway=_gateway(ctx),
                        group_action="history",
                        group_id=rest[0],
                        limit=n,
                    )
                )
            else:
                fn(n)
        return True

    if action in ("more", "older"):
        fn = ctx.get("group_more")
        n = int(rest[0]) if rest and rest[0].isdigit() else 20
        if callable(fn):
            fn(n)
        else:
            print("/group more — join a group first (opensquad code)")
        return True

    if action in ("members", "who", "member"):
        fn = ctx.get("group_members")
        if callable(fn):
            fn()
        else:
            print("/group members — join a group first (opensquad code)")
        return True

    if action in ("search", "find"):
        fn = ctx.get("group_search")
        if not rest:
            print("usage: /group search <keyword>")
            return True
        q = " ".join(rest)
        if callable(fn):
            fn(q)
        else:
            print("/group search — join a group first (opensquad code)")
        return True

    if action == "send":
        if len(rest) < 2:
            print("usage: /group send <id> <message>")
            return True
        from opensquad.cli.commands.group_cmd import run_group

        run_group(
            _ns(
                gateway=_gateway(ctx),
                group_action="send",
                group_id=rest[0],
                message=" ".join(rest[1:]),
            )
        )
        return True

    if action == "approve":
        fn = ctx.get("approve")
        if callable(fn):
            fn(rest[0] if rest else None)
            return True
    if action == "choose":
        fn = ctx.get("choose")
        if callable(fn) and len(rest) >= 2:
            fn(rest[0], " ".join(rest[1:]))
            return True

    # legacy watch — redirect to join
    if action == "watch":
        print("Use /group join <id> inside `opensquad chat` (watch is folded into join).")
        if rest:
            join = ctx.get("join_group")
            if callable(join):
                join(rest[0])
        return True

    print("usage: /group list|join|switch|members|search <q>|history [n]|more [n]|send|approve|choose")
    return True


def _need_action(args: list[str], default: str = "list") -> tuple[str, list[str]]:
    if not args:
        return default, []
    return args[0], args[1:]


def _run_skill(ctx, args: list[str]) -> None:
    from opensquad.cli.commands.skill_cmd import run_skill

    action, rest = _need_action(args)
    ns = _ns(gateway=_gateway(ctx), skill_action=action, name=None, path=None)
    if action in ("show", "rm") and rest:
        ns.name = rest[0]
    elif action == "install" and rest:
        ns.path = rest[0]
    run_skill(ns)


def _run_mcp(ctx, args: list[str]) -> None:
    from opensquad.cli.commands.mcp_cmd import run_mcp

    action, rest = _need_action(args)
    ns = _ns(
        gateway=_gateway(ctx),
        mcp_action=action,
        name=rest[0] if rest else None,
        file=None,
        command=None,
        arg=[],
        env=[],
        from_json=None,
    )
    if action == "set" and rest:
        ns.file = rest[0]
    if action == "add":
        if rest:
            ns.name = rest[0]
        if len(rest) >= 2:
            ns.command = rest[1]
            ns.arg = rest[2:]
    run_mcp(ns)


def _run_plugin(ctx, args: list[str]) -> None:
    from opensquad.cli.commands.plugin_cmd import run_plugin

    action, rest = _need_action(args)
    ns = _ns(
        gateway=_gateway(ctx),
        plugin_action=action,
        plugin_id=rest[0] if rest else None,
        mode="smart",
        set_json=None,
    )
    if action == "config" and len(rest) >= 3 and rest[1] == "--set-json":
        ns.set_json = rest[2]
    run_plugin(ns)


def _run_role(ctx, args: list[str]) -> None:
    from opensquad.cli.commands.role_cmd import run_role

    action, rest = _need_action(args)
    ns = _ns(
        gateway=_gateway(ctx),
        role_action=action,
        name=None,
        agent=None,
        file=None,
        content=None,
    )
    if action in ("show", "rm", "edit") and rest:
        ns.name = rest[0]
        if action == "edit" and len(rest) >= 2:
            ns.file = rest[1]
    if action == "assign" and len(rest) >= 2:
        ns.name, ns.agent = rest[0], rest[1]
    if action == "unassign" and rest:
        ns.agent = rest[0]
    run_role(ns)


def _run_model(ctx, args: list[str]) -> None:
    from opensquad.cli.commands.model_cmd import run_model

    action, rest = _need_action(args)
    ns = _ns(
        gateway=_gateway(ctx),
        model_action=action,
        name=None,
        agent=None,
        file=None,
        reveal=False,
        title=None,
        api_protocol=None,
        provider=None,
        model_name=None,
        base_url=None,
        api_key=None,
        token_max=None,
        temperature=None,
        tool_call_mode=None,
        render_mode=None,
    )
    if action in ("show", "rm", "edit") and rest:
        ns.name = rest[0]
        if action == "show" and "--reveal" in rest:
            ns.reveal = True
        if action == "edit" and len(rest) >= 2:
            ns.file = rest[1]
    if action == "assign" and len(rest) >= 2:
        ns.name, ns.agent = rest[0], rest[1]
    if action == "unassign" and rest:
        ns.agent = rest[0]
    run_model(ns)


def _run_collab(ctx, args: list[str]) -> None:
    from opensquad.cli.commands.collab_cmd import run_collab

    action, rest = _need_action(args)
    ns = _ns(
        gateway=_gateway(ctx),
        collab_action=action,
        name=None,
        file=None,
        content=None,
        board_action="tasks",
        collab_id=None,
        agent_id=None,
        scope="public",
    )
    if action in ("show", "rm", "edit") and rest:
        ns.name = rest[0]
        if action == "edit" and len(rest) >= 2:
            ns.file = rest[1]
    if action == "board":
        ns.board_action = rest[0] if rest else "tasks"
        if ns.board_action == "items" and len(rest) >= 2:
            ns.collab_id = rest[1]
    run_collab(ns)


def _run_agentctl(ctx, args: list[str]) -> None:
    from opensquad.cli.commands.agent_cmd import run_agent

    action, rest = _need_action(args)
    ns = _ns(
        gateway=_gateway(ctx),
        agent_action=action,
        name=rest[0] if rest else None,
        set_json=None,
        tail=50,
    )
    if action == "config" and len(rest) >= 3 and rest[1] == "--set-json":
        ns.set_json = rest[2]
    run_agent(ns)
