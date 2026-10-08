"""Tests for :mod:`opensquad.mods_compat` — the offline mod compatibility gate.

The point of these tests is not that the scanner returns *a* verdict but that it
returns the verdict the design doc decided, with the exact gap named:

* ``docs/claude-code-mods-compat-v0.md`` §3/§4 are the decision record; the tables
  in ``mods_compat`` are their machine-readable form, so the rows pinned here
  (``tool.call`` served, ``prompt.edit`` refused, ``ui.blit`` refused,
  ``model.complete`` refused) fail loudly if someone quietly flips a decision.
* A real mod from the tutorial (the ``tool.call`` guard that also calls
  ``$.ui.log``) lands on **partial**, not ``runnable`` — the scanner must not
  round that up.

Fixtures are written into ``tmp_path`` rather than kept on disk: the layout is what
matters (``.claude-plugin/plugin.json`` + ``hooks/hooks.json`` + a module), and a
tmp copy keeps the tests off the real workspace.
"""

from __future__ import annotations

import json
import os

import pytest

from opensquad import mods_compat


def _write_mod(
    root, dir_name: str, *, manifest: dict | None = None, hooks: dict | None = None, module: str = ""
) -> str:
    """Materialise a mod directory in the shape a real one has."""
    mod_dir = os.path.join(str(root), dir_name)
    os.makedirs(os.path.join(mod_dir, ".claude-plugin"), exist_ok=True)
    os.makedirs(os.path.join(mod_dir, "hooks"), exist_ok=True)
    with open(os.path.join(mod_dir, ".claude-plugin", "plugin.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest if manifest is not None else {"name": dir_name, "version": "1.0.0"}, fh)
    if hooks is not None:
        with open(os.path.join(mod_dir, "hooks", "hooks.json"), "w", encoding="utf-8") as fh:
            json.dump(hooks, fh)
    if module:
        with open(os.path.join(mod_dir, "hooks", "mod.mjs"), "w", encoding="utf-8") as fh:
            fh.write(module)
    return mod_dir


# The `tool.call` guard from the tutorial, in the shape a real mod ships it.
# Modelled on the reference fixture in deepseek-ai/deepseek-harness (MIT), which
# itself follows Claude Code's events page.
GUARD_MODULE = """
const RISKY = /\\bgit\\s+push\\b.*--force|\\brm\\s+-rf?\\b/

async function guard($, e, next) {
  if (RISKY.test(String(e.command ?? ''))) {
    return { deny: 'refused: ' + e.command }
  }
  const result = await next(e)
  $.ui.log('ran ' + e.tool)
  return result
}

export function register(on) {
  on('tool.call', { tool: 'Bash' }, guard).catch(async ($, e, next) => {
    return { deny: 'the guard failed' }
  })
}
"""


# ── verdicts ───────────────────────────────────────────────────────────────


def test_served_only_mod_is_runnable(tmp_path):
    mod_dir = _write_mod(
        tmp_path,
        "clean-mod",
        hooks={"modules": ["./mod.mjs"]},
        module="""
export function register(on) {
  on('tool.call', async ($, e, next) => {
    const seen = await $.store.get('seen')
    $.clock.now()
    return next(e)
  })
}
""",
    )
    info = mods_compat.scan_mod(mod_dir)

    assert info["verdict"] == "runnable"
    assert info["used_events"] == ["tool.call"]
    assert info["blocked_by"] == []
    assert info["degraded_by"] == []
    assert info["has_manifest"] is True
    assert info["has_hooks"] is True


def test_degraded_reference_makes_it_partial(tmp_path):
    mod_dir = _write_mod(
        tmp_path,
        "partial-mod",
        hooks={"modules": ["./mod.mjs"]},
        module="""
export function register(on) {
  on('tool.call', async ($, e, next) => {
    const history = $.session.messages()
    return next(e)
  })
}
""",
    )
    info = mods_compat.scan_mod(mod_dir)

    assert info["verdict"] == "partial"
    assert [d["name"] for d in info["degraded_by"]] == ["$.session.messages"]
    assert info["blocked_by"] == []


def test_refused_reference_blocks_and_names_the_gap(tmp_path):
    mod_dir = _write_mod(
        tmp_path,
        "blocked-mod",
        hooks={"modules": ["./mod.mjs"]},
        module="""
export function register(on) {
  on('prompt.edit', async ($, e, next) => next(e))
  on('tool.call', async ($, e, next) => {
    const cfg = $.settings.read()
    return next(e)
  })
}
""",
    )
    info = mods_compat.scan_mod(mod_dir)

    assert info["verdict"] == "blocked"
    blocked = {(b["kind"], b["name"]) for b in info["blocked_by"]}
    assert ("event", "prompt.edit") in blocked
    assert ("dollar", "$.settings.read") in blocked
    # Every refusal must carry a reason — "unsupported" alone tells a mod author nothing.
    assert all(b["why"] for b in info["blocked_by"])


def test_tutorial_guard_mod_is_partial_not_runnable(tmp_path):
    """The real tutorial mod uses `$.ui.log`, which we only serve degraded.

    Rounding this up to `runnable` would be the exact kind of over-claim the
    matrix exists to prevent.
    """
    mod_dir = _write_mod(
        tmp_path,
        "guard-mod",
        manifest={"name": "guard-mod", "version": "0.1.0", "description": "block risky commands"},
        hooks={"modules": ["./mod.mjs"]},
        module=GUARD_MODULE,
    )
    info = mods_compat.scan_mod(mod_dir)

    assert info["verdict"] == "partial"
    assert info["used_events"] == ["tool.call"]
    assert [d["name"] for d in info["degraded_by"]] == ["$.ui.log"]
    assert info["has_catch"] is True


def test_plain_typescript_is_accepted_and_tsx_is_not(tmp_path):
    """Policy change in P6-B1: Node 24 strips types, so `.ts` runs as-is.

    `.tsx` still needs a *JSX* transform, which type stripping does not do — that
    stays the gap.
    """
    plain = _write_mod(tmp_path, "ts-mod", hooks={"modules": ["./mod.ts"]})
    # `_write_mod` always writes `hooks/mod.mjs`; this case needs a real `.ts`.
    with open(os.path.join(plain, "hooks", "mod.ts"), "w", encoding="utf-8") as fh:
        fh.write("export function register() {}\n")
    info = mods_compat.scan_mod(plain)
    assert info["modules"] == ["hooks/mod.ts"], info["modules"]
    assert not any(b["kind"] == "module" for b in info["blocked_by"])

    # `.tsx` is loadable too (P6-B2): the host transpiles JSX at load time, and
    # the card says so rather than staying silent about the build step.
    tsx = _write_mod(tmp_path, "tsx-mod", hooks={"modules": ["./mod.tsx"]})
    with open(os.path.join(tsx, "hooks", "mod.tsx"), "w", encoding="utf-8") as fh:
        fh.write("export function register() {}\n")
    info = mods_compat.scan_mod(tsx)
    assert info["modules"] == ["hooks/mod.tsx"]
    assert not any(b["kind"] == "module" for b in info["blocked_by"])
    assert any("JSX" in n for n in info["notes"])


# ── matrix self-consistency ────────────────────────────────────────────────


def test_every_degraded_verdict_explains_how_it_differs():
    """`degraded` means "served, but differently" — so the difference is the point.

    An empty reason renders as a bare "降级（行为有差异）" on the Mods page, which
    tells the reader nothing. (Eleven entries shipped that way.)
    """
    empty = [
        f"$.{key}"
        for key, (verdict, why) in mods_compat._DOLLAR_TABLE.items()
        if verdict == mods_compat.DEGRADED and not why.strip()
    ] + [
        name
        for name, (verdict, why) in mods_compat._EVENT_TABLE.items()
        if verdict == mods_compat.DEGRADED and not why.strip()
    ]
    assert empty == [], f"degraded verdicts with no stated difference: {empty}"


def test_wired_members_are_never_advertised_as_refused():
    """What we ship must not contradict the matrix the page renders next to it."""
    contradictions = [
        member
        for member in mods_compat.WIRED["dollar"]
        if mods_compat.dollar_verdict(*member.removeprefix("$.").split(".", 1))[0] == mods_compat.REFUSED
    ]
    assert contradictions == []


# ── render tree validation (铁律 3) ────────────────────────────────────────


def _node(kind, props=None, children=None):
    out = {"type": kind, "props": props or {}}
    if children is not None:
        out["children"] = children
    return out


def test_validation_keeps_the_whitelist_and_drops_the_rest():
    tree = _node(
        "Box",
        {"flexDirection": "row", "backgroundColor": "#fff"},
        [_node("Text", {"bold": True, "children": "hi"})],
    )
    result = mods_compat.validate_element_tree(tree)
    assert result["node"]["type"] == "Box"
    assert result["node"]["props"] == {"flexDirection": "row"}
    assert any("backgroundColor" in note for note in result["dropped"])


def test_structural_key_is_accepted_without_a_diagnostic():
    """`key` is a list hint every real mod sets; dropping it spammed a
    diagnostic per node (found by the real-mod smoke)."""
    result = mods_compat.validate_element_tree(_node("Text", {"key": "d0", "bold": True}, ["hi"]))
    assert result["node"]["props"] == {"key": "d0", "bold": True}
    assert result["dropped"] == []


def test_validation_refuses_a_client_element_with_a_reason():
    result = mods_compat.validate_element_tree(_node("Client", {"module": "./evil.js"}))
    assert result["node"] is None
    assert any("任意前端模块" in note for note in result["dropped"])


def test_validation_refuses_an_unknown_element():
    result = mods_compat.validate_element_tree(_node("Marquee", {}))
    assert result["node"] is None
    assert any("未知元素" in note for note in result["dropped"])


def test_validation_caps_depth_and_node_count():
    deep = _node("Text", {}, ["leaf"])
    for _ in range(mods_compat.RENDER_MAX_DEPTH + 2):
        deep = _node("Box", {}, [deep])
    result = mods_compat.validate_element_tree(deep)
    assert any("最大深度" in note for note in result["dropped"])

    wide = _node("Box", {}, [_node("Text", {}, ["x"]) for _ in range(mods_compat.RENDER_MAX_NODES + 5)])
    result = mods_compat.validate_element_tree(wide)
    assert any("节点数超过上限" in note for note in result["dropped"])


def test_validation_truncates_a_huge_text_node():
    result = mods_compat.validate_element_tree(_node("Text", {}, ["x" * (mods_compat.RENDER_MAX_TEXT + 50)]))
    assert len(result["node"]["children"][0]) == mods_compat.RENDER_MAX_TEXT


def test_validation_rejects_a_non_element():
    result = mods_compat.validate_element_tree({"nothing": "useful"})
    assert result["node"] is None
    assert any("未知元素" in note for note in result["dropped"])


# ── host observation journal ───────────────────────────────────────────────


def test_host_status_aggregates_recent_observations(tmp_path, monkeypatch):
    monkeypatch.setattr(mods_compat, "mods_state_root", lambda: str(tmp_path / "data"))
    mods_compat.write_host_status("a1", state="running", mods=2)
    mods_compat.write_host_status("a2", state="degraded", last_failure="tool.call timed out")

    status = mods_compat.host_status()
    assert status["observed"]["running"] == 1
    assert status["observed"]["degraded"] == 1
    assert status["observed"]["stale"] == 0
    assert status["last_failure"] == "tool.call timed out"


def test_host_status_counts_a_stale_observation(tmp_path, monkeypatch):
    """A killed agent leaves its record behind — age it out instead of lying."""
    monkeypatch.setattr(mods_compat, "mods_state_root", lambda: str(tmp_path / "data"))
    mods_compat.write_host_status("dead", state="running")
    path = mods_compat.host_journal_path("dead")
    with open(path, encoding="utf-8") as fh:
        record = json.load(fh)
    record["updated_at_ts"] -= mods_compat.HOST_STALE_SECONDS + 5
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh)

    status = mods_compat.host_status()
    assert status["observed"]["running"] == 0
    assert status["observed"]["stale"] == 1


def test_host_journal_path_sanitises_the_agent_id():
    assert mods_compat.host_journal_path("a/../../etc").endswith("a_.._.._etc.json")
    assert mods_compat.host_journal_path("").endswith("unknown.json")


# ── per-contribution loading ───────────────────────────────────────────────


def test_load_plan_keeps_a_mod_with_refusals_and_names_them(tmp_path):
    """A refused member costs the mod that contribution, not the whole mod."""
    mod_dir = _write_mod(
        tmp_path,
        "with-gaps",
        hooks={"modules": ["./mod.mjs"]},
        module="on('tool.call', () => ({ deny: 'x' }))\non('ui.close', () => {})",
    )
    info = mods_compat.scan_mod(mod_dir)
    assert info["verdict"] == "blocked"

    plan = mods_compat.load_plan(info)
    assert plan["loadable"] is True
    assert plan["modules"] == ["hooks/mod.mjs"]
    assert any(b["kind"] == "event" and b["name"] == "ui.close" for b in plan["inert"])
    assert plan["reason"] == ""


def test_effect_is_none_when_no_hooked_event_ever_fires(tmp_path):
    """A ui.render-only mod loads but can never start: that is `no_effect`."""
    mod_dir = _write_mod(
        tmp_path,
        "render-only",
        hooks={"modules": ["./mod.mjs"]},
        module="on('ui.render', async ($, e, next) => { await $.ui.open(); return next(e) })",
    )
    effect = mods_compat.effect_summary(mods_compat.scan_mod(mod_dir))
    assert effect["kind"] == "no_effect"
    assert effect["events"] == []
    assert effect["draws"] is True


def test_effect_follows_whether_a_slot_is_actually_wired(tmp_path, monkeypatch):
    """`invisible` is not a property of the mod — it is a property of the host.

    The scanner sees `ui.render` but not which component it targets, so the only
    honest statement is "a slot is wired or not". This locks that the verdict
    flips with the host, not with a hard-coded string.
    """
    mod_dir = _write_mod(
        tmp_path,
        "draws-on-tool-call",
        hooks={"modules": ["./mod.mjs"]},
        module="on('tool.call', async ($, e, next) => { $.ui.invalidate('ui.render'); return next(e) })",
    )
    info = mods_compat.scan_mod(mod_dir)

    monkeypatch.setitem(mods_compat.WIRED, "slots", ())
    invisible = mods_compat.effect_summary(info)
    assert invisible["kind"] == "invisible"
    assert invisible["events"] == ["tool.call"]
    assert invisible["draws"] is True

    monkeypatch.setitem(mods_compat.WIRED, "slots", ("AbovePrompt",))
    assert mods_compat.effect_summary(info)["kind"] == "works"


def test_effect_is_works_when_a_served_path_is_not_a_drawing(tmp_path):
    mod_dir = _write_mod(
        tmp_path,
        "reads-files",
        hooks={"modules": ["./mod.mjs"]},
        module="on('turn.complete', async ($, e, next) => { await $.fs.read('a.txt'); return next(e) })",
    )
    effect = mods_compat.effect_summary(mods_compat.scan_mod(mod_dir))
    assert effect["kind"] == "works"
    assert effect["events"] == ["turn.complete"]
    assert effect["dollar"] == ["$.fs.read"]
    assert effect["draws"] is False


def test_effect_is_not_loadable_without_a_module(tmp_path):
    mod_dir = _write_mod(tmp_path, "ts-effect", hooks={"modules": ["./mod.ts"]})
    assert mods_compat.effect_summary(mods_compat.scan_mod(mod_dir))["kind"] == "not_loadable"


def test_load_plan_refuses_a_mod_with_no_resolvable_module(tmp_path):
    """The only fatal case left: hooks.json declares a module that is not there."""
    mod_dir = _write_mod(tmp_path, "ghost", hooks={"modules": ["./nowhere.mjs"]})
    plan = mods_compat.load_plan(mods_compat.scan_mod(mod_dir))
    assert plan["loadable"] is False
    assert "modules" in plan["reason"]


def test_load_plan_has_nothing_inert_for_a_clean_mod(tmp_path):
    mod_dir = _write_mod(
        tmp_path, "clean", hooks={"modules": ["./mod.mjs"]}, module="on('tool.call', () => ({ deny: 'x' }))"
    )
    plan = mods_compat.load_plan(mods_compat.scan_mod(mod_dir))
    assert plan["loadable"] is True
    assert plan["inert"] == []


def test_missing_hooks_json_is_unknown_with_a_note(tmp_path):
    mod_dir = _write_mod(tmp_path, "no-hooks", hooks=None)
    info = mods_compat.scan_mod(mod_dir)

    assert info["verdict"] == "unknown"
    assert info["has_hooks"] is False
    assert any("hooks/hooks.json" in n for n in info["notes"])


def test_module_declared_but_absent_is_reported(tmp_path):
    mod_dir = _write_mod(tmp_path, "ghost-mod", hooks={"modules": ["./nowhere.mjs"]})
    info = mods_compat.scan_mod(mod_dir)

    assert info["verdict"] == "unknown"
    assert any("nowhere.mjs" in n for n in info["notes"])


# ── table decisions (the doc's §3/§4, pinned) ──────────────────────────────


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ("tool.call", mods_compat.SERVED),
        ("turn.complete", mods_compat.SERVED),
        ("command.run", mods_compat.SERVED),
        ("prompt.submit", mods_compat.DEGRADED),
        ("ui.render", mods_compat.DEGRADED),
        ("prompt.edit", mods_compat.REFUSED),
        # Re-rated in M1 P3: we *do* emit a tick per tool round (degraded, not
        # Claude Code's async-generator streaming). See the doc's §3 row.
        ("turn.step", mods_compat.DEGRADED),
        ("engine.create", mods_compat.REFUSED),
    ],
)
def test_event_verdicts_match_the_design_doc(event, expected):
    verdict, why = mods_compat.event_verdict(event)
    assert verdict == expected
    if expected == mods_compat.REFUSED:
        assert why


