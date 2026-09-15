"""Parameter annotations must survive the trip into the JSON Schema the model reads.

Two real regressions are pinned here. Both were silent — nothing raised, the
schema just lied, and the model obeyed the lie.

1. ``suggest_followups.suggestions`` was annotated ``Any``, and the ``Any``
   fallback emits ``{"type": "string"}``. The docstring asked for an array of
   1-3 items, the schema said string, so the model sent one string and the
   follow-up-chips feature degraded to a single chip.
2. ``_python_type_to_json_schema`` only matched ``typing.Union``. PEP 604
   ``X | None`` has origin ``types.UnionType``, so it fell through to the same
   string fallback — ``start_job.max_wait_seconds: float | None`` was advertised
   as a string, telling the model to quote a numeric timeout.

No ``from __future__ import annotations`` here on purpose: it would stringify the
annotations under test and make the whole file vacuous.
"""

from typing import Any

from opensquad.registry import ToolRegistry


def _probe(
    opt_array: list[str] | None = None,
    pep604_float: float | None = None,
    pep604_array: list[str] | None = None,
    pep604_mapping: dict[str, int] | None = None,
    pep604_plain_str: str | None = None,
    pep604_int: int | None = None,
    bare_any: Any = None,
) -> str:
    """Annotation zoo — never called, only introspected."""
    return ""


def _props() -> dict:
    return ToolRegistry()._extract_parameters_schema(_probe)["properties"]


def test_optional_list_becomes_array_of_string():
    # The shape `followup_tools.suggestions` needs: array is the primary signal,
    # so the model sends 1-3 items rather than one blob.
    assert _props()["opt_array"] == {
        "type": "array",
        "items": {"type": "string"},
        "description": "Parameter opt_array",
    }


def test_pep604_float_optional_is_a_number_not_a_string():
    # The regression: origin is types.UnionType, which used to be unmatched.
    assert _props()["pep604_float"]["type"] == "number"


def test_pep604_list_optional_is_an_array():
    assert _props()["pep604_array"]["type"] == "array"
    assert _props()["pep604_array"]["items"] == {"type": "string"}


def test_pep604_dict_optional_is_an_object():
    assert _props()["pep604_mapping"]["type"] == "object"


def test_pep604_int_optional_is_an_integer():
    assert _props()["pep604_int"]["type"] == "integer"


def test_pep604_str_optional_still_maps_to_string():
    # Unchanged behaviour, kept so the new branch cannot silently swallow it.
    assert _props()["pep604_plain_str"]["type"] == "string"


def test_typing_union_optional_still_works():
    # `typing.Optional[X]` is `typing.Union[X, None]` — the branch that already
    # worked, and the one ruff's UP045 pushes callers away from. Exercised as a
    # runtime expression (not an annotation) so both spellings stay covered.
    from typing import Union

    # noqa: UP007 — the legacy spelling IS the thing under test here.
    legacy_union = Union[list[str], None]  # noqa: UP007
    assert ToolRegistry()._python_type_to_json_schema(legacy_union) == {
        "type": "array",
        "items": {"type": "string"},
    }


def test_bare_any_falls_back_to_string():
    # Documented fallback, not a bug: `Any` carries no shape. The fix for the
    # follow-up tool was to stop annotating `Any`, not to guess a type here.
    assert _props()["bare_any"]["type"] == "string"


def test_real_suggest_followups_advertises_an_array():
    """End-to-end: the actual tool's actual annotation reaches the schema."""
    from opensquad.tools import followup_tools

    props = ToolRegistry()._extract_parameters_schema(followup_tools.suggest_followups)["properties"]
    assert props["suggestions"]["type"] == "array"
    assert props["suggestions"]["items"]["type"] == "string"
    assert "array" in props["suggestions"]["description"].lower()
