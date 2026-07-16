"""opensquad chat — text-first multi-agent shell (Web parity, no GUI)."""

from __future__ import annotations

import json
import os
import sys
import threading
from typing import Any

from opensquad.cli.api_client import GatewayClient, handle_api_error, load_credentials, pick_default_agent
from opensquad.cli.banner import render_banner
from opensquad.cli.group_bridge import GroupBridge
from opensquad.cli.media import (
    PendingMedia,
    attach_from_clipboard,
    attach_from_path,
    chip_label,
    format_pending_chips,
    upload_for_agent,
    upload_for_group,
)
from opensquad.cli.slash_dispatch import dispatch_slash


def run_chat(args) -> None:
    gateway = getattr(args, "gateway", None)
    client = GatewayClient(gateway_url=gateway)

    if not client.token:
        print("[chat] Not logged in — use /login inside the shell, or: opensquad login")
        print("[chat] Tip: `opensquad code` auto-starts services and opens TUI")

    agent = getattr(args, "agent", None)
    if not agent and client.token:
        agent = pick_default_agent(client)

    message = getattr(args, "message", None)
    if message:
        if not client.token or not agent:
            print("[chat] login + agent required for -m")
            sys.exit(1)
        _oneshot(client, agent, message)
        return

    # Default: full-screen Textual TUI (Claude Code / OpenCode style).
    # Fallback: --legacy framed prompt_toolkit REPL.
    use_legacy = bool(getattr(args, "legacy", False))
    if not use_legacy:
        try:
            from opensquad.cli.tui import run_tui

            run_tui(gateway=gateway, agent=agent)
            return
        except SystemExit:
            raise
        except Exception as e:
            print(f"[chat] TUI unavailable ({e}), falling back to legacy prompt…\n")

    try:
        InteractiveShell(client, agent).run()
    except KeyboardInterrupt:
        print("\nbye")
    except Exception as e:
        from opensquad.cli.api_client import ApiError

        if isinstance(e, ApiError):
            handle_api_error(e)
        print(f"[chat] {e}")
        sys.exit(1)


def _pick_default_agent(client: GatewayClient) -> str | None:
    """Backward-compatible alias."""
    from opensquad.cli.api_client import pick_default_agent

    return pick_default_agent(client)


def _oneshot(client: GatewayClient, agent: str, message: str) -> None:
    bridge = AgentBridge(client, agent, interactive=False)
    try:
        bridge.connect()
        bridge.wait_connected(timeout=15)
        bridge.send_chat(message)
        bridge.wait_turn(timeout=300)
    finally:
        bridge.close()


