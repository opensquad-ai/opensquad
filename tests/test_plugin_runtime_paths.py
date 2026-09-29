"""Regression guards for the plugin-runtime defects reported from a pip install.

Three separate shapes, all of which only bite in a *pip* layout where the
plugin tree and the interpreter that executes it disagree:

1. ``sys.path.insert(0, ROOT_DIR)`` in a plugin module, where ``ROOT_DIR`` is
   3 levels up from ``plugins/<name>/<file>.py`` — i.e. a site-packages. Plugin
   services are executed by the bundled Agent Python (3.11) even when the tree
   was installed by 3.12, so putting it first shadows the runtime's own
   compiled deps and dies with ``No module named 'pydantic_core._pydantic_core'``.
2. Import-probing every declared dependency at startup. A cold ``import
   lark_oapi`` measures 10-13s, so one disabled plugin's dependency can blow the
   probe budget and hold every other service's start.
3. The legacy reranker download set ``HF_ENDPOINT`` / ``HF_HUB_DISABLE_XET``
   *after* importing huggingface_hub, which freezes both into
   ``huggingface_hub.constants`` at its own import time — so the mirror was
   never used and the Xet path 401'd mid-download.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_PLUGINS = os.path.join(_ROOT, "src", "plugins")


# ── 1. no plugin module may put a site-packages first on sys.path ──────────


# The modules that carried the insert(0) form (fixed 2026-09-29). They are
# listed explicitly so a revert is caught even if the expression is rewritten.
_PATH_FIXED = (
    "external_api/adapter.py",
    "external_api/config.py",
    "feishu/adapter.py",
    "feishu/config.py",
    "feishu/send_tools.py",
    "telegram/adapter.py",
    "telegram/config.py",
    "telegram/send_tools.py",
    "websearch/websearch.py",
    "plugin_manager.py",
    # Added later the same day: its insert(0) put the plugins tree in front, and
    # the tree contains `telegram/` — so a later `import telegram` in the same
    # process (plugins/telegram/adapter.py) resolved to the plugin directory
    # instead of the installed python-telegram-bot and raised ImportError.
    "websearch/reranker_model_store.py",
)


@pytest.mark.parametrize("rel", _PATH_FIXED)
def test_no_insert_zero_in_path_sensitive_module(rel):
    text = open(os.path.join(_PLUGINS, rel), encoding="utf-8").read()
    assert "sys.path.insert(0" not in text, (
        f"{rel} must APPEND, not insert(0): its computed root IS a site-packages "
        "in a pip install, and plugin modules are executed by a different "
        "interpreter than the one that installed the tree"
    )


def test_no_plugin_inserts_a_root_shaped_path_first():
    """Companion guard for the *inline* form of the same mistake.

    It only sees the shape written out in the call itself (a 3-levels-up
    ``dirname`` chain, or a ``".."`` join) — a module that stores that
    expression in a variable first needs the explicit list above, because a
    bare name cannot be told apart from the legitimate plugin-local dirs
    (``_here``, ``_plugin_dir``, the ``plugins`` package dir), which stay.
    """
    offenders: list[str] = []
    for dirpath, _dirs, files in os.walk(_PLUGINS):
        if "__pycache__" in dirpath:
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            text = open(path, encoding="utf-8", errors="replace").read()
            for expr in re.findall(r"sys\.path\.insert\(\s*0\s*,\s*([^)]*)\)", text):
                dirnames = expr.count("dirname(")
                if dirnames >= 3 or '".."' in expr or "'..'" in expr:
                    offenders.append(f"{os.path.relpath(path, _ROOT)}: {expr.strip()}")
    assert not offenders, "site-packages-shaped path inserted first:\n  " + "\n  ".join(offenders)


# ── 2. dependency checks must not import-probe an installed distribution ────


def _pm():
    from opensquad.launcher import process_manager as pm

    return pm


class TestMetadataFastPath:
    def test_installed_dist_is_present_without_an_import_probe(self, monkeypatch):
        pm = _pm()
        monkeypatch.setattr(pm, "_plugin_dist_module_map", lambda _py: {"lark-oapi": ["lark_oapi"]})
        monkeypatch.setattr(pm, "_plugin_python_executable", lambda: sys.executable)

        def _no_probe(_name):
            raise AssertionError("an installed distribution must not be import-probed")

        monkeypatch.setattr(pm, "_plugin_python_import_status", _no_probe)
        assert pm._plugin_python_dist_status("lark-oapi") == "present"

    def test_name_and_version_pin_are_normalized(self, monkeypatch):
        pm = _pm()
        monkeypatch.setattr(pm, "_plugin_dist_module_map", lambda _py: {"python-telegram-bot": ["telegram"]})
        monkeypatch.setattr(pm, "_plugin_python_executable", lambda: sys.executable)
        monkeypatch.setattr(pm, "_plugin_python_import_status", lambda _n: pytest.fail("must not probe"))
        assert pm._plugin_python_dist_status("Python_Telegram_Bot>=21.0") == "present"

    def test_uninstalled_dep_still_falls_back_to_the_import_probe(self, monkeypatch):
        pm = _pm()
        monkeypatch.setattr(pm, "_plugin_dist_module_map", lambda _py: {})
        monkeypatch.setattr(pm, "_plugin_python_executable", lambda: sys.executable)
        seen: list[str] = []

        def _fake(name):
            seen.append(name)
            return "present" if name == "from_source" else "missing"

        monkeypatch.setattr(pm, "_plugin_python_import_status", _fake)
        monkeypatch.setattr(pm, "_resolve_import_candidates", lambda _d: ["from_source"])
        assert pm._plugin_python_dist_status("not-installed-dist") == "present"
        assert seen == ["from_source"]


class TestStartupBatch:
    @staticmethod
    def _run_batch(monkeypatch, infos, deps_of):
        pm = _pm()
        installed: list[list[str]] = []
        monkeypatch.setattr(pm, "_plugin_python_dist_status", lambda dep: deps_of.get(dep, "missing"))
        monkeypatch.setattr(
            pm, "_ensure_pip_and_install", lambda packages, label="": installed.append(list(packages)) or True
        )
        monkeypatch.setattr(pm, "resolve_auto_start", lambda pid, cfg: cfg.get("auto_start", True))
        monkeypatch.setattr(pm, "syscfg", pm.syscfg)
        pm._plugin_deps_ready.clear()
        pm._install_builtin_plugin_deps(infos)
        return installed

    def test_disabled_service_deps_are_not_pre_installed(self, monkeypatch):
        infos = [
            {
                "plugin_id": "feishu",
                "plugin_enabled": False,
                "service_cfg": {"auto_start": True},
                "dependencies": {"pip": ["lark-oapi"]},
            },
            {
                "plugin_id": "websearch",
                "plugin_enabled": True,
                "service_cfg": {"auto_start": True},
                "dependencies": {"pip": ["trafilatura"]},
            },
        ]
        installed = self._run_batch(monkeypatch, infos, {"lark-oapi": "missing", "trafilatura": "missing"})
        assert installed == [["trafilatura"]]
        assert _pm()._plugin_deps_ready.is_set(), "the batch must still release the gate"

    def test_auto_start_false_service_deps_are_not_pre_installed(self, monkeypatch):
        infos = [
            {
                "plugin_id": "sensevoice",
                "plugin_enabled": True,
                "service_cfg": {"auto_start": False},
                "dependencies": {"pip": ["onnxruntime"]},
            },
            {
                "plugin_id": "websearch",
                "plugin_enabled": True,
                "service_cfg": {"auto_start": True},
                "dependencies": {"pip": ["trafilatura"]},
            },
        ]
        installed = self._run_batch(monkeypatch, infos, {"onnxruntime": "missing", "trafilatura": "missing"})
        assert installed == [["trafilatura"]]

    def test_no_bootable_service_still_releases_the_gate(self, monkeypatch):
        infos = [
            {
                "plugin_id": "feishu",
                "plugin_enabled": False,
                "service_cfg": {},
                "dependencies": {"pip": ["lark-oapi"]},
            }
        ]
        assert self._run_batch(monkeypatch, infos, {"lark-oapi": "missing"}) == []
        assert _pm()._plugin_deps_ready.is_set()


# ── 3. legacy reranker download: env before the hf import ──────────────────


class TestLegacyDownloadEnv:
    @staticmethod
    def _source() -> str:
        path = os.path.join(_PLUGINS, "websearch", "service", "reranker_sidecar.py")
        return open(path, encoding="utf-8").read()

    def test_hf_env_is_set_before_huggingface_hub_is_imported(self):
        src = self._source()
        i_endpoint = src.index('setdefault("HF_ENDPOINT"')
        i_xet = src.index('setdefault("HF_HUB_DISABLE_XET"')
        i_import = src.index("import huggingface_hub")
        assert i_endpoint < i_import, "HF_ENDPOINT is frozen at huggingface_hub import time"
        assert i_xet < i_import, "HF_HUB_DISABLE_XET is frozen at huggingface_hub import time"

    def test_xet_is_disabled_for_the_legacy_path(self):
        src = self._source()
        assert 'setdefault("HF_HUB_DISABLE_XET", "1")' in src

    def test_the_env_is_really_frozen_at_import_time(self):
        """Documents *why* the ordering above matters (real huggingface_hub)."""
        code = (
            "import os, huggingface_hub.constants as c\n"
            "before = c.HF_HUB_DISABLE_XET\n"
            "os.environ['HF_HUB_DISABLE_XET'] = '1'\n"
            "print(1 if (before is False and c.HF_HUB_DISABLE_XET is False) else 0)\n"
        )
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
        if r.returncode != 0 or "huggingface_hub" in (r.stderr or ""):
            pytest.skip("huggingface_hub not installed in this interpreter")
        if r.stdout.strip() != "1":
            pytest.skip("this huggingface_hub re-reads the variable per call")


# ── 4. a persisted download error must not outlive the files ───────────────


class TestStatusReconciliation:
    @staticmethod
    def _module():
        from plugins.websearch import reranker_model_store as m

        return m

    def test_complete_model_with_persisted_error_is_reconciled(self, monkeypatch):
        m = self._module()
        state = {"state": "error"}

        class _Store:
            def get_status(self):
                return dict(state)

            def mark_ready(self, message: str = "") -> None:
                state["state"] = "ready"

        monkeypatch.setattr(m, "is_complete", lambda: True)
        monkeypatch.setattr(m, "_get_store", lambda: _Store())
        monkeypatch.setattr(m, "active_snapshot_dir", lambda: "")
        monkeypatch.setattr(m, "_legacy_snapshot_dir", lambda: "")
        monkeypatch.setattr(m, "model_dir", lambda: "")
        monkeypatch.setattr(m, "file_sizes", lambda *_a: {})
        monkeypatch.setattr(m, "missing_files", lambda *_a: [])

        status = m.get_status()
        assert status["ready"] is True
        assert status["download"]["state"] == "ready"

    def test_incomplete_model_keeps_the_error(self, monkeypatch):
        m = self._module()
        state = {"state": "error"}

        class _Store:
            def get_status(self):
                return dict(state)

            def mark_ready(self, message: str = "") -> None:
                raise AssertionError("must not mark a model ready while files are missing")

        monkeypatch.setattr(m, "is_complete", lambda: False)
        monkeypatch.setattr(m, "_get_store", lambda: _Store())
        monkeypatch.setattr(m, "active_snapshot_dir", lambda: "")
        monkeypatch.setattr(m, "_legacy_snapshot_dir", lambda: "")
        monkeypatch.setattr(m, "model_dir", lambda: "")
        monkeypatch.setattr(m, "file_sizes", lambda *_a: {})
        monkeypatch.setattr(m, "missing_files", lambda *_a: ["model.safetensors"])

        status = m.get_status()
        assert status["ready"] is False
        assert status["download"]["state"] == "error"