@pytest.mark.parametrize(
    ("ns", "member", "expected"),
    [
        ("plugin", "name", mods_compat.SERVED),
        ("store", "keys", mods_compat.SERVED),
        ("clock", "every", mods_compat.SERVED),
        ("session", "messages", mods_compat.DEGRADED),
        ("ui", "resolve", mods_compat.DEGRADED),
        ("fs", "write", mods_compat.DEGRADED),
        ("ui", "blit", mods_compat.REFUSED),
        ("settings", "read", mods_compat.REFUSED),
        ("model", "complete", mods_compat.REFUSED),
        ("session", "authorize", mods_compat.REFUSED),
    ],
)
def test_dollar_verdicts_match_the_design_doc(ns, member, expected):
    assert mods_compat.dollar_verdict(ns, member)[0] == expected


def test_unknown_names_are_refused_not_ignored():
    """Outside the matrix the answer is a named refusal, never a silent pass."""
    verdict, why = mods_compat.event_verdict("something.brand.new")
    assert verdict == mods_compat.REFUSED
    assert why

    verdict, why = mods_compat.dollar_verdict("nope", "whatever")
    assert verdict == mods_compat.REFUSED
    assert why


def test_unlisted_member_of_a_known_namespace_uses_the_namespace_default():
    assert mods_compat.dollar_verdict("store", "delete")[0] == mods_compat.SERVED
    assert mods_compat.dollar_verdict("ui", "scrollBy")[0] == mods_compat.REFUSED


