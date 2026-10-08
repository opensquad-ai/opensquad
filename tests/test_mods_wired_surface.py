"""The page's promise must not drift from the runtime's offer.

``mods_compat.WIRED`` is what the Mods page tells users this host serves. It is
a plain constant, so it can rot silently while the page keeps promising it. This
cross-checks it against the code that actually implements each half.

Deliberately coarse (a literal `name:` in host.mjs): the strong check is
behavioural — `tests/test_mods_host_deny_e2e.py` drives these members for real.
"""

from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))

from opensquad import mods_compat  # noqa: E402

HOST_MJS = os.path.join(REPO, "src", "plugins", "mods_host", "host", "host.mjs")
PLUGIN_PY = os.path.join(REPO, "src", "plugins", "mods_host", "plugin.py")
HOST_DIR = os.path.join(REPO, "src", "plugins", "mods_host", "host")
SPEC_PY = os.path.join(REPO, "src", "opensquad", "gateway", "backend", "opensquad_backend.spec")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_every_wired_dollar_member_is_implemented_in_the_host():
    src = _read(HOST_MJS)
    missing = []
    for member in mods_compat.WIRED["dollar"]:
        namespace, name = member.removeprefix("$.").split(".", 1)
        if f"{namespace}:" not in src or f"{name}:" not in src:
            missing.append(member)
    assert missing == [], f"advertised but not implemented in host.mjs: {missing}"


def test_every_wired_event_is_dispatched_by_the_plugin():
    src = _read(PLUGIN_PY)
    missing = [event for event in mods_compat.WIRED["events"] if f'"{event}"' not in src]
    assert missing == [], f"advertised but never emitted by the bridge: {missing}"


FRONTEND = os.path.join(REPO, "src", "opensquad", "gateway", "nexuschat-pro")


def test_every_advertised_slot_has_a_frontend_mount():
    """Advertised == mounted. A slot the frontend never renders is a lie.

    This is why the wired list is short: `Pane`/`Sidebar`/`ToolUse`/`Spinner` are
    deliberately *not* advertised until each has a real mount site (the backend
    already renders and pushes them — the promise is what must wait).
    """
    sources: list[str] = []
    for root, _dirs, files in os.walk(FRONTEND):
        if "node_modules" in root or "/dist" in root:
            continue
        for name in files:
            if name.endswith((".ts", ".tsx")):
                with open(os.path.join(root, name), encoding="utf-8", errors="replace") as fh:
                    sources.append(fh.read())
    blob = "\n".join(sources)

    unmounted = [slot for slot in mods_compat.WIRED["slots"] if f'slot="{slot}"' not in blob]
    assert unmounted == [], f"advertised but never mounted by the frontend: {unmounted}"

    status = mods_compat.host_status()
    assert f"渲染插槽：{len(mods_compat.WIRED['slots'])}" in status["scope"]
    assert status["wired"]["events"] == list(mods_compat.WIRED["events"])


def test_the_render_element_whitelist_matches_the_host_factories():
    """Elements the host hands out must be exactly the ones validation accepts."""
    src = _read(HOST_MJS)
    for element in mods_compat.RENDER_ELEMENTS:
        assert f"node('{element}'" in src, f"{element} is whitelisted but the host never builds it"
    for element in mods_compat.REFUSED_ELEMENTS:
        assert f"node('{element}'" not in src, f"{element} is refused but the host hands it out anyway"


def test_every_file_the_host_needs_survives_the_frozen_build():
    """The Node host ships as *data*, so the packaging filter can silently drop it.

    `opensquad_backend.spec::_is_plugin_runtime_data` keeps a plugin's non-`.py`
    files except: `node_modules`, `__pycache__`, anything under `/ui/` except the
    built `index.js`, and `*.map` / `*.d.ts` / `*.ts` / `package.json` /
    `package-lock.json` / `tsconfig.json`.

    That set is fine for the host **today** (it is `.mjs` + a LICENSE/notes pair)
    but it is one careless `mv host.ts host.mjs` away from a packaged app whose
    mods host cannot spawn — and the failure would only show up in a release
    build, never in dev. This pins the invariant instead of the incident.
    """
    dropped_names = {"package.json", "package-lock.json", "tsconfig.json"}
    dropped_suffixes = (".map", ".d.ts", ".ts")
    offenders: list[str] = []
    for root, _dirs, files in os.walk(HOST_DIR):
        rel_root = os.path.relpath(root, HOST_DIR).replace("\\", "/")
        for name in files:
            if "__pycache__" in rel_root or "node_modules" in rel_root:
                continue
            if rel_root.startswith("ui") or "/ui/" in f"/{rel_root}/":
                continue
            if name in dropped_names or name.endswith(dropped_suffixes):
                offenders.append(os.path.join(rel_root, name))
    assert offenders == [], (
        f"the packaging filter drops these, so the frozen host would fail to spawn: {offenders}. "
        "Rename the file, or extend _is_plugin_runtime_data in opensquad_backend.spec."
    )

    # And the entry points actually used at spawn time are present at all.
    for needed in ("host.mjs", "resolver.mjs", "claude-code.mjs", "runtime.mjs", "vendor/sucrase.bundle.mjs"):
        assert os.path.isfile(os.path.join(HOST_DIR, *needed.split("/"))), f"{needed} is missing from the host"
