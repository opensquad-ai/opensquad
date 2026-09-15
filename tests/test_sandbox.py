"""Tests for opensquad.security.sandbox (M0 weak sandbox)."""

from __future__ import annotations

from opensquad.security import sandbox


class TestBuiltinBlocklist:
    def test_blocks_disk_format(self):
        assert sandbox.check_shell_command("format C: /q") is not None

    def test_blocks_shutdown(self):
        assert sandbox.check_shell_command("shutdown /s /t 0") is not None
        assert sandbox.check_shell_command("Restart-Computer -Force") is not None

    def test_blocks_registry_edit(self):
        assert sandbox.check_shell_command('reg add "HKLM\\SOFTWARE\\x" /v y /d 1') is not None

    def test_blocks_root_recursive_delete(self):
        assert sandbox.check_shell_command("rm -rf /") is not None

    def test_blocks_fork_bomb(self):
        assert sandbox.check_shell_command(":(){ :|:& };:") is not None

    def test_blocks_firewall_off(self):
        assert sandbox.check_shell_command("netsh advfirewall set allprofiles state off") is not None

    def test_allows_normal_commands(self):
        assert sandbox.check_shell_command("npm run build") is None
        assert sandbox.check_shell_command("git status") is None
        assert sandbox.check_shell_command("python -m pytest tests/ -q") is None
        assert sandbox.check_shell_command("del old_report.txt") is None  # project file, allowed

    def test_empty_command_passes(self):
        assert sandbox.check_shell_command("") is None
        assert sandbox.check_shell_command(None) is None


class TestConfigSurface:
    def test_disabled_config_bypasses(self, monkeypatch):
        monkeypatch.setattr(sandbox, "_load_config", lambda: {"enabled": False})
        assert sandbox.check_shell_command("format C: /q") is None

    def test_user_patterns_appended(self, monkeypatch):
        monkeypatch.setattr(
            sandbox,
            "_load_config",
            lambda: {"enabled": True, "blocked_patterns": [r"\bnpm\s+publish\b"]},
        )
        assert sandbox.check_shell_command("npm publish") is not None
        assert sandbox.check_shell_command("npm install") is None

    def test_invalid_user_pattern_ignored(self, monkeypatch):
        monkeypatch.setattr(
            sandbox,
            "_load_config",
            lambda: {"enabled": True, "blocked_patterns": ["([invalid"]},
        )
        assert sandbox.check_shell_command("npm install") is None


class TestToolIntegration:
    def test_start_job_blocks_dangerous_command(self):
        from opensquad.tools import system

        result = system.start_job(command="shutdown /s /t 0", description="x")
        assert result["status"] == "error"
        assert "sandbox" in result["message"].lower() or "Blocked" in result["message"]
