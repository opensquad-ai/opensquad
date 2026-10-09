"""Bidirectional NDJSON client for the mod host — docs/mods-bridge-m0.md §2.

One pipe carries both directions, so the two directions share one protocol. The
discriminator is the id space:

* we send ids ``p<n>``; the host sends ``h<n>``;
* a frame whose id sits in our pending table is a **response**;
* a frame carrying ``method`` is an inbound **request**.

That single rule is what makes reentrancy work with no extra machinery: while we
serve an inbound request, our own outstanding requests stay in the pending table.

The 8s default budget sits *below* the framework's 10s hook budget
(``plugins/plugin_manager.py:_HOOK_HANDLER_TIMEOUT``) so the bridge gives up
before ``asyncio.wait_for`` yanks the handler. A late reply then arrives for an
id nobody is waiting on and is dropped with a warning rather than treated as a
protocol error.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import threading
from typing import Any, Callable

from . import _node

_log = logging.getLogger("plugins.mods_host.rpc")

DEFAULT_TIMEOUT = 8.0


class NodeHostError(RuntimeError):
    """Base class for mod-host failures."""


class NodeHostUnavailableError(NodeHostError):
    """The host is not running, or the pipe is closed."""


class NodeHostTimeoutError(NodeHostError):
    """A request outlived its budget; its late reply will be dropped."""


class NodeHostClient:
    def __init__(
        self,
        *,
        request_handler: Callable[[str, dict], Any] | None = None,
        cwd: str | None = None,
    ) -> None:
        self._request_handler = request_handler
        # A mod using a relative path must not land wherever the agent process
        # happened to be started (a probe mod's relative write ended up in the
        # repo root). Pin it, and say so when nobody pinned it.
        self._cwd = os.path.abspath(cwd) if cwd else ""
        self._proc: Any = None
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._tasks: set[asyncio.Task] = set()
        self._seq = 0
        self._start_lock = asyncio.Lock()
        # Bumped on every successful spawn. Callers that cache per-host state
        # (the plugin caches the `init` handshake) must key it on this: the
        # client object survives a restart, so object identity is not enough.
        self._generation = 0

    # ── lifecycle ────────────────────────────────────────────────────

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    @property
    def generation(self) -> int:
        return self._generation

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    async def start(self) -> None:
        """Start the host if it is not already running. Idempotent."""
        if self.is_running():
            return
        async with self._start_lock:
            if self.is_running():
                return
            node_exe = _node.resolve_node_executable()
            if not node_exe:
                raise NodeHostUnavailableError("node runtime not found — install Node.js or set OPENSQUAD_NODE")
            cwd = self._cwd or os.path.abspath(".")
            if not self._cwd:
                _log.warning(
                    "[mods_host] no cwd pinned; the host inherits %s — pass the workspace so "
                    "mods using relative paths are predictable",
                    cwd,
                )
            # Spawning is blocking (Popen); hop a thread so the agent's event
            # loop is never frozen by it.
            proc = await asyncio.to_thread(_node.spawn_host, node_exe, cwd=cwd, env=None)
            self._proc = proc
            self._generation += 1
            self._loop = asyncio.get_running_loop()
            self._reader = threading.Thread(
                target=self._read_stdout, args=(proc,), daemon=True, name="mods-host-stdout"
            )
            self._reader.start()
            self._stderr_reader = threading.Thread(
                target=self._read_stderr, args=(proc,), daemon=True, name="mods-host-stderr"
            )
            self._stderr_reader.start()
            _log.info("[mods_host] node host started (pid=%s, node=%s)", proc.pid, node_exe)

    def close_sync(self, *, grace: float = 2.0) -> None:
        """Reap the host. Safe from sync code (``Plugin.on_unload``).

        Two independent reaping paths exist on purpose: closing stdin makes the
        host exit on EOF, and this method then waits/kills. Either one alone
        leaves no orphan.
        """
        proc = self._proc
        self._proc = None
        if proc is not None:
            with contextlib.suppress(Exception):
                if proc.stdin is not None:
                    proc.stdin.close()
            try:
                proc.wait(timeout=grace)
            except Exception:
                with contextlib.suppress(Exception):
                    proc.kill()
                with contextlib.suppress(Exception):
                    proc.wait(timeout=grace)
            _log.info("[mods_host] node host reaped (rc=%s)", proc.poll())
        self._fail_pending("mod host closed")
        if self._reader is not None:
            self._reader.join(timeout=1.0)
            self._reader = None

    async def close(self, *, grace: float = 2.0) -> None:
        await asyncio.to_thread(self.close_sync, grace=grace)

    # ── requests ─────────────────────────────────────────────────────

    async def request(self, method: str, params: dict | None = None, *, timeout: float = DEFAULT_TIMEOUT) -> Any:
        await self.start()
        loop = self._loop
        if loop is None:
            raise NodeHostUnavailableError("mod host loop missing")
        self._seq += 1
        rid = f"p{self._seq}"
        fut: asyncio.Future = loop.create_future()
        self._pending[rid] = fut
        try:
            self._send({"id": rid, "method": method, "params": params or {}})
        except NodeHostUnavailableError:
            self._pending.pop(rid, None)
            raise
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            raise NodeHostTimeoutError(f"{method} timed out after {timeout}s") from None

    def send_notification(self, method: str, params: dict | None = None) -> None:
        """Fire-and-forget: runs the host's handler, expects no reply.

        Used from synchronous code (``on_unload``) where awaiting is impossible.
        """
        self._send({"method": method, "params": params or {}})

    def _send(self, msg: dict) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.poll() is not None:
            raise NodeHostUnavailableError("mod host is not running")
        try:
            proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, ValueError, OSError) as exc:
            raise NodeHostUnavailableError(f"mod host pipe closed: {exc}") from exc

    # ── reader threads (sync: never touch the loop directly) ─────────

    def _read_stdout(self, proc: Any) -> None:
        stream = proc.stdout
        if stream is None:
            return
        for line in stream:
            loop = self._loop
            if loop is None or loop.is_closed():
                break
            try:
                loop.call_soon_threadsafe(self._on_frame, line)
            except RuntimeError:
                break
        loop = self._loop
        if loop is not None and not loop.is_closed():
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(self._on_eof)

    def _read_stderr(self, proc: Any) -> None:
        stream = proc.stderr
        if stream is None:
            return
        for line in stream:
            text = line.rstrip()
            if text:
                _log.debug("[mods_host:stderr] %s", text)

    # ── frame dispatch (runs on the loop thread) ────────────────────

    def _on_frame(self, line: str) -> None:
        text = line.strip()
        if not text:
            return
        try:
            msg = json.loads(text)
        except ValueError:
            _log.warning("[mods_host] ignoring non-JSON frame: %r", text[:200])
            return
        if not isinstance(msg, dict):
            return

        mid = msg.get("id")

        # Order matters. Check `method` *before* crying "stale reply": an inbound
        # request carries an id from the other side's id space, so it is
        # guaranteed to be absent from our pending table. Treating that as a late
        # reply silently drops every reentrant call and the outer request then
        # times out (found by the smoke test — see tests/test_mods_host_rpc.py).
        method = msg.get("method")
        if method:
            if self._loop is not None:
                task = self._loop.create_task(self._serve(method, msg.get("params") or {}, mid))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            return

        if mid is not None and mid in self._pending:
            fut = self._pending.pop(mid)
            if not fut.done():
                error = msg.get("error")
                if error:
                    fut.set_exception(NodeHostError(str(error.get("message") or error)))
                else:
                    fut.set_result(msg.get("result"))
            return

        if mid is not None:
            _log.warning("[mods_host] dropping late/unmatched reply id=%s", mid)

    async def _serve(self, method: str, params: dict, mid: Any) -> None:
        if self._request_handler is None:
            if mid is not None:
                self._send_safe({"id": mid, "error": {"code": "no_handler", "message": method}})
            return
        try:
            result = self._request_handler(method, params)
            if asyncio.iscoroutine(result):
                result = await result
        except Exception as exc:  # noqa: BLE001 - reported back over the wire
            _log.warning("[mods_host] host request %s failed: %s", method, exc)
            if mid is not None:
                self._send_safe({"id": mid, "error": {"code": "handler_error", "message": str(exc)}})
            return
        if mid is not None:
            self._send_safe({"id": mid, "result": result if result is not None else {}})

    def _send_safe(self, msg: dict) -> None:
        with contextlib.suppress(NodeHostUnavailableError):
            self._send(msg)

    def _on_eof(self) -> None:
        self._fail_pending("mod host exited")

    def _fail_pending(self, reason: str) -> None:
        for rid, fut in list(self._pending.items()):
            self._pending.pop(rid, None)
            exc = NodeHostUnavailableError(reason)
            if self._loop is not None and self._loop.is_running():
                with contextlib.suppress(RuntimeError):
                    self._loop.call_soon_threadsafe(self._set_exc, fut, exc)
            else:
                self._set_exc(fut, exc)

    @staticmethod
    def _set_exc(fut: asyncio.Future, exc: Exception) -> None:
        if not fut.done():
            fut.set_exception(exc)
