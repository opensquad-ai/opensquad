"""
OpenSquad CLI

Daily (just use these — OpenSquad starts services for you):
    opensquad code [agent]      Terminal TUI (auto-starts Gateway/Launcher if needed)
    opensquad web               Browser Web UI (same)

Optional:
    opensquad start --detach    Pre-warm daemon in background (faster next `code`/`web`)
    opensquad start             Foreground all services (incl. Vite; Ctrl+C stops)
    opensquad stop|restart|status|doctor|logs|config|update|help
    opensquad login|logout|whoami
    opensquad agent|mcp|skill|plugin|role|model|collab|group|chat
"""

import argparse
import os
import sys


def _add_gateway_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--gateway",
        default=None,
        help="Gateway base URL (default: OPENSQUAD_GATEWAY_URL or system_config)",
    )


def main():
    parser = argparse.ArgumentParser(
        prog="opensquad",
        description="OpenSquad - Local-first Multi-Agent Collaboration Framework",
    )
    parser.add_argument("--version", "-v", action="store_true", help="Show current version")
    parser.add_argument("--verbose", action="store_true", help="Show all service logs in the console (default: quiet)")
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # ── init ──
    p_init = sub.add_parser("init", help="Initialize a new workspace")
    p_init.add_argument("--workspace", "-w", default=None, help="Workspace path (default: ~/.opensquad/workspace)")
    p_init.add_argument("--no-config", action="store_true", help="Skip copying system_config.json template")

    # ── start ──
    p_start = sub.add_parser(
        "start",
        help="Start services (optional; `code`/`web` auto-start if needed)",
    )
    p_start.add_argument("--verbose", action="store_true", help="Show all service logs in the console (default: quiet)")
    p_start.add_argument("--port", "-p", type=int, default=None, help="Gateway port (default: from config)")
    p_start.add_argument("--no-launcher", action="store_true", help="Skip launcher service")
    p_start.add_argument("--no-gateway", action="store_true", help="Skip gateway backend")
    p_start.add_argument("--no-registry", action="store_true", help="Skip plugin registry")
    p_start.add_argument(
        "--no-frontend",
        action="store_true",
        help="Skip frontend (Vite). Preferred when Gateway serves built dist/",
    )
    p_start.add_argument(
        "--frontend",
        action="store_true",
        help="Force Vite dev server even when nexuschat-pro/dist exists",
    )
    p_start.add_argument("--no-watchdog", action="store_true", help="Skip health-check watchdog")
    p_start.add_argument(
        "--detach",
        action="store_true",
        help="Optional: pre-warm Gateway+Launcher in background (not required before code/web)",
    )

    # ── status ──
    p_status = sub.add_parser("status", help="Show agent and service status")
    p_status.add_argument("--port", type=int, default=None, help="Launcher management port")

    # ── stop / restart ──
    sub.add_parser("stop", help="Kill all OpenSquad services (clean up ports)")
    sub.add_parser("restart", help="Stop then start all services")

    # ── config ──
    p_config = sub.add_parser("config", help="Validate or show configuration")
    p_config.add_argument(
        "action", nargs="?", default="validate", choices=["validate", "show"], help="Action (default: validate)"
    )

    # ── doctor / logs / help / update ──
    sub.add_parser("doctor", help="Run system diagnostic report")
    p_logs = sub.add_parser("logs", help="View and filter service logs")
    p_logs.add_argument("--service", "-s", default="gateway", help="Service to show logs for (default: gateway)")
    p_logs.add_argument("--list", action="store_true", dest="list_services", help="List available log sources")
    p_logs.add_argument("--tail", "-n", type=int, default=30, help="Show last N lines (default: 30, 0=show all)")
    p_logs.add_argument("--level", "-l", default="", help="Filter by log level (e.g. ERROR, WARNING)")
    p_logs.add_argument("--grep", "-g", default="", help="Filter lines containing text (case-insensitive)")
    sub.add_parser("help", help="Show this help message")
    sub.add_parser("update", help="Check for updates and upgrade to the latest version")

    # ── Auth ──
    p_login = sub.add_parser("login", help="Log in to Gateway (saves JWT)")
    _add_gateway_flag(p_login)
    p_login.add_argument("--email", "-e", default=None, help="Account email")
    p_login.add_argument("--password", "-p", default=None, help="Password (prompted if omitted)")
    p_login.add_argument("--language", default="zh", choices=["zh", "en"], help="UI language for first-login bootstrap")
    sub.add_parser("logout", help="Clear saved CLI credentials")
    p_whoami = sub.add_parser("whoami", help="Show current logged-in user")
    _add_gateway_flag(p_whoami)

    # ── agent ──
    p_agent = sub.add_parser("agent", help="Manage agents")
    _add_gateway_flag(p_agent)
    agent_sub = p_agent.add_subparsers(dest="agent_action")
    agent_sub.add_parser("list", help="List agents")
    p_agent_show = agent_sub.add_parser("show", help="Show agent details")
    p_agent_show.add_argument("name", help="Agent dir_name or agent_id")
    for act in ("start", "stop", "restart"):
        p = agent_sub.add_parser(act, help=f"{act.capitalize()} an agent")
        p.add_argument("name", help="Agent dir_name")
    p_agent_cfg = agent_sub.add_parser("config", help="Get or set agent config.json")
    p_agent_cfg.add_argument("name", help="Agent dir_name")
    p_agent_cfg.add_argument("--set-json", dest="set_json", default=None, help="Path to JSON file to upload")
    p_agent_logs = agent_sub.add_parser("logs", help="Show agent logs")
    p_agent_logs.add_argument("name", help="Agent dir_name")
    p_agent_logs.add_argument("--tail", type=int, default=50, help="Tail lines")
    p_agent_boot = agent_sub.add_parser(
        "autostart",
        help="Show/set default boot agent (synced with Web 「设为默认启动」)",
    )
    p_agent_boot.add_argument(
        "name",
        nargs="?",
        default=None,
        help="Agent dir_name to mark as auto-start (omit to show current)",
    )
    p_agent_boot.add_argument(
        "--clear",
        action="store_true",
        help="Clear auto-start on all agents",
    )
    p_agent_boot.add_argument(
        "--keep-others",
        action="store_true",
        help="Do not clear other agents when setting this one",
    )

    # ── mcp ──
    p_mcp = sub.add_parser("mcp", help="Manage MCP servers")
    _add_gateway_flag(p_mcp)
    mcp_sub = p_mcp.add_subparsers(dest="mcp_action")
    mcp_sub.add_parser("list", help="List MCP servers")
    p_mcp_show = mcp_sub.add_parser("show", help="Show one MCP server config")
    p_mcp_show.add_argument("name")
    p_mcp_en = mcp_sub.add_parser("enable", help="Enable MCP server globally")
    p_mcp_en.add_argument("name")
    p_mcp_dis = mcp_sub.add_parser("disable", help="Disable MCP server globally")
    p_mcp_dis.add_argument("name")
    p_mcp_set = mcp_sub.add_parser("set", help="Replace full mcpServers from JSON file")
    p_mcp_set.add_argument("file", help="JSON file ({mcpServers:...} or bare map)")
    p_mcp_add = mcp_sub.add_parser("add", help="Add or overwrite an MCP server")
    p_mcp_add.add_argument("name")
    p_mcp_add.add_argument("--command", default=None)
    p_mcp_add.add_argument("--arg", action="append", default=[])
    p_mcp_add.add_argument("--env", action="append", default=[], help="KEY=VALUE")
    p_mcp_add.add_argument("--from-json", dest="from_json", default=None)
    p_mcp_rm = mcp_sub.add_parser("remove", help="Remove an MCP server")
    p_mcp_rm.add_argument("name")

    # ── skill ──
    p_skill = sub.add_parser("skill", help="Manage skills")
    _add_gateway_flag(p_skill)
    skill_sub = p_skill.add_subparsers(dest="skill_action")
    skill_sub.add_parser("list", help="List skills")
    p_skill_show = skill_sub.add_parser("show", help="Show SKILL.md")
    p_skill_show.add_argument("name")
    p_skill_inst = skill_sub.add_parser("install", help="Upload a skill directory")
    p_skill_inst.add_argument("path", help="Path to skill folder")
    p_skill_rm = skill_sub.add_parser("rm", help="Delete a skill")
    p_skill_rm.add_argument("name")

    # ── plugin ──
    p_plugin = sub.add_parser("plugin", help="Manage plugins")
    _add_gateway_flag(p_plugin)
    plugin_sub = p_plugin.add_subparsers(dest="plugin_action")
    p_install = plugin_sub.add_parser("install", help="Install a plugin from the store or Git URL")
    p_install.add_argument("plugin_id", help="Plugin ID or Git URL")
    p_install.add_argument("--mode", choices=["smart", "build"], default="smart", help="Install mode")
    p_uninstall = plugin_sub.add_parser("uninstall", help="Uninstall a plugin")
    p_uninstall.add_argument("plugin_id", help="Plugin ID to uninstall")
    plugin_sub.add_parser("list", help="List installed plugins")
    for act, help_txt in (
        ("enable", "Enable plugin via Gateway"),
        ("disable", "Disable plugin via Gateway"),
        ("status", "Show plugin status from Gateway"),
    ):
        p = plugin_sub.add_parser(act, help=help_txt)
        p.add_argument("plugin_id", help="Plugin ID")
    p_pcfg = plugin_sub.add_parser("config", help="Get/set plugin config via Gateway")
    p_pcfg.add_argument("plugin_id", help="Plugin ID")
    p_pcfg.add_argument("--set-json", dest="set_json", default=None, help="JSON file to upload as config")

    # ── role ──
    p_role = sub.add_parser("role", help="Manage role cards")
    _add_gateway_flag(p_role)
    role_sub = p_role.add_subparsers(dest="role_action")
    role_sub.add_parser("list", help="List role cards")
    p_role_show = role_sub.add_parser("show", help="Show role card markdown")
    p_role_show.add_argument("name")
    p_role_edit = role_sub.add_parser("edit", help="Create/update a role card")
    p_role_edit.add_argument("name")
    p_role_edit.add_argument("--file", default=None, help="Markdown file")
    p_role_edit.add_argument("--content", default=None, help="Inline markdown content")
    p_role_asg = role_sub.add_parser("assign", help="Assign role card to agent")
    p_role_asg.add_argument("name", help="Role card name")
    p_role_asg.add_argument("agent", help="Agent dir_name")
    p_role_unasg = role_sub.add_parser("unassign", help="Unassign role card from agent")
    p_role_unasg.add_argument("agent")
    p_role_rm = role_sub.add_parser("rm", help="Delete role card")
    p_role_rm.add_argument("name")

    # ── model ──
    p_model = sub.add_parser("model", help="Manage model cards")
    _add_gateway_flag(p_model)
    model_sub = p_model.add_subparsers(dest="model_action")
    model_sub.add_parser("list", help="List model cards")
    p_model_show = model_sub.add_parser("show", help="Show model card")
    p_model_show.add_argument("name")
    p_model_show.add_argument("--reveal", action="store_true", help="Show full api_key")
    p_model_edit = model_sub.add_parser("edit", help="Create/update a model card")
    p_model_edit.add_argument("name")
    p_model_edit.add_argument("--file", default=None, help="JSON file with card fields")
    p_model_edit.add_argument("--title", default=None)
    p_model_edit.add_argument("--api-protocol", dest="api_protocol", default=None)
    p_model_edit.add_argument("--provider", default=None)
    p_model_edit.add_argument("--model-name", dest="model_name", default=None)
    p_model_edit.add_argument("--base-url", dest="base_url", default=None)
    p_model_edit.add_argument("--api-key", dest="api_key", default=None)
    p_model_edit.add_argument("--token-max", dest="token_max", type=int, default=None)
    p_model_edit.add_argument("--temperature", type=float, default=None)
    p_model_edit.add_argument("--tool-call-mode", dest="tool_call_mode", default=None)
    p_model_edit.add_argument("--render-mode", dest="render_mode", default=None)
    p_model_asg = model_sub.add_parser("assign", help="Assign model card to agent")
    p_model_asg.add_argument("name", help="Model card name")
    p_model_asg.add_argument("agent", help="Agent dir_name")
    p_model_unasg = model_sub.add_parser("unassign", help="Unassign model card from agent")
    p_model_unasg.add_argument("agent")
    p_model_rm = model_sub.add_parser("rm", help="Delete model card")
    p_model_rm.add_argument("name")

    # ── collab ──
    p_collab = sub.add_parser("collab", help="Manage collab cards and board")
    _add_gateway_flag(p_collab)
    collab_sub = p_collab.add_subparsers(dest="collab_action")
    collab_sub.add_parser("list", help="List collab cards")
    p_collab_show = collab_sub.add_parser("show", help="Show collab card")
    p_collab_show.add_argument("name")
    p_collab_edit = collab_sub.add_parser("edit", help="Create/update collab card")
    p_collab_edit.add_argument("name")
    p_collab_edit.add_argument("--file", default=None)
    p_collab_edit.add_argument("--content", default=None)
    p_collab_rm = collab_sub.add_parser("rm", help="Delete collab card")
    p_collab_rm.add_argument("name")
    p_collab_board = collab_sub.add_parser("board", help="Inspect collab board")
    board_sub = p_collab_board.add_subparsers(dest="board_action")
    board_sub.add_parser("tasks", help="List board tasks")
    p_board_items = board_sub.add_parser("items", help="List board items for a collab_id")
    p_board_items.add_argument("collab_id")
    p_board_items.add_argument("--agent-id", dest="agent_id", default=None)
    p_board_items.add_argument("--scope", default="public", choices=["public", "all"])

    # ── code (daily TUI — auto-starts services) ──
    p_code = sub.add_parser(
        "code",
        help="Terminal TUI (auto-starts services; just run this)",
    )
    _add_gateway_flag(p_code)
    p_code.add_argument("agent", nargs="?", default=None, help="Agent dir_name (default: last / first ready)")
    p_code.add_argument("-m", "--message", default=None, help="One-shot message (non-interactive)")
    p_code.add_argument(
        "--no-start",
        action="store_true",
        help="Do not auto-start services (advanced; assume already running)",
    )
    p_code.add_argument(
        "--legacy",
        action="store_true",
        help="Use framed prompt_toolkit REPL instead of full-screen Textual TUI",
    )

    # ── web (browser UI — auto-starts services) ──
    p_web = sub.add_parser(
        "web",
        help="Open Web UI (auto-starts services; built static UI preferred)",
    )
    _add_gateway_flag(p_web)
    p_web.add_argument(
        "--no-start",
        action="store_true",
        help="Do not start services / frontend (only open browser if already up)",
    )
    p_web.add_argument(
        "--no-browser",
        action="store_true",
        help="Print URL only; do not open a browser",
    )
    p_web.add_argument(
        "--dev",
        action="store_true",
        help="Use the Vite dev server instead of the built static UI (requires npm)",
    )

    # ── dev (developer mode: source + hot-reload + Vite) ──
    p_dev = sub.add_parser(
        "dev",
        help="Developer mode: run backend from source with hot-reload + Vite frontend (no packaging)",
    )
    _add_gateway_flag(p_dev)
    p_dev.add_argument(
        "--no-browser",
        action="store_true",
        help="Print URL only; do not open a browser",
    )

    # ── chat / shell (Claude-Code-like interactive UI) ──
    p_chat = sub.add_parser(
        "chat",
        aliases=["shell"],
        help="Interactive shell (slash commands + agent chat)",
    )
    _add_gateway_flag(p_chat)
    p_chat.add_argument("agent", nargs="?", default=None, help="Agent dir_name (default: first ready)")
    p_chat.add_argument("-m", "--message", default=None, help="One-shot message (non-interactive)")
    p_chat.add_argument(
        "--legacy",
        action="store_true",
        help="Use framed prompt_toolkit REPL instead of full-screen Textual TUI",
    )
    p_chat.add_argument(
        "--start",
        action="store_true",
        help="Auto-start Gateway/Launcher if down (same as opensquad code)",
    )

    # ── group ──
    p_group = sub.add_parser("group", help="Group chat and approvals")
    _add_gateway_flag(p_group)
    group_sub = p_group.add_subparsers(dest="group_action")
    group_sub.add_parser("list", help="List groups")
    p_gh = group_sub.add_parser("history", help="Show recent messages")
    p_gh.add_argument("group_id")
    p_gh.add_argument("--limit", type=int, default=30)
    p_gs = group_sub.add_parser("send", help="Send a message")
    p_gs.add_argument("group_id")
    p_gs.add_argument("message")
    p_gw = group_sub.add_parser("watch", help="Live group chat (WS)")
    p_gw.add_argument("group_id")
    p_ga = group_sub.add_parser("approve", help="Resolve a collab approval card")
    p_ga.add_argument("group_id")
    p_ga.add_argument("approval_id")
    p_ga.add_argument("--reject", action="store_true", help="Reject instead of approve")
    p_ga.add_argument("--note", default="")
    p_ga.add_argument("--message-id", dest="message_id", default=None)
    p_gc = group_sub.add_parser("choose", help="Resolve a propose-options card")
    p_gc.add_argument("group_id")
    p_gc.add_argument("proposal_id")
    p_gc.add_argument("value")
    p_gc.add_argument("--action", default="choose", choices=["choose", "custom", "ignore"])
    p_gc.add_argument("--note", default="")
    p_gc.add_argument("--message-id", dest="message_id", default=None)

    args = parser.parse_args()

    if getattr(args, "version", False):
        from opensquad import __version__

        print(f"opensquad v{__version__}")
        sys.exit(0)

    if not args.command:
        # `opensquad --verbose` (no subcommand) == `opensquad web` + console log
        # streaming: start all services in the FOREGROUND so their logs stream
        # to this terminal, open the browser once ready, and block until Ctrl+C.
        if getattr(args, "verbose", False):
            from argparse import Namespace

            from opensquad.cli.commands.start_cmd import run_start

            run_start(
                Namespace(
                    command="start",
                    verbose=True,
                    open_browser=True,
                    port=None,
                    detach=False,
                    no_gateway=False,
                    no_launcher=False,
                    no_registry=False,
                    no_frontend=False,
                    frontend=False,
                    no_watchdog=False,
                )
            )
            return
        print(
            "OpenSquad — just run:\n"
            "  opensquad code             # terminal TUI (auto-starts services)\n"
            "  opensquad web              # browser Web UI\n"
            "\n"
            "Optional: opensquad start --detach  (pre-warm) · stop · help · --version\n"
            "         opensquad --verbose  # web UI + live service logs in this console\n"
        )
        sys.exit(0)

    if args.command == "help":
        parser.print_help()
        print(
            "\nDaily:\n"
            "  opensquad code             → TUI (services start automatically)\n"
            "  opensquad web              → Web UI\n"
            "\n"
            "Optional: opensquad start --detach  (pre-warm daemon only)\n"
        )
        sys.exit(0)

    _dispatch(args)


