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

A second, narrower tier covers inline ``open()``: the cost there is
proportional to the payload, so a user upload write stalls the loop for as long
as the bytes take to reach disk. Large/user-sized sites use
``opensquad.utils.blocking_io``; the reviewed-small ones (few-KB config JSON,
scratch files) are enumerated in ``FILE_IO_ALLOWLIST`` so a new inline open
cannot slip in unnoticed.

This is a static check (fast, no event loop needed); the measured counterpart is
the ASR test. Mutation-verified below so it cannot silently pass on nothing.
"""

from __future__ import annotations

import ast
from collections import Counter
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

# Synchronous disk IO. Separate tier from BLOCKING_CALLS because the cost is
# proportional to the payload rather than to a fixed timeout: a 50MB upload
# write stalls the loop for 50MB worth of disk time. The reviewed-small sites
# (a few-KB config JSON, a registry file, an image-path scratch file) are
# listed in FILE_IO_ALLOWLIST instead of being given a redundant thread hop.
FILE_IO_CALLS = frozenset({"open"})

# (path suffix, function name, mode) -> number of inline opens reviewed as
# bounded/small. Counted on purpose: matching on (path, func, mode) alone would
# let a *second*, possibly large, inline open hide inside an already-reviewed
# function. Adding one therefore fails the guard until it is reviewed.
#
# "Bounded" here means the payload is written by this process from an in-memory
# object it just built (a config dict, a 20-byte timestamp, a path list) — not
# bytes that arrived from a user, the network, or a model. Those go through
# opensquad.utils.blocking_io.
FILE_IO_ALLOWLIST: dict[tuple[str, str, str], int] = {
    # ── agent kernel: per-turn stats / config hot-reload / vision scratch ────
    ("opensquad/_runner/_output_handler.py", "broadcast_token_stats", "w"): 1,
    ("opensquad/_runner/_state_machine.py", "idle_wait", "r"): 1,
    ("opensquad/_runner/_turn_loop.py", "handle_turn_result", "w"): 1,
    ("opensquad/agents_boot.py", "main", "r"): 1,
    ("opensquad/agents_boot.py", "main", "w"): 1,
    ("opensquad/runner.py", "_run_serial", "r"): 2,  # config.json, img_path.txt
    ("opensquad/runner.py", "_run_serial", "w"): 1,  # img_path.txt drain
    # ── agent config persistence (model_switch / gateway_adapter) ────────────
    ("opensquad/model_switch.py", "apply_reasoning_effort", "r"): 1,
    ("opensquad/model_switch.py", "apply_reasoning_effort", "w"): 1,
    ("opensquad/model_switch.py", "apply_agent_mode", "r"): 1,
    ("opensquad/model_switch.py", "apply_agent_mode", "w"): 1,
    ("opensquad/gateway_adapter.py", "_handle_command", "r"): 1,
    ("opensquad/gateway_adapter.py", "_handle_command", "w"): 1,
    # ── plugin discovery ────────────────────────────────────────────────────
    ("plugins/plugin_manager.py", "discover_and_load_async", "r"): 1,
    # ── gateway admin / voice / market endpoints ────────────────────────────
    ("ai_web/routes/_admin.py", "admin_get_system_config", "r"): 1,
    ("ai_web/routes/_admin.py", "admin_update_system_config", "w"): 1,
    ("ai_web/routes/_admin.py", "admin_set_log_level", "r"): 1,
    ("ai_web/routes/_admin.py", "admin_set_log_level", "w"): 1,
    ("ai_web/routes/_main.py", "agent_synthesize_speech", "r"): 1,  # config.json
    ("ai_web/routes/_main.py", "agent_transcribe_audio", "r"): 1,  # config.json
    ("ai_web/routes/_market.py", "market_install_plugin", "r"): 1,  # plugin.json
    ("ai_web/routes/_market.py", "market_install_plugin", "rb"): 1,  # plugin.py
    ("ai_web/routes/_market.py", "market_install_plugin", "w"): 1,  # .reload_ts
    ("ai_web/routes/_market.py", "market_uninstall_plugin", "w"): 1,  # .reload_ts
    ("ai_web/routes/_market.py", "_run_git_install_job", "w"): 1,  # .reload_ts
    ("ai_web/routes/_market.py", "_ai_review_plugin", "r"): 1,  # model card
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


def _find_sync_file_io_in_async(src: str) -> list[tuple[str, str, str]]:
    """Return [(func_name, mode, lineno)] for inline ``open()`` in async bodies.

    Nested sync scopes are excluded by ``_async_scope_nodes``, so the
    documented-correct shape — a module-level or nested plain ``def`` that
    calls ``open`` and is dispatched via ``asyncio.to_thread`` — is not flagged.
    """
    tree = ast.parse(src)
    hits: list[tuple[str, str, str]] = []
    for func in ast.walk(tree):
        if not isinstance(func, ast.AsyncFunctionDef):
            continue
        for node in _async_scope_nodes(func):
            if not isinstance(node, ast.Call):
                continue
            if _dotted(node.func) not in FILE_IO_CALLS:
                continue
            mode = "r"
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                mode = str(node.args[1].value)
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = str(kw.value.value)
            hits.append((func.name, mode, node.lineno))
    return hits


def _file_io_hits() -> list[tuple[str, str, str, int]]:
    """Return [(rel_path, func_name, mode, lineno)] for every inline open()."""
    out: list[tuple[str, str, str, int]] = []
    for path in sorted(_SRC.rglob("*.py")):
        if any(part in _SKIP_PARTS for part in path.parts):
            continue
        src = path.read_text(encoding="utf-8", errors="replace")
        try:
            io_hits = _find_sync_file_io_in_async(src)
        except SyntaxError:
            continue
        rel = path.relative_to(_SRC.parent).as_posix()
        for func_name, mode, lineno in io_hits:
            out.append((rel, func_name, mode, lineno))
    return out


def _reviewed_count(rel: str, func_name: str, mode: str) -> int:
    return sum(
        n for (suffix, fn, m), n in FILE_IO_ALLOWLIST.items() if rel.endswith(suffix) and fn == func_name and m == mode
    )


def _unreviewed_file_io(hits: list[tuple[str, str, str, int]] | None = None) -> list[str]:
    if hits is None:
        hits = _file_io_hits()
    counts = Counter((rel, fn, mode) for rel, fn, mode, _ in hits)
    first_line: dict[tuple[str, str, str], int] = {}
    for rel, fn, mode, lineno in hits:
        first_line.setdefault((rel, fn, mode), lineno)
    out: list[str] = []
    for key in sorted(counts):
        rel, fn, mode = key
        allowed = _reviewed_count(rel, fn, mode)
        if counts[key] > allowed:
            out.append(
                f"{rel}:{first_line[key]}: async 体内同步文件 IO open(mode={mode!r}) @ {fn}()"
                f"  [已登记 {allowed} 处，实际 {counts[key]} 处]"
            )
    return out


def _scan_repo() -> tuple[list[str], list[str]]:
    blocking: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        if any(part in _SKIP_PARTS for part in path.parts):
            continue
        src = path.read_text(encoding="utf-8", errors="replace")
        try:
            hits = _find_blocking_in_async(src)
        except SyntaxError:
            continue
        rel = path.relative_to(_SRC.parent).as_posix()
        for name, lineno in hits:
            if any(rel.endswith(suffix) and name == allowed for suffix, allowed in ALLOWLIST):
                continue
            blocking.append(f"{rel}:{lineno}: async 体内同步阻塞调用 {name}()")
    return blocking, _unreviewed_file_io()


def test_no_blocking_calls_in_async_bodies():
    blocking, _ = _scan_repo()
    assert not blocking, (
        "async 体内的同步阻塞调用会冻结整个事件循环（所有 WS 推送 + API 请求）。"
        "改用 `await asyncio.to_thread(fn, ...)`:\n" + "\n".join(blocking)
    )


def test_no_sync_file_io_in_async_bodies():
    """Inline `open()` on the event loop stalls every WS push while it runs.

    Large/user-sized sites must use ``opensquad.utils.blocking_io`` (which
    dispatches a module-level sync helper through ``asyncio.to_thread``).
    Reviewed-small sites are enumerated in FILE_IO_ALLOWLIST with their mode,
    so a new inline open cannot slip in unnoticed.
    """
    _, file_io = _scan_repo()
    assert not file_io, (
        "async 体内的同步文件 IO 会按载荷大小冻结事件循环。"
        "用户尺寸的载荷请用 `await blocking_io.write_bytes(...)` / `read_bytes(...)`；"
        "确属小文件（几 KB 配置）的请在 FILE_IO_ALLOWLIST 里登记 (路径, 函数, mode):\n" + "\n".join(file_io)
    )


def test_file_io_allowlist_has_no_stale_entries():
    """Every allowlisted entry must still match as many inline opens as reviewed.

    Without this the allowlist rots into a list of names nobody dares delete,
    and a moved/renamed function would silently stop being covered.  An entry
    that matches *fewer* opens than reviewed is stale; one that matches more is
    reported by the guard above (an unreviewed new open).
    """
    counts = Counter((rel, fn, mode) for rel, fn, mode, _ in _file_io_hits())
    stale: dict[tuple[str, str, str], tuple[int, int]] = {}
    for (suffix, fn, mode), n in FILE_IO_ALLOWLIST.items():
        # Resolve the entry the same way the guard does (suffix match against the
        # full rel path), otherwise every key looks stale.
        actual = sum(c for (rel, f, m), c in counts.items() if rel.endswith(suffix) and f == fn and m == mode)
        if actual < n:
            stale[(suffix, fn, mode)] = (n, actual)
    assert not stale, f"FILE_IO_ALLOWLIST 条目已失效 (登记处数, 实际处数): {stale}"


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


def test_file_io_guard_detects_inline_open_with_mode():
    """Mutation check — before 2026-09-15 `open` was not scanned at all."""
    broken = "async def handler():\n    with open('/tmp/x', 'wb') as f:\n        f.write(b'x')\n"
    assert _find_sync_file_io_in_async(broken) == [("handler", "wb", 2)]


def test_file_io_guard_reads_keyword_mode():
    broken = "async def handler():\n    f = open('/tmp/x', mode='rb')\n    f.close()\n"
    assert _find_sync_file_io_in_async(broken) == [("handler", "rb", 2)]


def test_file_io_guard_defaults_to_read_mode():
    broken = "async def handler():\n    with open('/tmp/x') as f:\n        return f.read()\n"
    assert _find_sync_file_io_in_async(broken) == [("handler", "r", 2)]


def test_file_io_guard_allows_nested_sync_helper_dispatched_to_thread():
    """The documented fix shape must stay clean."""
    fixed = (
        "import asyncio\n"
        "\n"
        "def _write(path, data):\n"
        "    with open(path, 'wb') as f:\n"
        "        f.write(data)\n"
        "\n"
        "async def handler():\n"
        "    await asyncio.to_thread(_write, '/tmp/x', b'y')\n"
    )
    assert _find_sync_file_io_in_async(fixed) == []


def test_file_io_guard_ignores_sync_functions():
    ok = "def helper():\n    with open('/tmp/x') as f:\n        return f.read()\n"
    assert _find_sync_file_io_in_async(ok) == []


def test_count_aware_allowlist_catches_a_second_open_in_a_reviewed_function():
    """The allowlist is counted, not just matched.

    `runner.py:_run_serial` is reviewed with two `r` opens. A third one slipping
    into the same function must be reported, even though the (path, func, mode)
    key already exists.
    """
    reviewed = ("src/opensquad/runner.py", "_run_serial", "r")
    assert FILE_IO_ALLOWLIST[("opensquad/runner.py", "_run_serial", "r")] == 2

    at_limit = [(reviewed[0], reviewed[1], "r", 10), (reviewed[0], reviewed[1], "r", 20)]
    assert _unreviewed_file_io(at_limit) == []

    one_extra = [*at_limit, (reviewed[0], reviewed[1], "r", 30)]
    reported = _unreviewed_file_io(one_extra)
    assert len(reported) == 1 and "实际 3 处" in reported[0], reported


def test_new_unreviewed_function_is_reported():
    """A brand-new inline open in an unreviewed function must be reported."""
    hits = [("src/opensquad/newmod.py", "fresh_handler", "wb", 7)]
    reported = _unreviewed_file_io(hits)
    assert len(reported) == 1 and "已登记 0 处" in reported[0], reported
