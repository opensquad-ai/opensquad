"""Regression tests for plugin dependency probing.

The launcher decides whether to start a plugin service by importing each
declared pip dependency in the *plugin* interpreter. Three defects made that
decision wrong and left healthy services stuck in ``error`` with
"dependencies not installed":

1. ``pkg_import_map.json`` had no ``python-telegram-bot`` entry, so the probe
   imported ``python_telegram_bot`` — a module that has never existed. The
   package was reinstalled on every startup and reported missing every time.
2. The probe budget was 15s. Cold imports of real deps are not fast
   (``lark_oapi`` measured ~10s), so under load a probe timed out and the
   timeout was read as "missing".
3. Plugin children got the launcher's PYTHONPATH, which belongs to the
   launcher's own interpreter.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest


def _pm():
    from opensquad.launcher import process_manager as pm

    return pm


@pytest.fixture(autouse=True)
def _isolate_caches(monkeypatch):
    """Keep the module-level probe caches from leaking between tests."""
    pm = _pm()
    monkeypatch.setattr(pm, "_plugin_module_present", set())
    monkeypatch.setattr(pm, "_plugin_dist_module_map_cache", {})


# ── distribution name → import name ─────────────────────────────────────


class TestImportNameResolution:
    @staticmethod
    def _candidates(dep, monkeypatch, metadata=None):
        pm = _pm()
        monkeypatch.setattr(pm, "_plugin_dist_module_map", lambda _py: metadata or {})
        return pm._resolve_import_candidates(dep)

    def test_python_telegram_bot_maps_to_telegram(self, monkeypatch):
        """The regression: the dist name is not the import name."""
        assert self._candidates("python-telegram-bot", monkeypatch)[0] == "telegram"

    def test_version_pin_is_stripped(self, monkeypatch):
        assert self._candidates("python-telegram-bot>=21.0", monkeypatch)[0] == "telegram"

    def test_existing_map_entries_kept(self, monkeypatch):
        assert self._candidates("beautifulsoup4", monkeypatch)[0] == "bs4"
        assert self._candidates("PyMuPDF", monkeypatch)[0] == "fitz"

    def test_metadata_beats_underscore_guess(self, monkeypatch):
        """A dist that is installed tells us its real import name."""
        got = self._candidates("pillow", monkeypatch, metadata={"pillow": ["PIL"]})
        assert got[0] == "PIL"

    def test_metadata_public_names_first(self, monkeypatch):
        """pyyaml announces both ``_yaml`` and ``yaml``."""
        got = self._candidates("pyyaml", monkeypatch, metadata={"pyyaml": ["_yaml", "yaml"]})
        assert got[0] == "yaml"
        assert "_yaml" in got

    def test_underscore_guess_is_last_resort(self, monkeypatch):
        got = self._candidates("some-new-pkg", monkeypatch)
        assert got == ["some_new_pkg"]

    def test_candidates_are_deduplicated(self, monkeypatch):
        got = self._candidates("lark-oapi", monkeypatch, metadata={"lark-oapi": ["lark_oapi"]})
        assert got == ["lark_oapi"]

    def test_every_builtin_declared_dep_resolves(self):
        """No plugin.json dependency may resolve to an empty candidate list."""
        import json

        plugins_dir = os.path.normpath(
            os.path.join(os.path.dirname(os.path.abspath(_pm().__file__)), "..", "..", "plugins")
        )
        checked = 0
        for name in sorted(os.listdir(plugins_dir)):
            manifest = os.path.join(plugins_dir, name, "plugin.json")
            if not os.path.isfile(manifest):
                continue
            with open(manifest, encoding="utf-8") as f:
                deps = json.load(f).get("dependencies", {}).get("pip", []) or []
            for dep in deps:
                assert _pm()._resolve_import_candidates(dep), f"{name}: {dep!r} resolved to nothing"
                checked += 1
        assert checked > 20, "plugin manifests were not read as expected"


# ── probe outcome classification ────────────────────────────────────────


def _fake_probe(monkeypatch, mapping):
    """Make ``_plugin_python_dist_status`` return canned statuses."""
    pm = _pm()
    calls = []

    def fake(dep):
        calls.append(dep)
        return mapping.get(dep, "missing")

    monkeypatch.setattr(pm, "_plugin_python_dist_status", fake)
    return calls


class TestProbeClassification:
    def test_timeout_is_unknown_not_missing(self, monkeypatch):
        pm = _pm()
        monkeypatch.setattr(pm, "_plugin_python_executable", lambda: sys.executable)

        def boom(*_a, **_kw):
            raise subprocess.TimeoutExpired(cmd="import x", timeout=60)

        monkeypatch.setattr(pm.subprocess, "run", boom)
        assert pm._plugin_python_import_status("lark_oapi") == "unknown"
        assert pm._plugin_python_dist_status("lark-oapi") == "unknown"
        assert pm._plugin_python_has_module("lark_oapi") is False

    def test_nonzero_exit_is_missing(self, monkeypatch):
        pm = _pm()
        monkeypatch.setattr(pm, "_plugin_python_executable", lambda: sys.executable)
        monkeypatch.setattr(
            pm.subprocess,
            "run",
            lambda *a, **kw: subprocess.CompletedProcess(a, 1, b"", b"ModuleNotFoundError"),
        )
        assert pm._plugin_python_import_status("nope") == "missing"

    def test_success_is_present_and_cached(self, monkeypatch):
        pm = _pm()
        monkeypatch.setattr(pm, "_plugin_python_executable", lambda: sys.executable)
        runs = []

        def fake_run(cmd, *a, **kw):
            runs.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        monkeypatch.setattr(pm.subprocess, "run", fake_run)
        assert pm._plugin_python_import_status("requests") == "present"
        assert pm._plugin_python_import_status("requests") == "present"
        assert len(runs) == 1, "a verified module must not be re-probed"

    def test_classify_splits_missing_from_inconclusive(self, monkeypatch):
        """Only a real miss may block a start; a timeout installs and proceeds."""
        _fake_probe(
            monkeypatch,
            {"present-dep": "present", "gone-dep": "missing", "slow-dep": "unknown"},
        )
        missing, unknown = _pm()._classify_plugin_deps(["present-dep", "gone-dep", "slow-dep"])
        assert missing == ["gone-dep"]
        assert unknown == ["slow-dep"]

    def test_inconclusive_dep_never_lands_in_missing(self, monkeypatch):
        _fake_probe(monkeypatch, {"slow-dep": "unknown"})
        missing, unknown = _pm()._classify_plugin_deps(["slow-dep"])
        assert missing == []
        assert unknown == ["slow-dep"]


class TestInstallDependenciesGate:
    """The whole point: an inconclusive probe must not refuse to start."""

    @staticmethod
    def _process(pm, tmp_path, deps):
        import json

        plugin_dir = tmp_path / "slowplug"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(json.dumps({"dependencies": {"pip": deps}}), encoding="utf-8")
        proc = pm.PluginServiceProcess.__new__(pm.PluginServiceProcess)
        proc.plugin_id = "slowplug"
        proc.plugin_dir = str(plugin_dir)
        proc.dependencies = {"pip": deps}
        proc._circuit_last_failure_reason = None
        return proc

    def test_unknown_only_proceeds_without_installing(self, monkeypatch, tmp_path):
        pm = _pm()
        _fake_probe(monkeypatch, {"lark-oapi": "unknown"})
        installed = []
        monkeypatch.setattr(pm, "_ensure_pip_and_install", lambda *a, **kw: installed.append(a))

        proc = self._process(pm, tmp_path, ["lark-oapi"])
        assert proc._install_dependencies() is True
        assert installed == [], "nothing was reported missing, so nothing to install"

    def test_missing_dep_still_blocks_after_failed_install(self, monkeypatch, tmp_path):
        pm = _pm()
        _fake_probe(monkeypatch, {"ghost-pkg": "missing"})
        monkeypatch.setattr(pm, "_ensure_pip_and_install", lambda *a, **kw: False)

        proc = self._process(pm, tmp_path, ["ghost-pkg"])
        assert proc._install_dependencies() is False
        assert "ghost-pkg" in (proc._circuit_last_failure_reason or "")

    def test_missing_dep_that_installs_cleanly_proceeds(self, monkeypatch, tmp_path):
        pm = _pm()
        statuses = {"new-pkg": "missing"}
        _fake_probe(monkeypatch, statuses)
        monkeypatch.setattr(
            pm, "_ensure_pip_and_install", lambda *a, **kw: statuses.update({"new-pkg": "present"}) or True
        )

        proc = self._process(pm, tmp_path, ["new-pkg"])
        assert proc._install_dependencies() is True


# ── child environment ───────────────────────────────────────────────────


class TestChildProcessEnv:
    @staticmethod
    def _env(monkeypatch, python_exe):
        pm = _pm()
        monkeypatch.setattr(pm.syscfg, "get_builtin_root", lambda: os.path.join("C:", "repo"))
        monkeypatch.setenv("PYTHONPATH", os.path.join("C:", "launcher", "site-packages"))
        monkeypatch.delenv("OPENSQUAD_WORKSPACE", raising=False)
        monkeypatch.delenv("OPENSQUAD_USER_DATA", raising=False)
        monkeypatch.delenv("OPENSQUAD_APP_DATA", raising=False)
        monkeypatch.setattr(pm.syscfg, "get_workspace", lambda: os.path.join("C:", "ws"))
        return pm._build_child_process_env(python_exe=python_exe)

    def test_foreign_interpreter_gets_no_pythonpath(self, monkeypatch):
        env = self._env(monkeypatch, os.path.join("C:", "agent", "python311", "python.exe"))
        assert env["PYTHONPATH"] == ""

    def test_own_interpreter_keeps_launcher_pythonpath(self, monkeypatch):
        env = self._env(monkeypatch, sys.executable)
        assert "repo" in env["PYTHONPATH"]
        assert "site-packages" in env["PYTHONPATH"]

    def test_omitted_interpreter_keeps_launcher_pythonpath(self, monkeypatch):
        """Agents resolve their interpreter to sys.executable — unchanged."""
        env = self._env(monkeypatch, None)
        assert "repo" in env["PYTHONPATH"]
