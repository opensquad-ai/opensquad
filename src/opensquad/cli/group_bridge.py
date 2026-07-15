"""Group chat WebSocket bridge for the interactive CLI shell."""

from __future__ import annotations

import json
import threading
from collections import deque
from typing import Callable

from opensquad.cli.api_client import GatewayClient
from opensquad.cli.group_render import (
    PendingApproval,
    PendingProposal,
    parse_approvals,
    parse_proposals,
)


class GroupBridge:
    """
    Subscribe to Gateway /ws for one focused group.

    Modes:
      - active (group mode): print full messages
      - background (solo + unmute): only print one-line approval/proposal alerts
    """

    def __init__(self, client: GatewayClient):
        self.client = client
        self.group_id: str | None = None
        self.group_name: str = ""
        self.active = False  # True when shell is in group mode
        self.muted = False  # when True, suppress background alerts
        self._ws = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._connected = threading.Event()
        self._seen_ids: deque[str] = deque(maxlen=200)
        self.pending_approvals: list[PendingApproval] = []
        self.pending_proposals: list[PendingProposal] = []
        self._lock = threading.Lock()
        self.on_alert: Callable[[str], None] | None = None
        self.on_line: Callable[[str], None] | None = None
        # Fired when new pending approval/options cards arrive (TUI decision picker)
        self.on_pending_cards: Callable[[], None] | None = None

    def _emit(self, text: str) -> None:
        if self.on_line:
            try:
                self.on_line(text)
                return
            except Exception:
                pass
        print(text)

    def connect(
        self,
        group_id: str,
        *,
        group_name: str = "",
        history_limit: int = 15,
    ) -> None:
        """Join/subscribe a group over Gateway WS and optionally print recent history."""
        self.close()
        self.group_id = group_id
        self.group_name = group_name or group_id
        self._stop.clear()
        self._connected.clear()
        self._seen_ids.clear()
        self.pending_approvals.clear()
        self.pending_proposals.clear()

        if history_limit > 0:
            try:
                msgs = self.client.get(
                    f"/api/groups/{group_id}/messages",
                    params={"limit": history_limit},
                )
                if isinstance(msgs, list):
                    self._emit(f"[group] joined {self.group_name} — last {len(msgs)} messages:")
                    for m in msgs:
                        if isinstance(m, dict):
                            self._ingest_message(m, print_full=True)
                            mid = m.get("id")
                            if mid:
                                self._seen_ids.append(str(mid))
            except Exception as e:
                self._emit(f"[group] history: {e}")

        import websockets.sync.client as ws_sync

        url = self.client.group_ws_url()
        self._ws = ws_sync.connect(url, open_timeout=15)
        # Gateway accepts group_id at top-level or in data
        self._ws.send(json.dumps({"type": "subscribe", "group_id": group_id}))
        self._ws.send(json.dumps({"type": "subscribe", "data": {"group_id": group_id}}))
        self._thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._thread.start()
        self._connected.wait(timeout=5)

    def close(self) -> None:
        self._stop.set()
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._thread = None
        self.group_id = None

    def set_active(self, active: bool) -> None:
        self.active = active

    def set_muted(self, muted: bool) -> None:
        self.muted = muted

    def send(self, content: str) -> dict | None:
        if not self.group_id:
            raise RuntimeError("Not joined to a group")
        result = self.client.post(
            f"/api/groups/{self.group_id}/messages",
            {"content": content, "group_id": self.group_id, "type": "TEXT"},
        )
        if isinstance(result, dict) and result.get("id"):
            self._seen_ids.append(str(result["id"]))
        return result if isinstance(result, dict) else None

    def latest_pending_approval(self) -> PendingApproval | None:
        with self._lock:
            pending = [a for a in self.pending_approvals if a.status == "pending"]
            return pending[-1] if pending else None

    def latest_pending_proposal(self) -> PendingProposal | None:
        with self._lock:
            return self.pending_proposals[-1] if self.pending_proposals else None

    def find_approval(self, approval_id: str | None) -> PendingApproval | None:
        with self._lock:
            if not approval_id:
                return self.latest_pending_approval()
            for a in reversed(self.pending_approvals):
                if a.id == approval_id:
                    return a
        return None

    def find_proposal(self, proposal_id: str | None) -> PendingProposal | None:
        with self._lock:
            if not proposal_id:
                return self.latest_pending_proposal()
            for p in reversed(self.pending_proposals):
                if p.id == proposal_id:
                    return p
        return None

    def resolve_approval(self, approval_id: str | None, *, reject: bool = False, note: str = "") -> dict:
        ap = self.find_approval(approval_id)
        if not ap:
            raise RuntimeError("No pending approval (pass an id or wait for a card)")
        gid = ap.group_id or self.group_id
        if not gid:
            raise RuntimeError("No group context")
        body = {
            "action": "reject" if reject else "approve",
            "note": note,
        }
        if ap.message_id:
            body["message_id"] = ap.message_id
        result = self.client.post(
            f"/api/groups/{gid}/collab-approvals/{ap.id}/resolve",
            body,
        )
        with self._lock:
            ap.status = "rejected" if reject else "approved"
        return result if isinstance(result, dict) else {"ok": True}

    def resolve_choose(self, proposal_id: str | None, value: str, *, action: str = "choose") -> dict:
        pr = self.find_proposal(proposal_id)
        if not pr and not proposal_id:
            raise RuntimeError("No pending options card")
        pid = (pr.id if pr else proposal_id) or ""
        gid = (pr.group_id if pr else None) or self.group_id
        if not gid:
            raise RuntimeError("No group context")
        body: dict = {"action": action, "value": value, "note": ""}
        if pr and pr.message_id:
            body["message_id"] = pr.message_id
        result = self.client.post(
            f"/api/groups/{gid}/propose-options/{pid}/resolve",
            body,
        )
        return result if isinstance(result, dict) else {"ok": True}

    def resolve_numeric_reply(self, text: str) -> bool:
        """
        If user types a bare number while a card is pending, map to Web button.
        Approval: 1=approve 2=reject
        Options: 1..N = choose that option
        Returns True if handled.
        """
        if not text.isdigit():
            return False
        n = int(text)
        with self._lock:
            pr = self.pending_proposals[-1] if self.pending_proposals else None
            ap = None
            for a in reversed(self.pending_approvals):
                if a.status == "pending":
                    ap = a
                    break

        if pr and 1 <= n <= len(pr.options):
            _label, value = pr.options[n - 1]
            self.resolve_choose(pr.id, value)
            self._emit(f"[group] chose [{n}] {_label}")
            return True
        if ap and n in (1, 2):
            reject = n == 2
            self.resolve_approval(ap.id, reject=reject)
            self._emit(f"[group] {'rejected' if reject else 'approved'} {ap.id}")
            return True
        return False

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
                self._handle_event(msg)
        except Exception as e:
            if not self._stop.is_set():
                self._emit(f"[group ws] closed: {e}")
        finally:
            self._connected.clear()

    def _handle_event(self, msg: dict) -> None:
        mtype = msg.get("type")
        data = msg.get("data") or {}
        if mtype in ("subscribed", "joined"):
            self._connected.set()
            return
        if mtype in ("typing", "presence", "pong", "unread_update"):
            return
        if mtype in ("new_message", "message_updated"):
            m = data if isinstance(data, dict) else msg
            mid = str(m.get("id") or "")
            if mid and mid in self._seen_ids and mtype == "new_message":
                return
            if mid:
                self._seen_ids.append(mid)
            self._ingest_message(m, print_full=self.active)
            return

    def _ingest_message(self, m: dict, *, print_full: bool) -> None:
        content = m.get("content") or ""
        mid = str(m.get("id") or "")
        gid = str(m.get("group_id") or self.group_id or "")

        approvals = parse_approvals(content, group_id=gid, message_id=mid)
        proposals = parse_proposals(content, group_id=gid, message_id=mid)

        with self._lock:
            for ap in approvals:
                self.pending_approvals = [a for a in self.pending_approvals if a.id != ap.id]
                self.pending_approvals.append(ap)
                if len(self.pending_approvals) > 50:
                    self.pending_approvals = self.pending_approvals[-50:]
            for pr in proposals:
                self.pending_proposals = [p for p in self.pending_proposals if p.id != pr.id]
                if pr.options:
                    self.pending_proposals.append(pr)
                if len(self.pending_proposals) > 50:
                    self.pending_proposals = self.pending_proposals[-50:]

        if print_full:
            from opensquad.cli.group_render import format_message_lines

            for line in format_message_lines(m, shell_style=True):
                text = line.lstrip("\n") if line.startswith("\n") else line
                if text:
                    self._emit(text)
            pending_now = [a for a in approvals if a.status == "pending"] or [
                p for p in proposals if (p.raw.get("status") or "pending") == "pending"
            ]
            if pending_now and self.on_pending_cards:
                try:
                    self.on_pending_cards()
                except Exception:
                    pass
            return

        if self.muted:
            return
        pending_ap = [a for a in approvals if a.status == "pending"]
        if pending_ap or proposals:
            gname = self.group_name or gid
            n = len(pending_ap) + len(proposals)
            alert = f"[group!] {n} pending card(s) in {gname} — /group join {gid}"
            self._emit(alert)
            if self.on_alert:
                try:
                    self.on_alert(alert)
                except Exception:
                    pass
            if self.on_pending_cards:
                try:
                    self.on_pending_cards()
                except Exception:
                    pass
