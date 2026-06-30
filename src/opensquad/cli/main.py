"""
OpenSquad CLI

Usage:
    opensquad                                   Start all services (same as 'opensquad start')
    opensquad --version                         Show current version
    opensquad init [--workspace <path>]         Initialize workspace (default: ~/.opensquad/workspace)
    opensquad start [--port <port>] [--no-launcher] [--no-gateway] [--no-registry] [--no-frontend]
    opensquad status                            Show agent and service status
    opensquad stop                              Kill all OpenSquad services by port
    opensquad update                            Check for updates and upgrade
    opensquad plugin install <id>               Install a plugin from the store
    opensquad plugin uninstall <id>             Uninstall a plugin
    opensquad plugin list                       List installed plugins
"""

import argparse
import sys


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
    p_start = sub.add_parser("start", help="Start all OpenSquad services")
    p_start.add_argument("--verbose", action="store_true", help="Show all service logs in the console (default: quiet)")
    p_start.add_argument("--port", "-p", type=int, default=None, help="Gateway port (default: from config)")
    p_start.add_argument("--no-launcher", action="store_true", help="Skip launcher service")
    p_start.add_argument("--no-gateway", action="store_true", help="Skip gateway backend")
    p_start.add_argument("--no-registry", action="store_true", help="Skip plugin registry")
    p_start.add_argument("--no-frontend", action="store_true", help="Skip frontend dev server")
    p_start.add_argument("--no-watchdog", action="store_true", help="Skip health-check watchdog")

    # ── status ──
    p_status = sub.add_parser("status", help="Show agent and service status")
    p_status.add_argument("--port", type=int, default=None, help="Launcher management port")

    # ── stop ──
    sub.add_parser("stop", help="Kill all OpenSquad services (clean up ports)")

    # ── restart ──
    sub.add_parser("restart", help="Stop then start all services")

    # ── config ──
    p_config = sub.add_parser("config", help="Validate or show configuration")
    p_config.add_argument(
        "action", nargs="?", default="validate", choices=["validate", "show"], help="Action (default: validate)"
    )

    # ── doctor ──
    sub.add_parser("doctor", help="Run system diagnostic report")

    # ── logs ──
    p_logs = sub.add_parser("logs", help="View and filter service logs")
    p_logs.add_argument("--service", "-s", default="gateway", help="Service to show logs for (default: gateway)")
    p_logs.add_argument("--list", action="store_true", dest="list_services", help="List available log sources")
    p_logs.add_argument("--tail", "-n", type=int, default=30, help="Show last N lines (default: 30, 0=show all)")
    p_logs.add_argument("--level", "-l", default="", help="Filter by log level (e.g. ERROR, WARNING)")
    p_logs.add_argument("--grep", "-g", default="", help="Filter lines containing text (case-insensitive)")

    # ── help ──
    sub.add_parser("help", help="Show this help message")

    # ── update ──
    sub.add_parser("update", help="Check for updates and upgrade to the latest version")

    # ── plugin ──
    p_plugin = sub.add_parser("plugin", help="Manage plugins")
    plugin_sub = p_plugin.add_subparsers(dest="plugin_action")

    p_install = plugin_sub.add_parser("install", help="Install a plugin from the store or Git URL")
    p_install.add_argument("plugin_id", help="Plugin ID or Git URL")
    p_install.add_argument("--mode", choices=["smart", "build"], default="smart", help="Install mode")

    p_uninstall = plugin_sub.add_parser("uninstall", help="Uninstall a plugin")
    p_uninstall.add_argument("plugin_id", help="Plugin ID to uninstall")

    plugin_sub.add_parser("list", help="List installed plugins")

    args = parser.parse_args()

    # --version flag (before subcommand parsing)
    if getattr(args, "version", False):
        from opensquad import __version__

        print(f"opensquad v{__version__}")
        sys.exit(0)

    if not args.command:
        # Default: run 'start' when no subcommand is given
        from argparse import Namespace

        args = Namespace(
            command="start",
            port=None,
            no_launcher=False,
            no_gateway=False,
            no_registry=False,
            no_frontend=False,
            no_watchdog=False,
            verbose=getattr(args, "verbose", False),
        )

    if args.command == "help":
        parser.print_help()
        sys.exit(0)

    if args.command == "init":
        from opensquad.cli.commands.init_cmd import run_init

        run_init(args)
    elif args.command == "start":
        from opensquad.cli.commands.start_cmd import run_start

        run_start(args)
    elif args.command == "status":
        from opensquad.cli.commands.status_cmd import run_status

        run_status(args)
    elif args.command == "stop":
        from opensquad.cli.commands.stop_cmd import run_stop

        run_stop(args)
    elif args.command == "doctor":
        from opensquad.cli.commands.doctor_cmd import run_doctor

        run_doctor(args)
    elif args.command == "config":
        from opensquad.cli.commands.config_cmd import run_config

        run_config(args)
    elif args.command == "logs":
        from opensquad.cli.commands.logs_cmd import run_logs

        run_logs(args)
    elif args.command == "restart":
        print("[restart] Stopping services...")
        from opensquad.cli.commands.stop_cmd import run_stop

        run_stop(args)
        import time

        time.sleep(1)
        print("[restart] Starting services...")
        from opensquad.cli.commands.start_cmd import run_start

        run_start(args)
    elif args.command == "update":
        from opensquad.cli.commands.update_cmd import run_update

        run_update(args)
    elif args.command == "plugin":
        from opensquad.cli.commands.plugin_cmd import run_plugin

        run_plugin(args)


if __name__ == "__main__":
    main()
