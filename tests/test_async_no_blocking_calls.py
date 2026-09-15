"""Regression lock: no blocking sync calls inside `async def` bodies.

The gateway is a single-event-loop FastAPI app. A blocking call in an async
handler freezes **every** WS push and API request for the duration, not just the
request that made it. Real incidents this locks down:

  * `audio/stepfun_asr.transcribe_file` ran ffmpeg via `subprocess.run(timeout=60)`
    → worst case a 60s freeze (covered separately by
    `tests/test_asr_event_loop_not_blocked.py`, which measures it for real).
  * `routes/_market.py` `market_uninstall_plugin` called `shutil.rmtree` inline —
    a plugin carrying node_modules/.git is tens of thousands of files.
  * `routes/_market.py` `market_install_plugin` / `market_install_skill` and
    `api.py` `upload_folder_as_zip` did full zip extract / DEFLATE compress
    inline (the upload path allows 50MB).

All of the above now go through `await asyncio.to_thread(...)`.

This is a static check (fast, no event loop needed); the measured counterpart is
the ASR test. Mutation-verified below so it cannot silently pass on nothing.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
_SKIP_PARTS = {"build", "node_modules", ".venv", "_internal", "__pycache__", ".mypy_cache"}

# Calls that can block for a long time (disk-synchronous work, processes, clock).
BLOCKING_CALLS = frozenset(
    {
        "subprocess.run",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "subprocess.Popen",
        "subprocess.getoutput",
        "os.system",
        "os.popen",
        "time.sleep",
        "shutil.rmtree",
        "shutil.copytree",
        "shutil.move",
        "shutil.make_archive",
        "zipfile.ZipFile",
        "tarfile.open",
        "urllib.request.urlopen",
        "urllib.request.urlretrieve",
        "input",
    }
)

# (path suffix, callable) pairs that are knowingly allowed.
ALLOWLIST = {
    # Manual interactive CLI client for the external_api plugin: blocking on
    # stdin is the point, it is not part of the gateway event loop.
    ("plugins/external_api/test_client.py", "input"),
}


def _dotted(node: ast.AST) -> str:
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        parts.reverse()
        return ".".join(parts)
    return ""


def _async_scope_nodes(func: ast.AsyncFunctionDef) -> list[ast.AST]:
    """Nodes belonging to *func*'s own scope.

    Nested function scopes are excluded on purpose: a nested sync `def` is the
    normal shape of `await asyncio.to_thread(fn)` (see `ai_web/builder.py`), and
    counting its body would be a false positive.
    """
    nested: set[ast.AST] = set()
    for stmt in func.body:
        for inner in ast.walk(stmt):
            if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                for x in ast.walk(inner):
                    nested.add(x)
    out: list[ast.AST] = []
    for stmt in func.body:
        for inner in ast.walk(stmt):
            if inner not in nested:
                out.append(inner)
    return out


def _find_blocking_in_async(src: str) -> list[tuple[str, int]]:
    """Return [(func_name, lineno)] for blocking calls in async bodies."""
    tree = ast.parse(src)

    alias: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                alias[a.asname or a.name] = f"{node.module}.{a.name}"
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    alias[a.asname] = a.name

    hits: list[tuple[str, int]] = []
    for func in ast.walk(tree):
        if not isinstance(func, ast.AsyncFunctionDef):
            continue
        for node in _async_scope_nodes(func):
            if not isinstance(node, ast.Call):
                continue
            name = _dotted(node.func)
            resolved = alias.get(name, name)
            if resolved in BLOCKING_CALLS or name in BLOCKING_CALLS:
                # Report the resolved name so an aliased import still names the
                # real callable (e.g. `rmtree` -> `shutil.rmtree`).
                hits.append((resolved if resolved in BLOCKING_CALLS else name, node.lineno))
    return hits


def _scan_repo() -> list[str]:
    problems: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        if any(part in _SKIP_PARTS for part in path.parts):
            continue
        src = path.read_text(encoding="utf-8", errors="replace")
        try:
            hits = _find_blocking_in_async(src)
        except SyntaxError:
            continue
        for name, lineno in hits:
            rel = path.relative_to(_SRC.parent).as_posix()
            if any(rel.endswith(suffix) and name == allowed for suffix, allowed in ALLOWLIST):
                continue
            problems.append(f"{rel}:{lineno}: async 体内同步阻塞调用 {name}()")
    return problems


def test_no_blocking_calls_in_async_bodies():
    problems = _scan_repo()
    assert not problems, (
        "async 体内的同步阻塞调用会冻结整个事件循环（所有 WS 推送 + API 请求）。"
        "改用 `await asyncio.to_thread(fn, ...)`:\n" + "\n".join(problems)
    )


def test_guard_detects_inline_rmtree():
    """Mutation check — reproduces the 2026-09-13 _market.py bug shape."""
    broken = "import shutil\n\nasync def handler():\n    shutil.rmtree('/tmp/x', onerror=None)\n"
    hits = _find_blocking_in_async(broken)
    assert [h[0] for h in hits] == ["shutil.rmtree"], hits


def test_guard_detects_from_import_alias():
    broken = "from shutil import rmtree\n\nasync def handler():\n    rmtree('/tmp/x')\n"
    assert [h[0] for h in _find_blocking_in_async(broken)] == ["shutil.rmtree"]


def test_guard_allows_nested_sync_fn_dispatched_to_thread():
    """The documented-correct shape must not be flagged."""
    fixed = (
        "import asyncio, subprocess\n"
        "\n"
        "async def handler():\n"
        "    def _probe():\n"
        "        return subprocess.run(['node', '--version'])\n"
        "    return await asyncio.to_thread(_probe)\n"
    )
    assert _find_blocking_in_async(fixed) == []


def test_guard_allows_module_level_sync_helper():
    """Blocking work in a plain (non-async) helper is not this guard's business."""
    ok = "import time\n\ndef helper():\n    time.sleep(1)\n"
    assert _find_blocking_in_async(ok) == []
