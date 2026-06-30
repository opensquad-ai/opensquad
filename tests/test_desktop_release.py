"""Tests for desktop GitHub Release asset selection."""

from opensquad.utils.desktop_release import (
    normalize_desktop_arch,
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


def test_normalize_desktop_arch():
    assert normalize_desktop_arch("x64") == "x64"
    assert normalize_desktop_arch("arm64") == "arm64"
    assert normalize_desktop_arch("AMD64") == "x64"
    assert normalize_desktop_arch(None) is None


def test_pick_windows_prefers_nsis_setup():
    assets = [
        {"name": "OpenSquad-0.4.2-win-x64-Portable.exe", "browser_download_url": "https://x/p", "size": 1},
        {"name": "OpenSquad-0.4.2-win-x64-Setup.exe", "browser_download_url": "https://x/s", "size": 2},
    ]
    picked = pick_desktop_installer_asset(assets, "win32")
    assert picked == {"name": "OpenSquad-0.4.2-win-x64-Setup.exe", "url": "https://x/s", "size": 2}


def test_pick_macos_prefers_matching_arch_dmg():
    assets = [
        {"name": "OpenSquad-0.4.2-mac-x64.dmg", "browser_download_url": "https://x/x64", "size": 3},
        {"name": "OpenSquad-0.4.2-mac-arm64.dmg", "browser_download_url": "https://x/arm", "size": 4},
    ]
    picked = pick_desktop_installer_asset(assets, "darwin", arch="arm64")
    assert picked == {"name": "OpenSquad-0.4.2-mac-arm64.dmg", "url": "https://x/arm", "size": 4}


def test_pick_linux_prefers_appimage():
    assets = [
        {"name": "OpenSquad-0.4.2-linux-x64.deb", "browser_download_url": "https://x/deb", "size": 5},
        {"name": "OpenSquad-0.4.2-linux-x64.AppImage", "browser_download_url": "https://x/ai", "size": 6},
    ]
    picked = pick_desktop_installer_asset(assets, "linux")
    assert picked == {"name": "OpenSquad-0.4.2-linux-x64.AppImage", "url": "https://x/ai", "size": 6}


def test_pick_legacy_windows_names_still_work():
    assets = [
        {"name": "OpenSquad Setup 0.4.2.exe", "browser_download_url": "https://x/s", "size": 2},
    ]
    picked = pick_desktop_installer_asset(assets, "win32")
    assert picked == {"name": "OpenSquad Setup 0.4.2.exe", "url": "https://x/s", "size": 2}
