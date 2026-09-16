"""Regression lock: ``opensquad/_context.py`` is a LEAF module.

Why this exists
---------------
``AgentContext`` is the one module every subsystem is allowed to depend on: ~14
modules do ``from opensquad._context import get_current_context`` — all of them
from *inside function bodies*, because at module level that would be an import
cycle. That lazy-import convention is invisible to the module system, to mypy,
and to anyone reading one file at a time.

It also made ``_context`` part of a 41-module coupling cycle (see
``docs/report_opensquad_architecture_review.md`` P1-4). The reason was
``AgentContext.from_boot()`` — a factory that imported eight concrete singletons
(``bus`` / ``input_hub`` / ``session_manager`` / …) inside its body. It had **0
callers**, and it was the *only* source of runtime out-edges from this module.
Deleting it took ``_context`` out of every cycle and shrank the big one from 41
to 32 modules (measured; ``tests`` cannot run the scanner, so the numbers live
in the docstring and in the module docstring of ``_context.py``).

The rules below encode the leaf property in four independent ways, because each
one alone has a blind spot:

  R1 (AST)         every project import lives inside ``if TYPE_CHECKING:``
                   — catches function-body imports, which R4 cannot see.
  R2 (AST)         the type-only block still imports exactly the names the field
                   annotations use — nobody may "simplify" it to ``Any``.
  R3 (frozen set)  the dataclass field names are the shared contract; a rename
                   must break loudly, since ``Any``-free typing is the only
                   thing keeping consumers honest.
  R4 (subprocess)  importing the module in a fresh interpreter pulls in nothing
                   but ``opensquad`` itself — the runtime graph, not the parse.

Mutating the module in the five obvious ways must fail this file; see
``C:/tmp/prov/mutate_context_leaf.py`` (7/7 killed).
"""

from __future__ import annotations

import ast
import builtins
import json
import subprocess
import sys
from pathlib import Path

import pytest

from opensquad._context import AgentContext

_REPO = Path(__file__).resolve().parents[1]
_CONTEXT_PY = _REPO / "src" / "opensquad" / "_context.py"
_PKG = "opensquad"

# ---------------------------------------------------------------------------
# R3 — the frozen shared contract
# ---------------------------------------------------------------------------

_EXPECTED_FIELDS = (
    # runtime services (wired by the boot layer)
    "event_bus",
    "input_hub",
    "message_queue",
    "sleep_controller",
    "state_manager",
    "event_pipeline",
    "message_router",
    "session_manager",
    # core domain objects
    "chat_api",
    "tool_registry",
    "memory_manager",
    "plugin_manager",
    # metadata
    "agent_id",
    "agent_name",
    "config_path",
    "agent_dir",
    "workspace_dir",
    "session_cwd",
)

_EXPECTED_MODULE_FUNCTIONS = {
    "set_current_context",
    "reset_current_context",
    "get_current_context",
    "require_context",
}
_EXPECTED_MODULE_CLASSES = {"AgentContext"}

# Importing the leaf must not drag in a single sibling module. `opensquad`
# itself is the package __init__ (version string + lazy `__getattr__` exports),
# so it is the only allowed companion.
_EXPECTED_RUNTIME_MODULES = {"opensquad", "opensquad._context"}

# Names the type-only block must expose for the field annotations below.
_EXPECTED_TYPE_ONLY_NAMES = {
    "ChatAPI",
    "ClaudeAPI",
    "EventBus",
    "EventPipeline",
    "GoogleAPI",
    "InputHub",
    "MessageQueue",
    "MessageRouter",
    "SleepController",
    "AIStateManager",
    "SessionManager",
    "ToolRegistry",
    "ChatAPIType",
}


def _tree() -> ast.Module:
    return ast.parse(_CONTEXT_PY.read_text(encoding="utf-8"))


