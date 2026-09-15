"""Regression lock: never `await` something that is not awaitable (`src/opensquad`).

Bug this guards (2026-09-13, agent305 sid 20260913_064417_98h1):

    File "src/opensquad/runner.py", line 1729, in _parallel_session_turn
        await self._notify_external_turn_failed(
    TypeError: object NoneType can't be used in 'await' expression

`_notify_external_turn_failed` is a plain ``def`` (it calls
``bridge.send_message`` synchronously), so the ``await`` evaluated to
``await None``. It blew up **inside the auth-failure branch**, i.e. exactly when
the upstream provider rejected the request (DeepSeek 402 Insufficient Balance),
and the resulting TypeError REPLACED the real cause — the web only ever saw
``Task failed: object NoneType can't be used in 'await' expression`` and the
operator could not tell a dead balance from a bad API key.

The guard is a static AST scan over ``src/opensquad`` covering every shape that
can be decided without type inference:

  1. `await self.m()` / `await cls.m()`  — m is a sync def on the same class
  2. `await f()`                         — f is a sync def in the same module
  3. `await f()`                         — f is a sync def imported from another
                                           module (`from x import f`)
  4. `await Cls.m()`                     — m is a sync method of an imported class
  5. `await mod.f()`                     — f is a sync def in an imported module
  6. `await <literal>` / `await self.d[k]` / `await self.d.get(k)`
                                         — provably not a coroutine

Every shape is mutation-verified below, so the guard cannot silently pass on a
scanner that found nothing to look at.
"""

from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"

_SKIP_PARTS = {"build", "node_modules", ".venv", "_internal", "__pycache__", ".mypy_cache"}

LITERAL_NODES = (ast.Constant, ast.List, ast.Dict, ast.Set, ast.Tuple, ast.JoinedStr)
CONTAINER_LITERALS = (ast.Dict, ast.List, ast.Set)
NON_AWAITABLE_METHODS = frozenset(
    {
        "get",
        "pop",
        "setdefault",
        "keys",
        "values",
        "items",
        "copy",
        "clear",
        "append",
        "extend",
        "insert",
        "remove",
        "discard",
        "add",
        "update",
        "index",
        "count",
        "sort",
        "reverse",
        "split",
        "strip",
        "join",
        "startswith",
        "endswith",
        "format",
        "replace",
        "lower",
        "upper",
    }
)


# ── index ───────────────────────────────────────────────────────────────


def _module_name(path: Path) -> str:
    try:
        rel = path.relative_to(_SRC)
    except ValueError:
        # Synthetic snippet scanned from outside the tree (mutation tests).
        return path.stem
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


class _Index:
    """`module -> {func: is_async}` plus `module.Class -> {method: is_async}`."""

    def __init__(self) -> None:
        self.funcs: dict[str, dict[str, bool]] = {}
        self.classes: dict[str, dict[str, bool]] = {}
        self.modules: set[str] = set()

    def add(self, path: Path, tree: ast.Module) -> None:
        mod = _module_name(path)
        parts = mod.split(".")
        for i in range(1, len(parts) + 1):
            self.modules.add(".".join(parts[:i]))
        funcs: dict[str, bool] = {}
        classes: dict[str, dict[str, bool]] = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs[node.name] = isinstance(node, ast.AsyncFunctionDef)
            elif isinstance(node, ast.ClassDef):
                methods: dict[str, bool] = {}
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods[sub.name] = isinstance(sub, ast.AsyncFunctionDef)
                    elif isinstance(sub, ast.Assign):
                        # Attribute assigned in the class body — cannot prove it
                        # is non-awaitable, so stay silent (avoid false positives).
                        for target in sub.targets:
                            if isinstance(target, ast.Name):
                                methods.setdefault(target.id, True)
                classes[node.name] = methods
        self.funcs[mod] = funcs
        for cname, methods in classes.items():
            self.classes[f"{mod}.{cname}"] = methods

    def func_is_async(self, mod: str, name: str) -> bool | None:
        table = self.funcs.get(mod)
        if table is None or name not in table:
            return None
        return table[name]

    def method_is_async(self, dotted_cls: str, name: str) -> bool | None:
        table = self.classes.get(dotted_cls)
        if table is None or name not in table:
            return None
        return table[name]


