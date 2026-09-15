"""Role / collab / model cards and role prompts for the Launcher management API.

Extracted verbatim from ``launcher_main._start_management_server`` -- method
bodies are byte-identical to the original, only re-indented, so this is a pure
move.  See ``management_api/__init__.py`` for the composed handler.

Shared launcher registries are imported **by value** from ``launcher_main``.
That is safe because launcher_main only ever mutates these containers in place
(``dict[...] = ...`` / ``.append`` / ``.add``); the single rebind of
``_BUILTIN_PLUGINS`` happens at launcher_main module load, i.e. before this
module can be imported -- ``launcher_main`` imports this package lazily, from
inside the function that starts the server.
"""

from __future__ import annotations

import json
import os

from opensquad.launcher.process_manager import _read_json
from opensquad.launcher_main import (
    AGENTS_DIR,
    COLLAB_CARDS_DIR,
    MODEL_CARDS_DIR,
    ROLE_CARDS_DIR,
    _processes,
)

# Known model-card fields and their defaults, in write order.  The PUT below
# reads this table so it can distinguish "the client sent a value" from "the
# client said nothing" -- see ``_handle_put_model_card``.
_MODEL_CARD_DEFAULTS: dict[str, object] = {
    "title": "",
    "api_protocol": "openai_compat",
    "provider": "",
    "api_key": "",
    "base_url": "",
    "model_name": "",
    "token_max": 128000,
    "tool_output_max_chars": 50000,
    "temperature": 0,
    "frequency_penalty": 0.0,
    "presence_penalty": 0.0,
    "top_k": 0,
    "is_think": False,
    "is_image": False,
    "is_audio": False,
    "is_video": False,
    "is_audio_output": False,
    "is_image_output": False,
    # Image-generation knobs (read by chat_api / agents_boot).  They were missing
    # from the old whitelist, so saving e.g. builtin ``step-image-edit-2`` from the
    # Agent Web silently dropped them and the runtime fell back to these defaults.
    "image_size": "1024x1024",
    "image_steps": 8,
    "image_cfg_scale": 1.0,
    "audio_output_voice": "alloy",
    "asr_protocol": "",
    "builtin_service": "",
    "is_builtin": False,
    "group_asr": False,
    "auto_asr": False,
    "render_mode": "strict",
    "enabled": True,  # false = hidden from Agent Web switcher
    "tool_call_mode": "auto",
    "enable_repetition_check": False,
}


