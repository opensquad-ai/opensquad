"""opensquad update — check for new versions and upgrade from GitHub Releases.

Releases ship desktop installers only (.exe / .dmg / .AppImage / .deb); there is
no wheel or sdist attached, so asset selection delegates to the same picker the
desktop app uses (`utils/desktop_release.py`). The previous local implementation
matched only .whl/.tar.gz/.zip and therefore reported "No suitable release asset
found" on every platform.
"""

import contextlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request

from opensquad.utils.desktop_release import pick_desktop_installer_asset

GITHUB_REPO = "opensquad-ai/opensquad"
GITHUB_API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"


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


def current_desktop_platform() -> tuple[str, str | None]:
    """(platform, arch) in the vocabulary `pick_desktop_installer_asset` expects."""
    return sys.platform, platform.machine()


def is_desktop_build() -> bool:
    """True when running from the packaged app (PyInstaller/Electron bundle)."""
    return bool(getattr(sys, "frozen", False))


def select_installer(release: dict, plat: str, arch: str | None) -> dict | None:
    """Pick this platform's installer asset: {name, url, size}."""
    return pick_desktop_installer_asset(release.get("assets") or [], plat, arch)


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
        with urllib.request.urlopen(req, timeout=300) as resp, open(dest, "wb") as f:
            shutil.copyfileobj(resp, f)
        return True
    except Exception as e:
        print(f"Download failed: {e}")
        return False


def run_installer(installer_path: str, plat: str) -> None:
    """Hand the downloaded installer to the OS. Raises if this platform is unsupported.

    On Windows the NSIS installer kills the running app and its backend children
    (see assets/installer.nsh) and relaunches itself when installed silently, so
    the caller must not try to restart anything afterwards.
    """
    if not os.path.isfile(installer_path):
        raise FileNotFoundError(installer_path)

    if plat == "win32":
        subprocess.Popen([installer_path, "/S"], creationflags=_win_detached_flags())
        return

    if plat == "darwin":
        subprocess.Popen(["open", installer_path])
        return

    if plat == "linux":
        low = installer_path.lower()
        if low.endswith(".appimage"):
            os.chmod(installer_path, 0o755)
            subprocess.Popen([installer_path])
            return
        if low.endswith(".deb"):
            subprocess.Popen(["xdg-open", installer_path])
            return

    raise RuntimeError(f"Automatic install is not supported for {plat}")


def _win_detached_flags() -> int:
    # Detach so the installer survives this process being killed by the install.
    return getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def print_python_install_guidance(picked: dict, latest: str) -> None:
    """A pip/uv install has no installer to run in place of it — name the two real paths."""
    print("This is a source / pip install of OpenSquad, not the packaged desktop app,")
    print("so there is no installer to run in place of it. Upgrade it either way:")
    print()
    print("  pip install --upgrade opensquad")
    print(f"  git fetch --tags && git checkout v{latest} && python -m pip install -e .")
    print()
    print(f"Or install the desktop app for your platform ({picked['name']}):")
    print(f"  {picked['url']}")


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

    plat, arch = current_desktop_platform()
    picked = select_installer(release, plat, arch)
    if not picked:
        print(f"Release v{latest} has no installer asset for platform '{plat}'.")
        print(f"Browse the releases page instead: {RELEASES_PAGE}")
        sys.exit(1)

    size_mb = picked.get("size", 0) / (1024 * 1024)
    print(f"Selected {picked['name']} ({size_mb:.1f} MB)")

    if not is_desktop_build():
        print_python_install_guidance(picked, latest)
        sys.exit(1)

    with tempfile.NamedTemporaryFile(suffix="-" + picked["name"], delete=False) as tf:
        tmp_path = tf.name

    if not _download_asset(picked["url"], tmp_path):
        with contextlib.suppress(Exception):
            os.unlink(tmp_path)
        sys.exit(1)

    print("Launching installer...")
    try:
        run_installer(tmp_path, plat)
    except Exception as e:
        # Keep the file: the user can finish the upgrade by hand.
        print(f"Could not start the installer: {e}")
        print(f"It was downloaded to: {tmp_path}")
        print("Run it manually to finish the upgrade.")
        sys.exit(1)

    # The installer owns the temp file from here (and on Windows kills this
    # process while copying), so nothing is cleaned up afterwards.
    if plat == "win32":
        print("The installer runs silently and will relaunch OpenSquad when it finishes.")
    else:
        print("Follow the installer window to finish the upgrade.")
