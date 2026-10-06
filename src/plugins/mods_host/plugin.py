"""mods_host — runs Claude Code Mods in a Node child process.

M0 scope: host lifecycle + the ``tool.call`` deny path. Design:
docs/mods-bridge-m0.md.

Two properties matter for blast radius, because ``plugin.json`` declares a hook
and therefore this module is imported on **every** agent boot:

* imports are stdlib + ``opensquad`` only;
* nothing starts a process at import or ``on_load`` time — the host is lazy
  (``on_load`` is synchronous, and spawning would block the caller).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from typing import Any

from opensquad.plugin_api import Context, Plugin, hook, register

from . import _node
from ._rpc import NodeHostClient, NodeHostError, NodeHostTimeoutError

logger = logging.getLogger("plugins.mods_host")

# Verdict keys the reference documents as "refuse the call". `{result}` /
# `{value}` are deliberately NOT acted on in M0 (deny-only scope) — they are
# logged rather than silently swallowed, because a mod author debugging a
# no-op needs to see why it did nothing.
_DENY_KEYS = ("deny", "refuse", "drop", "skip")

# Matrix §1.5: `$.fs` is capped at 4 MiB per call.
_FS_READ_LIMIT = 4 * 1024 * 1024


def _confined_path(raw: str, root: str) -> str:
    """Resolve ``raw`` and refuse anything outside ``root`` (sync: thread-hopped).

    Confinement is the whole point of routing `$` through Python: the mod hands
    over a path, it never gets a handle. Per-mod authorization is still missing
    (M1) — everything enabled shares this one read-only cone.
    """
    if not raw:
        raise ValueError("empty path")
    candidate = os.path.realpath(os.path.abspath(raw if os.path.isabs(raw) else os.path.join(root, raw)))
    base = os.path.realpath(root)
    if candidate != base and not candidate.startswith(base + os.sep):
        raise ValueError(f"path outside the workspace: {raw}")
    return candidate


def _read_text_capped(path: str, limit: int) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:  # noqa: PTH123 - capped read
        return fh.read(limit)


# Matrix §1.5 caps `$.fs` per call; a directory listing needs its own bound.
_FS_LIST_LIMIT = 500

# Matrix §1.5: `$.process.run` defaults to 30s, hard cap 10 minutes.
_PROCESS_DEFAULT_SECONDS = 30.0
_PROCESS_MAX_SECONDS = 600.0
# Per-stream capture bound, so a chatty command cannot balloon the bridge frame.
_PROCESS_OUTPUT_LIMIT = 64 * 1024


def _write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:  # noqa: PTH123 - capped at the call site
        fh.write(text)


def _run_argv_capped(argv: list[str], timeout: float) -> dict[str, Any]:
    """Run an argv list (never a shell string) with a wall-clock cap."""
    import subprocess

    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return {"error": f"命令超过 {timeout:.0f}s 上限"}
    except OSError as exc:
        return {"error": f"无法执行：{exc}"}
    return {
        "code": done.returncode,
        "stdout": (done.stdout or "")[:_PROCESS_OUTPUT_LIMIT],
        "stderr": (done.stderr or "")[:_PROCESS_OUTPUT_LIMIT],
    }


def _url_host(url: str) -> str:
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return ""
    return parsed.hostname.lower()


def _http_fetch_capped(url: str, method: str, body: Any, limit: int) -> dict[str, Any]:
    """One request, capped response. `body` may be a string or None."""
    import urllib.request

    data = None
    if isinstance(body, str) and body:
        data = body.encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method if method in ("GET", "POST") else "GET")
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 - domain allowlisted above
        payload = response.read(limit + 1)
        return {
            "status": int(getattr(response, "status", 0) or 0),
            "text": payload[:limit].decode("utf-8", errors="replace"),
            "truncated": len(payload) > limit,
        }


def _list_dir_capped(path: str, limit: int) -> list[dict[str, Any]] | None:
    """One level, capped. `None` when the path is not a directory."""
    if not os.path.isdir(path):
        return None
    out: list[dict[str, Any]] = []
    for index, name in enumerate(sorted(os.listdir(path))):
        if index >= limit:
            break
        full = os.path.join(path, name)
        is_dir = os.path.isdir(full)
        out.append({"name": name, "type": "dir" if is_dir else "file", "size": 0 if is_dir else _safe_size(full)})
    return out


def _safe_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _stat_path(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {"exists": False}
    is_dir = os.path.isdir(path)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0.0
    return {
        "exists": True,
        "type": "dir" if is_dir else "file",
        "size": 0 if is_dir else _safe_size(path),
        "mtime": mtime,
    }


@register(
    name="mods_host",
    author="OpenSquad",
    description="Runs Claude Code Mods in a Node child process and bridges their hooks.",
    version="0.1.0",
    plugin_type="hook",
    display_name="Claude Code Mods Host",
    tags=["mods", "claude-code", "bridge"],
)
class ModsHostPlugin(Plugin):
    def __init__(self, context: Context):
        super().__init__(context)
        self._client: NodeHostClient | None = None
        self._initialized = False
        self._init_generation = -1
        # Enabled mods handed to the host by `init`. Empty == bridge is inert.
        self._mods: list[dict[str, Any]] = []
        # Last `init` reply — the per-mod load diagnostics. Kept for the Mods
        # page (S3) and for the smoke script.
        self._last_init: dict[str, Any] = {}
        # Successful host→Python gate calls, by method. This is the evidence
        # that a mod's reentrant call actually completed (as opposed to being
        # swallowed by the mod's own catch).
        self._gate_calls: dict[str, int] = {}
        self._last_failure: str = ""
        # Last pushed tree per slot, so a turn boundary that changes nothing does
        # not spam the UI with identical frames.
        self._last_slot_payload: dict[str, str] = {}
        # Session facts, learned from the hook context (`sid` was added to the
        # task hooks for exactly this). `$.session.id` reads it back.
        self._session_id: str = ""
        self._session_started_for: int = -1
        self._turn_step: int = 0
        # Commands each mod contributed, so they can be withdrawn on unload.
        self._mod_commands: dict[str, set[str]] = {}

    # ── lifecycle ────────────────────────────────────────────────────

    def on_load(self) -> None:
        try:
            self._mods = self._load_mods()
        except Exception as exc:  # noqa: BLE001 - a broken workspace must not break boot
            logger.warning("[mods_host] mod discovery failed: %s", exc)
            self._mods = []
        bus = getattr(self.context, "event_bus", None)
        if bus is not None:
            try:
                # Button presses come back through the gateway's generic command
                # channel → adapter → this bus topic. An async subscriber is
                # scheduled by the bus itself (see EventBus.emit).
                bus.subscribe("mod_action", self._on_mod_action)
                # A user-invoked mod slash command arrives the same way.
                bus.subscribe("mod_command", self._on_mod_command)
            except Exception as exc:  # noqa: BLE001 - hooks must not break boot
                logger.warning("[mods_host] could not subscribe to mod_action: %s", exc)
        logger.info("[mods_host] loaded (host lazy, %d enabled mod(s))", len(self._mods))

        # Warm the host in the background when there is something to serve. Mods
        # register their slash commands on `session.start`, so with a cold host
        # the *first* message after an agent restart cannot be a mod command —
        # measured on the live agent: `/azioni` reached the model because the
        # host only started on the first hook that needed it.
        if self._mods:
            try:
                import asyncio as _asyncio

                _asyncio.get_running_loop().create_task(self._warm())
            except RuntimeError:
                pass  # no running loop (import path) — the first hook will start it

    async def _warm(self) -> None:
        """Start the host and run its handshake, off the boot path."""
        try:
            client = await self._ensure_client()
            await self._ensure_init(client)
            self._announce_commands()
        except Exception as exc:  # noqa: BLE001 - warming must never break boot
            logger.debug("[mods_host] host warm-up skipped: %s", exc)

    async def _on_mod_action(self, payload: dict) -> None:
        """Run a button press the host hoisted, then refresh the band."""
        action = str((payload or {}).get("action") or "")
        if not action or not self._mods:
            return
        try:
            await self.invoke_action(action)
        except (NodeHostError, NodeHostTimeoutError) as exc:
            self._degrade(f"action {action}: {exc}")
        # The handler usually changed the mod's state, and asked for a redraw.
        sid = str((payload or {}).get("sid") or "")
        await self._push_slot(sid=sid)
        await self._push_panes(sid)

    def on_unload(self) -> None:
        # Withdraw the mods' commands: a registry entry outliving its mod would
        # leave the CLI offering commands nothing can run.
        if self._mod_commands:
            with contextlib.suppress(Exception):
                from opensquad.cli import slash_commands

                for mod in list(self._mod_commands):
                    slash_commands.clear_runtime_commands(mod)
            self._mod_commands.clear()
            self._announce_commands()
        if self._client is not None:
            # Fire-and-forget: `on_unload` is synchronous, so this cannot be a
            # request. A notification (no id) runs the mod's handler and is not
            # answered — see the host's `respond`.
            with contextlib.suppress(Exception):
                self._client.send_notification("session.end", {"agent_id": self._agent_id()})
            self._client.close_sync()
            self._client = None
        self._initialized = False
        if self._mods:
            self._write_journal("stopped")

    # ── observation journal ──────────────────────────────────────────

    def _write_journal(self, state: str, *, failure: str = "") -> None:
        """Leave the gateway a readable trace of what this agent's host observed.

        The host lives here, in the agent process, so the Mods page cannot probe
        it. Without this, fail-open would be invisible — which is the one thing
        that makes fail-open unsafe.
        """
        try:
            from opensquad import mods_compat

            mods_compat.write_host_status(
                str(getattr(self.context, "agent_id", "") or ""),
                state=state,
                node=_node.resolve_node_executable(),
                pid=self._client.pid if self._client is not None else None,
                mods=len(self._mods),
                inert=sum(len(m.get("inert") or []) for m in self._mods),
                last_failure=failure,
            )
        except Exception as exc:  # noqa: BLE001 - observation must never break an agent
            logger.debug("[mods_host] journal write failed: %s", exc)

    def _degrade(self, reason: str) -> None:
        self._last_failure = reason
        logger.warning("[mods_host] degraded (fail-open, calls still go through): %s", reason)
        self._write_journal("degraded", failure=reason)

    # ── mod discovery ────────────────────────────────────────────────

    def _load_mods(self, root: str | None = None, state_root: str | None = None) -> list[dict[str, Any]]:
        """Enabled mods to hand to the host — **per-contribution**, with gaps named.

        A mod declaring capabilities we refuse still loads: the refused parts are
        inert because we never emit their events and never provide their ``$``
        (see ``mods_compat.load_plan``). Only "nothing loadable" is fatal. The
        gaps are reported here, at load time, per matrix §7.
        """
        from opensquad import mods_compat

        out: list[dict[str, Any]] = []
        for info in mods_compat.discover_mods(root, state_root):
            name = info.get("name") or info.get("dir_name") or "?"
            if not (info.get("state") or {}).get("enabled"):
                continue
            plan = mods_compat.load_plan(info)
            if not plan["loadable"]:
                logger.warning("[mods_host] mod '%s' enabled but not loadable: %s", name, plan["reason"])
                continue
            inert = [str(b.get("name", "?")) for b in plan["inert"]]
            if inert:
                logger.warning(
                    "[mods_host] mod '%s' loading per-contribution; these stay inert: %s", name, ", ".join(inert)
                )
            out.append(
                {
                    "name": name,
                    "dir_name": info["dir_name"],
                    "root": info["dir"],
                    "modules": plan["modules"],
                    "inert": inert,
                }
            )
        return out

    # ── host access ──────────────────────────────────────────────────

    async def _ensure_client(self) -> NodeHostClient:
        if self._client is None:
            self._client = NodeHostClient(request_handler=self._on_host_request, cwd=self._workspace())
        await self._client.start()
        self._write_journal("running")
        return self._client

    async def _ensure_init(self, client: NodeHostClient) -> None:
        # A restarted host is a *different* process with none of the mods loaded,
        # so the handshake is keyed on the host generation, not the client object.
        if self._initialized and client.generation == self._init_generation and client.is_running():
            return
        result = await client.request("init", {"mods": self._mods, "apiVersion": 1})
        self._initialized = True
        self._init_generation = client.generation
        self._last_init = result
        # `session.start`, once per host: we are agent-scoped, not session-scoped
        # (matrix §3 rates this degraded for exactly that reason).
        if self._session_started_for != client.generation:
            self._session_started_for = client.generation
            self._turn_step = 0
            with contextlib.suppress(NodeHostError, NodeHostTimeoutError):
                await client.request(
                    "session.start",
                    {"agent_id": self._agent_id(), "sid": self._session_id},
                    timeout=5.0,
                )
        for diag in result.get("diagnostics") or []:
            logger.warning("[mods_host] %s: %s — %s", diag.get("mod"), diag.get("kind"), diag.get("detail"))
        logger.info(
            "[mods_host] host initialised: %s handler(s) across %s event(s), %s diagnostic(s)",
            result.get("registered"),
            len(result.get("events") or []),
            len(result.get("diagnostics") or []),
        )

    async def ping(self) -> dict[str, Any]:
        """Probe the host. Used by Mods 页 status (S3) and by tests."""
        client = await self._ensure_client()
        return await client.request("ping", timeout=5.0)

    async def render_slot(self, slot: str = "AbovePrompt", **extra: Any) -> dict[str, Any]:
        """Ask the enabled mods to draw one slot; validate whatever comes back.

        The mod declares; the host decides what is renderable. Anything outside
        the element whitelist is dropped with a diagnostic rather than blanking
        the surface.
        """
        from opensquad import mods_compat

        client = await self._ensure_client()
        await self._ensure_init(client)
        result = await client.request("ui.render", {"component": slot, **extra}, timeout=5.0)
        nodes = list(result.get("nodes") or [])
        validated = [mods_compat.validate_element_tree(node) for node in nodes]
        dropped = [note for check in validated for note in check["dropped"]]
        if dropped:
            logger.info("[mods_host] slot %s dropped %d element(s): %s", slot, len(dropped), "; ".join(dropped))
        return {
            "slot": slot,
            "nodes": [check["node"] for check in validated if check["node"] is not None],
            "dropped": dropped,
            "invalidations": result.get("invalidations", 0),
        }

    async def _push_panes(self, sid: str = "") -> None:
        """Render every pane a mod asked the surface to place.

        Panes are dynamic (`$.ui.open({id})`), so they cannot live in the static
        pane registry — the host collects the open ones and we render each by id.
        """
        if not self._mods:
            return
        try:
            client = await self._ensure_client()
            await self._ensure_init(client)
            listed = await client.request("pane.list", {}, timeout=5.0)
        except (NodeHostError, NodeHostTimeoutError) as exc:
            self._degrade(f"pane.list: {exc}")
            return
        for pane in listed.get("panes") or []:
            pane_id = str((pane or {}).get("id") or "")
            if pane_id:
                await self._push_slot("Pane", sid, pane_id=pane_id)

    async def _push_slot(self, slot: str = "AbovePrompt", sid: str = "", *, pane_id: str = "") -> None:
        """Render one slot and push it to the UI as a `mod_slot` frame.

        `sid` is required: a live UI event without one is dropped (or worse,
        routed to whichever pane is focused), so we simply do not emit.
        Best-effort by design: a render failure must never break a turn.
        """
        if not self._mods:
            return
        # Only the slots the UI actually mounts are worth a render round trip.
        # `ToolUse`/`StatusBar`/`Spinner` were pushed on every tool call and every
        # state change while nothing rendered them: the tree was built, sent and
        # dropped. `WIRED["slots"]` *is* the mounted set — the guard test asserts
        # each advertised slot has a frontend mount.
        from opensquad import mods_compat

        if slot not in mods_compat.WIRED["slots"]:
            return
        if not sid:
            logger.debug("[mods_host] %s not pushed: no session id for this turn", slot)
            return
        try:
            drawn = await self.render_slot(slot, **({"requestId": pane_id} if pane_id else {}))
        except (NodeHostError, NodeHostTimeoutError) as exc:
            self._degrade(f"{slot} render: {exc}")
            return

        # One pane id is its own slot instance, so it dedupes on its own.
        key = f"{slot}:{pane_id}" if pane_id else slot
        fingerprint = json.dumps(drawn["nodes"], ensure_ascii=False, sort_keys=True)
        if fingerprint == self._last_slot_payload.get(key):
            return
        # An empty tree that was never drawn is not worth a frame; an empty tree
        # *after* something was drawn is the instruction to clear the surface.
        if not drawn["nodes"] and key not in self._last_slot_payload:
            return
        self._last_slot_payload[key] = fingerprint

        bus = getattr(self.context, "event_bus", None)
        if bus is None:
            return
        # `mod_slot` is registered as a relayed + broadcast agent-output type in
        # `opensquad.protocol_version` (three places, locked by
        # tests/test_ws_event_contract.py) — the gateway forwards it verbatim.
        # The launcher relay only routes a frame to a pane when the payload is
        # the bus envelope (`{"sid", "data", ...}`) — `_extract_sid` reads exactly
        # that shape (see runner._emit). A bare payload loses the sid and the
        # frame lands in whichever pane is focused.
        bus.emit(
            "mod_slot",
            {
                "sid": sid,
                "data": {
                    "slot": slot,
                    "nodes": drawn["nodes"],
                    "dropped": len(drawn["dropped"]),
                    **({"paneId": pane_id} if pane_id else {}),
                },
                "agent_id": str(getattr(self.context, "agent_id", "") or ""),
            },
        )

    async def invoke_action(self, action_id: str) -> dict[str, Any]:
        """Run a button press the host hoisted. The mod never sees the caller."""
        client = await self._ensure_client()
        return await client.request("action.invoke", {"action": action_id}, timeout=5.0)

    def _workspace(self) -> str:
        try:
            from opensquad.system_config import syscfg

            ws = (syscfg.get_workspace() or "").strip()
            if ws:
                return ws
        except Exception:  # noqa: BLE001 - cwd is a fine fallback
            pass
        return os.getcwd()

    async def _on_host_request(self, method: str, params: dict) -> Any:
        """Serve a host→Python call. Python owns every privileged gate.

        This is the reentrant path: it runs while our own `tool.call` request is
        still pending, which is exactly why the transport is a duplex pipe rather
        than two HTTP endpoints (docs/mods-bridge-m0.md §1.3).
        """
        if method == "log":
            mod = str(params.get("mod") or "mod")
            level = str(params.get("level") or "info")
            message = str(params.get("message") or "")
            emit = level if level in ("debug", "info", "warning", "error") else "info"
            getattr(logger, emit)("[%s] %s", mod, message)
            return {}

        if method == "gate.session.cwd":
            self._bump_gate(method)
            return {"cwd": self._workspace()}

        if method == "gate.session.id":
            self._bump_gate(method)
            return {"id": self._session_id or str(params.get("_sid") or "")}

        if method == "gate.fs.write":
            return await self._serve_fs_write(str(params.get("_mod") or ""), params)

        if method == "gate.process.run":
            return await self._serve_process_run(str(params.get("_mod") or ""), params)

        if method == "gate.http.fetch":
            return await self._serve_http_fetch(str(params.get("_mod") or ""), params)

        if method == "gate.env.set":
            return await self._serve_env_set(str(params.get("_mod") or ""), params)

        if method in ("gate.fs.list", "gate.fs.stat"):
            raw = str(params.get("path") or ".")
            try:
                target = await asyncio.to_thread(_confined_path, raw, self._workspace())
            except ValueError as exc:
                logger.warning("[mods_host] refused %s: %s", method, exc)
                return {"error": str(exc)}
            if method == "gate.fs.list":
                entries = await asyncio.to_thread(_list_dir_capped, target, _FS_LIST_LIMIT)
                if entries is None:
                    return {"error": f"not a directory: {raw}"}
                self._bump_gate(method)
                return {"entries": entries}
            info = await asyncio.to_thread(_stat_path, target)
            self._bump_gate(method)
            return {"stat": info}

        if method in ("gate.fs.exists", "gate.fs.read"):
            raw = str(params.get("path") or "")
            try:
                path = await asyncio.to_thread(_confined_path, raw, self._workspace())
            except ValueError as exc:
                logger.warning("[mods_host] refused %s: %s", method, exc)
                return {"error": str(exc)}
            if method == "gate.fs.exists":
                exists = await asyncio.to_thread(os.path.isfile, path)
                if exists:
                    self._bump_gate(method)
                return {"exists": exists}
            if not await asyncio.to_thread(os.path.isfile, path):
                return {"error": f"not a file: {raw}"}
            size = await asyncio.to_thread(os.path.getsize, path)
            if size > _FS_READ_LIMIT:
                return {"error": f"file too large for $.fs.read ({size} > {_FS_READ_LIMIT} bytes)"}
            text = await asyncio.to_thread(_read_text_capped, path, _FS_READ_LIMIT)
            self._bump_gate(method)
            return {"text": text}

        if method.startswith("gate.store."):
            return await self._serve_store(method, str(params.get("_mod") or ""), params)

        if method.startswith("gate.command."):
            return await self._serve_command(method, str(params.get("_mod") or ""), params)

        raise ValueError(f"unsupported host request: {method}")

    def _refuse_unless_granted(self, capability: str, mod: str) -> str:
        """'' when granted, otherwise the refusal the mod will see.

        Default-deny is an *API contract*, not a sandbox: the mod runs as ordinary
        Node and can reach `node:fs` without us (measured — plan §3.4). What this
        buys is a definite answer plus a per-mod record of what was declared.
        """
        from opensquad import mods_compat

        if not mod:
            return "缺少 mod 身份，无法核对授权"
        if capability in (mods_compat.read_mod_permissions(mod).get("granted") or []):
            return ""
        blurb = mods_compat.PERMISSION_BLURB.get(capability, "")
        logger.info("[mods_host] mod '%s' called $.%s without a grant — refusing", mod, capability)
        return f"未授权：{capability}（{blurb}）—— 在「系统设置 → Mods」为该 mod 授权后重试"

    async def _serve_fs_write(self, mod: str, params: dict) -> dict[str, Any]:
        refusal = self._refuse_unless_granted("fs.write", mod)
        if refusal:
            return {"error": refusal}
        raw = str(params.get("path") or "")
        text = str(params.get("text") or "")
        if len(text.encode("utf-8")) > _FS_READ_LIMIT:
            return {"error": f"写入超过 {_FS_READ_LIMIT} 字节上限"}
        try:
            path = await asyncio.to_thread(_confined_path, raw, self._workspace())
        except ValueError as exc:
            return {"error": str(exc)}
        if not os.path.isdir(os.path.dirname(path)):
            return {"error": f"父目录不存在：{raw}"}
        try:
            await asyncio.to_thread(_write_text, path, text)
        except OSError as exc:
            return {"error": f"写入失败：{exc}"}
        self._bump_gate("gate.fs.write")
        return {"ok": True, "bytes": len(text.encode("utf-8"))}

    async def _serve_process_run(self, mod: str, params: dict) -> dict[str, Any]:
        refusal = self._refuse_unless_granted("process.run", mod)
        if refusal:
            return {"error": refusal}
        command = str(params.get("command") or "")
        if not command:
            return {"error": "missing command"}
        args = [str(a) for a in (params.get("args") or [])]
        requested = float(params.get("timeout_ms") or 0) / 1000.0
        timeout = min(max(requested, 1.0), _PROCESS_MAX_SECONDS) if requested else _PROCESS_DEFAULT_SECONDS
        outcome = await asyncio.to_thread(_run_argv_capped, [command, *args], timeout)
        self._bump_gate("gate.process.run")
        return outcome

    async def _serve_http_fetch(self, mod: str, params: dict) -> dict[str, Any]:
        refusal = self._refuse_unless_granted("http.fetch", mod)
        if refusal:
            return {"error": refusal}
        from opensquad import mods_compat

        url = str(params.get("url") or "")
        host = _url_host(url)
        if not host:
            return {"error": f"非法 URL：{url}"}
        allowed = mods_compat.read_mod_permissions(mod).get("domains") or []
        if not any(host == d or host.endswith("." + d) for d in allowed):
            return {"error": f"域名不在白名单：{host}（已授权：{', '.join(allowed) or '无'}）"}
        try:
            result = await asyncio.to_thread(
                _http_fetch_capped, url, str(params.get("method") or "GET"), params.get("body"), _FS_READ_LIMIT
            )
        except OSError as exc:
            return {"error": f"请求失败：{exc}"}
        self._bump_gate("gate.http.fetch")
        return result

    async def _serve_env_set(self, mod: str, params: dict) -> dict[str, Any]:
        refusal = self._refuse_unless_granted("env.set", mod)
        if refusal:
            return {"error": refusal}
        key = str(params.get("key") or "")
        if not key or "=" in key:
            return {"error": "非法环境变量名"}
        os.environ[key] = str(params.get("value") or "")
        self._bump_gate("gate.env.set")
        # Say what actually happened: this is the agent's own process, so the
        # change is visible to every other plugin in it.
        return {"ok": True, "scope": "agent 进程（同一 agent 内的其它插件也能看到）"}

    async def _serve_store(self, method: str, mod: str, params: dict) -> dict[str, Any]:
        """`$.store` — per-mod persisted KV, keyed by the mod's directory name.

        The host stamps `_mod` on every gate call, so a mod cannot reach another
        mod's namespace by naming it.
        """
        from opensquad import mods_compat

        if not mod:
            return {"error": "store call without a mod identity"}
        key = str(params.get("key") or "")
        action = method.rsplit(".", 1)[-1]
        try:
            data = await asyncio.to_thread(mods_compat.read_mod_store, mod)
            if action == "keys":
                self._bump_gate(method)
                return {"keys": sorted(data)}
            if not key:
                return {"error": "missing key"}
            if action == "get":
                self._bump_gate(method)
                return {"value": data.get(key)}
            if action == "set":
                data[key] = params.get("value")
                await asyncio.to_thread(mods_compat.write_mod_store, mod, data)
                self._bump_gate(method)
                return {"ok": True}
            if action == "delete":
                data.pop(key, None)
                await asyncio.to_thread(mods_compat.write_mod_store, mod, data)
                self._bump_gate(method)
                return {"ok": True}
        except (OSError, ValueError) as exc:
            logger.warning("[mods_host] %s refused for mod '%s': %s", method, mod, exc)
            return {"error": str(exc)}
        return {"error": f"unsupported store method: {method}"}

    async def _serve_command(self, method: str, mod: str, params: dict) -> dict[str, Any]:
        """`$.command.*` — mod-contributed slash commands."""
        from opensquad.cli import slash_commands

        action = method.rsplit(".", 1)[-1]
        if action == "register":
            command = slash_commands.register_runtime_command(
                str(params.get("name") or ""),
                help=str(params.get("description") or params.get("help") or ""),
                usage=str(params.get("usage") or ""),
                subcommands=params.get("subcommands") or (),
                aliases=params.get("aliases") or (),
                source=mod,
            )
            if command is None:
                return {"error": "invalid command name"}
            self._bump_gate(method)
            self._mod_commands.setdefault(mod, set()).add(command.name)
            logger.info("[mods_host] mod '%s' registered command /%s", mod, command.name)
            self._announce_commands()
            return {"command": {"name": command.name, "source": mod}}

        if action == "list":
            self._bump_gate(method)
            return {
                "commands": [
                    {
                        "name": c.name,
                        "help": c.help,
                        "usage": c.usage,
                        "category": c.category,
                        # quick-buttons filters on this to build its panel.
                        "source": slash_commands.command_source(c.name),
                    }
                    for c in slash_commands.all_commands()
                ]
            }

        if action == "run":
            # A mod asking to run a command (its own or another mod's).
            name = str(params.get("name") or "").lstrip("/+")
            if not name:
                return {"error": "missing command name"}
            verdict = await self.run_command(name, list(params.get("args") or []), mod=mod)
            self._bump_gate(method)
            return {"verdict": verdict}

        return {"error": f"unsupported command method: {method}"}

    async def _on_mod_command(self, payload: dict) -> None:
        """Run a mod's slash command and speak its `{text}` answer.

        The plugin speaks because the adapter has no handle on it: emitting
        `to_user_final` with the bus envelope is what the relay turns into the
        UI's normal `message` frame. (`to_user` is *not* in the relay table, so
        emitting that one would be dropped silently.)
        """
        name = str((payload or {}).get("name") or "").lstrip("/+")
        if not name or not self._mods:
            return
        sid = str((payload or {}).get("sid") or "")
        verdict = await self.run_command(name, list((payload or {}).get("args") or []), sid=sid)
        text = ""
        if isinstance(verdict, dict):
            text = str(verdict.get("text") or "")
        elif isinstance(verdict, str):
            text = verdict
        if not text:
            # A command whose whole job is to open a panel (quick-buttons'
            # `/azioni`) returns no text — and returning here meant the user saw
            # nothing at all: the pane existed in the host but the UI was never
            # told. Redraw the surfaces anyway.
            logger.info("[mods_host] command /%s produced no text", name)
            await self._push_slot(sid=sid)
            await self._push_panes(sid)
            return
        bus = getattr(self.context, "event_bus", None)
        if bus is None:
            return
        bus.emit(
            "to_user_final",
            {"sid": sid, "data": text, "agent_id": self._agent_id(), "turn_id": 0, "round_id": 0},
        )
        # Commands are how a mod opens its panel, so panes are re-read here too.
        await self._push_slot(sid=sid)
        await self._push_panes(sid)

    async def run_command(
        self, name: str, args: list | None = None, *, mod: str = "", sid: str = ""
    ) -> dict[str, Any] | None:
        """Dispatch one `command.run` to the mods and return their verdict.

        Shared by the user path (a slash command reaching the host) and the mod
        path (`$.command.run`), so both behave identically.
        """
        if not self._mods:
            return None
        try:
            client = await self._ensure_client()
            await self._ensure_init(client)
            result = await client.request(
                "command.dispatch",
                {"command": name, "args": list(args or []), "sid": sid or self._session_id},
                timeout=5.0,
            )
        except (NodeHostError, NodeHostTimeoutError) as exc:
            self._degrade(f"command {name}: {exc}")
            return None
        return result.get("verdict") if isinstance(result, dict) else None

    def _announce_commands(self) -> None:
        """Tell the UI which commands the mods contribute, so they can be offered.

        Broadcast with no session: the command list belongs to the agent, and a
        mod command typed anywhere is intercepted the same way. (`to_user` style
        typo protection: this topic *is* registered for relay — see
        `opensquad.protocol_version`.)
        """
        bus = getattr(self.context, "event_bus", None)
        if bus is None:
            return
        from opensquad.cli import slash_commands

        commands = [
            {"name": c.name, "help": c.help, "usage": c.usage, "source": slash_commands.command_source(c.name)}
            for c in slash_commands.all_commands()
            if slash_commands.command_source(c.name) not in ("", "builtin")
        ]
        # The same bus envelope as `mod_slot`, with an empty sid: `_unwrap` only
        # strips the wrapper when both `sid` and `data` are present, so a bare
        # `{"agent_id", "data"}` arrives at the browser as `content.data.commands`
        # — one level deeper than the handler reads, and the menu stays empty.
        bus.emit(
            "mod_commands",
            {
                "sid": "",
                "data": {"commands": commands},
                "agent_id": self._agent_id(),
            },
        )

    def _agent_id(self) -> str:
        return str(getattr(self.context, "agent_id", "") or "")

    def _remember_sid(self, ctx: dict[str, Any]) -> None:
        sid = str(ctx.get("sid") or "")
        if sid and sid != self._session_id:
            self._session_id = sid

    def _bump_gate(self, method: str) -> None:
        self._gate_calls[method] = self._gate_calls.get(method, 0) + 1

    async def _emit(self, event: str, payload: dict) -> None:
        """Best-effort push of a lifecycle event into the host."""
        if not self._mods:
            return
        try:
            client = await self._ensure_client()
            await self._ensure_init(client)
            await client.request(event, payload, timeout=5.0)
        except (NodeHostError, NodeHostTimeoutError) as exc:
            # Fail-open: a broken host must never break a turn.
            self._degrade(f"{event}: {exc}")

    # ── hooks ────────────────────────────────────────────────────────

    @hook.on_before_tool
    async def on_before_tool(self, ctx: dict[str, Any]) -> dict[str, Any]:
        # Fast path: with no mod enabled the bridge costs one falsy check per
        # tool call, and no process is ever started for an empty mod set.
        if not self._mods:
            return ctx
        return await self._apply_tool_call(ctx)

    async def _apply_tool_call(self, ctx: dict[str, Any]) -> dict[str, Any]:
        try:
            client = await self._ensure_client()
            await self._ensure_init(client)
            verdict = await client.request(
                "tool.call",
                {
                    "tool_name": ctx.get("tool_name") or "",
                    "arguments": ctx.get("arguments") or {},
                    "agent_id": ctx.get("agent_id"),
                },
            )
        except (NodeHostError, NodeHostTimeoutError) as exc:
            # Fail-open: a broken host must not brick the agent. The journal makes
            # it visible on the Mods page, which is what keeps fail-open from
            # becoming a silent bypass (docs/mods-bridge-m0.md §3.1).
            self._degrade(f"tool.call: {exc}")
            return ctx

        reason = self._deny_reason(verdict)
        if reason is None:
            if isinstance(verdict, dict) and set(verdict) - {"next"}:
                logger.info("[mods_host] verdict %s not acted on in M0 (deny-only)", sorted(verdict))
            return ctx

        ctx["skip"] = True
        ctx["result"] = f"Error: {reason}"
        logger.info("[mods_host] refused tool '%s': %s", ctx.get("tool_name"), reason)
        return ctx

    @hook.on_message_received
    async def on_message_received(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """Run a mod's slash command typed as a message.

        One mechanism for every surface: a slash command typed in Agent Web, the
        TUI or the plain CLI all arrive here as message text, so intercepting
        before the turn is what makes mod commands work without building a
        per-surface command UI.
        """
        if not self._mods:
            return ctx
        text = str(ctx.get("message") or "").strip()
        if not text.startswith(("/", "+")):
            return ctx
        parts = text.split()
        token = parts[0].lstrip("/+").lower()
        if not self._is_mod_command(token):
            return ctx

        self._remember_sid(ctx)
        logger.info("[mods_host] intercepting /%s as a mod command", token)
        await self._on_mod_command({"name": token, "args": parts[1:], "sid": self._session_id})
        ctx["__stop__"] = True
        return ctx

    def _is_mod_command(self, token: str) -> bool:
        """True only for a command one of *our* mods contributed.

        The registry is process-wide, so asking the registry alone could swallow
        a command another plugin owns.
        """
        if not token:
            return False
        return any(token in names for names in self._mod_commands.values())

    @hook.on_after_tool
    async def on_after_tool(self, ctx: dict[str, Any]) -> dict[str, Any]:
        # One `turn.step` per tool round. Deliberately *not* Claude Code's async
        # generator semantics — the matrix rates this degraded and says so.
        self._remember_sid(ctx)
        self._turn_step += 1
        await self._emit(
            "turn.step",
            {
                "step": self._turn_step,
                "tool_name": ctx.get("tool_name"),
                "agent_id": ctx.get("agent_id"),
                "sid": self._session_id,
            },
        )
        await self._push_slot("ToolUse", sid=self._session_id)
        return ctx

    @hook.on_after_send
    async def on_after_send(self, ctx: dict[str, Any]) -> dict[str, Any]:
        self._remember_sid(ctx)
        sid = self._session_id
        # Measured on the live agent (2026-10-07): the turn-boundary hooks
        # (`on_task_start` / `on_task_complete`) are driven by the task logger,
        # which never starts a task for an ordinary chat turn — so this hook is
        # the only one that fires, and the band never appeared. Redraw here too.
        await self._push_slot("AbovePrompt", sid=sid)
        await self._push_panes(sid)
        await self._push_slot("AssistantMessage", sid=sid)
        return ctx

    @hook.on_state_change
    async def on_state_change(self, ctx: dict[str, Any]) -> dict[str, Any]:
        # The status bar and the working indicator both follow agent state.
        self._remember_sid(ctx)
        sid = self._session_id
        await self._push_slot("StatusBar", sid=sid)
        await self._push_slot("Spinner", sid=sid)
        return ctx

    @hook.on_task_start
    async def on_task_start(self, ctx: dict[str, Any]) -> dict[str, Any]:
        # mods `turn.start`. `agentId` is intentionally not set: it distinguishes
        # agent-originated turns, which we cannot tell apart yet (M1).
        self._remember_sid(ctx)
        self._turn_step = 0
        await self._emit(
            "turn.start",
            {
                "task_id": ctx.get("task_id"),
                "source": ctx.get("source"),
                "agent_id": ctx.get("agent_id"),
                "sid": self._session_id,
            },
        )
        await self._push_slot(sid=self._session_id)
        await self._push_panes(self._session_id)
        return ctx

    @hook.on_task_complete
    async def on_task_complete(self, ctx: dict[str, Any]) -> dict[str, Any]:
        # mods `turn.complete`.
        await self._emit(
            "turn.complete",
            {
                "task_id": ctx.get("task_id"),
                "status": ctx.get("completion_status"),
                "turns": ctx.get("turns"),
                "agent_id": ctx.get("agent_id"),
                "sid": ctx.get("sid", ""),
            },
        )
        self._remember_sid(ctx)
        # Mods commit their turn state on `turn.complete`, so the band is read
        # *after* that — this is the redraw the matrix promised for turn ends.
        await self._push_slot(sid=self._session_id)
        return ctx

    @staticmethod
    def _deny_reason(verdict: Any) -> str | None:
        if not isinstance(verdict, dict):
            return None
        for key in _DENY_KEYS:
            value = verdict.get(key)
            if value:
                return str(value) if value is not True else "refused by mod"
        if verdict.get("isDelivered") is False or verdict.get("isOffered") is False:
            return str(verdict.get("reason") or "refused by mod")
        if "consumed" in verdict:
            return str(verdict.get("consumed") or "refused by mod")
        return None
