#!/usr/bin/env python
"""Smoke-test the mods host against mods installed in a workspace.

Four separate questions, because they have different answers:

1. **Verdict** — what the scanner says (static compatibility).
2. **Load**     — would the real loader keep it? Since per-contribution loading,
   a `blocked` mod still loads as long as it has a resolvable module; only
   "no module at all" (TypeScript source) is fatal.
3. **Inert**    — which declared capabilities the loader kept but that can never
   fire here (we never emit their events, never provide their `$`).
4. **Reach**    — did the mod's `tool.call` handler actually run, and which `$`
   members did it reach for that we do not serve? Handler *throws* are useless
   as evidence: real mods wrap their work in `catch {}` on purpose ("recording
   must never stop the edit"), so a mod can run and silently do nothing. That is
   why the host is run with `OPENSQUAD_MODS_HOST_TRACE=1`.

Usage:
    python scripts/mods_smoke.py [--mods-root DIR] [--tools Edit,Bash]

`--mods-root` defaults to the workspace mods root (`<workspace>/mods`).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from opensquad import mods_compat  # noqa: E402
from plugins.mods_host import _node  # noqa: E402
from plugins.mods_host.plugin import ModsHostPlugin  # noqa: E402


def _args_for(tool: str, probe_file: str) -> dict:
    """Tool arguments shaped like the real thing, so mods take their real branch."""
    if tool == "Edit":
        return {"file_path": probe_file, "old_string": "note", "new_string": "note (edited)"}
    if tool == "Write":
        # The file exists, so mods that diff against the previous content take
        # their read path rather than reporting "new file".
        return {"file_path": probe_file, "content": "note\nsecond line\n"}
    if tool == "MultiEdit":
        return {"file_path": probe_file, "edits": [{"old_string": "a", "new_string": "b"}]}
    return {}


SMOKE_AGENT_ID = "mods-smoke"


class _SmokeContext:
    """Minimal stand-in so the journal does not land under `unknown` (a real agent
    id would otherwise be reused/ghosted on the Mods page)."""

    agent_id = SMOKE_AGENT_ID


def _effective(probe: dict) -> bool:
    """The kill-criteria question: did the mod *do* anything observable?

    Loading and registering are not effect — a mod can do both and still never
    run (none of its events fire). This counts what a user would notice.
    """
    if (probe.get("render") or {}).get("roots"):
        return True
    if any((p or {}).get("kinds") for p in probe.get("panes") or []):
        return True
    if (probe.get("invoked") or {}).get("tool.call"):
        return True
    verdict = (probe.get("command") or {}).get("verdict")
    return bool(isinstance(verdict, dict) and verdict.get("text"))


async def _probe(mod_entry: dict, tools: list[str], probe_file: str) -> dict:
    """Drive one loaded mod entry through the real bridge and report what it did."""
    plugin = ModsHostPlugin(_SmokeContext())
    plugin._mods = [mod_entry]
    out: dict = {"diagnostics": [], "registered": 0, "invoked": {}, "missing": {}, "denied": [], "gates": {}}
    try:
        # Mirror the turn loop's order — turn.start FIRST (mods clear their
        # per-turn state on it), then the tool rounds, then turn.complete.
        await plugin.on_task_start({"task_id": "smoke", "source": "web", "agent_id": "smoke", "sid": "smoke"})
        for tool in tools:
            ctx = await plugin.on_before_tool(
                {"tool_name": tool, "arguments": _args_for(tool, probe_file), "agent_id": "smoke", "sid": "smoke"}
            )
            if ctx.get("skip"):
                out["denied"].append(tool)
            # Mirror the turn loop: a tool round also fires `on_after_tool`, which
            # is what drives `turn.step` and the ToolUse slot.
            await plugin.on_after_tool({"tool_name": tool, "agent_id": "smoke", "sid": "smoke"})
        # A turn boundary: state machines that only commit at turn end run here.
        await plugin.on_task_complete(
            {"task_id": "smoke", "completion_status": "completed", "turns": 1, "agent_id": "smoke", "sid": "smoke"}
        )
        # Command probe: run the first command the mod contributed (the path a
        # user's `/x` takes) and see whether it now draws something.
        init = plugin._last_init or {}
        events = init.get("events") or []
        contributed = [name for mod_names in plugin._mod_commands.values() for name in mod_names]
        if contributed:
            verdict = await plugin.run_command(contributed[0], sid="smoke")
            out["command"] = {"name": contributed[0], "verdict": verdict}
        del events

        # Render probe: does the mod get element factories, and what does it draw?
        render = await plugin.render_slot("AbovePrompt", maxRows=20, bodyColumns=100)
        out["render"] = {
            "roots": len(render["nodes"]),
            "kinds": sorted({n.get("type", "?") for n in render["nodes"]}),
            "dropped": render["dropped"][:4],
        }
        # Panes a mod asked for (a mod that gets a pane draws there, not in the band).
        if plugin._client is not None:
            panes = (await plugin._client.request("pane.list", {}, timeout=5.0)).get("panes") or []
            out["panes"] = []
            for pane in panes:
                drawn = await plugin.render_slot("Pane", requestId=str(pane.get("id") or ""))
                out["panes"].append(
                    {
                        "id": pane.get("id"),
                        "mod": pane.get("mod"),
                        "kinds": sorted({n.get("type", "?") for n in drawn["nodes"]}),
                    }
                )
        init = plugin._last_init or {}
        out["registered"] = init.get("registered", 0)
        out["diagnostics"] = [f"{d['kind']}: {d['detail']}" for d in init.get("diagnostics") or []]
        out["gates"] = dict(plugin._gate_calls)
        if plugin._client is not None:
            stats = await plugin._client.request("host.stats", timeout=5.0)
            out["invoked"] = stats.get("invocations") or {}
            out["missing"] = stats.get("missingDollar") or {}
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        out["diagnostics"].append(f"{type(exc).__name__}: {exc}")
    finally:
        plugin.on_unload()
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mods-root", default=None)
    ap.add_argument("--tools", default="Edit,Write,MultiEdit,Bash")
    args = ap.parse_args()

    # The child inherits this at spawn time; _node.build_env copies os.environ.
    os.environ["OPENSQUAD_MODS_HOST_TRACE"] = "1"

    root = args.mods_root or mods_compat.mods_root()
    state_root = os.path.join(root, "_state")
    tools = [t.strip() for t in args.tools.split(",") if t.strip()]

    print(f"mods root : {root}")
    print(f"node      : {_node.resolve_node_executable() or '(NOT FOUND — host cannot start)'}")
    print(f"probing   : tool.call with {', '.join(tools)}\n")

    mods = mods_compat.discover_mods(root, state_root)
    if not mods:
        print("no mods found.")
        return 1

    rows = []
    for info in mods:
        print(f"── {info['dir_name']}  [{info['verdict']}]")
        print(f"   events : {', '.join(info['used_events']) or '-'}")
        print(f"   $      : {', '.join(info['used_dollar']) or '-'}")

        # The real path: enable it, then ask the real loader.
        mods_compat.write_mod_state(info["dir_name"], enabled=True, state_root=state_root)
        loader = ModsHostPlugin(None)
        entry = next(
            (
                m
                for m in loader._load_mods(root=root, state_root=state_root)
                if m["name"] in (info["name"], info["dir_name"])
            ),
            None,
        )

        if entry is None:
            print(f"   load   : NOT LOADABLE — {mods_compat.load_plan(info)['reason']}\n")
            rows.append(
                (info["dir_name"], info["verdict"], False, len(info.get("blocked_by") or []), -1, -1, -1, -1, False)
            )
            continue

        inert = entry.get("inert") or []
        print("   load   : loaded (per-contribution)")
        if inert:
            print(f"   inert  : {', '.join(inert)}")
        probe = await _probe(entry, tools, os.path.join(root, "_PROVENANCE.json"))
        invoked = ", ".join(f"{k}×{v}" for k, v in sorted(probe["invoked"].items())) or "-"
        print(
            f"   probe  : register={probe['registered']} handler(s)  invoked={invoked}  denied={probe['denied'] or '-'}"
        )
        gates = ", ".join(f"{k.split('.', 1)[1]}×{v}" for k, v in sorted(probe["gates"].items())) or "-"
        print(f"   gates  : {gates}   (host→Python calls that succeeded)")
        command = probe.get("command")
        if command:
            text = (
                (command.get("verdict") or {}).get("text")
                if isinstance(command.get("verdict"), dict)
                else command.get("verdict")
            )
            print(f"   command: /{command['name']} → {text!r}")
        render = probe.get("render") or {}
        kinds = "/".join(render.get("kinds") or []) or "-"
        print(f"   render : AbovePrompt → {render.get('roots', 0)} node(s) [{kinds}]")
        for pane in probe.get("panes") or []:
            print(f"   pane   : {pane['id']} ({pane['mod']}) → {'/'.join(pane['kinds']) or 'empty'}")
        for note in render.get("dropped") or []:
            print(f"            drop : {note}")
        for d in probe["diagnostics"]:
            print(f"            diag : {d}")
        for name, hits in sorted(probe["missing"].items()):
            print(f"            miss : $.{name}  x{hits}")
        print()
        rows.append(
            (
                info["dir_name"],
                info["verdict"],
                True,
                len(inert),
                probe["registered"],
                probe["invoked"].get("tool.call", 0),
                len(probe["gates"]),
                len(probe["missing"]),
                _effective(probe),
            )
        )

    print("=" * 78)
    print(
        f"{'mod':<18}{'verdict':<10}{'loads':<7}{'inert':<7}{'handlers':<10}{'tool.call':<11}{'gates':<7}{'missing $'}"
    )
    for name, verdict, gate, inert, reg, invoked, gates, miss, effective in rows:
        print(
            f"{name:<18}{verdict:<10}{'yes' if gate else 'no':<7}{inert:<7}{reg:<10}{invoked:<11}{gates:<7}{miss}"
            f"{'  ← 有效果' if effective else ''}"
        )

    effective_count = sum(1 for row in rows if row[-1])
    print()
    print(f"有效果（画出节点 / 工具调用被触发 / 命令产出文本）: {effective_count}/{len(rows)}")
    print("M2 kill criteria: <3 就停这个 initiative")

    # Leave the workspace as we found it: mods must not self-enable, and the
    # journal entry must not linger as a phantom "lost agent" on the Mods page.
    for info in mods:
        mods_compat.write_mod_state(info["dir_name"], enabled=False, state_root=state_root)
    try:
        os.remove(mods_compat.host_journal_path(SMOKE_AGENT_ID))
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