class InteractiveShell:
    """
    Single-focus shell:
      mode=solo  → plain text to agent WS
      mode=group → plain text to group messages
    All Web features via /commands; clickable UI → numbered lists.
    Images: attach/paste/send as text chips — never rendered.
    """

    def __init__(self, client: GatewayClient, agent: str | None):
        self.client = client
        self.agent = agent
        self.mode = "solo"  # solo | group
        self.bridge: AgentBridge | None = None
        self.group: GroupBridge | None = None
        self.pending_media: list[PendingMedia] = []
        self.muted = False
        self._running = True
        self._agent_paused = False

    # ── lifecycle ─────────────────────────────────────────────────────────

    def run(self) -> None:
        creds = load_credentials()
        print(
            render_banner(
                agent=self.agent,
                gateway_url=self.client.gateway_url,
                email=creds.get("email"),
                cwd=os.getcwd(),
            )
        )
        print(
            "Tip: framed box below = message input  ·  Enter send · Alt+Enter newline\n"
            "     Tab=/commands  ·  Ctrl+V image  ·  /group join  ·  Ctrl+C quit\n"
        )

        if self.client.token and self.agent:
            self._connect_agent(self.agent)
        elif not self.client.token:
            print("Not logged in. Type /login\n")
        elif not self.agent:
            print("No agent. /agent list then /agent <name>  ·  or /group join <id>\n")

        session = self._make_prompt_session()
        while self._running:
            try:
                from opensquad.cli.input_box import prompt_message

                line = session.prompt(prompt_message(self))
            except EOFError:
                break
            except KeyboardInterrupt:
                print()
                continue

            line = (line or "").strip()
            if not line and not self.pending_media:
                continue

            if line.startswith(("/", "+")):
                try:
                    cont = dispatch_slash(line, self._ctx())
                except Exception as e:
                    # Never crash the shell on a slash command failure
                    print(f"[chat] command error: {e}")
                    continue
                if not cont:
                    self._shutdown()
                    print("bye")
                    break
                continue

            self._send_plain(line)

        self._shutdown()

    def _make_prompt_session(self):
        try:
            from opensquad.cli.input_box import build_chat_session

            return build_chat_session(self)
        except Exception as e:
            print(f"[chat] Framed input unavailable ({e}). Install: pip install 'prompt_toolkit>=3.0.36'\n")
            return _BasicSession(self)

    def _ctx(self) -> dict[str, Any]:
        return {
            "client": self.client,
            "agent": self.agent,
            "gateway": self.client.gateway_url,
            "mode": self.mode,
            "group": self.group,
            "shell": self,
            "switch_agent": self._switch_agent,
            "session_cmd": self._session_cmd,
            "refresh_client": self._refresh_client,
            "join_group": self.join_group,
            "leave_group": self.leave_group,
            "attach_image": self.attach_image,
            "detach_media": self.detach_media,
            "set_muted": self.set_muted,
            "history": self.show_history,
            "approve": self.approve,
            "reject": self.reject,
            "choose": self.choose,
        }

    # ── send ──────────────────────────────────────────────────────────────

    def _send_plain(self, line: str) -> None:
        if not self.client.token:
            print("Login first: /login")
            return

        # Bare number → resolve Web card buttons as list options
        if self.mode == "group" and self.group and line.isdigit():
            try:
                if self.group.resolve_numeric_reply(line):
                    return
            except Exception as e:
                print(f"[group] {e}")
                return

        if self.mode == "group":
            self._send_group(line)
        else:
            self._send_solo(line)

    def _send_solo(self, line: str) -> None:
        if not self.agent or not self.bridge or self._agent_paused:
            if self.agent and (not self.bridge or self._agent_paused):
                self._connect_agent(self.agent)
            if not self.bridge:
                print("Select an agent: /agent <name>")
                return
        images: list[str] = []
        attachments: list[dict] = []
        chips = []
        for media in list(self.pending_media):
            try:
                upload_for_agent(self.client, self.agent, media)
                chips.append(chip_label(media))
                if media.kind == "image" and media.uploaded_path:
                    images.append(media.uploaded_path)
                else:
                    attachments.append(
                        {
                            "name": media.label,
                            "url": media.uploaded_url or "",
                            "size": media.size,
                            "type": media.kind,
                        }
                    )
            except Exception as e:
                print(f"[attach] upload failed {media.label}: {e}")
        self.pending_media.clear()

        content = line
        if chips:
            print(f"  sending with: {' '.join(chips)}")
        try:
            self.bridge.turn_reset()
            self.bridge.send_chat(content, images=images or None, attachments=attachments or None)
            self.bridge.wait_turn(timeout=600)
        except AgentWsError as e:
            print(f"[chat] {e}")
            print(f"  → try: opensquad agent start {self.agent}   then /agent {self.agent}")

    def _send_group(self, line: str) -> None:
        if not self.group or not self.group.group_id:
            print("Join a group first: /group join <id>")
            return
        atts = []
        chips = []
        for media in list(self.pending_media):
            try:
                att = upload_for_group(self.client, media)
                atts.append(att)
                chips.append(chip_label(media))
            except Exception as e:
                print(f"[attach] upload failed {media.label}: {e}")
        self.pending_media.clear()
        body: dict[str, Any] = {
            "content": line or "(attachment)",
            "group_id": self.group.group_id,
            "type": "TEXT",
        }
        if atts:
            body["attachments"] = atts
            print(f"  sending with: {' '.join(chips)}")
        try:
            self.client.post(f"/api/groups/{self.group.group_id}/messages", body)
        except Exception as e:
            print(f"[group] send failed: {e}")

    # ── group mode ────────────────────────────────────────────────────────

    def join_group(self, group_ref: str) -> None:
        if not self.client.token:
            print("Login first: /login")
            return
        gid, gname = self._resolve_group(group_ref)
        if not gid:
            print(f"[group] not found: {group_ref}")
            return

        # Pause agent stream (keep connection if possible)
        if self.bridge:
            self.bridge.set_paused(True)
            self._agent_paused = True

        if self.group:
            self.group.close()
        self.group = GroupBridge(self.client)
        self.group.muted = self.muted
        try:
            self.group.connect(gid, group_name=gname, history_limit=15)
        except Exception as e:
            print(f"[group] connect failed: {e}")
            self.group = None
            return
        self.group.set_active(True)
        self.mode = "group"
        print("[group] mode=group  ·  /leave to return to solo  ·  /approve /choose for cards")

    def leave_group(self) -> None:
        if self.group:
            self.group.set_active(False)
            # Keep subscription for background alerts unless muted
            if self.muted:
                self.group.close()
                self.group = None
        self.mode = "solo"
        if self.bridge:
            self.bridge.set_paused(False)
            self._agent_paused = False
        elif self.agent:
            self._connect_agent(self.agent)
        print(f"[group] left → solo ({self.agent or 'no agent'})")

    def _resolve_group(self, ref: str) -> tuple[str | None, str]:
        try:
            groups = self.client.get("/api/groups")
        except Exception as e:
            print(f"[group] {e}")
            return None, ""
        if not isinstance(groups, list):
            return ref, ref
        for g in groups:
            if g.get("id") == ref or g.get("name") == ref:
                return g.get("id"), g.get("name") or g.get("id")
        # fuzzy name / id substring
        low = ref.lower()
        for g in groups:
            name = str(g.get("name") or "")
            gid = str(g.get("id") or "")
            if low in name.lower() or low in gid.lower() or gid.startswith(ref) or gid.startswith(f"g-{ref}"):
                return g.get("id"), name or g.get("id")
        if ref.isdigit():
            idx = int(ref) - 1
            if 0 <= idx < len(groups):
                g = groups[idx]
                return g.get("id"), g.get("name") or g.get("id")
        return ref if ref.startswith("g-") else None, ref

    # ── cards / media / mute ──────────────────────────────────────────────

    def approve(self, approval_id: str | None = None, note: str = "") -> None:
        if not self.group:
            print("Join a group first (or keep watch group): /group join <id>")
            return
        try:
            self.group.resolve_approval(approval_id, reject=False, note=note)
            print(f"[group] approved {approval_id or '(latest)'}")
        except Exception as e:
            print(f"[group] {e}")

    def reject(self, approval_id: str | None = None, note: str = "") -> None:
        if not self.group:
            print("Join a group first: /group join <id>")
            return
        try:
            self.group.resolve_approval(approval_id, reject=True, note=note)
            print(f"[group] rejected {approval_id or '(latest)'}")
        except Exception as e:
            print(f"[group] {e}")

    def choose(self, proposal_id: str | None, value: str) -> None:
        if not self.group:
            print("Join a group first: /group join <id>")
            return
        try:
            # numeric value → option index
            if value.isdigit() and self.group:
                pr = self.group.find_proposal(proposal_id)
                n = int(value)
                if pr and 1 <= n <= len(pr.options):
                    value = pr.options[n - 1][1]
            self.group.resolve_choose(proposal_id, value)
            print(f"[group] chose {value}")
        except Exception as e:
            print(f"[group] {e}")

    def attach_image(self, path: str | None = None) -> None:
        try:
            if path:
                media = attach_from_path(path)
            else:
                media = attach_from_clipboard()
                if not media:
                    print("[attach] No image on clipboard. Use /image <path> or Ctrl+V with a bitmap.")
                    return
            self.pending_media.append(media)
            print(f"  queued {chip_label(media)}  ({media.size}) — will send with next message")
            print(f"  pending: {format_pending_chips(self.pending_media)}")
        except Exception as e:
            print(f"[attach] {e}")

    def detach_media(self) -> None:
        n = len(self.pending_media)
        self.pending_media.clear()
        print(f"[attach] cleared {n} pending")

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        if self.group:
            self.group.set_muted(muted)
        print(f"[group] background alerts {'muted' if muted else 'on'}")

    def show_history(self, n: int = 20) -> None:
        if self.mode == "group" and self.group and self.group.group_id:
            from opensquad.cli.group_render import print_message

            msgs = self.client.get(
                f"/api/groups/{self.group.group_id}/messages",
                params={"limit": n},
            )
            if isinstance(msgs, list):
                for m in msgs:
                    if isinstance(m, dict):
                        print_message(m, shell_style=True)
            return
        if self.agent:
            self._session_cmd("sessions")
            return
        print("Nothing to show")

    # ── agent ─────────────────────────────────────────────────────────────

    def _refresh_client(self) -> None:
        self.client = GatewayClient(gateway_url=self.client.gateway_url)
        if self.agent and self.client.token and self.mode == "solo":
            self._connect_agent(self.agent)

    def _switch_agent(self, name: str) -> None:
        if self.mode == "group":
            self.leave_group()
        self._connect_agent(name)

    def _connect_agent(self, name: str) -> None:
        if self.bridge:
            self.bridge.close()
            self.bridge = None
        self.agent = name
        self._agent_paused = False
        if not self.client.token:
            print("Login required")
            return
        self.bridge = AgentBridge(self.client, name, interactive=True)
        try:
            self.bridge.connect()
            if self.bridge.wait_connected(timeout=3) or self.bridge.is_open:
                print(f"Connected to agent '{name}' (solo)")
            else:
                print(f"Warning: agent '{name}' WS handshake incomplete")
        except AgentWsError as e:
            print(f"[chat] {e}")
            self.bridge = None
        except Exception as e:
            print(f"Could not connect to '{name}': {e}")
            self.bridge = None

    def _session_cmd(self, name: str, args: list | None = None) -> None:
        args = list(args or [])
        if name in ("sessions", "session"):
            if not self.agent:
                print("No agent selected")
                return
            try:
                key = self.client.resolve_agent_ws_id(self.agent)
                data = self.client.ai_web_get(f"agent-sessions/{key}/list", timeout=20)
                sessions = data.get("sessions") or []
                print("Sessions:  ( /session <n> 或 /session <id> 切换 )")
                for i, s in enumerate(sessions, 1):
                    mark = "*" if s.get("current") else " "
                    print(f"  [{i}]{mark} {(s.get('id') or '')[:14]}  {s.get('title') or ''}")
                if args:
                    ref = args[0].strip()
                    target = None
                    if ref.isdigit():
                        n = int(ref)
                        if 1 <= n <= len(sessions):
                            target = sessions[n - 1]
                    if target is None:
                        for s in sessions:
                            sid = str(s.get("id") or "")
                            if sid == ref or sid.startswith(ref):
                                target = s
                                break
                    if not target:
                        print(f"Session not found: {ref}")
                        return
                    sid = str(target.get("id") or "")
                    if not self.bridge or not self.bridge.is_open:
                        print("Agent WebSocket not ready — /agent <name> first")
                        return
                    self.bridge.send_command(
                        "switch_and_reply",
                        {"session_id": sid, "content": ""},
                    )
                    print(f"Switched to session {sid} ({target.get('title') or ''})")
            except Exception as e:
                print(e)
            return

        if not self.bridge or not self.bridge.is_open:
            print("Agent WebSocket not ready (agent offline or still starting).")
            print("  → opensquad agent start <name>   then   /agent <name>")
            if self.agent:
                print(f"  → retrying connect to '{self.agent}'…")
                self._connect_agent(self.agent)
            if not self.bridge or not self.bridge.is_open:
                return

        try:
            if name == "new":
                self.bridge.send_command("new_session")
                print("new session requested")
            elif name == "stop":
                self.bridge.send_command("stop_task")
                self.bridge.turn_done()
                print("stop requested")
            elif name == "compress":
                self.bridge.send_command("compress_context")
                print("compress requested")
        except AgentWsError as e:
            print(f"[chat] {e}")
            print("  Tip: start the agent process, then /agent <name> to reconnect")
        except Exception as e:
            print(f"[chat] command failed: {e}")

    def _shutdown(self) -> None:
        self._running = False
        if self.bridge:
            self.bridge.close()
            self.bridge = None
        if self.group:
            self.group.close()
            self.group = None


