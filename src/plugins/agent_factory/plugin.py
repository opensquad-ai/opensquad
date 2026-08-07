"""
Agent Factory Plugin

Dynamically create, configure, and manage Agents via the Launcher HTTP API:
  1. create_agent       — Create a new Agent directory with a default config.json
  2. configure_agent    — Write a full config.json (model, email, password, groups, etc.)
  3. set_agent_role     — Write role.md (role/persona definition)
  4. start_agent        — Start the Agent process
  5. stop_agent         — Stop the Agent process
  6. restart_agent      — Restart the Agent process
  7. list_agents        — List all Agents and their runtime status
  8. list_model_cards   — List all available model cards in the model card library
  9. get_model_card     — Get the full configuration of a specific model card
  10. assign_model_card — Assign a model card to an Agent
  11. create_model_card — Create a new model card configuration
"""

import logging
from typing import Any

import requests

from opensquad.plugin_api import Context, Plugin, register, tool

logger = logging.getLogger("plugins.agent_factory")


@register(
    name="agent_factory",
    author="OpenSquad",
    description="Agent Factory: dynamically create, configure, and launch new Agents via the Launcher API",
    version="1.0.0",
    plugin_type="tool",
    display_name="Agent Factory",
    tags=["agent"],
)
class AgentFactoryPlugin(Plugin):
    """Manages the Agent lifecycle via the Launcher API."""

    def __init__(self, context: Context):
        super().__init__(context)
        self._launcher_url: str = ""

    def on_load(self) -> None:
        from opensquad.system_config import syscfg

        self._launcher_url = syscfg.launcher_url()
        logger.info(f"[AgentFactoryPlugin] Loaded, Launcher={self._launcher_url}")

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        try:
            r = requests.get(f"{self._launcher_url}{path}", params=params, timeout=15)
            if r.status_code >= 400:
                try:
                    return {"error": r.json().get("error", r.text[:300])}
                except Exception:
                    return {"error": r.text[:300]}
            return r.json()
        except Exception as e:
            return {"error": str(e)}

    def _post(self, path: str, body: dict | None = None) -> dict[str, Any]:
        try:
            r = requests.post(f"{self._launcher_url}{path}", json=body or {}, timeout=15)
            if r.status_code >= 400:
                try:
                    return {"error": r.json().get("error", r.text[:300])}
                except Exception:
                    return {"error": r.text[:300]}
            return r.json()
        except Exception as e:
            return {"error": str(e)}

    def _put(self, path: str, body: dict | None = None) -> dict[str, Any]:
        try:
            r = requests.put(f"{self._launcher_url}{path}", json=body or {}, timeout=15)
            if r.status_code >= 400:
                try:
                    return {"error": r.json().get("error", r.text[:300])}
                except Exception:
                    return {"error": r.text[:300]}
            return r.json()
        except Exception as e:
            return {"error": str(e)}

    # ── Tool methods ──────────────────────────────────────────────────────────

    @tool(name="agent_factory", level="extended", auto_register=False)
    def create_agent(
        self,
        dir_name: str,
        agent_name: str,
        agent_type: str = "general",
        description: str = "",
    ) -> dict[str, Any]:
        """
        Create a new Agent under the agents/ directory, generating a default config.json, role.md, and mcp_config.json.

        Args:
            dir_name:    Directory name (letters/digits/underscores only, e.g. "my_agent").
            agent_name:  Display name (e.g. "My Assistant").
            agent_type:  Agent type, default "general".
            description: Agent description (optional).

        Returns:
            {"success": true, "dir_name": "my_agent", "message": "..."}
        """
        result = self._post(
            "/api/agents/create",
            {
                "name": dir_name,
                "agent_name": agent_name,
                "agent_type": agent_type,
                "description": description,
            },
        )
        if "error" in result:
            return {"success": False, "error": result["error"]}
        logger.info(f"[AgentFactoryPlugin] Created agent: {dir_name}")
        return {"success": True, "dir_name": dir_name, "message": result.get("message", "OK")}

    @tool(name="agent_factory", level="extended", auto_register=False)
    def configure_agent(
        self,
        dir_name: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Write the Agent's full config.json. This overwrites the existing configuration; pass the complete structure.

        Typical configuration example:
        {
          "agent_id": "mybot-001",
          "agent_name": "My Bot",
          "agent_type": "general",
          "description": "...",
          "model": {
            "api_protocol": "openai_compat",
            "provider": "DeepSeek",
            "api_key": "sk-xxx",
            "base_url": "https://api.deepseek.com",
            "model_name": "deepseek-chat",
            "token_max": 128000,
            "temperature": 0.3,
            "tool_call_mode": "native",  # recommended: native (force Native FC) | auto (auto-detect) | xml (force XML)
            "tool_filter": "high"      # recommended: high (97 tools) | all (124) | baseline (57)
          },
          "tools": ["system", "filesystem", "im", "agent_setup"],
          "group_chat": {
            "enabled": true,
            "email": "mybot@ai",
            "password": "123456",
            "groups": ["gXXXXX"]
          },
          "web_server": {"enabled": true, "port": 8010},
          "gateway": {"enabled": true, "url": "ws://127.0.0.1:9555/ai-ws/register"},
          "mcp": {"enabled": true},
          "collaboration": {"enabled": true, "role": "worker"},
          "skills": {"enabled": true, "active": []}
        }

        Args:
            dir_name: Agent directory name.
            config:   Full configuration dictionary.

        Returns:
            {"success": true, "dir_name": "..."}
        """
        result = self._put(f"/api/agents/{dir_name}/config", {"config": config})
        if "error" in result:
            return {"success": False, "error": result["error"]}
        logger.info(f"[AgentFactoryPlugin] Configured agent: {dir_name}")
        return {"success": True, "dir_name": dir_name, "message": result.get("message", "OK")}

    @tool(name="agent_factory", level="extended", auto_register=False)
    def set_agent_role(
        self,
        dir_name: str,
        role_content: str,
    ) -> dict[str, Any]:
        """
        Write the Agent's role.md (role/persona definition file).

        role.md defines the Agent's identity, expertise, and behavior, and is injected
        into the system prompt on every conversation.

        Args:
            dir_name:     Agent directory name.
            role_content: Full content of role.md (Markdown format).

        Returns:
            {"success": true, "dir_name": "..."}
        """
        result = self._put(f"/api/agents/{dir_name}/role", {"content": role_content})
        if "error" in result:
            return {"success": False, "error": result["error"]}
        logger.info(f"[AgentFactoryPlugin] Set role for agent: {dir_name}")
        return {"success": True, "dir_name": dir_name, "message": result.get("message", "OK")}

    @tool(name="agent_factory", level="extended", auto_register=False)
    def start_agent(self, dir_name: str) -> dict[str, Any]:
        """
        Start the specified Agent process. The Agent directory must exist and have a valid config.json.

        Args:
            dir_name: Agent directory name.

        Returns:
            {"success": true, "dir_name": "...", "pid": 12345, "port": 8010}
        """
        result = self._post(f"/api/agents/{dir_name}/start")
        if "error" in result:
            return {"success": False, "error": result["error"]}
        logger.info(f"[AgentFactoryPlugin] Started agent: {dir_name} pid={result.get('pid')}")
        return {
            "success": True,
            "dir_name": dir_name,
            "pid": result.get("pid"),
            "port": result.get("port"),
            "message": result.get("message", "OK"),
        }

    @tool(name="agent_factory", level="extended", auto_register=False)
    def stop_agent(self, dir_name: str) -> dict[str, Any]:
        """
        Stop the specified Agent process.

        Args:
            dir_name: Agent directory name.

        Returns:
            {"success": true, "dir_name": "..."}
        """
        result = self._post(f"/api/agents/{dir_name}/stop")
        if "error" in result:
            return {"success": False, "error": result["error"]}
        logger.info(f"[AgentFactoryPlugin] Stopped agent: {dir_name}")
        return {"success": True, "dir_name": dir_name, "message": result.get("message", "OK")}

    @tool(name="agent_factory", level="extended", auto_register=False)
    def restart_agent(self, dir_name: str) -> dict[str, Any]:
        """
        Restart the specified Agent process (stop then start).
        Call this after modifying configuration to apply changes immediately.

        **IMPORTANT**: Restarting will interrupt your current work. Before calling this tool,
        if you have an ongoing task that needs to continue after restart, FIRST register a
        restart-gated reminder using reminder.set_on_next_restart():
          reminder.set_on_next_restart(message="Continue the previous task")
        This survives restarts — the message fires immediately after the agent boots up.

        Args:
            dir_name: Agent directory name.

        Returns:
            {"success": true, "dir_name": "...", "pid": 12345}
        """
        result = self._post(f"/api/agents/{dir_name}/restart")
        if "error" in result:
            return {"success": False, "error": result["error"]}
        logger.info(f"[AgentFactoryPlugin] Restarted agent: {dir_name} pid={result.get('pid')}")
        return {
            "success": True,
            "dir_name": dir_name,
            "pid": result.get("pid"),
            "message": result.get("message", "OK"),
        }

    @tool(name="agent_factory", level="extended", auto_register=False)
    def list_agents(self) -> dict[str, Any]:
        """
        List all discovered Agents and their real-time runtime status.

        Returns:
            {
              "success": true,
              "count": 3,
              "agents": [
                {
                  "dir_name": "coder",
                  "agent_id": "coder-001",
                  "agent_name": "coder-001",
                  "alive": true,
                  "pid": 12345,
                  "port": 8002
                },
                ...
              ]
            }
        """
        result = self._get("/api/agents")
        if "error" in result:
            return {"success": False, "error": result["error"]}
        agents_raw = result.get("agents", [])
        agents = [
            {
                "dir_name": a.get("dir_name", a.get("name", "")),
                "agent_id": a.get("agent_id", ""),
                "agent_name": a.get("agent_name", ""),
                "alive": a.get("alive", False),
                "pid": a.get("pid"),
                "port": a.get("actual_port") or a.get("port"),
            }
            for a in agents_raw
            if isinstance(a, dict)
        ]
        return {"success": True, "count": len(agents), "agents": agents}

    @tool(name="agent_factory", level="extended", auto_register=False)
    def list_model_cards(self) -> dict[str, Any]:
        """
        List all available model cards in the model card library.

        Model cards contain pre-configured model parameters (api_protocol, provider, model_name, base_url,
        token_max, etc.) and can be assigned directly to Agents, avoiding manual configuration
        of complex model parameters.

        Returns:
            {
              "success": true,
              "count": 5,
              "cards": [
                {
                  "name": "deepseek_chat",
                  "title": "DeepSeek Chat",
                  "api_protocol": "openai_compat",
                  "provider": "DeepSeek",
                  "model_name": "deepseek-chat",
                  "base_url": "https://api.deepseek.com/v1",
                  "token_max": 128000,
                  "temperature": 0.3,
                  "is_think": false,
                  "is_image": false,
                  "is_video": false
                },
                ...
              ]
            }
        """
        result = self._get("/api/model-cards")
        if "error" in result:
            return {"success": False, "error": result["error"]}
        cards = result.get("cards", [])
        return {"success": True, "count": len(cards), "cards": cards}

    @tool(name="agent_factory", level="extended", auto_register=False)
    def get_model_card(self, card_name: str) -> dict[str, Any]:
        """
        Get the full configuration of a specific model card.

        Args:
            card_name: Model card name (e.g. "deepseek_chat", "GLM-5")

        Returns:
            {
              "success": true,
              "name": "deepseek_chat",
              "card": {
                "name": "deepseek_chat",
                "title": "DeepSeek Chat",
                "api_protocol": "openai_compat",
                "provider": "DeepSeek",
                "api_key": "sk-xxx",
                "base_url": "https://api.deepseek.com/v1",
                "model_name": "deepseek-chat",
                "token_max": 128000,
                "temperature": 0.3,
                "tool_call_mode": "native",
                "is_think": false,
                "is_image": false,
                "is_video": false
              }
            }
        """
        result = self._get(f"/api/model-cards/{card_name}")
        if "error" in result:
            return {"success": False, "error": result["error"]}
        return {"success": True, "name": card_name, "card": result.get("card", {})}

    @tool(name="agent_factory", level="extended", auto_register=False)
    def assign_model_card(self, dir_name: str, card_name: str) -> dict[str, Any]:
        """
        Assign a model card to the specified Agent.

        After assignment, the model card's configuration is written to the Agent's
        config.json model field. The Agent will use the model card settings
        (provider, model_name, base_url, etc.) after restart.

        Args:
            dir_name:  Agent directory name (e.g. "coder", "pm")
            card_name: Model card name (e.g. "deepseek_chat", "GLM-5")

        Returns:
            {
              "success": true,
              "message": "Model card 'deepseek_chat' assigned to agent 'coder'. Restart required."
            }

        Example:
            # Assign DeepSeek model card to coder Agent
            result = agent_factory.assign_model_card("coder", "deepseek_chat")

            # Restart Agent to apply changes
            agent_factory.restart_agent("coder")
        """
        # Fetch model card content first
        card_result = self._get(f"/api/model-cards/{card_name}")
        if "error" in card_result:
            return {"success": False, "error": f"Model card not found: {card_name}"}

        card = card_result.get("card", {})

        # Assign the model card via the Launcher API
        result = self._put(f"/api/agents/{dir_name}/model-card", card)
        if "error" in result:
            return {"success": False, "error": result["error"]}

        return {
            "success": True,
            "message": f"Model card '{card_name}' assigned to Agent '{dir_name}'. Agent restart required to take effect.",
        }

    @tool(name="agent_factory", level="extended", auto_register=False)
    def create_model_card(
        self,
        card_name: str,
        title: str,
        api_protocol: str = "openai_compat",
        provider: str = "",
        model_name: str = "",
        api_key: str = "",
        base_url: str = "",
        token_max: int = 128000,
        temperature: float = 0.3,
        tool_call_mode: str = "native",
        is_think: bool = False,
        is_image: bool = False,
        is_video: bool = False,
    ) -> dict[str, Any]:
        """
        Create a new model card configuration.

        Args:
            card_name:      Model card name (e.g. "my_model"), used as the filename
            title:          Model card display title (e.g. "My Model")
            api_protocol:   API protocol (openai_compat | openai | anthropic | google) -- 之前叫 provider
            provider:       Vendor / provider display name (e.g. "DeepSeek", "OpenAI", "Google Gemini")
            model_name:     Model name (e.g. "gpt-4o", "deepseek-chat")
            api_key:        API key (optional; leave empty to read from environment variable)
            base_url:       API endpoint URL (required for openai_compat)
            token_max:      Maximum token count (default 128000)
            temperature:    Temperature parameter (default 0.3)
            tool_call_mode: Tool call mode (native | xml | auto, default native)
            is_think:       Whether this is a reasoning model (e.g. o1, DeepSeek Reasoner)
            is_image:       Whether image input is supported
            is_video:       Whether video input is supported

        Returns:
            {
              "success": true,
              "message": "Model card 'my_model' created successfully"
            }

        Example:
            # Create a custom model card
            result = agent_factory.create_model_card(
                card_name="my_deepseek",
                title="My DeepSeek Config",
                api_protocol="openai_compat",
                provider="DeepSeek",
                model_name="deepseek-chat",
                base_url="https://api.deepseek.com/v1",
                api_key="sk-xxx",
                token_max=128000,
                temperature=0.5
            )
        """
        card = {
            "name": card_name,
            "title": title,
            "api_protocol": api_protocol,
            "provider": provider,
            "api_key": api_key,
            "base_url": base_url,
            "model_name": model_name,
            "token_max": token_max,
            "temperature": temperature,
            "tool_call_mode": tool_call_mode,
            "is_think": is_think,
            "is_image": is_image,
            "is_video": is_video,
        }

        result = self._put(f"/api/model-cards/{card_name}", card)
        if "error" in result:
            return {"success": False, "error": result["error"]}

        return {"success": True, "message": f"Model card '{card_name}' created successfully"}
