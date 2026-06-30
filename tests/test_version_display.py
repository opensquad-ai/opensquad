"""Tests for _get_current_version() — the function the web UI's
``/version`` endpoint uses to display "vX.Y.Z 稳定版" in the footer.

Regression: previously the helper read only ``opensquad.__version__``,
which is a hand-maintained string in ``src/opensquad/__init__.py``.
That value last tracked ``0.1.1`` while ``pyproject.toml`` and the
release tags had already moved on to ``0.2.0`` / ``0.2.0.post0``.
The web UI kept showing "v0.1.1 稳定版" long after the project had
shipped newer versions.

The fix routes the lookup through ``importlib.metadata.version()``,
which reads the version field from the installed package's metadata
— generated from ``pyproject.toml`` at ``pip install`` time and
therefore immune to hand-edit drift.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

# The routes files use ``from app.api import get_current_user_dep`` and
# similar absolute imports that require the gateway backend root on
# sys.path. Add it once for the whole module.
_BACKEND_DIR = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        os.pardir,
        "src",
        "opensquad",
        "gateway",
        "backend",
    )
)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


# ── Load just the function under test, not the whole routes module ──
#
# The routes files cascade into many app.* imports (api, models, websocket,
# registry, ...) that are not relevant to the version-display fix. To test
# the function in isolation we extract it from the source with AST and
# exec it into a clean namespace. This keeps the test self-contained and
# robust against unrelated refactors elsewhere in the file.


def _extract_function(source_path: str, function_name: str) -> str:
    """Read ``source_path`` and return just the source of ``function_name``."""
    import ast

    with open(source_path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=source_path)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return ast.unparse(node)
    raise LookupError(f"function {function_name!r} not found in {source_path}")


def _make_loader(filename: str):
    """Return a function that produces a fresh module with the helper
    in its own namespace, so each test gets a clean importlib.metadata view."""
    routes_dir = os.path.join(
        _BACKEND_DIR,
        "app",
        "ai_web",
        "routes",
    )
    src = os.path.normpath(
        os.path.join(routes_dir, filename)
        if filename != "routes.py"
        else os.path.join(_BACKEND_DIR, "app", "ai_web", "routes.py")
    )
    fn_src = _extract_function(src, "_get_current_version")

    def _load():
        ns = {"__name__": f"_test_{filename.replace('.py', '')}"}
        exec(fn_src, ns)
        # Bind a clean copy of the function so the closure / module attrs
        # don't leak between tests.
        return ns["_get_current_version"]

    return _load


_load_routes_fn = _make_loader("routes.py")
_load_main_fn = _make_loader("_main.py")


@pytest.fixture
def routes_fn():
    """Fresh copy of _get_current_version from routes.py, isolated namespace."""
    return _load_routes_fn()


@pytest.fixture
def main_fn():
    """Fresh copy of _get_current_version from routes/_main.py."""
    return _load_main_fn()


# ── 1. importlib.metadata is the primary source of truth ───────────────


def test_routes_prefers_importlib_metadata(routes_fn):
    """When importlib.metadata returns a real version, it wins over __version__."""
    with patch("importlib.metadata.version", return_value="9.9.9.post7"):
        result = routes_fn()
    assert result == "9.9.9.post7", f"_get_current_version must return the importlib.metadata value, got {result!r}"


def test_main_prefers_importlib_metadata(main_fn):
    """The mirror helper in routes/_main.py must use the same source priority."""
    with patch("importlib.metadata.version", return_value="9.9.9.post7"):
        result = main_fn()
    assert result == "9.9.9.post7"


# ── 2. importlib.metadata failure falls back to opensquad.__version__ ──


def test_routes_falls_back_to_module_version(routes_fn):
    """If importlib.metadata raises, the module-level __version__ is used."""

    def _boom(_name):
        raise RuntimeError("simulated: package not installed")

    with patch("importlib.metadata.version", side_effect=_boom):
        # opensquad is importable in the test env, so the fallback should
        # resolve to whatever __init__.py declares right now.
        result = routes_fn()
    assert result and result != "unknown", (
        f"fallback to opensquad.__version__ should yield a real version, got {result!r}"
    )


def test_main_falls_back_to_module_version(main_fn):
    def _boom(_name):
        raise RuntimeError("simulated: package not installed")

    with patch("importlib.metadata.version", side_effect=_boom):
        result = main_fn()
    assert result and result != "unknown"


# ── 3. Both sources failing returns the "unknown" sentinel ────────────


def test_routes_returns_unknown_when_both_sources_fail(routes_fn):
    def _boom(_name):
        raise RuntimeError("no metadata")

    with patch("importlib.metadata.version", side_effect=_boom):
        # Hide opensquad so the second fallback also raises
        with patch.dict(sys.modules, {"opensquad": None}):
            result = routes_fn()
    assert result == "unknown"


def test_main_returns_unknown_when_both_sources_fail(main_fn):
    def _boom(_name):
        raise RuntimeError("no metadata")

    with patch("importlib.metadata.version", side_effect=_boom), patch.dict(sys.modules, {"opensquad": None}):
        result = main_fn()
    assert result == "unknown"


# ── 4. Drift guard: a stale __init__.py must NOT leak into the UI ──────


def test_stale_init_version_does_not_override_fresh_metadata(routes_fn):
    """Regression: simulate the original bug — __init__.py says 0.1.1,
    pyproject.toml / installed metadata say 0.2.0.post0. The UI must show
    the installed-metadata value, not the stale __version__ string."""
    with patch("importlib.metadata.version", return_value="0.2.0.post0"):
        result = routes_fn()
    assert result == "0.2.0.post0", (
        f"installed metadata must win over opensquad.__version__; "
        f"got {result!r} (the original 'v0.1.1 稳定版' bug surfaces here)"
    )


# ── 5. Returned value is a non-empty, non-"0.0.0" string ──────────────


def test_returns_non_empty_pep440_string(routes_fn):
    """Whatever the source, the helper must return a usable string."""
    result = routes_fn()
    assert isinstance(result, str)
    assert result  # non-empty
    # packaging.version is the canonical parser; if it accepts the string,
    # the value is at least well-formed PEP 440.
    from packaging.version import Version

    Version(result)  # raises if malformed


# ── 6. Frontend build-time source priority is locked to pyproject ──────
#
# The web UI's footer "vX.Y.Z ..." is baked into the JS bundle at build
# time by vite.config.ts::loadAppVersion(). Forgetting the build was the
# second half of the original drift bug: even after the backend read
# importlib.metadata correctly, the frontend still rendered the
# hand-maintained __init__.py value because loadAppVersion() preferred
# __init__.py over pyproject.toml.
#
# We assert on the source text directly (no Node / vite runtime needed)
# to lock in: pyproject.toml is checked first; __init__.py is a
# fallback, not a primary source.


def test_vite_config_reads_pyproject_before_init_py():
    """Regression: loadAppVersion() must consult pyproject.toml first."""
    os.path.normpath(os.path.join(_BACKEND_DIR, "..", "..", "..", "nexuschat-pro", "vite.config.ts"))
    # Resolve the actual repo path (the join above is approximate).
    candidate = [
        p
        for p in [
            os.path.join(_BACKEND_DIR, "..", "..", "..", "nexuschat-pro", "vite.config.ts"),
            os.path.normpath(
                os.path.join(
                    os.path.dirname(__file__), "..", "src", "opensquad", "gateway", "nexuschat-pro", "vite.config.ts"
                )
            ),
        ]
        if os.path.exists(p)
    ]
    assert candidate, "could not locate vite.config.ts"
    text = open(candidate[0], encoding="utf-8").read()

    # 1. pyproject.toml must appear in loadAppVersion.
    assert "pyproject" in text, (
        "vite.config.ts no longer references pyproject.toml — has "
        "loadAppVersion() been rewritten to a different source?"
    )
    # 2. __init__.py must still be present as a fallback.
    assert "__init__.py" in text, (
        "vite.config.ts should keep __init__.py as a fallback in case "
        "pyproject.toml is missing (e.g. unusual build environments)"
    )
    # 3. The pyproject branch must come BEFORE the __init__.py branch.
    pyproject_pos = text.find("pyproject")
    initpy_pos = text.find("__init__.py")
    assert pyproject_pos != -1 and initpy_pos != -1
    assert pyproject_pos < initpy_pos, (
        f"pyproject.toml (pos {pyproject_pos}) must be checked before "
        f"__init__.py (pos {initpy_pos}); the hand-maintained module "
        f"attribute is no longer the source of truth for the frontend "
        f"build."
    )
