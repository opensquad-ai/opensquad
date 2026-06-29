"""
Chat Account Manager Plugin

Manage accounts and groups in the ChatPro system:
  1. register_account   — Register a new account
  2. create_group       — Create a new group
  3. join_group         — Join a group
  4. leave_group        — Leave a group
  5. list_groups        — List joined groups

All operations call the ChatPro HTTP API directly, with no coupling to a bridge instance.
email/password are optional; if omitted, the current agent's group_chat config credentials are used.
"""

import logging
from typing import Any

import requests

from opensquad.plugin_api import Context, Plugin, register, tool

logger = logging.getLogger("plugins.chat_account")


@register(
    name="chat_account",
    author="OpenSquad",
    description="ChatPro account and group management: register account, create group, join group, leave group, list groups",
    version="1.0.0",
    plugin_type="tool",
    display_name="Chat Account Manager",
    tags=["im"],
)
class ChatAccountPlugin(Plugin):
    """ChatPro account and group management plugin."""

    def __init__(self, context: Context):
        super().__init__(context)
        self._base_url: str = ""
        self._node_secret: str = ""

    def on_load(self) -> None:
        from opensquad.system_config import syscfg

        self._base_url = syscfg.gateway_http()
        self._node_secret = syscfg.node_secret() or ""
        logger.info(f"[ChatAccountPlugin] Loaded, ChatPro base_url={self._base_url}")

    def _internal_headers(self) -> dict:
        """Headers for internal (trusted) calls to the gateway.

        Carries the node_secret so the backend can treat the request as an
        internal call (bypasses web-only gates such as the first-user-only
        registration lock on /auth/register).
        """
        if self._node_secret:
            return {"X-Node-Secret": self._node_secret}
        return {}

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _resolve_credentials(self, email: str | None, password: str | None) -> tuple[str, str]:
        """If credentials are not provided, fetch them from the current agent's group_chat config."""
        if email and password:
            return email, password
        gc = self.context.config.get("group_chat", {})
        return gc.get("email", ""), gc.get("password", "")

    def _login(self, email: str, password: str) -> tuple[str | None, str | None]:
        """
        Log in and return (token, error_message).
        On success returns (token_string, None); on failure returns (None, error_message).
        """
        try:
            r = requests.post(
                f"{self._base_url}/api/auth/login",
                json={"email": email, "password": password},
                timeout=10,
            )

            # Handle 401 - account does not exist or wrong password
            if r.status_code == 401:
                detail = r.json().get("detail", "Incorrect email or password")
                logger.error(f"[ChatAccountPlugin] Login failed ({email}): {detail}")
                return None, f"Authentication failed: {detail} (account not found or wrong password)"

            # Handle other HTTP errors
            if r.status_code != 200:
                error_msg = f"HTTP {r.status_code}"
                try:
                    detail = r.json().get("detail", r.text[:100])
                    error_msg = f"{error_msg}: {detail}"
                except (KeyError, ValueError, AttributeError):
                    error_msg = f"{error_msg}: {r.text[:100]}"
                logger.error(f"[ChatAccountPlugin] Login failed ({email}): {error_msg}")
                return None, f"Server error: {error_msg}"

            # Success
            token = r.json()["access_token"]
            return token, None
        except requests.exceptions.Timeout:
            error_msg = "Request timed out (10s)"
            logger.error(f"[ChatAccountPlugin] Login timeout ({email})")
            return None, error_msg
        except requests.exceptions.ConnectionError as e:
            error_msg = f"Connection failed: {str(e)[:100]}"
            logger.error(f"[ChatAccountPlugin] Login connection error ({email}): {e}")
            return None, error_msg
        except KeyError:
            error_msg = "Invalid response format (missing access_token)"
            logger.error(f"[ChatAccountPlugin] Login response missing access_token ({email})")
            return None, error_msg
        except Exception as e:
            error_msg = f"Unknown error: {str(e)[:100]}"
            logger.error(f"[ChatAccountPlugin] Login failed ({email}): {e}")
            return None, error_msg

    # ── Tool methods ──────────────────────────────────────────────────────────

    @tool(name="chat_account", level="extended", auto_register=False)
    def register_account(
        self,
        email: str,
        password: str,
        name: str,
    ) -> dict[str, Any]:
        """
        Register a new account in the ChatPro system.

        Args:
            email:    Login email (e.g. "mybot@ai").
            password: Login password.
            name:     Display name (user nickname).

        Returns:
            {"success": true, "user_id": "...", "name": "...", "email": "..."}
        """
        try:
            r = requests.post(
                f"{self._base_url}/api/auth/register",
                json={"email": email, "password": password, "name": name},
                headers=self._internal_headers(),
                timeout=10,
            )
            if r.status_code == 400:
                detail = r.json().get("detail", r.text)
                return {"success": False, "error": detail}
            r.raise_for_status()
            data = r.json()
            user = data.get("user", {})
            logger.info(f"[ChatAccountPlugin] Registered: {email} ({user.get('id')})")
            return {
                "success": True,
                "user_id": user.get("id", ""),
                "name": user.get("name", name),
                "email": email,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool(name="chat_account", level="extended", auto_register=False)
    def create_group(
        self,
        name: str,
        description: str = "",
        is_private: bool = False,
        email: str | None = None,
        password: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a new group. The creator is automatically added as a member.

        Args:
            name:        Group name.
            description: Group description (optional).
            is_private:  Whether the group is private (default False, public group).
            email:       Account email (omit to use the current agent's credentials).
            password:    Account password (omit to use the current agent's credentials).

        Returns:
            {"success": true, "group_id": "gXXXXX", "name": "...", "is_private": false}
        """
        email, password = self._resolve_credentials(email, password)
        if not email or not password:
            return {"success": False, "error": "No credentials available; please provide email and password"}

        token, error = self._login(email, password)
        if not token:
            return {"success": False, "error": f"Login failed: {error}"}

        try:
            r = requests.post(
                f"{self._base_url}/api/groups",
                params={"token": token},
                json={"name": name, "description": description, "is_private": is_private},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            gid = data.get("id", "")
            logger.info(f"[ChatAccountPlugin] Created group: {gid} ({name})")
            return {
                "success": True,
                "group_id": gid,
                "name": data.get("name", name),
                "is_private": data.get("is_private", is_private),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool(name="chat_account", level="extended", auto_register=False)
    def join_group(
        self,
        group_id: str,
        email: str | None = None,
        password: str | None = None,
    ) -> dict[str, Any]:
        """
        Make the specified account join a group.

        Args:
            group_id: Group ID (e.g. "g813q4").
            email:    Account email (omit to use the current agent's credentials).
            password: Account password (omit to use the current agent's credentials).

        Returns:
            {"success": true, "group_id": "g813q4"}
        """
        email, password = self._resolve_credentials(email, password)
        if not email or not password:
            return {"success": False, "error": "No credentials available; please provide email and password"}

        token, error = self._login(email, password)
        if not token:
            return {"success": False, "error": f"Login failed: {error}"}

        try:
            r = requests.post(
                f"{self._base_url}/api/groups/{group_id}/join",
                params={"token": token},
                timeout=10,
            )
            if r.status_code == 200:
                logger.info(f"[ChatAccountPlugin] {email} joined group {group_id}")
                return {"success": True, "group_id": group_id}
            detail = r.json().get("detail", r.text) if r.content else f"HTTP {r.status_code}"
            return {"success": False, "error": detail}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool(name="chat_account", level="extended", auto_register=False)
    def leave_group(
        self,
        group_id: str,
        email: str | None = None,
        password: str | None = None,
    ) -> dict[str, Any]:
        """
        Make the specified account leave a group.

        Args:
            group_id: Group ID (e.g. "g813q4").
            email:    Account email (omit to use the current agent's credentials).
            password: Account password (omit to use the current agent's credentials).

        Returns:
            {"success": true, "group_id": "g813q4"}
        """
        email, password = self._resolve_credentials(email, password)
        if not email or not password:
            return {"success": False, "error": "No credentials available; please provide email and password"}

        token, error = self._login(email, password)
        if not token:
            return {"success": False, "error": f"Login failed: {error}"}

        try:
            r = requests.post(
                f"{self._base_url}/api/groups/{group_id}/leave",
                params={"token": token},
                timeout=10,
            )
            if r.status_code == 200:
                logger.info(f"[ChatAccountPlugin] {email} left group {group_id}")
                return {"success": True, "group_id": group_id}
            detail = r.json().get("detail", r.text) if r.content else f"HTTP {r.status_code}"
            return {"success": False, "error": detail}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool(name="chat_account", level="extended", auto_register=False)
    def list_groups(
        self,
        email: str | None = None,
        password: str | None = None,
    ) -> dict[str, Any]:
        """
        List all groups the specified account has joined.

        Args:
            email:    Account email (omit to use the current agent's credentials).
            password: Account password (omit to use the current agent's credentials).

        Returns:
            {"success": true, "count": 2, "groups": [{"id": "...", "name": "..."}, ...]}
        """
        email, password = self._resolve_credentials(email, password)
        if not email or not password:
            return {"success": False, "error": "No credentials available; please provide email and password"}

        token, error = self._login(email, password)
        if not token:
            return {"success": False, "error": f"Login failed: {error}"}

        try:
            r = requests.get(
                f"{self._base_url}/api/groups",
                params={"token": token},
                timeout=10,
            )
            r.raise_for_status()
            groups = r.json() if isinstance(r.json(), list) else []
            result = [
                {"id": g.get("id", ""), "name": g.get("name", ""), "description": g.get("description", "")}
                for g in groups
                if isinstance(g, dict)
            ]
            return {"success": True, "count": len(result), "groups": result}
        except Exception as e:
            return {"success": False, "error": str(e)}
