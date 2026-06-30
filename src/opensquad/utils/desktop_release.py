"""Pick platform-specific desktop installer assets from GitHub Release metadata."""

from __future__ import annotations

from typing import Any


def normalize_desktop_platform(platform: str | None) -> str | None:
    """Map client platform strings to win32 / darwin / linux."""
    if not platform:
        return None
    key = platform.strip().lower()
    if key in {"win32", "windows", "win"}:
        return "win32"
    if key in {"darwin", "mac", "macos", "osx"}:
        return "darwin"
    if key in {"linux"}:
        return "linux"
    return None


def normalize_desktop_arch(arch: str | None) -> str | None:
    """Normalize client CPU arch strings (``x64``, ``arm64``, …)."""
    if not arch:
        return None
    key = arch.strip().lower()
    if key in {"x64", "amd64", "x86_64"}:
        return "x64"
    if key in {"arm64", "aarch64"}:
        return "arm64"
    return key or None


def pick_desktop_installer_asset(
    assets: list[dict[str, Any]] | None,
    platform: str,
    arch: str | None = None,
) -> dict[str, Any] | None:
    """Return ``{name, url, size}`` for the best installer on *platform*."""
    if not assets:
        return None

    entries: list[tuple[str, str, int]] = []
    for asset in assets:
        name = asset.get("name")
        url = asset.get("browser_download_url")
        if not isinstance(name, str) or not isinstance(url, str):
            continue
        size = asset.get("size")
        entries.append((name, url, int(size) if isinstance(size, int) else 0))

    normalized_arch = normalize_desktop_arch(arch)
    entries = _prefer_platform_marked(entries, platform, normalized_arch)

    if platform == "win32":
        exe = [(n, u, s) for n, u, s in entries if n.lower().endswith(".exe")]
        setup = [item for item in exe if "setup" in item[0].lower() and "portable" not in item[0].lower()]
        if setup:
            return _asset_dict(*setup[0])
        non_portable = [item for item in exe if "portable" not in item[0].lower()]
        if non_portable:
            return _asset_dict(*non_portable[0])
        if exe:
            return _asset_dict(*exe[0])
        return None

    if platform == "darwin":
        dmg = [item for item in entries if item[0].lower().endswith(".dmg")]
        if dmg:
            return _asset_dict(*dmg[0])
        zip_files = [item for item in entries if item[0].lower().endswith(".zip")]
        if zip_files:
            return _asset_dict(*zip_files[0])
        return None

    if platform == "linux":
        appimage = [item for item in entries if item[0].lower().endswith(".appimage")]
        if appimage:
            return _asset_dict(*appimage[0])
        deb = [item for item in entries if item[0].lower().endswith(".deb")]
        if deb:
            return _asset_dict(*deb[0])
        return None

    return None


def _prefer_platform_marked(
    entries: list[tuple[str, str, int]],
    platform: str,
    arch: str | None,
) -> list[tuple[str, str, int]]:
    """Prefer new-style filenames that include ``-win-`` / ``-mac-`` / ``-linux-``."""
    marker = {"win32": "-win-", "darwin": "-mac-", "linux": "-linux-"}.get(platform)
    if not marker:
        return entries

    marked = [item for item in entries if marker in item[0].lower()]
    pool = marked if marked else entries

    if arch:
        arch_tag = f"-{arch.lower()}"
        arch_matches = [item for item in pool if arch_tag in item[0].lower()]
        if arch_matches:
            return arch_matches

    return pool


def _asset_dict(name: str, url: str, size: int) -> dict[str, Any]:
    return {"name": name, "url": url, "size": size}