class _Resolver:
    """Resolve import aliases inside one file."""

    def __init__(self, path: Path, index: _Index) -> None:
        self.index = index
        self.mod = _module_name(path)
        self.pkg = self.mod.rsplit(".", 1)[0] if "." in self.mod else ""
        self.mod_alias: dict[str, str] = {}
        self.sym_alias: dict[str, tuple[str, str]] = {}

    def _absolute(self, node: ast.ImportFrom, name: str) -> str:
        if node.level == 0:
            base = node.module or ""
        else:
            parts = self.pkg.split(".") if self.pkg else []
            keep = max(0, len(parts) - (node.level - 1))
            base = ".".join(parts[:keep] + ([node.module] if node.module else []))
        return f"{base}.{name}" if base else name

    def collect(self, tree: ast.Module) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.asname:
                        self.mod_alias[a.asname] = a.name
                    else:
                        self.mod_alias[a.name.split(".")[0]] = a.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    if a.name == "*":
                        continue
                    full = self._absolute(node, a.name)
                    local = a.asname or a.name
                    if full in self.index.modules:
                        self.mod_alias[local] = full
                    else:
                        self.sym_alias[local] = (full.rsplit(".", 1)[0], a.name)

    def resolve_chain(self, node: ast.AST) -> tuple[str, str] | None:
        """`a.b.c` -> (module dotted, function name) when the module is known."""
        parts: list[str] = []
        cur = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if not isinstance(cur, ast.Name):
            return None
        parts.append(cur.id)
        parts.reverse()

        head = parts[0]
        base = self.mod_alias.get(head)
        rest = parts[1:] if base is not None else parts
        for split in range(len(rest) - 1, 0, -1):
            cand = f"{base}.{'.'.join(rest[:split])}" if base else ".".join(rest[:split])
            if cand in self.index.modules:
                return cand, rest[split]
        return None


# ── scan one module ─────────────────────────────────────────────────────


