"""A members argument that arrives as a string must never be read one character at a time.

Reported from a real run: start_collaboration was called with members='["pm"]' - a string. The
loop that records participants iterated it, so five members were invented - [, ", p, m and ] -
all permanently invited, because no such agent exists to accept. The collaboration card grew
around them, and assign_task, which validates the same member table, then refused to dispatch.
The mistake surfaced several steps later, as "dispatch denied", with nothing pointing at the
argument.

An obvious JSON list is parsed; anything else that is not a list is refused where it is given.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from opensquad.tools import collaboration as collab  # noqa: E402


def _call(members):
    return collab.start_collaboration(card="no-such-card", members=members)


def test_a_bare_string_is_refused_rather_than_exploded_into_members():
    result = _call("pm")

    assert result["status"] == "error"
    assert result["code"] == "members_invalid"
    assert "string" in result["message"]
    assert "character" in result["message"], "the message must say why this shape is dangerous"


def test_a_string_that_is_not_a_list_is_refused():
    for value in ("['pm']", '{"members": ["pm"]}', " pm ", "pm,qa", ""):
        result = _call(value)
        assert result.get("code") == "members_invalid", value


def test_other_shapes_are_refused():
    for value in (42, {"pm": True}):
        result = _call(value)
        assert result.get("code") == "members_invalid", value


def test_a_json_list_is_parsed_and_then_treated_as_the_list_it_describes():
    result = _call('["pm", "qa"]')

    assert result.get("code") != "members_invalid", "an obvious JSON list is what the caller meant"


def test_a_real_list_still_goes_straight_through():
    result = _call(["pm"])

    assert result.get("code") != "members_invalid"


def test_none_and_an_empty_list_are_still_allowed():
    for value in (None, []):
        result = _call(value)
        assert result.get("code") != "members_invalid", value
