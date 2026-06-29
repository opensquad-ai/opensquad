# -*- coding: utf-8 -*-
"""Unit tests for launcher.process_manager utility functions.

Tests cover: _read_json, _registry_path, check_port_conflict,
AgentProcess mock tests (get_status, get_logs), _cleanup_runtime_registry.
"""
import json
import os
import pytest


# ── _read_json ───────────────────────────────────────────────────────────

class TestReadJson:
    """Test _read_json — BOM-safe JSON file reading."""

    @staticmethod
    def _target(path, default=None):
        from opensquad.launcher.process_manager import _read_json
        return _read_json(path, default)

    def test_valid_json(self, tmp_file):
        path = tmp_file('{"key": "value"}')
        result = self._target(path)
        assert result == {"key": "value"}

    def test_bom_json(self, tmp_file):
        path = tmp_file(b'\xef\xbb\xbf{"key": "value"}'.decode('utf-8'))
        result = self._target(path)
        assert result == {"key": "value"}

    def test_file_not_found(self):
        result = self._target("/nonexistent/path.json")
        assert result == {}

    def test_custom_default(self):
        result = self._target("/nonexistent/path.json", default=[])
        assert result == []

    def test_invalid_json(self, tmp_file):
        path = tmp_file("not valid json")
        result = self._target(path)
        assert result == {}


# ── _registry_path ──────────────────────────────────────────────────────

class TestRegistryPath:
    """Test _registry_path — identifier file path generation."""

    @staticmethod
    def _target(kind, identifier):
        from opensquad.launcher.process_manager import _registry_path
        return _registry_path(kind, identifier)

    def test_basic(self):
        path = self._target("agent", "test-agent-01")
        # Should be absolute and end with the expected filename
        assert path.endswith("agent_test-agent-01.json")
        assert os.path.isabs(path)  # noqa: F821

    def test_special_chars_sanitized(self):
        path = self._target("plugin", "my/plugin:id@123")
        basename = path.split(os.sep)[-1]  # noqa: F821
        assert "/" not in basename
        assert ".json" in path

    def test_empty_identifier(self):
        path = self._target("agent", "")
        assert path.endswith("_.json")


# ── check_port_conflict ─────────────────────────────────────────────────

class TestCheckPortConflict:
    """Test check_port_conflict — port conflict detection logic."""

    @staticmethod
    def _target(config, processes=None):
        from opensquad.launcher.process_manager import check_port_conflict
        from opensquad.launcher.process_manager import _processes
        import copy
        saved = dict(_processes)
        try:
            _processes.clear()
            for k, v in (processes or {}).items():
                _processes[k] = v
            return check_port_conflict(config)
        finally:
            _processes.clear()
            _processes.update(saved)

    def test_no_port_in_config(self):
        assert self._target({}) == ""

    def test_no_port_specified(self):
        assert self._target({"web_server": {}}) == ""

    def test_non_conflicting_config(self):
        assert self._target({"web_server": {"port": 8001}}) == ""
