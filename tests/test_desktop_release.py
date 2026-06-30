"""Tests for desktop GitHub Release asset selection."""

from opensquad.utils.desktop_release import (
    normalize_desktop_platform,
    pick_desktop_installer_asset,
)


def test_normalize_desktop_platform():
    assert normalize_desktop_platform("win32") == "win32"
    assert normalize_desktop_platform("Windows") == "win32"
    assert normalize_desktop_platform("darwin") == "darwin"
    assert normalize_desktop_platform("macOS") == "darwin"
    assert normalize_desktop_platform("linux") == "linux"
    assert normalize_desktop_platform(None) is None
    assert normalize_desktop_platform("android") is None


def test_pick_windows_prefers_nsis_setup():
    assets = [
        {"name": "OpenSquad-0.4.2-portable.exe", "browser_download_url": "https://x/p", "size": 1},
        {"name": "OpenSquad Setup 0.4.2.exe", "browser_download_url": "https://x/s", "size": 2},
    ]
    picked = pick_desktop_installer_asset(assets, "win32")
    assert picked == {"name": "OpenSquad Setup 0.4.2.exe", "url": "https://x/s", "size": 2}


def test_pick_macos_prefers_dmg():
    assets = [
        {"name": "OpenSquad-0.4.2-mac.zip", "browser_download_url": "https://x/z", "size": 3},
        {"name": "OpenSquad-0.4.2.dmg", "browser_download_url": "https://x/d", "size": 4},
    ]
    picked = pick_desktop_installer_asset(assets, "darwin")
    assert picked == {"name": "OpenSquad-0.4.2.dmg", "url": "https://x/d", "size": 4}


def test_pick_linux_prefers_appimage():
    assets = [
        {"name": "opensquad_0.4.2_amd64.deb", "browser_download_url": "https://x/deb", "size": 5},
        {"name": "OpenSquad-0.4.2.AppImage", "browser_download_url": "https://x/ai", "size": 6},
    ]
    picked = pick_desktop_installer_asset(assets, "linux")
    assert picked == {"name": "OpenSquad-0.4.2.AppImage", "url": "https://x/ai", "size": 6}