class _BasicSession:
    """Fallback without prompt_toolkit — ASCII frame around input()."""

    def __init__(self, shell: InteractiveShell):
        self.shell = shell

    def prompt(self, *_a, **_k):
        from opensquad.cli.banner import status_right

        w = 60
        bar = "─" * (w - 2)
        print(f"╭{bar}╮")
        status = status_right(
            agent=self.shell.agent,
            mode=self.shell.mode,
            group_name=(self.shell.group.group_name if self.shell.group else None),
            pending_n=len(self.shell.pending_media),
        )
        prefix = "g❯ " if self.shell.mode == "group" else "❯ "
        try:
            line = input(f"│ {prefix}")
        except EOFError:
            print(f"╰{bar}╯")
            raise
        print(f"╰{bar}╯")
        print(f"  {status}")
        return line


class AgentWsError(RuntimeError):
    """Agent chat WebSocket not usable (offline / closed / not ready)."""


class AgentBridge:
    def __init__(self, client: GatewayClient, agent: str, interactive: bool = True):
        self.client = client
        self.agent = agent
        self.interactive = interactive
        self._ws = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._connected = threading.Event()
        self._turn_done = threading.Event()
        self._streaming = False
        self._paused = False
        self._close_reason = ""
        self._thought_open = False
        self._thought_buf = ""
        self._stream_acc = ""
        # Optional sinks for TUI (otherwise print to stdout)
        self.on_line: Any = None  # Callable[[str], None]
        self.on_stream: Any = None  # Callable[[str], None]
        self.on_thinking: Any = None  # Callable[[str], None] — full thought buffer so far
        self.on_thinking_end: Any = None  # Callable[[str], None] — thought closed (flush to transcript)
        self.on_agent_mode: Any = None  # Callable[[str], None] — plan|build from WS info
        self.on_token_stats: Any = None  # Callable[[dict], None]
        self.on_model_info: Any = None  # Callable[[str|None, str|None], None] — card, model
        self.on_reasoning_effort: Any = None  # Callable[[str], None] — low|medium|high
        self.on_context_compressed: Any = None  # Callable[[], None] — clear TUI after compress
        # Side streams (sub-agent / shell) — do not dump into main chat
        self.on_side_chunk: Any = None  # Callable[[str, str, str, str], None] or with fresh kw
        self.on_side_summary: Any = None  # Callable[[str], None] — one-line main chat summary
        self.on_side_done: Any = None  # Callable[[str], None] — key done
        # Decision cards (propose_options / mode_switch_approval) — TUI picker
        self.on_decision: Any = None  # Callable[[str, dict], None] — event name + payload
        # File edit diffs (OpenCode-style) — Callable[[list[str]], None] Rich markup lines
        self.on_file_diff: Any = None
        # Plan / Todos (OpenCode-style) — Callable[[Any], None] raw WS payload
        self.on_plan: Any = None
        # Full tool result body for TUI ^O expand (name, call_id, text)
        self.on_tool_detail: Any = None  # Callable[[str, str, str], None]
        # call_ids that already painted a diff from tool_call (avoid double on result)
        self._file_diff_painted: set[str] = set()
        # call_ids that already painted a ✓ tool_result (avoid duplicate green lamps)
        self._tool_result_painted: set[str] = set()
        # Per-side-channel section state (coalesce tokens; start new blocks cleanly)
        self._side_thought_open: set[str] = set()
        self._side_reply_open: set[str] = set()
        # Avoid double green lines: tool_result + job_status both used to emit ✓ Shell
        self._shell_result_summarized: bool = False

    def _emit(self, text: str) -> None:
        if self.on_line:
            try:
                self.on_line(text)
                return
            except Exception:
                pass
        print(text)

    def _emit_stream(self, chunk: str) -> None:
        if self.on_stream:
            try:
                self.on_stream(chunk)
                return
            except Exception:
                pass
        import sys

        sys.stdout.write(chunk)
        sys.stdout.flush()

    def _emit_file_diff_lines(self, lines: list[str]) -> None:
        if not lines:
            return
        if self.on_file_diff:
            try:
                self.on_file_diff(lines)
                return
            except Exception:
                pass
        for line in lines:
            self._emit(line)

    def _try_emit_file_diff(self, payload: Any, *, phase: str) -> bool:
        """Paint OpenCode-style file edit/write diff. Returns True if handled."""
        if not isinstance(payload, dict):
            return False
        try:
            from opensquad.cli.tui.file_diff import is_file_edit_tool, markup_from_event_payload, parse_tool_args
        except Exception:
            return False

        name = str(payload.get("name") or payload.get("tool") or "")
        args = parse_tool_args(payload.get("args") or payload.get("arguments") or {})
        if not is_file_edit_tool(name, args):
            return False

        call_id = str(payload.get("id") or "")
        painted = bool(call_id and call_id in self._file_diff_painted)

        # Result already shown from args, and no server-expanded context → skip ✓
        if phase == "result" and painted:
            if payload.get("diff_old") is None or payload.get("diff_new") is None:
                return True

        lines = markup_from_event_payload(payload, phase=phase)
        if not lines:
            return False

        self._emit_file_diff_lines(lines)
        if call_id:
            self._file_diff_painted.add(call_id)
            if len(self._file_diff_painted) > 200:
                self._file_diff_painted.clear()
        return True

    def _try_emit_plan(self, payload: Any) -> bool:
        try:
            from opensquad.cli.tui.plan_block import markup_from_plan_payload, parse_plan_content
        except Exception:
            return False
        # Prefer TUI callback with raw payload (theme-aware render in app)
        if self.on_plan:
            try:
                content = payload
                if isinstance(payload, dict):
                    content = (
                        payload.get("text") or payload.get("content") or payload.get("plan") or payload.get("steps")
                    )
                if not parse_plan_content(content):
                    return False
                self.on_plan(payload)
                return True
            except Exception:
                pass
        lines = markup_from_plan_payload(payload)
        if not lines:
            return False
        for line in lines:
            self._emit(line)
        return True

    @property
    def is_open(self) -> bool:
        ws = self._ws
        if ws is None:
            return False
        try:
            from websockets.protocol import State

            state = getattr(getattr(ws, "protocol", None), "state", None)
            if state is not None:
                return state is State.OPEN
        except Exception:
            pass
        # Fallback: no close_code yet
        return getattr(ws, "close_code", None) is None

    def set_paused(self, paused: bool) -> None:
        self._paused = paused

    def turn_reset(self) -> None:
        self._turn_done.clear()
        self._streaming = False
        self._thought_open = False
        self._thought_buf = ""
        self._stream_acc = ""
        self._shell_result_summarized = False
        # Keep _tool_result_painted across turns — call_ids stay unique enough;
        # clear only when oversized (handled on add).

    def turn_done(self) -> None:
        self._turn_done.set()

    def _finish_thought_line(self) -> None:
        """Close an in-progress thought stream; notify TUI to persist Thinking in order."""
        if not self._thought_open:
            return
        self._thought_open = False
        buf = self._thought_buf
        self._thought_buf = ""
        if self.on_thinking_end:
            try:
                self.on_thinking_end(buf)
            except Exception:
                pass
        elif not self.on_thinking:
            # legacy CLI: newline after same-line thought append
            self._emit_stream("\n")

    def connect(self, *, retries: int = 8, delay: float = 0.6) -> None:
        """Connect with retries — Gateway returns 1013 while agent is still starting."""
        import time

        import websockets.sync.client as ws_sync
        from websockets.exceptions import ConnectionClosed, InvalidStatus

        self.close()
        self._stop.clear()
        self._connected.clear()
        self._close_reason = ""
        url = self.client.ai_ws_url(self.agent)
        last_err: Exception | None = None

        for attempt in range(1, retries + 1):
            try:
                self._ws = ws_sync.connect(url, open_timeout=15)
                self._thread = threading.Thread(target=self._recv_loop, daemon=True)
                self._thread.start()
                # Wait briefly for either 'connected' event or immediate 1013 close
                if self._connected.wait(timeout=2.0):
                    return
                if not self.is_open:
                    reason = self._close_reason or "agent_not_ready"
                    last_err = AgentWsError(reason)
                    self.close()
                    if attempt < retries:
                        self._emit(f"  agent starting… retry {attempt}/{retries}")
                        time.sleep(delay)
                        continue
                    break
                # Socket open but no hello yet — still usable
                return
            except (ConnectionClosed, InvalidStatus, OSError) as e:
                last_err = e
                msg = str(e)
                if "1013" in msg or "agent_not_ready" in msg or "try again" in msg.lower():
                    if attempt < retries:
                        self._emit(f"  agent starting… retry {attempt}/{retries}")
                        time.sleep(delay)
                        continue
                break
            except Exception as e:
                last_err = e
                break

        raise AgentWsError(
            f"Agent '{self.agent}' not ready after {retries} tries. "
            f"In TUI: /start {self.agent}   then /new   ({last_err})"
        )

    def close(self) -> None:
        self._stop.set()
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._thread = None
        self._connected.clear()

    def wait_connected(self, timeout: float = 15) -> bool:
        return self._connected.wait(timeout)

    def wait_turn(self, timeout: float = 300) -> bool:
        return self._turn_done.wait(timeout)

    def send_chat(
        self,
        content: str,
        images: list[str] | None = None,
        attachments: list[dict] | None = None,
    ) -> None:
        msg: dict[str, Any] = {"type": "chat", "content": content}
        if images:
            msg["images"] = images
        if attachments:
            msg["attachments"] = attachments
        self._send(msg)

    def send_command(self, command: str, data: dict | None = None) -> None:
        msg: dict[str, Any] = {"type": "command", "command": command}
        if data:
            msg["data"] = data
        self._send(msg)

    def _send(self, payload: dict) -> None:
        if self._ws is None or not self.is_open:
            raise AgentWsError(
                f"WebSocket closed ({self._close_reason or 'not connected'}). "
                f"Agent may be offline — try /agent {self.agent}"
            )
        try:
            self._ws.send(json.dumps(payload, ensure_ascii=False))
        except Exception as e:
            self._close_reason = str(e)
            raise AgentWsError(f"Send failed: {e}. Agent may still be starting or offline.") from e

    def _recv_loop(self) -> None:
        assert self._ws is not None
        try:
            for raw in self._ws:
                if self._stop.is_set():
                    break
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                self._handle_message(msg)
        except Exception as e:
            reason = str(e).strip() if e is not None else ""
            if not reason or reason == "None":
                reason = self._close_reason or "connection closed"
            self._close_reason = reason
            if not self._stop.is_set() and self.interactive and not self._paused:
                low = reason.lower()
                if "1013" in low or "agent_not_ready" in low or "try again" in low:
                    self._emit("[ws] agent offline — type /start to boot it, then /new to chat")
                else:
                    self._emit(f"[ws] closed: {reason}")
        finally:
            self._connected.clear()
            self._turn_done.set()

    def _handle_message(self, msg: dict) -> None:
        mtype = str(msg.get("type") or "")
        content = msg.get("content")
        data = msg.get("data")
        payload = content if content is not None else data

        if mtype in ("connected", "status", "wake", "state"):
            self._connected.set()

        if self._paused and mtype not in ("connected", "current_session", "status"):
            return

        if mtype == "connected":
            return

        if mtype == "thought" and self.interactive:
            text = _extract_text(payload)
            if not text:
                return
            if _payload_is_sub(payload):
                key, _title = _side_key(payload if isinstance(payload, dict) else {}, default="sub")
                if key not in self._side_thought_open:
                    self._side_thought_open.add(key)
                    self._side_reply_open.discard(key)
                    self._side_emit(
                        payload if isinstance(payload, dict) else {},
                        f"Thinking: {text}",
                        kind="sub",
                        fresh=True,
                    )
                else:
                    self._side_emit(
                        payload if isinstance(payload, dict) else {},
                        text,
                        kind="sub",
                        fresh=False,
                    )
                return
            if not self._thought_open:
                self._thought_open = True
                self._thought_buf = ""
                if self.on_thinking:
                    self.on_thinking("")
                else:
                    # legacy: one prefix, then append chars on the same line
                    self._emit_stream("\n  · thought: ")
            # Servers may send deltas OR cumulative snapshots. Blind += causes
            # "LetLet me me" stutter when each event is the full text so far.
            prev = self._thought_buf or ""
            if not prev or text.startswith(prev):
                self._thought_buf = text
            elif prev.startswith(text):
                pass  # stale shorter snapshot
            elif text in prev:
                pass
            else:
                self._thought_buf = prev + text
            if self.on_thinking:
                self.on_thinking(self._thought_buf)
            else:
                # legacy CLI: only emit the new tail
                if self._thought_buf.startswith(prev):
                    delta = self._thought_buf[len(prev) :]
                else:
                    delta = text
                if delta:
                    self._emit_stream(delta.replace("\n", " "))
            return

        if mtype == "stream":
            if _payload_is_sub(payload):
                text = payload if isinstance(payload, str) else _extract_text(payload) or str(payload or "")
                if text:
                    key, _title = _side_key(payload if isinstance(payload, dict) else {}, default="sub")
                    fresh = key not in self._side_reply_open
                    if fresh:
                        self._side_reply_open.add(key)
                        self._side_thought_open.discard(key)
                    self._side_emit(
                        payload if isinstance(payload, dict) else {},
                        text,
                        kind="sub",
                        fresh=fresh,
                    )
                return
            self._finish_thought_line()
            text = payload if isinstance(payload, str) else str(payload or "")
            if text:
                self._streaming = True
                self._stream_acc += text
                self._emit_stream(text)
            return

        if mtype in ("message", "response", "to_user_final", "to_user_reply"):
            # Multi-device sync (and similar) echo user text as type=message role=user.
            # Web UI checks role; CLI must too — otherwise the user's own words are
            # painted as an agent reply with a green lamp.
            role = str(msg.get("role") or "").strip().lower()
            if not role and isinstance(payload, dict):
                role = str(payload.get("role") or "").strip().lower()
            if role in ("user", "human"):
                return
            mid = str(msg.get("message_id") or msg.get("id") or "")
            if mid.startswith("user_"):
                return
            if _payload_is_sub(payload):
                text = _extract_text(payload) or ""
                if text:
                    key, _title = _side_key(payload if isinstance(payload, dict) else {}, default="sub")
                    # Final message may duplicate streamed tokens — only emit if not streaming
                    if key not in self._side_reply_open:
                        self._side_emit(
                            payload if isinstance(payload, dict) else {},
                            text,
                            kind="sub",
                            fresh=True,
                        )
                    self._side_reply_open.discard(key)
                    self._side_thought_open.discard(key)
                return
            self._finish_thought_line()
            text = _extract_text(payload) or self._stream_acc
            self._stream_acc = ""
            # Always emit one complete reply line (avoid per-token RichLog newlines)
            if self._streaming and self.on_stream and not self.on_line:
                self._emit_stream("\n")
            elif text:
                # TUI sets both on_stream + on_line: on_line flushes stream once; dedup drops reprints.
                self._emit(text)
            elif self._streaming and not self.on_line:
                self._emit_stream("\n")
            self._streaming = False
            _print_media_refs(payload, self._emit)
            self._turn_done.set()
            return

        if mtype in ("to_user_end_task", "turn_elapsed"):
            self._finish_thought_line()
            if self._stream_acc and not self._streaming:
                # edge: content only in acc
                pass
            if self._streaming and not self.on_line:
                self._emit_stream("\n")
            self._streaming = False
            self._stream_acc = ""
            self._turn_done.set()
            return

        if mtype == "output_media":
            _print_media_refs(payload, self._emit)
            return

        if mtype == "propose_options":
            self._finish_thought_line()
            data = payload if isinstance(payload, dict) else {}
            if self.on_decision and isinstance(data, dict):
                try:
                    self.on_decision("propose_options", data)
                except Exception:
                    _print_propose_list(payload, self._emit)
            else:
                _print_propose_list(payload, self._emit)
            return

        if mtype == "tool_call":
            self._finish_thought_line()
            name, args_preview = _tool_call_preview(payload)
            call_id = ""
            if isinstance(payload, dict):
                call_id = str(payload.get("id") or payload.get("call_id") or "").strip()
            if _payload_is_sub(payload):
                self._side_emit(
                    payload if isinstance(payload, dict) else {},
                    f"⚙ {name}({args_preview})",
                    kind="sub",
                    fresh=True,
                )
                key, _t = _side_key(payload if isinstance(payload, dict) else {}, default="sub")
                self._side_thought_open.discard(key)
                self._side_reply_open.discard(key)
                return
            from opensquad.cli.tui.side_stream import is_delegate_tool, is_shell_tool

            if is_delegate_tool(str(name)):
                task = ""
                if isinstance(payload, dict):
                    args = payload.get("arguments") or payload.get("args") or {}
                    if isinstance(args, dict):
                        task = str(args.get("task") or args.get("prompt") or "")[:80]
                    elif isinstance(args, str):
                        task = args[:80]
                summary = f"  ⚙ Sub-agent: {task or name} (running)  [dim]Ctrl+X live[/]"
                if self.on_side_summary:
                    try:
                        self.on_side_summary(summary)
                    except Exception:
                        self._emit(summary)
                else:
                    self._emit(summary)
                key = f"sub:{task[:40] or name}"
                if self.on_side_chunk:
                    try:
                        self.on_side_chunk(key, "sub", task or str(name), f"— started {name} —\n", True)
                    except TypeError:
                        try:
                            self.on_side_chunk(key, "sub", task or str(name), f"— started {name} —\n")
                        except Exception:
                            pass
                    except Exception:
                        pass
                return
            if is_shell_tool(str(name)):
                self._shell_result_summarized = False
                cmd = ""
                if isinstance(payload, dict):
                    args = payload.get("arguments") or payload.get("args") or {}
                    if isinstance(args, dict):
                        cmd = str(args.get("command") or args.get("cmd") or args.get("script") or "")[:80]
                summary = f"  ⚙ Shell: {cmd or name} (running)  [dim]Ctrl+X live[/]"
                if self.on_side_summary:
                    try:
                        self.on_side_summary(summary)
                    except Exception:
                        self._emit(summary)
                else:
                    self._emit(summary)
                key = f"shell:{cmd[:40] or name}"
                if self.on_side_chunk:
                    try:
                        self.on_side_chunk(key, "shell", cmd or str(name), f"— started {name} —\n", True)
                    except TypeError:
                        try:
                            self.on_side_chunk(key, "shell", cmd or str(name), f"— started {name} —\n")
                        except Exception:
                            pass
                    except Exception:
                        pass
                return
            # File edit/write → OpenCode-style unified diff (from args preview)
            if self._try_emit_file_diff(payload, phase="call"):
                return
            # Tag call_id so TUI can dedupe even if WS redelivers tool_result
            if call_id:
                self._emit(f"  ⚙ {name}#{call_id}({args_preview})")
            else:
                self._emit(f"  ⚙ {name}({args_preview})")
            return

        if mtype == "tool_result":
            if _payload_is_sub(payload):
                text = _extract_text(payload) or ""
                if text:
                    self._side_emit(
                        payload if isinstance(payload, dict) else {},
                        f"✓ {text.replace(chr(10), ' ')[:200]}\n",
                        kind="sub",
                        fresh=True,
                    )
                    key, _t = _side_key(payload if isinstance(payload, dict) else {}, default="sub")
                    self._side_thought_open.discard(key)
                    self._side_reply_open.discard(key)
                return
            text = _extract_text(payload)
            name = ""
            call_id = ""
            if isinstance(payload, dict):
                name = str(payload.get("name") or payload.get("tool") or "")
                call_id = str(payload.get("id") or payload.get("call_id") or "").strip()
            # Hard dedupe by call_id (desktop shows one call; WS/history may redeliver)
            if call_id and call_id in self._tool_result_painted:
                return
            from opensquad.cli.tui.side_stream import is_delegate_tool, is_shell_tool

            if is_delegate_tool(name) or is_shell_tool(name):
                preview = (text or "").replace("\n", " ")[:100]
                kind = "sub" if is_delegate_tool(name) else "shell"
                label = "Sub-agent" if kind == "sub" else "Shell"
                summary = f"  ✓ {label} done" + (f": {preview}" if preview else "")
                if kind == "shell":
                    self._shell_result_summarized = True
                if call_id:
                    self._tool_result_painted.add(call_id)
                    if len(self._tool_result_painted) > 400:
                        self._tool_result_painted.clear()
                # Keep full body for TUI Ctrl+O expand (chat line stays compact)
                if self.on_tool_detail:
                    try:
                        self.on_tool_detail(label, call_id, text or "")
                    except Exception:
                        pass
                if self.on_side_summary:
                    try:
                        self.on_side_summary(summary)
                    except Exception:
                        self._emit(summary)
                else:
                    self._emit(summary)
                return
            # Prefer server diff_* on result; skip short ✓ if we already painted at call
            if self._try_emit_file_diff(payload, phase="result"):
                if call_id:
                    self._tool_result_painted.add(call_id)
                return
            # Compact green lamp in chat; full body via on_tool_detail for ^O
            label = (name or "tool").strip() or "tool"
            if self.on_tool_detail:
                try:
                    body = text or ""
                    if len(body) > 12000:
                        body = body[:12000] + "…"
                    self.on_tool_detail(label, call_id, body)
                except Exception:
                    pass
            if call_id:
                self._tool_result_painted.add(call_id)
                if len(self._tool_result_painted) > 400:
                    self._tool_result_painted.clear()
                self._emit(f"  ✓ {label}#{call_id}")
            else:
                self._emit(f"  ✓ {label}")
            return

        if mtype == "plan":
            if self._try_emit_plan(payload):
                return
            # Fallback: show raw plan text if parse failed
            text = _extract_text(payload)
            if text:
                self._emit(f"  · Plan: {text.replace(chr(10), ' ')[:120]}")
            return

        if mtype == "job_stdout":
            data = payload if isinstance(payload, dict) else {"text": str(payload or "")}
            text = ""
            if isinstance(data, dict):
                text = str(data.get("text") or data.get("chunk") or data.get("data") or "")
                if not text and isinstance(data.get("data"), str):
                    text = data["data"]
            else:
                text = str(data)
            if text:
                self._side_emit(data if isinstance(data, dict) else {}, text, kind="shell")
            return

        if mtype == "job_status":
            data = payload if isinstance(payload, dict) else {}
            if isinstance(data, dict):
                st = str(data.get("status") or data.get("state") or "")
                jid = str(data.get("job_id") or "")
                if st in ("done", "completed", "failed", "stopped", "error"):
                    key = f"job:{jid}" if jid else "shell"
                    if self.on_side_done:
                        try:
                            self.on_side_done(key)
                        except Exception:
                            pass
                    # tool_result already painted ✓ Shell done — skip duplicate green line
                    if getattr(self, "_shell_result_summarized", False):
                        return
                    if self.on_side_summary:
                        try:
                            self.on_side_summary(f"  ✓ Shell {st}" + (f" ({jid[:12]})" if jid else ""))
                            self._shell_result_summarized = True
                        except Exception:
                            pass
            return

        if mtype == "error":
            self._finish_thought_line()
            err = _extract_text(payload)
            if not err:
                err = msg.get("message") or msg.get("detail") or msg.get("error")
            if not err and payload is not None:
                err = str(payload)
            self._emit(f"[error] {err or 'unknown error'}")
            self._turn_done.set()
            return

        if mtype == "info":
            data = payload if isinstance(payload, dict) else None
            if data is None and isinstance(msg.get("data"), dict):
                data = msg["data"]
            if not isinstance(data, dict):
                return
            evt = str(data.get("event") or "")
            mode = None
            if evt == "agent_mode_changed":
                mode = data.get("mode")
            elif evt == "mode_switch_resolved":
                mode = data.get("to_mode") or data.get("mode")
            if mode in ("plan", "build") and self.on_agent_mode:
                try:
                    self.on_agent_mode(str(mode))
                except Exception:
                    pass
            # Decision cards for TUI (same payloads as Web OptionsApprovalCard / ModeSwitchApprovalCard)
            if (
                evt
                in (
                    "propose_options",
                    "propose_options_resolved",
                    "mode_switch_approval",
                    "mode_switch_resolved",
                )
                and self.on_decision
            ):
                try:
                    self.on_decision(evt, data)
                except Exception:
                    if evt == "propose_options":
                        _print_propose_list(data, self._emit)
                    elif evt == "mode_switch_approval":
                        self._emit(
                            f"★ MODE SWITCH {data.get('from_mode')} → {data.get('to_mode')} (id={data.get('id')})"
                        )
            elif evt == "propose_options":
                _print_propose_list(data, self._emit)
            elif evt == "mode_switch_approval":
                self._emit(
                    f"★ MODE SWITCH {data.get('from_mode')} → {data.get('to_mode')} "
                    f"— Approve/Deny (id={data.get('id')})"
                )
            if evt == "model_card_switched" and self.on_model_info:
                try:
                    self.on_model_info(data.get("card"), data.get("model"))
                except Exception:
                    pass
            if evt == "reasoning_effort_changed" and self.on_reasoning_effort:
                try:
                    self.on_reasoning_effort(str(data.get("effort") or ""))
                except Exception:
                    pass
            if evt == "context_summary_generated" and self.on_context_compressed:
                try:
                    self.on_context_compressed()
                except Exception:
                    pass
            if evt == "sub_agent_result":
                key, title = _side_key(data)
                if self.on_side_done:
                    try:
                        self.on_side_done(key)
                    except Exception:
                        pass
                text = str(data.get("text") or data.get("result") or "")[:120]
                if self.on_side_summary:
                    try:
                        self.on_side_summary("  ✓ Sub-agent done" + (f": {text}" if text else ""))
                    except Exception:
                        pass
            return

        if mtype == "history_sync":
            data = payload if isinstance(payload, dict) else None
            if data is None and isinstance(msg.get("data"), dict):
                data = msg["data"]
            if isinstance(data, dict) and data.get("reason") == "compression" and self.on_context_compressed:
                try:
                    self.on_context_compressed()
                except Exception:
                    pass
            return

        if mtype == "token_stats":
            data = payload if isinstance(payload, dict) else None
            if data is None and isinstance(msg.get("data"), dict):
                data = msg["data"]
            # Adapter may nest as {data: {...}} or flat used/max
            if isinstance(data, dict) and isinstance(data.get("data"), dict) and "used" not in data:
                data = data["data"]
            if isinstance(data, dict) and self.on_token_stats:
                try:
                    self.on_token_stats(data)
                except Exception:
                    pass
            return

    def _side_emit(self, payload: dict, text: str, *, kind: str, fresh: bool = False) -> None:
        key, title = _side_key(payload if isinstance(payload, dict) else {}, default=kind)
        if self.on_side_chunk:
            try:
                self.on_side_chunk(key, kind, title, text, fresh)
                return
            except TypeError:
                try:
                    self.on_side_chunk(key, kind, title, text)
                    return
                except Exception:
                    pass
            except Exception:
                pass
        # fallback: do not spam main chat
        return