class CardsMixin:
    """Role / collab / model cards and role prompts."""

    def _list_cards(self, cards_dir: str):
        """Scan the cards directory, parse SKILL.md-style frontmatter, and return the card list"""
        cards = []
        if not os.path.isdir(cards_dir):
            return cards
        for fname in sorted(os.listdir(cards_dir)):
            if not fname.endswith(".md"):
                continue
            card_name = fname[:-3]
            fpath = os.path.join(cards_dir, fname)
            try:
                with open(fpath, encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                content = ""
            # Parse YAML frontmatter (--- ... --- block)
            fm = {}
            body = content
            if content.startswith("---"):
                end = content.find("\n---", 3)
                if end != -1:
                    fm_text = content[3:end].strip()
                    for line in fm_text.splitlines():
                        if ":" in line:
                            k, _, v = line.partition(":")
                            fm[k.strip()] = v.strip()
                    body = content[end + 4 :].lstrip("\n")
            # Extract title (frontmatter name > first # heading in body > card_name)
            title = fm.get("name", card_name)
            for line in body.splitlines():
                stripped = line.strip()
                if stripped.startswith("# "):
                    title = stripped[2:].strip()
                    break
            # Extract tags (comma-separated string → list)
            tags_raw = fm.get("tags", "")
            tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
            # description: prefer frontmatter value; otherwise use first 100 chars of body
            description = fm.get("description", "") or " ".join(body.split())[:100]
            cards.append(
                {
                    "name": card_name,
                    "title": title,
                    "description": description,
                    "tags": tags,
                    "char_count": len(content),
                }
            )
        return cards

    def _handle_list_role_cards(self):
        return self._send_json({"cards": self._list_cards(ROLE_CARDS_DIR)})

    def _handle_get_role_card(self, card_name: str):
        fpath = os.path.join(ROLE_CARDS_DIR, f"{card_name}.md")
        if not os.path.isfile(fpath):
            return self._send_json({"error": "Card not found"}, 404)
        with open(fpath, encoding="utf-8") as f:
            content = f.read()
        return self._send_json({"name": card_name, "content": content})

    def _handle_put_role_card(self, card_name: str, body: dict):
        os.makedirs(ROLE_CARDS_DIR, exist_ok=True)
        content = body.get("content", "")
        fpath = os.path.join(ROLE_CARDS_DIR, f"{card_name}.md")
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(content)
        return self._send_json({"ok": True, "name": card_name})

    def _handle_delete_role_card(self, card_name: str):
        fpath = os.path.join(ROLE_CARDS_DIR, f"{card_name}.md")
        if not os.path.isfile(fpath):
            return self._send_json({"error": "Card not found"}, 404)
        os.remove(fpath)
        return self._send_json({"ok": True, "name": card_name})

    def _handle_list_collab_cards(self):
        return self._send_json({"cards": self._list_cards(COLLAB_CARDS_DIR)})

    def _handle_get_collab_card(self, card_name: str):
        fpath = os.path.join(COLLAB_CARDS_DIR, f"{card_name}.md")
        if not os.path.isfile(fpath):
            return self._send_json({"error": "Card not found"}, 404)
        with open(fpath, encoding="utf-8") as f:
            content = f.read()
        return self._send_json({"name": card_name, "content": content})

    def _handle_put_collab_card(self, card_name: str, body: dict):
        os.makedirs(COLLAB_CARDS_DIR, exist_ok=True)
        content = body.get("content", "")
        fpath = os.path.join(COLLAB_CARDS_DIR, f"{card_name}.md")
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(content)
        return self._send_json({"ok": True, "name": card_name})

    def _handle_delete_collab_card(self, card_name: str):
        fpath = os.path.join(COLLAB_CARDS_DIR, f"{card_name}.md")
        if not os.path.isfile(fpath):
            return self._send_json({"error": "Card not found"}, 404)
        os.remove(fpath)
        return self._send_json({"ok": True, "name": card_name})

    def _handle_put_role_prompt(self, name: str, body: dict):
        """Write role card content to agents/{name}/role_prompt.md and update config.json"""
        agent_dir = os.path.join(AGENTS_DIR, name)
        if not os.path.isdir(agent_dir):
            return self._send_json({"error": "Agent not found"}, 404)
        content = body.get("content", "")
        card_name = body.get("card_name", "")
        with open(os.path.join(agent_dir, "role_prompt.md"), "w", encoding="utf-8") as f:
            f.write(content)
        config_path = os.path.join(agent_dir, "config.json")
        cfg = _read_json(config_path)
        cfg.setdefault("prompt", {})["role"] = "role_prompt.md"
        cfg["prompt"]["role_card"] = card_name
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        if name in _processes:
            _processes[name].reload_config()
        return self._send_json({"ok": True})

    def _handle_delete_role_prompt(self, name: str):
        """Unassign role card: restore role.md, delete role_prompt.md"""
        agent_dir = os.path.join(AGENTS_DIR, name)
        if not os.path.isdir(agent_dir):
            return self._send_json({"error": "Agent not found"}, 404)
        config_path = os.path.join(agent_dir, "config.json")
        cfg = _read_json(config_path)
        cfg.setdefault("prompt", {})["role"] = "role.md"
        cfg["prompt"].pop("role_card", None)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        rp = os.path.join(agent_dir, "role_prompt.md")
        if os.path.isfile(rp):
            os.remove(rp)
        if name in _processes:
            _processes[name].reload_config()
        return self._send_json({"ok": True})

    def _handle_list_model_cards(self):
        try:
            from opensquad.workspace_utils import ensure_builtin_model_cards

            ensure_builtin_model_cards()
        except Exception:
            pass
        cards = []
        if not os.path.isdir(MODEL_CARDS_DIR):
            return self._send_json({"cards": cards})
        for fname in sorted(os.listdir(MODEL_CARDS_DIR)):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(MODEL_CARDS_DIR, fname)
            try:
                with open(fpath, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
            card_name = fname[:-5]
            cards.append(
                {
                    "name": card_name,
                    "title": data.get("title", card_name),
                    "api_protocol": data.get("api_protocol", ""),
                    "asr_protocol": data.get("asr_protocol", ""),
                    "provider": data.get("provider", ""),
                    "model_name": data.get("model_name", ""),
                    "base_url": data.get("base_url", ""),
                    "token_max": data.get("token_max", 0),
                    "temperature": data.get("temperature", 0),
                    "frequency_penalty": data.get("frequency_penalty", 0.0),
                    "presence_penalty": data.get("presence_penalty", 0.0),
                    "top_k": data.get("top_k", 0),
                    "is_think": data.get("is_think", False),
                    "is_image": data.get("is_image", False),
                    "is_audio": data.get("is_audio", False),
                    "is_video": data.get("is_video", False),
                    "is_audio_output": data.get("is_audio_output", False),
                    "is_image_output": data.get("is_image_output", False),
                    "audio_output_voice": data.get("audio_output_voice", "alloy"),
                    "is_builtin": bool(data.get("is_builtin", False)),
                    "builtin_service": data.get("builtin_service", ""),
                    "group_asr": bool(data.get("group_asr", False)),
                    "auto_asr": bool(data.get("auto_asr", False)),
                    "render_mode": data.get("render_mode", "strict"),  # full | strict (Default: strict)
                    "enabled": bool(data.get("enabled", True)),  # false = hidden from Agent Web switcher
                }
            )
        return self._send_json({"cards": cards})

    def _handle_get_model_card(self, card_name: str):
        fpath = os.path.join(MODEL_CARDS_DIR, f"{card_name}.json")
        if not os.path.isfile(fpath):
            return self._send_json({"error": "Card not found"}, 404)
        with open(fpath, encoding="utf-8") as f:
            data = json.load(f)
        # Ensure render_mode exists in response
        if "render_mode" not in data:
            data["render_mode"] = "strict"
        return self._send_json({"name": card_name, "card": data})

    def _handle_put_model_card(self, card_name: str, body: dict):
        os.makedirs(MODEL_CARDS_DIR, exist_ok=True)
        fpath = os.path.join(MODEL_CARDS_DIR, f"{card_name}.json")

        # Merge, never rebuild.  The Agent Web sends only the fields it knows
        # about, so a blind rebuild from a whitelist silently dropped anything
        # else the file held (e.g. ``extra_headers`` written by the Custom
        # Provider form) -- and, worse, re-applied defaults over values the
        # user had already tuned for fields the request happened to omit.
        # Precedence per known field: request > existing file > default.
        try:
            with open(fpath, encoding="utf-8") as f:
                loaded = json.load(f)
            existing: dict = loaded if isinstance(loaded, dict) else {}
        except Exception:
            existing = {}

        card: dict = {}
        for field, default in _MODEL_CARD_DEFAULTS.items():
            if field in body:
                card[field] = body[field]
            elif field in existing:
                card[field] = existing[field]
            else:
                card[field] = default
        # Unknown keys survive: the file's copy first, then whatever the request
        # carries -- so a request can also *update* a field this table does not
        # list yet (forward compatible).
        for key, value in existing.items():
            if key not in card:
                card[key] = value
        for key, value in body.items():
            if key not in _MODEL_CARD_DEFAULTS:
                card[key] = value
        card["name"] = card_name
        card["title"] = card["title"] or card_name
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(card, f, ensure_ascii=False, indent=2)
        return self._send_json({"ok": True, "name": card_name})

    def _handle_delete_model_card(self, card_name: str):
        fpath = os.path.join(MODEL_CARDS_DIR, f"{card_name}.json")
        if not os.path.isfile(fpath):
            return self._send_json({"error": "Card not found"}, 404)
        os.remove(fpath)
        return self._send_json({"ok": True, "name": card_name})

    def _handle_put_model_card_assign(self, name: str, body: dict):
        """Write model card config to the model field of agents/{name}/config.json"""
        agent_dir = os.path.join(AGENTS_DIR, name)
        if not os.path.isdir(agent_dir):
            return self._send_json({"error": "Agent not found"}, 404)
        config_path = os.path.join(agent_dir, "config.json")
        cfg = _read_json(config_path)
        cfg["model"] = {
            "api_protocol": body.get("api_protocol", "openai_compat"),
            "provider": body.get("provider", ""),
            "api_key": body.get("api_key", ""),
            "base_url": body.get("base_url", ""),
            "model_name": body.get("model_name", ""),
            "token_max": body.get("token_max", 128000),
            "tool_output_max_chars": body.get("tool_output_max_chars", 50000),
            "temperature": body.get("temperature", 0),
            "frequency_penalty": body.get("frequency_penalty", 0.0),
            "presence_penalty": body.get("presence_penalty", 0.0),
            "top_k": body.get("top_k", 0),
            "is_think": body.get("is_think", False),
            "is_image": body.get("is_image", False),
            "is_audio_model": body.get("is_audio", False),
            "is_video": body.get("is_video", False),
            "is_audio_output": body.get("is_audio_output", False),
            "is_image_output": body.get("is_image_output", False),
            "audio_output_voice": body.get("audio_output_voice", "alloy"),
            "render_mode": body.get("render_mode", "strict"),
            # Prefer explicit card_name from assign API; fall back to body.name
            "_card": (body.get("card_name") or body.get("name") or "").strip(),
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        if name in _processes:
            _processes[name].reload_config()
        return self._send_json({"ok": True})

    def _handle_delete_model_card_unassign(self, name: str):
        """Unassign model card: clear the config.json model._card field"""
        agent_dir = os.path.join(AGENTS_DIR, name)
        if not os.path.isdir(agent_dir):
            return self._send_json({"error": "Agent not found"}, 404)
        config_path = os.path.join(agent_dir, "config.json")
        cfg = _read_json(config_path)
        cfg.setdefault("model", {}).pop("_card", None)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        if name in _processes:
            _processes[name].reload_config()
        return self._send_json({"ok": True})
