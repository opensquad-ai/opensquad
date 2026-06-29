# -*- coding: utf-8 -*-
import json
import logging
import os
from typing import Any, Dict, List
from opensquad.plugin_api import register, Plugin, Context, hook

logger = logging.getLogger("plugins.git_core")


def _is_absolute_url(url: str) -> bool:
    """Return True if url is already a full absolute URL or SSH path."""
    return any(url.startswith(p) for p in (
        "http://", "https://", "git://", "ssh://", "git@", "file://"
    ))


def _inject_auth(server_url: str, username: str, token: str) -> str:
    """Embed username:token into an http(s):// URL for git HTTPS authentication."""
    if not token:
        return server_url
    from urllib.parse import urlparse, urlunparse
    parsed = urlparse(server_url)
    if parsed.scheme in ("http", "https"):
        user = username or "oauth2"
        netloc = f"{user}:{token}@{parsed.hostname}"
        if parsed.port:
            netloc += f":{parsed.port}"
        return urlunparse(parsed._replace(netloc=netloc))
    return server_url


@register(
    name="git_core",
    author="OpenSquad",
    description="Core Git tools for local repository management with auto-identity.",
    version="1.1.0",
    plugin_type="tool",
    display_name="Git Core",
    tags=["git", "vcs", "code"],
)
class GitCorePlugin(Plugin):
    def __init__(self, context: Context):
        super().__init__(context)

    def on_load(self) -> None:
        logger.info("[GitCorePlugin] Initialized.")

    # ── Config helper ──────────────────────────────────────────────

    def _get_plugin_config(self) -> dict:
        """
        Read plugin config with priority:
          data/plugins/git_core/config.json  (user-saved via UI)
          > system_config.json vcs section   (system-wide defaults)
          > built-in defaults
        """
        from opensquad.system_config import syscfg
        saved = {}
        config_path = os.path.join(
            syscfg.project_root(), "data", "plugins", "git_core", "config.json"
        )
        if os.path.isfile(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
            except Exception as e:
                logger.warning(f"[GitCore] Failed to read plugin config: {e}")

        return {
            "git_server":     (saved.get("git_server")     or syscfg.vcs_git_server()).rstrip("/"),
            "default_remote": saved.get("default_remote")  or syscfg.vcs_default_remote(),
            "default_branch": saved.get("default_branch")  or syscfg.vcs_default_branch(),
            "username":       saved.get("username", ""),
            "access_token":   saved.get("access_token", ""),
        }

    # ── Hook ───────────────────────────────────────────────────────

    @hook.on_before_tool
    async def inject_git_config(self, ctx: Dict[str, Any]):
        """
        Before any git.* tool call, inject:
          - git.commit     → agent identity as author
          - git.clone      → auto-prepend git_server when URL is a relative path
          - git.push/pull/fetch → fill default remote & branch from config
        """
        t_name = ctx.get("tool_name", "")
        args = ctx.get("arguments", {})

        # ── commit: inject agent identity ──
        if t_name == "git.commit":
            if not args.get("author_name") and self.context.agent_id:
                args["author_name"] = self.context.agent_id
                args["author_email"] = f"{self.context.agent_id}@opensquad.ai"
                logger.info(f"[GitCore] Injected identity: {args['author_name']}")

        # ── clone: expand relative path → full server URL ──
        elif t_name == "git.clone":
            url = args.get("url", "")
            if url and not _is_absolute_url(url):
                cfg = self._get_plugin_config()
                server = cfg.get("git_server", "")
                if server:
                    auth_server = _inject_auth(
                        server,
                        cfg.get("username", ""),
                        cfg.get("access_token", ""),
                    )
                    args["url"] = f"{auth_server}/{url.lstrip('/')}"
                    logger.info(f"[GitCore] Expanded clone URL → {args['url']}")

        # ── push / pull / fetch: inject default remote & branch ──
        elif t_name in ("git.push", "git.pull", "git.fetch"):
            cfg = self._get_plugin_config()
            if not args.get("remote") and cfg.get("default_remote"):
                args["remote"] = cfg["default_remote"]
            if t_name in ("git.push", "git.pull") and not args.get("branch") and cfg.get("default_branch"):
                args["branch"] = cfg["default_branch"]

        return ctx

    def get_tool_modules(self) -> List[Dict[str, Any]]:
        from . import git_tools
        return [
            {
                "name": "git",
                "module": git_tools,
                "level": "core",
                "auto_register": True,
            }
        ]
