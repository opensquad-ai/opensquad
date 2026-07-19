"""
Visualization Plugin — embed interactive HTML into Agent Web (classic mode).

Agent calls visualization.create(html=...) with a self-contained HTML document
(or fragment). The tool returns a structured payload; Agent Web classic mode
detects kind=html_embed and renders it in a sandboxed iframe inside the dialog.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from opensquad.plugin_api import Context, Plugin, register, tool

# Soft cap so tool results stay manageable for WS / session persistence.
_MAX_HTML_CHARS = 450_000


def _slugify(title: str) -> str:
    base = re.sub(r"[^\w\-]+", "-", (title or "").strip().lower(), flags=re.UNICODE)
    base = re.sub(r"-{2,}", "-", base).strip("-")
    return (base or "viz")[:48]


def _normalize_html(html: str) -> str:
    text = (html or "").strip()
    if not text:
        return ""
    lower = text[:200].lower()
    if "<html" in lower or "<!doctype" in lower:
        return text
    # Fragment → minimal document so iframe srcDoc has a proper root.
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<style>html,body{margin:0;padding:0;font-family:system-ui,-apple-system,sans-serif;}</style>"
        f"</head><body>{text}</body></html>"
    )


@register(
    name="visualization",
    author="OpenSquad",
    description=(
        "Create interactive HTML visualizations for Agent Web. "
        "Pass self-contained HTML; classic Agent Web embeds it in the chat dialog."
    ),
    version="1.0.0",
    plugin_type="tool",
    display_name="Visualization",
    tags=["ui", "visualization", "html"],
)
class VisualizationPlugin(Plugin):
    def __init__(self, context: Context):
        super().__init__(context)

    @tool(
        name="visualization",
        level="extended",
        auto_register=True,
        description=(
            "Create an interactive HTML visualization for the Agent Web chat UI. "
            "Provide a complete HTML page (or fragment) in `html`. "
            "Do NOT paste the HTML into the chat reply — the host embeds it automatically. "
            "Prefer self-contained HTML (inline CSS/JS); avoid remote script sources when possible."
        ),
    )
    def create(
        self,
        html: str,
        title: str = "Visualization",
        height: int = 480,
        filename: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a visualization from HTML for Agent Web to embed.

        Args:
            html: Self-contained HTML document or fragment to render.
            title: Short display title shown above the embed.
            height: Preferred iframe height in pixels (240–1200).
            filename: Optional logical filename (e.g. travel-plan.html) for host messaging.
        """
        normalized = _normalize_html(html)
        if not normalized:
            return {
                "ok": False,
                "kind": "html_embed",
                "error": "html is empty",
            }

        truncated = False
        if len(normalized) > _MAX_HTML_CHARS:
            normalized = normalized[:_MAX_HTML_CHARS]
            truncated = True

        try:
            h = int(height)
        except (TypeError, ValueError):
            h = 480
        h = max(240, min(1200, h))

        viz_id = str(uuid.uuid4())[:8]
        name = (filename or f"{_slugify(title)}.html").strip() or f"viz-{viz_id}.html"
        if not name.lower().endswith(".html"):
            name = f"{name}.html"

        host_msg = (
            f'Interactive visualization "{name}" was created. '
            "The host will automatically embed it after this turn. "
            "Do not print an inline directive or file path."
        )
        if truncated:
            host_msg += f" (HTML truncated to {_MAX_HTML_CHARS} characters.)"

        return {
            "ok": True,
            "kind": "html_embed",
            "id": viz_id,
            "title": (title or "Visualization").strip() or "Visualization",
            "filename": name,
            "height": h,
            "html": normalized,
            "truncated": truncated,
            "text": host_msg,
            # Also expose as content list so some model UIs show a short note.
            "content": [{"type": "text", "text": host_msg}],
        }
