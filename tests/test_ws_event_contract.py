"""Regression lock: the WS event-type contract has ONE source of truth.

Why this exists
---------------
An agent-origin WS frame has to be registered in three different places, in two
different processes::

    launcher ``GatewayAdapter`` relay subscriptions   (agent bus -> gateway)
    gateway  ``_AGENT_OUTPUT_BROADCAST_TYPES``        (broadcast vs directed)
    gateway  ``_agent_message_loop`` dispatch gate    (handle vs drop)

Forgetting one makes the frame vanish into
``logger.warning("Unknown message from agent ...")`` — the event is on disk, the
wire looks healthy, and only the UI is silently empty. Two real incidents:

  * ``turn_usage``     — missing from both gateway lists (消耗 badge never showed)
  * ``turn_cancelled`` — missing from the dispatch gate, although the frontend
                         has a handler for it: the frame was dropped before it
                         could ever reach the browser

All three places now derive from ``opensquad.protocol_version``. These tests fail
if a literal list is ever re-inlined, and assert the set relations that keep a
"relayed but undispatchable" type from being added silently.

The field-name half of the same module is checked across languages here too:
Python cannot import the TypeScript mirror (``utils/wsFieldNames.ts``) and vice
versa, so the two literal tables are compared as text.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from opensquad.protocol_version import (
    AGENT_OUTPUT_BROADCAST_TYPES,
    AGENT_OUTPUT_DISPATCH_TYPES,
    BUS_TOPIC_TO_WS_TYPE,
    EVENT_TYPES,
    GENERIC_RELAY_TOPICS,
    LAUNCHER_RELAY_TOPICS,
    RELAYED_WITHOUT_DISPATCH,
    WS_FIELD_NAMES,
    relayed_event_types,
)

_REPO = Path(__file__).resolve().parents[1]
_WS_PY = _REPO / "src" / "opensquad" / "gateway" / "backend" / "app" / "ai_web" / "websocket.py"
_ADAPTER_PY = _REPO / "src" / "opensquad" / "gateway_adapter.py"
_TS_FIELDS = _REPO / "src" / "opensquad" / "gateway" / "nexuschat-pro" / "utils" / "wsFieldNames.ts"

# Registration-point names that must never be assigned an inline literal again.
_DERIVED_NAMES = ("_AGENT_OUTPUT_BROADCAST_TYPES", "_AGENT_OUTPUT_DISPATCH_TYPES")


def _ws_tree() -> ast.Module:
    return ast.parse(_WS_PY.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The three registration points must derive, not re-declare
# ---------------------------------------------------------------------------


def test_gateway_imports_both_allow_lists_from_protocol_version() -> None:
    """The two gateway allow-lists must be imports, not literals."""
    imported = set()
    for node in ast.walk(_ws_tree()):
        if isinstance(node, ast.ImportFrom) and node.module == "opensquad.protocol_version":
            # The module renames them to private aliases (``as _AGENT_...``), so
            # match on the *source* names — checking the bound name would miss it.
            imported |= {a.name for a in node.names}
            imported |= {a.asname for a in node.names if a.asname}
    for name in ("AGENT_OUTPUT_BROADCAST_TYPES", "AGENT_OUTPUT_DISPATCH_TYPES"):
        assert name in imported, (
            f"{name} is no longer imported from opensquad.protocol_version — "
            "a hand-written copy drifts out of sync (see the module docstring)"
        )


def test_gateway_has_no_inlined_allow_list() -> None:
    """No module-level assignment of the derived names, and no frozenset copy."""
    tree = _ws_tree()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
            assert not (targets & set(_DERIVED_NAMES)), (
                f"{sorted(targets & set(_DERIVED_NAMES))} is assigned inline again — "
                "derive it from opensquad.protocol_version instead"
            )
        # A re-inlined copy shows up as a frozenset(...) literal carrying a
        # type name that is unique to the agent-output contract.
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "frozenset":
            for arg in node.args:
                if isinstance(arg, (ast.Set, ast.List, ast.Tuple)):
                    values = {e.value for e in arg.elts if isinstance(e, ast.Constant)}
                    assert "turn_usage" not in values, (
                        "an inline frozenset of WS event types was re-added to websocket.py — "
                        "the allow-lists must derive from opensquad.protocol_version"
                    )


def _message_loop_node() -> ast.AsyncFunctionDef | None:
    for node in ast.walk(_ws_tree()):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_agent_message_loop":
            return node
    return None


def test_dispatch_gate_compares_against_the_derived_constant() -> None:
    """The dispatch gate must name the constant, not a list.

    Scoped to ``_agent_message_loop`` on purpose: the same file also contains
    ``msg_type in _AGENT_OUTPUT_BROADCAST_TYPES``, and a whole-file walk would
    happily resolve that one instead (the exact false-negative that
    ``tests/test_task_rpc_bridge.py`` documents for its own resolver).
    """
    loop = _message_loop_node()
    assert loop is not None, "_agent_message_loop not found in websocket.py"

    gate: ast.AST | None = None
    for node in ast.walk(loop):
        if not isinstance(node, ast.Compare):
            continue
        if not (isinstance(node.left, ast.Name) and node.left.id == "msg_type"):
            continue
        if not any(isinstance(op, ast.In) for op in node.ops):
            continue
        comp = node.comparators[0]
        if (isinstance(comp, (ast.List, ast.Set, ast.Tuple)) and gate is None) or (
            isinstance(comp, ast.Name) and comp.id != "_AGENT_OUTPUT_BROADCAST_TYPES" and gate is None
        ):
            gate = comp
    assert gate is not None, "the `msg_type in ...` dispatch gate was not found in _agent_message_loop"
    assert isinstance(gate, ast.Name) and gate.id == "_AGENT_OUTPUT_DISPATCH_TYPES", (
        "the dispatch gate is a literal again — it must be `msg_type in _AGENT_OUTPUT_DISPATCH_TYPES`"
    )


def test_launcher_iterates_the_shared_relay_table() -> None:
    """The launcher must subscribe from LAUNCHER_RELAY_TOPICS, not per-topic calls."""
    src = _ADAPTER_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "opensquad.protocol_version":
            imported |= {a.asname or a.name for a in node.names}
    assert "LAUNCHER_RELAY_TOPICS" in imported, (
        "gateway_adapter.py must import LAUNCHER_RELAY_TOPICS from opensquad.protocol_version"
    )

    # A hand-written `_sub("some_topic", ...)` call is the drift we are removing.
    inline = [
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_sub"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ]
    assert not inline, f"gateway_adapter.py still hard-codes relay subscriptions: {inline}"

    assert re.search(r"for\s+\w+\s+in\s+LAUNCHER_RELAY_TOPICS\s*:", src), (
        "the launcher no longer iterates LAUNCHER_RELAY_TOPICS"
    )


# ---------------------------------------------------------------------------
# Set relations that keep a silent drop impossible
# ---------------------------------------------------------------------------


def test_every_registration_point_is_a_subset_of_event_types() -> None:
    assert set(AGENT_OUTPUT_BROADCAST_TYPES) <= set(EVENT_TYPES)
    assert set(AGENT_OUTPUT_DISPATCH_TYPES) <= set(EVENT_TYPES)
    assert relayed_event_types() <= set(EVENT_TYPES)


def test_relayed_types_are_either_dispatchable_or_explicitly_exempt() -> None:
    """A relayed type the gateway cannot dispatch must be listed as exempt.

    This is the turn_cancelled bug turned into a test: the launcher relayed it,
    the gateway did not handle it, and nothing anywhere said so.
    """
    unhandled = relayed_event_types() - set(AGENT_OUTPUT_DISPATCH_TYPES) - set(RELAYED_WITHOUT_DISPATCH)
    assert not unhandled, (
        f"relayed to the gateway but neither dispatchable nor listed in "
        f"RELAYED_WITHOUT_DISPATCH: {sorted(unhandled)} — the frame would be dropped "
        "with 'Unknown message from agent'"
    )


def test_broadcast_types_are_always_dispatchable() -> None:
    """``BROADCAST <= DISPATCH``.

    This is the invariant behind the turn_cancelled incident: a type that is
    broadcast-eligible but missing from the gate is dropped one branch earlier,
    so its "broadcast" policy can never take effect. Asserting it here means the
    two sets may drift apart later, but only in the harmless direction.
    """
    orphaned = set(AGENT_OUTPUT_BROADCAST_TYPES) - set(AGENT_OUTPUT_DISPATCH_TYPES)
    assert not orphaned, (
        f"broadcast-eligible but not dispatched: {sorted(orphaned)} — the frame is "
        "dropped by the dispatch gate before any broadcast decision happens"
    )


def test_dispatch_set_is_derived_from_the_broadcast_set() -> None:
    """The gate must not silently lose a type the broadcast policy lists."""
    assert set(AGENT_OUTPUT_DISPATCH_TYPES) == set(AGENT_OUTPUT_BROADCAST_TYPES) | {"turn_cancelled"}


def test_broadcast_and_dispatch_agree_on_the_shared_core() -> None:
    """Types that must both reach every pane AND survive the gate."""
    required = {
        "turn_start",
        "turn_elapsed",
        "turn_usage",
        "token_stats",  # 消耗 badge
        "turn_cancelled",  # frontend has a handler; used to be dropped
        "compression_progress",
        "task_update",
        "task_removed",
    }
    missing_broadcast = sorted(required - set(AGENT_OUTPUT_BROADCAST_TYPES))
    missing_dispatch = sorted(required - set(AGENT_OUTPUT_DISPATCH_TYPES))
    assert not missing_broadcast, f"missing from AGENT_OUTPUT_BROADCAST_TYPES: {missing_broadcast}"
    assert not missing_dispatch, (
        f"missing from AGENT_OUTPUT_DISPATCH_TYPES: {missing_dispatch} — "
        "the frame is logged as 'Unknown message from agent' and dropped"
    )


def test_event_types_covers_every_relayed_topic() -> None:
    """The relayed WS types must be declared, not just implied."""
    assert relayed_event_types() <= set(EVENT_TYPES)
    assert set(LAUNCHER_RELAY_TOPICS) == set(BUS_TOPIC_TO_WS_TYPE) | set(GENERIC_RELAY_TOPICS)
    assert len(set(LAUNCHER_RELAY_TOPICS)) == len(LAUNCHER_RELAY_TOPICS), "duplicate relay topic"


def test_bus_rename_table_matches_the_adapters_wire_types() -> None:
    """The bus->WS renames must be the ones the adapter actually emits."""
    src = _ADAPTER_PY.read_text(encoding="utf-8")
    # on_runner_output emits "message" (for to_user_final / to_user_reply),
    # on_runner_stream emits "stream", and so on: every rename target must appear
    # as a literal `_send_event(..., "<type>"` in the adapter.
    for topic, ws_type in BUS_TOPIC_TO_WS_TYPE.items():
        if ws_type == topic:
            continue  # no rename
        assert re.search(rf'_send_event\([^)]*"{re.escape(ws_type)}"', src, re.S), (
            f"{topic} is declared to become {ws_type!r} but gateway_adapter.py never emits {ws_type!r}"
        )


def test_relay_handler_methods_are_real_and_are_relay_topics() -> None:
    """``_RELAY_HANDLER_METHODS`` must not name a method that does not exist.

    A typo there raises AttributeError at agent boot, so it is worth an AST check
    that costs nothing at runtime.
    """
    tree = ast.parse(_ADAPTER_PY.read_text(encoding="utf-8"))
    mapping: dict[str, str] = {}
    methods: set[str] = set()
    for node in ast.walk(tree):
        # Annotated at module level (`_RELAY_HANDLER_METHODS: dict[str, str] = {...}`),
        # so it is an AnnAssign, not an Assign.
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (
                {t.id for t in node.targets if isinstance(t, ast.Name)}
                if isinstance(node, ast.Assign)
                else ({node.target.id} if isinstance(node.target, ast.Name) else set())
            )
            if "_RELAY_HANDLER_METHODS" in targets and isinstance(node.value, ast.Dict):
                for k, v in zip(node.value.keys, node.value.values, strict=True):
                    if isinstance(k, ast.Constant) and isinstance(v, ast.Constant):
                        mapping[k.value] = v.value
        if isinstance(node, ast.ClassDef) and node.name == "GatewayAdapter":
            methods = {n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert mapping, "_RELAY_HANDLER_METHODS not found in gateway_adapter.py"
    assert set(mapping) <= set(LAUNCHER_RELAY_TOPICS), (
        f"handler map keys are not relay topics: {sorted(set(mapping) - set(LAUNCHER_RELAY_TOPICS))}"
    )
    unknown = {k: v for k, v in mapping.items() if v not in methods}
    assert not unknown, f"_RELAY_HANDLER_METHODS names missing GatewayAdapter methods: {unknown}"


# ---------------------------------------------------------------------------
# Cross-language field-name spelling
# ---------------------------------------------------------------------------


def _ts_field_values() -> set[str]:
    src = _TS_FIELDS.read_text(encoding="utf-8")
    body = src[src.index("export const WS_FIELDS") :]
    body = body[: body.index("} as const")]
    return set(re.findall(r":\s*'([^']+)'", body))


def test_ts_field_mirror_exists() -> None:
    assert _TS_FIELDS.is_file(), f"missing {_TS_FIELDS}"


def test_field_names_match_across_python_and_typescript() -> None:
    """The wire field table must be identical on both sides.

    Neither language can import the other's constants, so the tables are compared
    as text. The incident this locks: `compression_progress` payloads written
    with `isFinal` while every consumer read `is_final`.
    """
    ts_values = _ts_field_values()
    assert ts_values == set(WS_FIELD_NAMES), (
        "wsFieldNames.ts and protocol_version.py disagree — "
        f"only in TS: {sorted(ts_values - set(WS_FIELD_NAMES))}; "
        f"only in Python: {sorted(set(WS_FIELD_NAMES) - ts_values)}"
    )


@pytest.mark.parametrize("wrong", ["isFinal", "is_final_done", "final"])
def test_camel_case_spellings_are_not_part_of_the_contract(wrong: str) -> None:
    assert wrong not in WS_FIELD_NAMES


def test_is_final_is_snake_case() -> None:
    assert "is_final" in WS_FIELD_NAMES
