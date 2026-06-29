# -*- coding: utf-8 -*-
"""opensquad update — check for new versions and upgrade from GitHub Releases."""
import subprocess
import sys
import urllib.request
import json
import tempfile
import os
import shutil


GITHUB_REPO = "opensquad-ai/opensquad"
GITHUB_API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def _get_latest_github_release() -> dict | None:
    """Fetch the latest GitHub release info (tag, assets, etc.)."""
    try:
        req = urllib.request.Request(
            GITHUB_API_LATEST,
            headers={
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "OpenSquad",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"Failed to fetch GitHub release: {e}")
        return None


def _get_latest_github_version(release: dict) -> str | None:
    """Extract version from release tag (e.g. 'v1.2.3' → '1.2.3')."""
    tag = release.get("tag_name", "")
    return tag.lstrip("v") if tag else None


def _compare_versions(current: str, latest: str) -> bool:
    """Return True if latest > current."""
    try:
        from packaging.version import Version
        return Version(latest) > Version(current)
    except Exception:
        def _parts(v: str):
            return tuple(int(x) for x in v.split(".") if x.isdigit())
        return _parts(latest) > _parts(current)


def _download_asset(url: str, dest: str) -> bool:
    """Download a GitHub release asset to dest. Returns True on success."""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/octet-stream",
                "User-Agent": "OpenSquad",
            },
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            with open(dest, "wb") as f:
                shutil.copyfileobj(resp, f)
        return True
    except Exception as e:
        print(f"Download failed: {e}")
        return False


def _pick_asset(assets: list) -> dict | None:
    """Pick the best asset for the current platform from the release assets list.
    
    Priority: wheel matching current platform > source tarball > any wheel.
    """
    if not assets:
        return None

    # Identify current platform tag
    is_win = sys.platform == "win32"
    is_mac = sys.platform == "darwin"
    is_linux = sys.platform.startswith("linux")

    wheel = None
    sdist = None

    for a in assets:
        name = a.get("name", "")
        url = a.get("browser_download_url", "")
        if not url:
            continue

        if name.endswith(".whl"):
            # Prefer platform-specific wheel
            if is_win and "win" in name.lower():
                return a  # Best match
            if is_mac and ("macosx" in name.lower() or "macos" in name.lower()):
                return a
            if is_linux and "linux" in name.lower():
                return a
            # Pure Python wheel (no platform tag)
            if "py3-none-any" in name:
                return a
            if wheel is None:
                wheel = a
        elif name.endswith(".tar.gz") or name.endswith(".zip"):
            if sdist is None:
                sdist = a

    return wheel or sdist


def run_update(args):
    from opensquad import __version__
    current = __version__

    print(f"Current version: v{current}")
    print("Checking GitHub for latest release...")

    release = _get_latest_github_release()
    if not release:
        print("Failed to fetch release info. Check your network.")
        sys.exit(1)

    latest = _get_latest_github_version(release)
    if not latest:
        print("Could not determine latest version from release tag.")
        sys.exit(1)

    print(f"Latest version:  v{latest}  ({release.get('name', release.get('tag_name', ''))})")

    if not _compare_versions(current, latest):
        print("You are already running the latest version.")
        return

    answer = input(f"Upgrade from v{current} to v{latest}? [y/N] ").strip().lower()
    if answer not in ("y", "yes"):
        print("Upgrade cancelled.")
        return

    # Pick suitable asset
    assets = release.get("assets", [])
    asset = _pick_asset(assets)
    if not asset:
        print("No suitable release asset found. Try upgrading manually:")
        print(f"  pip install --upgrade opensquad")
        sys.exit(1)

    asset_name = asset.get("name", "opensquad")
    asset_url = asset.get("browser_download_url", "")
    asset_size = asset.get("size", 0)
    size_mb = asset_size / (1024 * 1024) if asset_size else 0

    print(f"Downloading {asset_name} ({size_mb:.1f} MB)...")

    with tempfile.NamedTemporaryFile(suffix="-" + asset_name, delete=False) as tf:
        tmp_path = tf.name

    try:
        if not _download_asset(asset_url, tmp_path):
            sys.exit(1)

        print("Installing...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--upgrade", tmp_path]
        )
        print("Upgrade complete! Please restart OpenSquad.")
    except subprocess.CalledProcessError as e:
        print(f"Upgrade failed: {e}")
        sys.exit(1)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
