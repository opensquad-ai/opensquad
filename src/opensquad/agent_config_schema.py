"""
agent_config_schema.py - Agent config.json structure validation

Called by launcher.py (start/restart paths) to ensure that regardless of
how an agent writes its config, a clear error message and correct format
hint is provided at startup.
"""

from opensquad.system_config import gateway_register_url as _gateway_register_url


def apply_config_defaults(config: dict) -> None:
    """
    In-place completion of config, filling in system default values.
    Called before validate_agent_config to ensure optional fields have reasonable defaults.

    Current rules:
      - model.api_protocol missing -> default to "openai_compat"
        (older configs created before this field was added will not have it)
      - gateway not configured, or gateway.enabled=true but url is empty
        -> automatically fill in the default URL and enable it
      - group_chat not configured -> enabled=true with default IM credentials
      - group_chat.enabled=true but email/password missing -> fill bridge defaults
    """
    # model.api_protocol — required field added in v0.4.11. Old configs
    # created by earlier versions do not have this field. Infer it from
    # provider/base_url so we don't fail validation on upgrade.
    model = config.get("model")
    if isinstance(model, dict) and not model.get("api_protocol"):
        provider = (model.get("provider") or "").lower()
        base_url = (model.get("base_url") or "").lower()
        if provider == "anthropic":
            model["api_protocol"] = "anthropic"
        elif provider == "google":
            model["api_protocol"] = "google"
        elif "api.anthropic.com" in base_url:
            model["api_protocol"] = "anthropic"
        elif "generativelanguage.googleapis.com" in base_url:
            model["api_protocol"] = "google"
        elif "api.openai.com" in base_url:
            model["api_protocol"] = "openai"
        else:
            model["api_protocol"] = "openai_compat"

    gw = config.get("gateway")
    if not isinstance(gw, dict):
        config["gateway"] = {"enabled": True, "url": _gateway_register_url()}
    else:
        if not gw.get("url"):
            gw["url"] = _gateway_register_url()
        if "enabled" not in gw:
            gw["enabled"] = True

    gc = config.get("group_chat")
    if not isinstance(gc, dict):
        config["group_chat"] = {
            "enabled": True,
            "email": "ai@ai",
            "password": "aaaaaa",
            "groups": [],
        }
    else:
        if "enabled" not in gc:
            gc["enabled"] = True
        if gc.get("enabled"):
            if not gc.get("email"):
                gc["email"] = "ai@ai"
            if not gc.get("password"):
                gc["password"] = "aaaaaa"
        if "groups" not in gc or gc.get("groups") is None:
            gc["groups"] = []


def validate_agent_config(config: dict) -> list:
    """
    Validate the structure of agent config.json and return a list of error messages.
    An empty list means the config is valid and the agent can start.

    Required fields:
        agent_id, agent_name
        model.api_protocol, model.model_name, model.base_url
        gateway.url (required only when gateway.enabled=true)

    Optional fields (validated only when enabled):
        group_chat.enabled=true, group_chat.email, group_chat.password
        (group_chat.groups is optional — Bridge subscribes to all joined groups via API)
    """
    errors = []

    # - 0. Top-level flat account fields (structural errors, highest priority) -
    flat_fields = [f for f in ("email", "password", "group_id", "groups") if f in config]
    if flat_fields:
        errors.append(
            f"Top-level fields {flat_fields} are invalid. "
            "Account information must be nested inside the group_chat object. Correct format:\n"
            '  "group_chat": {\n'
            '    "enabled": true,\n'
            '    "email": "mybot@ai",\n'
            '    "password": "Bot@2026",\n'
            '    "groups": ["gXXXXX"]\n'
            "  }"
        )

    # - 1. Top-level required fields -
    for field in ("agent_id", "agent_name"):
        if not config.get(field):
            errors.append(f"Missing required field {field!r}")

    # - 2. model required fields -
    model = config.get("model")
    if not isinstance(model, dict):
        errors.append(
            "Missing required field 'model'. Correct format:\n"
            '  "model": {\n'
            '    "api_protocol": "openai_compat",\n'
            '    "provider": "DeepSeek",\n'
            '    "base_url": "https://api.example.com/v1",\n'
            '    "api_key": "sk-xxx",\n'
            '    "model_name": "gpt-4o",\n'
            '    "token_max": 128000,\n'
            '    "temperature": 0\n'
            "  }"
        )
    else:
        for field in ("api_protocol", "model_name", "base_url"):
            if not model.get(field):
                errors.append(f"Missing required field 'model.{field}'")

    # - 3. group_chat optional (only validate when enabled) -
    gc = config.get("group_chat")
    if isinstance(gc, dict) and gc.get("enabled") is True:
        if not gc.get("email"):
            errors.append("Missing required field 'group_chat.email'")
        if not gc.get("password"):
            errors.append("Missing required field 'group_chat.password'")
        groups = gc.get("groups")
        if isinstance(groups, str):
            errors.append('group_chat.groups must be an array, not a string. Should be ["gXXXXX"] instead of "gXXXXX"')

    return errors
