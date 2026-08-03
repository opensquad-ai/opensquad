"""一次性脚本：为所有插件 plugin.json 写入 category 字段"""

import json
import os

PLUGINS_DIR = os.path.join(os.path.dirname(__file__), "..", "plugins")

CATEGORIES = {
    "agent_factory": "ai",
    "api_browser": "development",
    "chat_account": "communication",
    "code_runner": "development",
    "email_assistant": "communication",
    "example_custom_icon_plugin": "demo",
    "example_nav_plugin": "demo",
    "external_api": "integration",
    "feishu": "communication",
    "git_core": "development",
    "long_memory": "ai",
    "mcp_query": "development",
    "media": "media",
    "opensquad_plugin_template": "demo",
    "plugin_admin": "productivity",
    "qq": "communication",
    "quick_note": "productivity",
    "reminder": "productivity",
    "sequential_think": "ai",
    "telegram": "communication",
    "token_analytics": "analytics",
    "translate_tool": "language",
    "vcs_remote": "development",
    "vision": "ai",
    "websearch": "search",
    "whisper": "media",
}

for name, cat in CATEGORIES.items():
    path = os.path.join(PLUGINS_DIR, name, "plugin.json")
    if not os.path.isfile(path):
        print(f"SKIP (no plugin.json): {name}")
        continue
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data["category"] = cat
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"OK  {name:35s} -> {cat}")

print("\nDone.")