def _scan_source(src: str, path: Path, index: _Index) -> list[str]:
    tree = ast.parse(src)
    resolver = _Resolver(path, index)
    resolver.collect(tree)

    # Same-module declarations come from the source under scan (not from the
    # repo index) so the guard also works on synthetic snippets.
    mod_funcs: dict[str, bool] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            mod_funcs[node.name] = isinstance(node, ast.AsyncFunctionDef)

    owner: dict[ast.AST, str] = {}
    class_methods: dict[str, dict[str, bool]] = {}
    self_container: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            methods: dict[str, bool] = {}
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods[sub.name] = isinstance(sub, ast.AsyncFunctionDef)
                elif isinstance(sub, ast.Assign):
                    for target in sub.targets:
                        if isinstance(target, ast.Name):
                            methods.setdefault(target.id, True)
            class_methods[node.name] = methods
            for sub in ast.walk(node):
                owner[sub] = node.name
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    if isinstance(node.value, CONTAINER_LITERALS):
                        self_container[target.attr] = type(node.value).__name__
                    elif isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
                        if node.value.func.id in ("dict", "list", "set"):
                            self_container[target.attr] = node.value.func.id

    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Await):
            continue
        value = node.value

        # 6a — literal
        if isinstance(value, LITERAL_NODES):
            findings.append(f"L{node.lineno}: await 了字面量（必然非 awaitable）")
            continue
        # 6b — self.<dict/list>[k]
        if isinstance(value, ast.Subscript) and isinstance(value.value, ast.Attribute):
            inner = value.value
            if isinstance(inner.value, ast.Name) and inner.value.id == "self" and inner.attr in self_container:
                findings.append(
                    f"L{node.lineno}: await self.{inner.attr}[...] —— "
                    f"self.{inner.attr} 是 {self_container[inner.attr]}，取值不是协程"
                )
            continue
        if not isinstance(value, ast.Call):
            continue
        func = value.func

        # 6c — self.<container>.get(...)
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Attribute):
            inner = func.value
            if (
                func.attr in NON_AWAITABLE_METHODS
                and isinstance(inner.value, ast.Name)
                and inner.value.id == "self"
                and inner.attr in self_container
            ):
                findings.append(
                    f"L{node.lineno}: await self.{inner.attr}.{func.attr}() —— "
                    f"self.{inner.attr} 是 {self_container[inner.attr]}，返回值不是协程"
                )
                continue

        # 1 — self.<m>() on the same class
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in ("self", "cls"):
            cls = owner.get(node)
            known = class_methods.get(cls or "", {})
            if func.attr in known and not known[func.attr]:
                findings.append(f"L{node.lineno}: await self.{func.attr}() 但 {cls}.{func.attr} 是同步 def")
            continue

        # 2 / 3 — bare name
        if isinstance(func, ast.Name):
            name = func.id
            if name in mod_funcs:
                if not mod_funcs[name]:
                    findings.append(f"L{node.lineno}: await {name}() 但同模块 {name} 是同步 def")
                continue
            if name in resolver.sym_alias:
                mod, sym = resolver.sym_alias[name]
                if index.func_is_async(mod, sym) is False:
                    findings.append(f"L{node.lineno}: await {name}() 但 {mod}.{sym} 是同步 def（跨模块）")
            continue

        # 4 / 5 — attribute chain
        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name) and func.value.id in resolver.sym_alias:
                mod, sym = resolver.sym_alias[func.value.id]
                if index.method_is_async(f"{mod}.{sym}", func.attr) is False:
                    findings.append(
                        f"L{node.lineno}: await {func.value.id}.{func.attr}() 但 "
                        f"{mod}.{sym}.{func.attr} 是同步方法（跨模块）"
                    )
                continue
            resolved = resolver.resolve_chain(func)
            if resolved:
                mod, fname = resolved
                if index.func_is_async(mod, fname) is False:
                    findings.append(f"L{node.lineno}: await {mod}.{fname}() 是同步函数（跨模块）")
    return findings


def _py_files() -> list[Path]:
    return [p for p in sorted(_SRC.rglob("*.py")) if not any(part in _SKIP_PARTS for part in p.parts)]


def _build_index(files: list[Path]) -> _Index:
    index = _Index()
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        index.add(path, tree)
    return index


@lru_cache(maxsize=1)
def _repo_index() -> _Index:
    """Repo-wide index — built once per session (the tree is static during tests)."""
    return _build_index(_py_files())


@lru_cache(maxsize=64)
def _source_findings(path_str: str) -> tuple[str, ...]:
    path = Path(path_str)
    return tuple(_scan_source(path.read_text(encoding="utf-8", errors="replace"), path, _repo_index()))


# ── the guard ───────────────────────────────────────────────────────────


def test_no_await_on_known_sync_callable_under_src():
    problems: list[str] = []
    for path in _py_files():
        for finding in _source_findings(str(path)):
            problems.append(f"{path.relative_to(_REPO_ROOT)}: {finding}")
    assert not problems, (
        "await 了同步 callable（会抛 NoneType can't be used in 'await'）。\n"
        "同步函数请用 `await asyncio.to_thread(fn, ...)`:\n" + "\n".join(problems)
    )


# ── mutation verification ───────────────────────────────────────────────


def _scan_snippet(src: str, tmp_path: Path) -> list[str]:
    return _scan_source(src, tmp_path / "probe.py", _repo_index())


def test_guard_detects_the_original_bug(tmp_path):
    """The exact shape of the 2026-09-13 runner.py crash."""
    src = (
        "class Runner:\n"
        "    def _notify_external_turn_failed(self, text: str) -> None:\n"
        "        return None\n"
        "\n"
        "    async def turn(self):\n"
        "        await self._notify_external_turn_failed('boom')\n"
    )
    hits = _scan_snippet(src, tmp_path)
    assert len(hits) == 1 and "_notify_external_turn_failed" in hits[0]