def _payload_is_sub(payload: Any) -> bool:
    try:
        from opensquad.cli.tui.side_stream import payload_is_sub_agent

        return payload_is_sub_agent(payload)
    except Exception:
        return isinstance(payload, dict) and bool(payload.get("sub_agent"))


def _side_key(payload: dict, default: str = "sub") -> tuple[str, str]:
    try:
        from opensquad.cli.tui.side_stream import side_key_from_payload

        return side_key_from_payload(payload, default=default)
    except Exception:
        return default, default


def _extract_text(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for key in ("content", "text", "message", "result", "output"):
            val = payload.get(key)
            if isinstance(val, str):
                return val
        return ""
    return str(payload)


def _print_media_refs(payload: Any, emit=print) -> None:
    """Never render pixels — only text references."""
    if not isinstance(payload, dict):
        return
    for key in ("images", "output_images", "files", "media"):
        items = payload.get(key)
        if not items:
            continue
        if isinstance(items, list):
            emit("  ★ MEDIA (not shown in CLI — paths/urls only)")
            for i, it in enumerate(items, 1):
                if isinstance(it, dict):
                    ref = it.get("url") or it.get("path") or it.get("name") or it
                else:
                    ref = it
                emit(f"     [{i}] {ref}")


def _print_propose_list(payload: Any, emit=print) -> None:
    if not isinstance(payload, dict):
        emit(f"[options] {payload}")
        return
    title = payload.get("title") or payload.get("prompt") or "Choose"
    options = payload.get("options") or []
    emit(f"★ OPTIONS — {title}")
    for i, opt in enumerate(options, 1):
        if isinstance(opt, dict):
            label = opt.get("label") or opt.get("text") or opt.get("value") or str(opt)
        else:
            label = str(opt)
        emit(f"  [{i}] {label}")
    emit("  → reply with number, or /choose <id> <value>")


def _tool_call_preview(payload: Any) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return ("?", str(payload)[:80])
    name = payload.get("name") or payload.get("tool") or payload.get("tool_name") or "?"
    args = payload.get("arguments") or payload.get("args") or payload.get("input") or {}
    if isinstance(args, str):
        preview = args[:80]
    else:
        preview = json.dumps(args, ensure_ascii=False)[:80]
    return str(name), preview