def _is_type_checking_test(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def _type_only_node_ids(tree: ast.Module) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_type_checking_test(node.test):
            for child in ast.walk(node):
                ids.add(id(child))
    return ids


def _project_imports(node: ast.AST) -> bool:
    """True if *node* is an import of this project (absolute or relative)."""
    if isinstance(node, ast.ImportFrom):
        if node.level:
            return True
        return bool(node.module) and (node.module == _PKG or node.module.startswith(_PKG + "."))
    if isinstance(node, ast.Import):
        return any(a.name == _PKG or a.name.startswith(_PKG + ".") for a in node.names)
    return False


def _agent_context_node(tree: ast.Module) -> ast.ClassDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "AgentContext":
            return node
    pytest.fail("AgentContext class not found in _context.py")
    raise AssertionError  # unreachable, keeps type checkers happy


def _annotation_names(ann: ast.expr | None) -> set[str]:
    """Names referenced by a (possibly `X | None`) annotation."""
    if ann is None:
        return set()
    return {n.id for n in ast.walk(ann) if isinstance(n, ast.Name)}


# ---------------------------------------------------------------------------
# R1 — no runtime project import, module level or function body
# ---------------------------------------------------------------------------


def test_context_has_no_runtime_project_import():
    """Every project import must sit inside `if TYPE_CHECKING:`.

    This is the rule that keeps the module a leaf. It covers the case R4 cannot:
    an import hidden in a function body — exactly the shape of the deleted
    ``from_boot()`` factory, which imported eight singletons lazily and put the
    module back into the 41-module cycle without changing the import-time graph.
    """
    tree = _tree()
    type_only = _type_only_node_ids(tree)

    offenders = [
        f"{_CONTEXT_PY.name}:{node.lineno}"
        for node in ast.walk(tree)
        if _project_imports(node) and id(node) not in type_only
    ]

    assert not offenders, (
        "_context.py must import no project module at runtime — found "
        f"{offenders}. A runtime import here re-creates the coupling cycle: ~14 "
        "modules import _context lazily, so any edge back out closes the loop. "
        "Type-only imports belong in the single `if TYPE_CHECKING:` block; "
        "boot-time wiring belongs in agents_boot / agent_boot_phases."
    )


def test_context_type_only_block_is_module_level():
    """The escape hatch must stay greppable: a module-level `if TYPE_CHECKING:`."""
    tree = _tree()
    blocks = [node for node in tree.body if isinstance(node, ast.If) and _is_type_checking_test(node.test)]
    assert len(blocks) == 1, f"expected exactly one module-level `if TYPE_CHECKING:` block, found {len(blocks)}"
    type_only = _type_only_node_ids(tree)
    assert [n for n in ast.walk(tree) if _project_imports(n) and id(n) in type_only], (
        "the TYPE_CHECKING block no longer imports any project module — "
        "the field annotations below would have degraded to Any"
    )


# ---------------------------------------------------------------------------
# R2 — the type-only block still types the fields (no Any drift)
# ---------------------------------------------------------------------------


def test_type_only_names_exactly_cover_the_field_annotations():
    """Locks *both* directions: no unused type import, no untyped annotation.

    Measured before trusting this: replacing the block with ``Any`` makes mypy
    stop reporting ``attr-defined`` on every ``ctx.<service>`` access across the
    codebase (probe: ``src/opensquad/_leaf_probe.py``, since removed). The block
    is load-bearing for the whole repo, so it is not "dead imports".
    """
    tree = _tree()
    type_only = _type_only_node_ids(tree)

    declared: set[str] = set()
    for node in ast.walk(tree):
        if id(node) not in type_only:
            continue
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                declared.add(a.asname or a.name)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            declared.add(node.target.id)
        elif isinstance(node, ast.Assign):
            # `ChatAPIType = ChatAPI | ClaudeAPI | GoogleAPI` is a plain Assign.
            declared |= {t.id for t in node.targets if isinstance(t, ast.Name)}

    assert declared == _EXPECTED_TYPE_ONLY_NAMES, (
        "the TYPE_CHECKING surface changed.\n"
        f"  missing: {sorted(_EXPECTED_TYPE_ONLY_NAMES - declared)}\n"
        f"  extra:   {sorted(declared - _EXPECTED_TYPE_ONLY_NAMES)}"
    )

    used: set[str] = set()
    for stmt in _agent_context_node(tree).body:
        if isinstance(stmt, ast.AnnAssign):
            used |= _annotation_names(stmt.annotation)
    untyped = used - declared - {"Any", "None"} - set(dir(builtins))
    assert not untyped, f"field annotations reference names the TYPE_CHECKING block does not declare: {sorted(untyped)}"


# ---------------------------------------------------------------------------
# R3 — field names / module surface are frozen
# ---------------------------------------------------------------------------


def test_agent_context_fields_are_frozen():
    """The dataclass is a cross-process contract; a rename must break loudly."""
    import dataclasses

    assert dataclasses.is_dataclass(AgentContext)
    actual = tuple(f.name for f in dataclasses.fields(AgentContext))
    assert actual == _EXPECTED_FIELDS, (
        "AgentContext fields changed.\n"
        f"  added:   {sorted(set(actual) - set(_EXPECTED_FIELDS))}\n"
        f"  removed: {sorted(set(_EXPECTED_FIELDS) - set(actual))}\n"
        "If the change is intentional, update _EXPECTED_FIELDS and the boot "
        "layer (agents_boot / agent_boot_phases) in the same commit."
    )


def test_context_module_surface_is_frozen():
    """A leaf with fan-in 14 should not grow new public helpers by accident."""
    tree = _tree()
    funcs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}
    classes = {n.name for n in tree.body if isinstance(n, ast.ClassDef)}
    assert funcs == _EXPECTED_MODULE_FUNCTIONS, f"module functions changed: {sorted(funcs)}"
    assert classes == _EXPECTED_MODULE_CLASSES, f"module classes changed: {sorted(classes)}"

    assert not any(
        isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "from_boot"
        for n in _agent_context_node(tree).body
    ), "AgentContext.from_boot() was re-added — it was dead code and the only runtime out-edge"


def test_agent_context_constructs_with_no_arguments():
    """All fields defaulted → pure data holder, no import-time side effects."""
    ctx = AgentContext()
    for name in _EXPECTED_FIELDS:
        assert getattr(ctx, name, None) in (None, ""), f"{name} lost its empty default"


# ---------------------------------------------------------------------------
# R4 — the runtime import graph, measured in a fresh interpreter
# ---------------------------------------------------------------------------


def test_importing_context_pulls_in_nothing_else():
    """Prove leaf-ness by execution, not by parsing.

    A module-level import added tomorrow would pass a naive "is it inside
    TYPE_CHECKING" review but show up here instantly.
    """
    code = (
        "import json, sys\n"
        "import opensquad._context  # noqa: F401\n"
        "print(json.dumps(sorted(m for m in sys.modules "
        "if m == 'opensquad' or m.startswith('opensquad.'))))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(_REPO),
        timeout=180,
        check=False,
    )
    assert proc.returncode == 0, f"importing opensquad._context failed:\n{proc.stderr[-3000:]}"
    loaded = set(json.loads(proc.stdout.strip().splitlines()[-1]))
    assert loaded == _EXPECTED_RUNTIME_MODULES, (
        "importing opensquad._context pulled in extra modules "
        f"{sorted(loaded - _EXPECTED_RUNTIME_MODULES)} — it is no longer a leaf, so "
        "the import order of every one of its ~14 consumers now matters."
    )
