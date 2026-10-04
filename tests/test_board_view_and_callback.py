"""Two findings from the cross-machine run: board_view's empty zones, and a callback nobody can reach.

Both were reported as separate defects and both turned out to be local, single-place mistakes.

board_view maps items into zones by their item_type, and the zone keys are plural while the stored
types are singular — requirement, task, discussion. Those three fell through the membership test and
vanished, so requirements / tasks / discussions read as empty on every machine, local or remote,
while board_list showed the same items. It looked like a cross-machine routing problem and was
nothing of the kind.

A subscription tells the peer where to push. That address used to come straight from this machine's
own gateway URL, which is loopback, so every relayed message grew a copy addressed to the sender
itself: 401 forever, one wasted retry per message, and an outbox that says nothing useful.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from opensquad import collab_board as cb  # noqa: E402
from opensquad import peer_bridge as pb  # noqa: E402
from opensquad.tools import collaboration as collab  # noqa: E402

COLLAB = "B15B90"


def _board(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "_board_dir", lambda: str(tmp_path))
    return tmp_path


def _item(item_type: str, title: str, **extra) -> dict:
    return {
        "id": f"{item_type}-1",
        "collab_id": COLLAB,
        "item_type": item_type,
        "item_key": f"{item_type}-1",
        "title": title,
        "content": title,
        "status": "active",
        "visibility": "public",
        "created_at": "2026-10-04T00:00:00Z",
        "updated_at": "2026-10-04T00:00:00Z",
        "extra": extra,
    }


def test_every_zone_board_view_promises_gets_its_items(tmp_path, monkeypatch):
    root = _board(tmp_path, monkeypatch)
    items = [
        _item("requirement", "需求"),
        _item("plan", "方案"),
        _item("task", "任务", structured=True, subtasks=[{"id": "t1"}]),
        _item("discussion", "讨论"),
        _item("status", "进度"),
    ]
    (root / "board_items.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")

    zones = collab.board_view(COLLAB)["zones"]

    assert len(zones["requirements"]) == 1, "the stored type is singular, the zone is plural"
    assert len(zones["plan"]) == 1
    assert len(zones["tasks"]) == 1
    assert len(zones["discussions"]) == 1
    assert len(zones["status"]) == 1
    assert zones["tasks"][0]["subtasks"] == [{"id": "t1"}], "task enrichment still applies"


def test_an_empty_board_still_reports_the_zones(tmp_path, monkeypatch):
    _board(tmp_path, monkeypatch)

    result = collab.board_view(COLLAB)

    assert result["status"] == "success"
    assert result["summary"]["requirements_count"] == 0
    assert set(result["zones"]) == {"requirements", "plan", "tasks", "status", "discussions"}


def test_the_advertised_callback_is_never_loopback(monkeypatch):
    class _Cfg:
        @staticmethod
        def port(name: str) -> int:
            return 9555

        @staticmethod
        def gateway_http() -> str:
            return "http://127.0.0.1:9555"

    monkeypatch.setattr("opensquad.system_config.syscfg", _Cfg())
    monkeypatch.setattr("opensquad.bridge.gateway_base_url", lambda: "http://127.0.0.1:9555", raising=False)
    monkeypatch.setattr("opensquad.net_addresses.lan_addresses", lambda: ["192.168.5.4"])

    assert pb._home_gateway_url() == "http://192.168.5.4:9555"


def test_a_real_lan_url_is_left_alone(monkeypatch):
    monkeypatch.setattr("opensquad.bridge.gateway_base_url", lambda: "http://192.168.5.9:9555", raising=False)

    assert pb._home_gateway_url() == "http://192.168.5.9:9555"
