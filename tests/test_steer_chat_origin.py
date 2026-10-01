"""A chat message that arrives mid-turn carries its origin out of band.

The steer text is also the session label, so the origin must not live in it: it
travels as `source` + `sender_name` on the message and on the `user_msg` payload
(the UI labels the row from those). See the revert of the earlier attempt that
rewrote the text — that leaked wire markers into session titles.
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "src"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

TURN_LOOP = (_BACKEND / "opensquad" / "_runner" / "_turn_loop.py").read_text(encoding="utf-8")


def test_the_origin_is_attached_for_chat_events_only():
    assert 'if evt.source in ("dm", "group"):' in TURN_LOOP
    assert '"source": str(evt.source),' in TURN_LOOP
    assert '"sender_name": str(' in TURN_LOOP


def test_the_message_text_is_not_rewritten():
    """The label is derived from the text, so the text must stay the message."""
    assert '"user",\n                            evt.content,' in TURN_LOOP


def test_the_meta_goes_to_both_the_session_and_the_ui():
    assert "**_chat_meta," in TURN_LOOP  # stored on the session message
    assert '{"text": evt.content, "steer": True, **_chat_meta},' in TURN_LOOP


def test_every_steer_is_receipted_so_the_fold_can_render_it_live():
    """Only client_id'd interjections used to be receipted: a DM that arrived
    mid-turn had none, so it lived in the session but never entered the live fold
    — it only showed up once a refresh rebuilt the timeline from disk."""
    at = TURN_LOOP.index('"steer_consumed"')
    receipt = TURN_LOOP[at : at + 300]
    assert '"message_id": _steer_cid,' in receipt
    assert '"content": evt.content,' in receipt
    assert "**_chat_meta," in receipt
    # Unconditional: the emit is no longer wrapped in `if _steer_cid:`.
    assert "if _steer_cid:" not in TURN_LOOP[max(0, at - 160) : at]


def test_the_ui_marks_steer_frames_so_no_bubble_is_finalized():
    """The frontend returns early on these (`raw.steer`): finalizing a bubble
    would seal the running fold and cut the tool stream in two."""
    assert '"steer": True' in TURN_LOOP