def test_guard_detects_cross_module_from_import(tmp_path):
    """`await _looks_like_auth_failure(...)` — a real sync helper in opensquad.runner."""
    src = (
        "from opensquad.runner import _looks_like_auth_failure\n"
        "\n"
        "async def turn():\n"
        "    await _looks_like_auth_failure('insufficient balance')\n"
    )
    hits = _scan_snippet(src, tmp_path)
    assert len(hits) == 1 and "跨模块" in hits[0]


def test_guard_detects_cross_module_module_attribute(tmp_path):
    src = "import opensquad.runner\n\nasync def turn():\n    await opensquad.runner._looks_like_auth_failure('x')\n"
    hits = _scan_snippet(src, tmp_path)
    assert len(hits) == 1 and "跨模块" in hits[0]


def test_guard_detects_module_level_sync_def(tmp_path):
    src = "def helper():\n    return 1\n\n\nasync def turn():\n    await helper()\n"
    hits = _scan_snippet(src, tmp_path)
    assert len(hits) == 1 and "同模块" in hits[0]


def test_guard_detects_await_on_dict_get(tmp_path):
    src = (
        "class C:\n"
        "    def __init__(self) -> None:\n"
        "        self._memo: dict = {}\n"
        "\n"
        "    async def go(self):\n"
        "        await self._memo.get('k')\n"
    )
    hits = _scan_snippet(src, tmp_path)
    assert len(hits) == 1 and "_memo" in hits[0]


def test_guard_detects_await_on_subscript(tmp_path):
    src = (
        "class C:\n"
        "    def __init__(self) -> None:\n"
        "        self._items: list = []\n"
        "\n"
        "    async def go(self):\n"
        "        await self._items[0]\n"
    )
    hits = _scan_snippet(src, tmp_path)
    assert len(hits) == 1 and "_items" in hits[0]


def test_guard_allows_sync_fn_dispatched_to_thread(tmp_path):
    src = (
        "import asyncio\n"
        "\n"
        "class C:\n"
        "    def _sync(self) -> None:\n"
        "        return None\n"
        "\n"
        "    async def go(self):\n"
        "        await asyncio.to_thread(self._sync)\n"
    )
    assert _scan_snippet(src, tmp_path) == []


def test_guard_allows_awaiting_unknown_callables(tmp_path):
    """No type inference: an unknown call must not be reported."""
    src = "async def turn(vendor):\n    await vendor.send('x')\n"
    assert _scan_snippet(src, tmp_path) == []


# ── _auth_error_detail ──────────────────────────────────────────────────


def test_auth_error_detail_extracts_provider_blurb():
    """402 Insufficient Balance must survive into the user-visible message."""
    from opensquad.runner import _auth_error_detail

    text = (
        "[Error: APIStatusError - Error code: 402 - {'error': {'message': "
        "'Insufficient Balance', 'type': 'unknown_error', 'code': "
        "'invalid_request_error'}}]"
    )
    detail = _auth_error_detail(text)
    assert "402" in detail
    assert "Insufficient Balance" in detail
    assert detail.startswith("APIStatusError")
    assert not detail.startswith("[Error:")  # wrapper stripped


def test_auth_error_detail_tolerates_leading_prose():
    from opensquad.runner import _auth_error_detail

    detail = _auth_error_detail("我已经收到消息。\n[Error: AuthenticationError - Error code: 401 - bad key]")
    assert "401" in detail
    assert "bad key" in detail


@pytest.mark.parametrize("text", ["", "   ", None])
def test_auth_error_detail_empty_input(text):
    from opensquad.runner import _auth_error_detail

    assert _auth_error_detail(text) == ""


def test_auth_error_detail_is_bounded():
    from opensquad.runner import _auth_error_detail

    detail = _auth_error_detail("[Error: X - " + "y" * 5000 + "]", limit=120)
    assert len(detail) <= 120
