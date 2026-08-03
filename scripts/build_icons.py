"""
Generate OpenSquad desktop-app icons from ``assets/logo-source.svg``.

Outputs (overwritten in place):

  src/opensquad/gateway/nexuschat-pro/assets/icon.png    1024x1024 master
  src/opensquad/gateway/nexuschat-pro/assets/icon@2x.png 512x512  (Linux HiDPI)
  src/opensquad/gateway/nexuschat-pro/assets/icon.ico    multi-size (Windows)
  src/opensquad/gateway/nexuschat-pro/assets/icon.icns   multi-size (macOS)
  src/opensquad/gateway/nexuschat-pro/assets/tray.png    32x32    (system tray)

These match the paths referenced by ``package.json`` -> ``build.win.icon``,
``build.mac.icon``, ``build.linux.icon``.

Run:

    python scripts/build_icons.py

Dependencies: Playwright (with chromium installed), Pillow.

The first run requires ``playwright install chromium`` so headless chromium
can rasterize the SVG.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Iterable

from PIL import Image

try:
    from playwright.async_api import async_playwright
except ImportError:  # pragma: no cover - import-time guidance
    sys.exit("Playwright is required: pip install playwright && playwright install chromium")


# ── Paths ──────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = REPO_ROOT / "src" / "opensquad" / "gateway" / "nexuschat-pro" / "assets"
SOURCE_SVG = ASSETS_DIR / "logo-source.svg"

# Icon sizes.
MASTER_SIZE = 1024  # icon.png (Linux master + source for ICO/ICNS)
HIDPI_SIZE = 512  # icon@2x.png
TRAY_SIZE = 32  # tray.png

# Windows ICO multi-size list (Favicon-friendly set).
ICO_SIZES: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)

# macOS ICNS multi-size list.
ICNS_SIZES: tuple[int, ...] = (16, 32, 64, 128, 256, 512, 1024)


# ── Step 1: rasterize the source SVG into a master PNG via headless Chromium


async def _render_master_png(svg_path: Path, out_path: Path, size: int) -> None:
    """Load the SVG inside a transparent HTML wrapper and screenshot it."""
    html = f"""<!doctype html>
<html><head><meta charset='utf-8'>
<style>
  html, body {{ margin: 0; padding: 0; background: transparent; }}
  body {{ width: {size}px; height: {size}px; }}
  svg {{ display: block; width: {size}px; height: {size}px; }}
</style></head>
<body>
  {svg_path.read_text(encoding="utf-8")}
</body></html>
"""
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page(
                viewport={"width": size, "height": size},
                device_scale_factor=1,
            )
            await page.set_content(html, wait_until="load")
            # Wait for fonts/SVG to settle.
            await page.wait_for_timeout(150)
            await page.screenshot(
                path=str(out_path),
                omit_background=True,
                clip={"x": 0, "y": 0, "width": size, "height": size},
                type="png",
            )
        finally:
            await browser.close()


def render_master(svg_path: Path, size: int) -> Image.Image:
    """Render ``svg_path`` at ``size`` x ``size`` and return an RGBA Pillow image."""
    tmp = svg_path.with_suffix(f".master-{size}.png")
    asyncio.run(_render_master_png(svg_path, tmp, size))
    img = Image.open(tmp).convert("RGBA")
    tmp.unlink(missing_ok=True)
    return img


# ── Step 2: derive all icon variants from the master PNG


def _save_resized(img: Image.Image, size: int, out: Path) -> Image.Image:
    # Lanczos gives the cleanest down-scale for logos.
    resized = img.resize((size, size), Image.Resampling.LANCZOS)
    resized.save(out, format="PNG", optimize=True)
    return resized


def write_ico(img: Image.Image, sizes: Iterable[int], out: Path) -> None:
    base = sizes[-1]
    base_img = img.resize((base, base), Image.Resampling.LANCZOS)
    frames = [img.resize((s, s), Image.Resampling.LANCZOS) for s in sizes]
    base_img.save(
        out,
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=frames,
    )


def write_icns(img: Image.Image, sizes: Iterable[int], out: Path) -> None:
    frames = [img.resize((s, s), Image.Resampling.LANCZOS) for s in sizes]
    frames[0].save(out, format="ICNS", append_images=frames[1:])


# ── Orchestration


def main() -> int:
    if not SOURCE_SVG.exists():
        print(f"[build_icons] Source SVG not found: {SOURCE_SVG}", file=sys.stderr)
        return 1

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[build_icons] Source: {SOURCE_SVG}")

    print(f"[build_icons] Rendering master {MASTER_SIZE}x{MASTER_SIZE} PNG…")
    master = render_master(SOURCE_SVG, MASTER_SIZE)

    # Linux master
    master.save(ASSETS_DIR / "icon.png", format="PNG", optimize=True)
    print(f"[build_icons]   wrote icon.png ({MASTER_SIZE}x{MASTER_SIZE})")

    # Linux HiDPI variant
    _save_resized(master, HIDPI_SIZE, ASSETS_DIR / "icon@2x.png")
    print(f"[build_icons]   wrote icon@2x.png ({HIDPI_SIZE}x{HIDPI_SIZE})")

    # Windows ICO
    write_ico(master, ICO_SIZES, ASSETS_DIR / "icon.ico")
    print(f"[build_icons]   wrote icon.ico (sizes={ICO_SIZES})")

    # macOS ICNS
    write_icns(master, ICNS_SIZES, ASSETS_DIR / "icon.icns")
    print(f"[build_icons]   wrote icon.icns (sizes={ICNS_SIZES})")

    # System tray
    _save_resized(master, TRAY_SIZE, ASSETS_DIR / "tray.png")
    print(f"[build_icons]   wrote tray.png ({TRAY_SIZE}x{TRAY_SIZE})")

    print("[build_icons] Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
