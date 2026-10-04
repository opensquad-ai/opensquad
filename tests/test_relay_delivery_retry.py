"""A push that missed once must not be lost forever.

Observed in a live cross-machine run: the owner's `relay_outbox.json` held the task-window event
retrying against the right address with `last_error: duplicate`, while the paired agent never saw
it. The cause was the order of two lines in `relay/deliver`: the dedup key was recorded *before*
the agent was reached, so when the agent's control socket was briefly absent (it had just been
restarted) the receiver answered `delivered: false`, the owner queued the frame — and every retry
hit the key it had already burned and came back `duplicate`. The event could never arrive, and the
outbox retried it until its TTL.

The rule these tests pin: a key is remembered when a delivery *succeeds*, and a miss leaves the
frame looking new so the retry can heal it.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from opensquad import relay_link  # noqa: E402


def setup_function():
    relay_link.reset_seen()


def test_a_peek_does_not_burn_the_key():
    """The fix: asking "have I seen this?" must not answer the next retry."""
    assert relay_link.already_seen("host", "evt_1", record=False) is False
    # …so a second look still sees it as new (that is what lets the owner's retry land)
    assert relay_link.already_seen("host", "evt_1", record=False) is False

    relay_link.note_seen("host", "evt_1")

    assert relay_link.already_seen("host", "evt_1", record=False) is True


def test_recording_still_happens_by_default():
    """The dedup window itself is unchanged for callers that want it."""
    assert relay_link.already_seen("host", "evt_2") is False
    assert relay_link.already_seen("host", "evt_2") is True


def test_two_hosts_and_two_events_do_not_collide():
    relay_link.note_seen("host_a", "evt_3")

    assert relay_link.already_seen("host_b", "evt_3", record=False) is False
    assert relay_link.already_seen("host_a", "evt_4", record=False) is False


def test_the_task_branch_peeks_and_records_only_on_success():
    """Source lock: the two order-dependent lines, and the reason one may not move back."""
    src = (_SRC / "opensquad" / "gateway" / "backend" / "app" / "relay_api.py").read_text(encoding="utf-8")

    assert "already_seen(origin_host, event_id, record=False)" in src
    assert "relay.note_seen(origin_host, event_id)" in src
    assert src.index("already_seen(origin_host, event_id, record=False)") < src.index(
        "relay.note_seen(origin_host, event_id)"
    )
    assert '"reason": "duplicate"' in src, "a genuine duplicate must still short-circuit"
