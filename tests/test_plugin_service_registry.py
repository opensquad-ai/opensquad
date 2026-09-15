"""Plugin service Start must hit the same registry the launcher populated.

Two regressions this file pins:

1. Dual-entry: ``run.py --service launcher`` and ``python launcher_main.py``
   load this file as ``_opensquad_launcher_entry`` / ``__main__``. Mixins then
   ``from opensquad.launcher_main import _plugin_services``. Without the alias,
   that import is a second copy whose dict stays empty — Service Manager lists
   websearch from plugin.json but Start returns 404.

2. Lazy register: a service the UI listed from disk but that was missed at
   boot must be registered on Start, not 404.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

from opensquad import launcher_main as lm

LAUNCHER_PATH = Path(lm.__file__).resolve()
SRC_ROOT = LAUNCHER_PATH.parents[1]


def test_ensure_registers_discovered_service(monkeypatch):
    lm._plugin_services.pop("websearch", None)
    monkeypatch.setattr(
        lm,
        "discover_all_plugin_services",
        lambda: [
            {
                "plugin_id": "websearch",
                "plugin_dir": str(SRC_ROOT / "plugins" / "websearch"),
                "service_cfg": {"entry": "service/main.py", "default_port": 9001},
                "display_name": "Web Search",
                "plugin_type": "tool",
                "dependencies": {},
            }
        ],
    )
    try:
        psp = lm.ensure_plugin_service_registered("websearch")
        assert psp is not None
        assert psp.plugin_id == "websearch"
        assert lm._plugin_services["websearch"] is psp
        assert lm.ensure_plugin_service_registered("websearch") is psp
    finally:
        lm._plugin_services.pop("websearch", None)


def test_ensure_unknown_plugin_returns_none(monkeypatch):
    monkeypatch.setattr(lm, "discover_all_plugin_services", lambda: [])
    assert lm.ensure_plugin_service_registered("definitely-not-a-plugin") is None


def test_file_loaded_launcher_shares_registry_with_management_api():
    """Mirrors ``run.py --service launcher``: load by path, then import mixins."""
    script = textwrap.dedent(
        f"""
        import importlib.util
        import sys

        path = {str(LAUNCHER_PATH)!r}
        spec = importlib.util.spec_from_file_location("_opensquad_launcher_entry", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_opensquad_launcher_entry"] = mod
        sys.modules["opensquad.launcher_main"] = mod
        spec.loader.exec_module(mod)

        from opensquad.launcher.management_api._plugin_services import _plugin_services as mixin_svcs
        assert mixin_svcs is mod._plugin_services, (
            id(mixin_svcs), id(mod._plugin_services),
            getattr(sys.modules.get("opensquad.launcher_main"), "__name__", None),
        )
        print("ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(SRC_ROOT.parent),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "ok" in result.stdout


def test_script_entry_aliases_without_pre_insert():
    """``python launcher_main.py`` never inserts ``opensquad.launcher_main`` first."""
    script = textwrap.dedent(
        f"""
        import importlib.util
        import sys

        path = {str(LAUNCHER_PATH)!r}
        spec = importlib.util.spec_from_file_location("_launcher_script_entry", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_launcher_script_entry"] = mod
        # Deliberately do NOT pre-alias opensquad.launcher_main — the module
        # body must do it, the way a direct script launch does.
        spec.loader.exec_module(mod)

        assert sys.modules["opensquad.launcher_main"] is mod
        from opensquad.launcher.management_api._plugin_services import _plugin_services as mixin_svcs
        assert mixin_svcs is mod._plugin_services
        print("ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(SRC_ROOT.parent),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "ok" in result.stdout


def _opensquad_import_names(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return [n for n in names if n == "opensquad" or n.startswith("opensquad.")]


def test_plugin_service_scripts_do_not_import_opensquad():
    """Agent Python that runs plugin HTTP services does not have opensquad.

    Importing it is the class of crash behind
    ``ModuleNotFoundError: No module named 'opensquad'`` on websearch Start.
    """
    plugins_root = SRC_ROOT / "plugins"
    offenders: list[str] = []
    for path in plugins_root.glob("*/service/**/*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        hits = _opensquad_import_names(tree)
        if hits:
            offenders.append(f"{path.relative_to(SRC_ROOT.parent)}: {hits}")
    runtime = plugins_root / "_service_runtime.py"
    tree = ast.parse(runtime.read_text(encoding="utf-8"), filename=str(runtime))
    hits = _opensquad_import_names(tree)
    if hits:
        offenders.append(f"{runtime.relative_to(SRC_ROOT.parent)}: {hits}")
    assert offenders == []


def test_reranker_sidecar_imports_without_opensquad():
    """Mirrors the spawned service layout: plugins/ + service/ on path, no opensquad."""
    plugins = SRC_ROOT / "plugins"
    service = plugins / "websearch" / "service"
    script = textwrap.dedent(
        f"""
        import sys
        sys.path[:0] = [{str(service)!r}, {str(plugins)!r}]

        class _BlockOpensquad:
            def find_spec(self, fullname, path, target=None):
                if fullname == "opensquad" or fullname.startswith("opensquad."):
                    raise ModuleNotFoundError(f"No module named {{fullname!r}}")
                return None

        sys.meta_path.insert(0, _BlockOpensquad())
        import reranker_sidecar
        assert callable(reranker_sidecar.open_local)
        print("ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(SRC_ROOT.parent),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "ok" in result.stdout
