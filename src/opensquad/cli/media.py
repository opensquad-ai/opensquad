"""CLI media attach: send images without rendering them (Claude-Code-like)."""

from __future__ import annotations

import os
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opensquad.cli.api_client import GatewayClient

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}


@dataclass
class PendingMedia:
    """Queued attachment shown as text chip, never rendered as pixels."""

    local_path: str
    label: str
    kind: str = "image"  # image | file
    uploaded_url: str | None = None
    uploaded_path: str | None = None  # agent-side path for WS images[]
    size: str = ""


def is_image_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTS


def chip_label(media: PendingMedia) -> str:
    return f"[{media.kind}: {media.label}]"


def grab_clipboard_image(dest_dir: str | None = None) -> Path | None:
    """
    Save clipboard bitmap to a temp PNG if present.
    Windows: PowerShell System.Windows.Forms
    Else: try Pillow ImageGrab when available.
    Returns path or None.
    """
    dest_dir = dest_dir or tempfile.gettempdir()
    out = Path(dest_dir) / f"opensquad-clip-{uuid.uuid4().hex[:8]}.png"

    if os.name == "nt":
        ps = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$img = [System.Windows.Forms.Clipboard]::GetImage()
if ($null -eq $img) {{ exit 2 }}
$img.Save('{str(out).replace("'", "''")}', [System.Drawing.Imaging.ImageFormat]::Png)
exit 0
"""
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True,
                timeout=15,
            )
            if r.returncode == 0 and out.is_file() and out.stat().st_size > 0:
                return out
        except Exception:
            pass

    try:
        from PIL import ImageGrab  # type: ignore

        img = ImageGrab.grabclipboard()
        if img is not None and hasattr(img, "save"):
            img.save(str(out), format="PNG")
            if out.is_file() and out.stat().st_size > 0:
                return out
    except Exception:
        pass
    return None


def attach_from_path(path: str) -> PendingMedia:
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"File not found: {path}")
    kind = "image" if is_image_path(p) else "file"
    size = _human_size(p.stat().st_size)
    return PendingMedia(local_path=str(p), label=p.name, kind=kind, size=size)


def attach_from_clipboard() -> PendingMedia | None:
    path = grab_clipboard_image()
    if not path:
        return None
    return PendingMedia(
        local_path=str(path),
        label=path.name,
        kind="image",
        size=_human_size(path.stat().st_size),
    )


def upload_for_agent(client: GatewayClient, agent_id: str, media: PendingMedia) -> PendingMedia:
    """Upload to /api/ai-web/agent-sessions/{id}/upload-image|upload-file."""
    client.require_auth()
    path = media.local_path
    name = Path(path).name
    endpoint = (
        f"/api/ai-web/agent-sessions/{agent_id}/upload-image"
        if media.kind == "image"
        else f"/api/ai-web/agent-sessions/{agent_id}/upload-file"
    )
    with open(path, "rb") as fh:
        result = client.request(
            "POST",
            endpoint,
            files={"file": (name, fh, _mime(name))},
            timeout=120.0,
        )
    if isinstance(result, dict):
        media.uploaded_url = result.get("url")
        media.uploaded_path = result.get("path") or result.get("url")
        if result.get("size") and not media.size:
            media.size = str(result.get("size"))
    return media


def upload_for_group(client: GatewayClient, media: PendingMedia) -> dict[str, Any]:
    """Upload via POST /api/upload — returns attachment dict for MessageCreate."""
    client.require_auth()
    path = media.local_path
    name = Path(path).name
    with open(path, "rb") as fh:
        result = client.request(
            "POST",
            "/api/upload",
            files={"file": (name, fh, _mime(name))},
            timeout=120.0,
        )
    if not isinstance(result, dict):
        raise RuntimeError(f"Upload failed: {result}")
    media.uploaded_url = result.get("url")
    return {
        "name": result.get("name") or name,
        "size": result.get("size") or media.size or "0",
        "url": result.get("url") or "",
        "type": result.get("type") or media.kind,
    }


def _human_size(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n / (1024 * 1024):.1f}MB"


def _mime(name: str) -> str:
    ext = Path(name).suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".pdf": "application/pdf",
    }.get(ext, "application/octet-stream")


def format_pending_chips(items: list[PendingMedia]) -> str:
    if not items:
        return ""
    return " ".join(chip_label(m) for m in items)
