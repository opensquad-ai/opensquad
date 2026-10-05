"""An unaddressed pane message is refused, not delivered to a guess.

The user's report was a session in one workspace answering from another's folder. One of the ways
that happened: switch_and_reply without a session id (a pane whose session did not exist yet) pushed
``__SWITCH_AND_REPLY__::<text>`` — an empty address, which the agent resolves to whichever session it
still considers current, i.e. the pane the user just left. The message quietly continued the old
conversation and inherited its working directory.
"""

import re
from pathlib import Path

ADAPTER = Path(__file__).resolve().parents[1] / "src" / "opensquad" / "gateway_adapter.py"


def test_the_empty_address_push_is_gone():
    text = ADAPTER.read_text(encoding="utf-8")

    assert "__SWITCH_AND_REPLY__:{sid}:{reply}" not in text, "the empty-address push is back"
    # The only remaining unaddressed push in this handler must be a refusal, not a delivery.
    assert "switch_and_reply without a session_id -> refused" in text


def test_the_refusal_sits_in_the_else_of_the_sid_check():
    """The guard has to be where an empty sid would be used, not somewhere nearby."""
    text = ADAPTER.read_text(encoding="utf-8")
    block = text[text.index('if command == "switch_and_reply"') : text.index('if command == "switch_model"')]

    assert "if sid and reply:" in block
    assert re.search(
        r"else:\s*\n(?:\s*#.*\n)*\s*logger\.warning\(\s*\n?\s*\"\[Adapter\] switch_and_reply without a session_id",
        block,
    )