def _dispatch(args) -> None:
    cmd = args.command
    if cmd == "init":
        from opensquad.cli.commands.init_cmd import run_init

        run_init(args)
    elif cmd == "start":
        from opensquad.cli.commands.start_cmd import run_start

        run_start(args)
    elif cmd == "status":
        from opensquad.cli.commands.status_cmd import run_status

        run_status(args)
    elif cmd == "stop":
        from opensquad.cli.commands.stop_cmd import run_stop

        run_stop(args)
    elif cmd == "doctor":
        from opensquad.cli.commands.doctor_cmd import run_doctor

        run_doctor(args)
    elif cmd == "config":
        from opensquad.cli.commands.config_cmd import run_config

        run_config(args)
    elif cmd == "logs":
        from opensquad.cli.commands.logs_cmd import run_logs

        run_logs(args)
    elif cmd == "restart":
        print("[restart] Stopping services...")
        from opensquad.cli.commands.stop_cmd import run_stop

        run_stop(args)
        import time

        time.sleep(1)
        print("[restart] Starting services...")
        from opensquad.cli.commands.start_cmd import run_start

        run_start(args)
    elif cmd == "update":
        from opensquad.cli.commands.update_cmd import run_update

        run_update(args)
    elif cmd == "login":
        from opensquad.cli.commands.login_cmd import run_login

        run_login(args)
    elif cmd == "logout":
        from opensquad.cli.commands.login_cmd import run_logout

        run_logout(args)
    elif cmd == "whoami":
        from opensquad.cli.commands.login_cmd import run_whoami

        run_whoami(args)
    elif cmd == "agent":
        from opensquad.cli.commands.agent_cmd import run_agent

        run_agent(args)
    elif cmd == "mcp":
        from opensquad.cli.commands.mcp_cmd import run_mcp

        run_mcp(args)
    elif cmd == "skill":
        from opensquad.cli.commands.skill_cmd import run_skill

        run_skill(args)
    elif cmd == "plugin":
        from opensquad.cli.commands.plugin_cmd import run_plugin

        run_plugin(args)
    elif cmd == "role":
        from opensquad.cli.commands.role_cmd import run_role

        run_role(args)
    elif cmd == "model":
        from opensquad.cli.commands.model_cmd import run_model

        run_model(args)
    elif cmd == "collab":
        from opensquad.cli.commands.collab_cmd import run_collab

        run_collab(args)
    elif cmd == "code":
        from opensquad.cli.runtime_boot import run_code

        run_code(args)
    elif cmd == "web":
        from opensquad.cli.commands.web_cmd import run_web

        run_web(args)
    elif cmd == "dev":
        # Developer mode: force source (non-frozen) launch + uvicorn hot-reload
        # so backend/agent Python edits take effect without repackaging.
        os.environ["OPENSQUAD_SOURCE_MODE"] = "1"
        os.environ["OPENSQUAD_RELOAD"] = "1"
        args.dev = True  # always use the Vite dev server (5173)
        from opensquad.cli.commands.web_cmd import run_web

        run_web(args)
    elif cmd in ("chat", "shell"):
        if getattr(args, "start", False):
            from opensquad.cli.runtime_boot import run_code

            run_code(args)
        else:
            from opensquad.cli.commands.chat_cmd import run_chat

            run_chat(args)
    elif cmd == "group":
        from opensquad.cli.commands.group_cmd import run_group

        run_group(args)
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