# ── matrix summary & state ────────────────────────────────────────────────


def test_matrix_summary_lists_are_disjoint_and_complete():
    summary = mods_compat.matrix_summary()
    for group in ("events", "dollar"):
        served = set(summary[group]["served"])
        degraded = set(summary[group]["degraded"])
        refused = set(summary[group]["refused"])
        assert not (served & degraded), group
        assert not (served & refused), group
        assert not (degraded & refused), group

    assert "tool.call" in summary["events"]["served"]
    assert "prompt.edit" in summary["events"]["refused"]
    assert "$.store" not in summary["dollar"]["served"]  # members, not namespaces
    assert any(d.startswith("$.store.") for d in summary["dollar"]["served"])


def test_availability_tracks_node_and_always_carries_a_scope(monkeypatch):
    """Compatibility must never be rendered as availability.

    `available` answers only "can a host start" (node present). Since M0 wires a
    single event, availability is only honest when it is accompanied by the
    scope that is actually wired — and it must be False with no node at all.
    """
    monkeypatch.setattr(mods_compat, "_node_hint", lambda: "")
    missing = mods_compat.host_status()
    assert missing["available"] is False
    assert missing["reason"]

    monkeypatch.setattr(mods_compat, "_node_hint", lambda: os.path.join(os.sep, "fake", "node"))
    found = mods_compat.host_status()
    assert found["available"] is True
    assert "tool.call" in found["scope"], "availability without a stated scope would be a lie"


