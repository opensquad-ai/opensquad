"""Mid-turn chat keeps its source in the UI text.

A DM/group message that arrives while the agent is working is recorded as a steer.
The UI tells a machine-delivered message from the user's own interjection by the
wire marker (`[DM] ss: hi`, `[Messages]\\n[<group> | group_id=…] ss: hi`), which the
steer text used to drop — so a private message showed up as a bare "插话".
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "src"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from opensquad.event_pipeline import PipelineEvent  # noqa: E402


def test_a_dm_keeps_its_marker_and_sender():
    evt = PipelineEvent(source="dm", content="hi", metadata={"sender_name": "ss"})

    assert evt.ui_steer_text() == "[DM] ss: hi"


def test_a_dm_without_a_sender_is_left_alone():
    evt = PipelineEvent(source="dm", content="hi", metadata={})

    assert evt.ui_steer_text() == "hi"


def test_a_group_message_uses_the_batch_form_the_ui_parses():
    evt = PipelineEvent(
        source="group",
        content="现在几点",
        metadata={"group_name": "开发协作组", "group_id": "g-default", "sender_name": "ss"},
    )

    # `[Messages]` head + the entry: that is what parseGroupEntries looks for.
    assert evt.ui_steer_text() == "[Messages]\n[开发协作组 | group_id=g-default] ss: 现在几点"


def test_a_group_message_without_the_identifiers_is_left_alone():
    evt = PipelineEvent(source="group", content="hi", metadata={"sender_name": "ss"})

    assert evt.ui_steer_text() == "hi"


@pytest.mark.parametrize("source", ["web", "gateway", "timer", "task_watch"])
def test_the_users_own_words_are_never_rewritten(source):
    evt = PipelineEvent(source=source, content="换个方向做", metadata={"sender_name": "ss"})

    assert evt.ui_steer_text() == "换个方向做"


def test_a_quoted_dm_survives_the_round_trip():
    """The marker inside a quoted DM must reach the UI, which renders it as a quote."""
    content = '[[DM_QUOTE]]{"id":"dm_1","name":"Agent305","text":"现在是 16:02。"}[[/DM_QUOTE]] 现在呢'
    evt = PipelineEvent(source="dm", content=content, metadata={"sender_name": "ss"})

    text = evt.ui_steer_text()

    assert text.startswith("[DM] ss: ")
    assert "[[DM_QUOTE]]" in text  # stripped at render time by parseDmQuote


def test_the_turn_loop_uses_it_for_both_the_session_and_the_ui():
    source = (_BACKEND / "opensquad" / "_runner" / "_turn_loop.py").read_text(encoding="utf-8")

    assert "_steer_text = evt.ui_steer_text()" in source
    assert '"user",\n                            _steer_text,' in source
    assert 'self.runner._emit("user_msg", _steer_text)' in source
