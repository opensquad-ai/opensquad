"""Smoke: wait-then-enter boot + TUI layout widgets present (2d78e00 prompt layout)."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tui_boot_smoke")


async def main() -> None:
    from opensquad.cli.api_client import GatewayClient
    from opensquad.cli.runtime_boot import ensure_services, prepare_code_session
    from opensquad.cli.tui.app import _build_app_class

    log.info("ensure services")
    assert ensure_services(quiet=True)

    log.info("blocking prepare_code_session (login ss@ss if needed)")
    c0 = GatewayClient()
    if not c0.token:
        c0.login("ss@ss", "ssssss", language="zh")

    client, agent = prepare_code_session(gateway=None, agent=None, no_start=True)
    assert client.token, "no token after prepare"
    assert agent, "no agent after prepare"
    log.info("prepared agent=%s", agent)

    App = _build_app_class()
    app = App(client=client, agent=agent, no_start=True)

    async with app.run_test(size=(100, 36)) as pilot:
        for i in range(40):
            await pilot.pause(0.25)
            ready = bool(app.bridge and getattr(app.bridge, "is_open", False))
            log.info("boot i=%d ready=%s wait=%s", i, ready, getattr(app, "_wait_label", None))
            if ready and not getattr(app, "_wait_label", None):
                break

        # 2d78e00 widgets: meta inside frame; footer-path + status-bar + Footer
        for wid in (
            "#header-bar",
            "#chat-input",
            "#prompt-frame",
            "#prompt-meta",
            "#footer-path",
            "#status-bar",
            "#wait-banner",
        ):
            w = app.query_one(wid)
            assert w is not None, wid
            log.info("widget ok %s", wid)

        header_txt = str(getattr(app, "_paint_cache", {}).get("header-bar", ""))
        meta = str(getattr(app, "_paint_cache", {}).get("prompt-meta", ""))
        log.info("header=%r", header_txt)
        log.info("meta=%r", meta)

        inp = app.query_one("#chat-input")
        await pilot.click("#chat-input")
        await pilot.pause(0.2)
        assert app.focused is inp or getattr(app.focused, "id", None) == "chat-input"

        assert "OpenSquad" in header_txt or agent in header_txt
        assert "Build" in meta or "Plan" in meta or "group" in meta.lower()
        # prompt-meta must be INSIDE the bordered frame (2d78e00)
        frame = app.query_one("#prompt-frame")
        assert app.query_one("#prompt-meta") in frame.children
        log.info("SMOKE_OK agent=%s ready=%s", agent, bool(app.bridge and app.bridge.is_open))
        app.exit()


if __name__ == "__main__":
    try:
        asyncio.run(main())
        print("PASS")
        sys.exit(0)
    except Exception as e:
        log.exception("FAIL: %s", e)
        print("FAIL", e)
        sys.exit(1)