def test_discover_mods_reads_state_from_the_injected_root(tmp_path):
    mods_root = tmp_path / "mods"
    state_root = tmp_path / "state"
    _write_mod(mods_root, "alpha", hooks={"modules": ["./mod.mjs"]}, module="on('tool.call', ($, e, next) => next(e))")
    _write_mod(mods_root, "beta", hooks={"modules": ["./mod.mjs"]}, module="on('prompt.edit', ($, e, next) => next(e))")

    found = mods_compat.discover_mods(str(mods_root), str(state_root))
    assert [m["dir_name"] for m in found] == ["alpha", "beta"]
    assert all(m["state"]["enabled"] is False for m in found)

    mods_compat.write_mod_state("alpha", enabled=True, state_root=str(state_root))
    again = {m["dir_name"]: m for m in mods_compat.discover_mods(str(mods_root), str(state_root))}
    assert again["alpha"]["state"]["enabled"] is True
    # The toggle must not leak into the vendor manifest.
    with open(os.path.join(str(mods_root), "alpha", ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
        assert "enabled" not in json.load(fh)


def test_discover_mods_on_a_missing_root_is_empty_not_an_error(tmp_path):
    assert mods_compat.discover_mods(str(tmp_path / "nope"), str(tmp_path / "state")) == []
