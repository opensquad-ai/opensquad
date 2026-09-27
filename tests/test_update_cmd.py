from __future__ import annotations

"""`opensquad update` must find the release's installer assets.

Reported 2026-09-27 by a user on v0.8.10:

    Latest version:  v0.8.46  (v0.8.46)
    Upgrade from v0.8.10 to v0.8.46? [y/N] y
    No suitable release asset found. Try upgrading manually:
      pip install --upgrade opensquad

Two separate faults behind that output:

1. `update_cmd` had its own asset matcher that only understood .whl / .tar.gz /
   .zip. Releases attach desktop installers only, so *every* platform returned
   None — while `utils/desktop_release.py` already had a correct picker that the
   desktop app used.
2. The suggested remedy was actively wrong *at the time*: PyPI `opensquad` was
   then a 0.1.1 placeholder (the Release workflow's "Publish to PyPI" job had
   never succeeded), so following the advice downgraded the user. That job went
   green with v0.8.47 (2026-09-27), so the guidance points at PyPI again.
"""

import builtins
from pathlib import Path

import pytest

from opensquad.cli.commands import update_cmd

SRC = Path(update_cmd.__file__).read_text(encoding="utf-8")

# Verbatim asset names from the real v0.8.46 release (5 assets, no wheel/sdist).
ASSETS_0_8_46 = [
    "OpenSquad-0.8.46-linux-amd64.deb",
    "OpenSquad-0.8.46-linux-x86_64.AppImage",
    "OpenSquad-0.8.46-mac-arm64.dmg",
    "OpenSquad-0.8.46-mac-x64.dmg",
    "OpenSquad-0.8.46-win-x64-Setup.exe",
]


def _release(tag: str = "v0.8.46") -> dict:
    return {
        "tag_name": tag,
        "name": tag,
        "assets": [{"name": n, "browser_download_url": f"https://dl.example/{n}", "size": 1024} for n in ASSETS_0_8_46],
    }


def test_release_with_only_installers_yields_a_platform_asset():
    """The bug: a release with no wheel used to match nothing at all."""
    cases = {
        ("win32", "AMD64"): "OpenSquad-0.8.46-win-x64-Setup.exe",
        ("darwin", "arm64"): "OpenSquad-0.8.46-mac-arm64.dmg",
        ("darwin", "x86_64"): "OpenSquad-0.8.46-mac-x64.dmg",
        ("linux", "x86_64"): "OpenSquad-0.8.46-linux-x86_64.AppImage",
    }
    for (plat, arch), expected in cases.items():
        picked = update_cmd.select_installer(_release(), plat, arch)
        assert picked, f"{plat}/{arch} got no asset — the original bug is back"
        assert picked["name"] == expected


def test_update_cmd_delegates_asset_choice_to_shared_picker():
    """One picker, not a second stale implementation inside the CLI."""
    assert "from opensquad.utils.desktop_release import pick_desktop_installer_asset" in SRC
    assert '"No suitable release asset found"' not in SRC
    assert "_pick_asset" not in SRC, "the stale local matcher is back"


def test_source_install_is_told_the_truth(monkeypatch, capsys):
    """A pip/uv install cannot self-upgrade in place — send it to PyPI, not a dead end."""
    import opensquad

    monkeypatch.setattr(opensquad, "__version__", "0.8.10")
    monkeypatch.setattr(update_cmd, "_get_latest_github_release", _release)
    monkeypatch.setattr(update_cmd, "current_desktop_platform", lambda: ("win32", "AMD64"))
    monkeypatch.setattr(update_cmd, "is_desktop_build", lambda: False)
    monkeypatch.setattr(builtins, "input", lambda *a: "y")

    with pytest.raises(SystemExit) as exc:
        update_cmd.run_update(object())

    out = capsys.readouterr().out
    assert exc.value.code == 1
    assert "OpenSquad-0.8.46-win-x64-Setup.exe" in out
    assert "https://dl.example/" in out
    assert "pip install --upgrade opensquad" in out
    assert "0.1.1 placeholder" not in out, "the stale 'PyPI is a placeholder' warning is back"
    assert "Try upgrading manually" not in out


def test_desktop_build_downloads_and_launches_installer(monkeypatch, capsys):
    import opensquad

    monkeypatch.setattr(opensquad, "__version__", "0.8.10")
    launched: list[tuple[str, str]] = []
    monkeypatch.setattr(update_cmd, "_get_latest_github_release", _release)
    monkeypatch.setattr(update_cmd, "current_desktop_platform", lambda: ("win32", "AMD64"))
    monkeypatch.setattr(update_cmd, "is_desktop_build", lambda: True)
    monkeypatch.setattr(update_cmd, "_download_asset", lambda url, dest: True)
    monkeypatch.setattr(update_cmd, "run_installer", lambda path, plat: launched.append((path, plat)))
    monkeypatch.setattr(builtins, "input", lambda *a: "yes")

    update_cmd.run_update(object())

    assert len(launched) == 1 and launched[0][1] == "win32"
    assert launched[0][0].endswith("-OpenSquad-0.8.46-win-x64-Setup.exe")
    assert "will relaunch OpenSquad" in capsys.readouterr().out


def test_already_latest_does_not_prompt(monkeypatch, capsys):
    import opensquad

    monkeypatch.setattr(opensquad, "__version__", "0.8.46")
    monkeypatch.setattr(update_cmd, "_get_latest_github_release", lambda: _release("v0.8.46"))

    def _boom(*a):
        raise AssertionError("must not prompt when up to date")

    monkeypatch.setattr(builtins, "input", _boom)

    update_cmd.run_update(object())
    assert "already running the latest" in capsys.readouterr().out


def test_run_installer_rejects_unknown_platform(tmp_path):
    """Silently doing nothing is what stranded users with a dead app."""
    fake = tmp_path / "OpenSquad-0.8.46-solaris-mumble.bin"
    fake.write_bytes(b"x")
    with pytest.raises(RuntimeError):
        update_cmd.run_installer(str(fake), "sunos")
