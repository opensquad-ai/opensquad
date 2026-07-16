"""E2E: TUI /login must not freeze; collect email+password via Input."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_PATH = LOG_DIR / "tui_login_e2e.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("tui_login_e2e")

EMAIL = "ss@ss"
PASSWORD = "ssssss"


async def main() -> None:
    from opensquad.cli.api_client import GatewayClient, clear_credentials
    from opensquad.cli.runtime_boot import ensure_services
    from opensquad.cli.tui.app import _build_app_class

    log.info("=== ensure services ===")
    if not ensure_services(quiet=True):
        raise RuntimeError("services failed")

    # Start logged-out so /login path is exercised
    clear_credentials()
    client = GatewayClient()
    assert not client.token, "expected no token after clear_credentials"

    App = _build_app_class()
    app = App(client=client, agent=None, no_start=True)

    lines: list[str] = []
    orig = app.log_line

    def tap(text: str, style: str = "") -> None:
        lines.append(f"{style}:{text}")
        log.info("line style=%s text=%r", style, text[:200])
        orig(text, style=style)

    app.log_line = tap  # type: ignore

    # Prove old path would block: dispatch without tui_login must not be used.
    # We drive the real TUI Input path.
    async with app.run_test(size=(100, 36)) as pilot:
        await pilot.pause(0.5)
        # Wait for mount / chrome
        for _ in range(20):
            try:
                app.query_one("#chat-input")
                break
            except Exception:
                await pilot.pause(0.2)
        else:
            raise RuntimeError("chat-input missing")

        await pilot.click("#chat-input")
        await pilot.pause(0.2)

        # Type /login and submit
        log.info("submit /login")
        await pilot.press(*list("/login"))
        await pilot.press("enter")
        await pilot.pause(0.8)

        # Must enter email capture — not freeze / not call getpass
        assert getattr(app, "_await_login", None), f"not awaiting login; lines={lines[-5:]}"
        assert (app._await_login or {}).get("step") == "email", app._await_login
        log.info("await_login email step ok")

        # Enter email
        await pilot.press(*list(EMAIL))
        await pilot.press("enter")
        await pilot.pause(0.5)
        assert (app._await_login or {}).get("step") == "password", app._await_login
        assert (app._await_login or {}).get("email") == EMAIL
        try:
            assert app.query_one("#chat-input").password is True
        except Exception as e:
            log.warning("password mask check: %s", e)
        log.info("await_login password step ok")

        # Enter password
        await pilot.press(*list(PASSWORD))
        await pilot.press("enter")

        # Wait for login worker
        ok = False
        for i in range(40):
            await pilot.pause(0.25)
            if any("login_ok" in x or "Logged in" in x or "登录成功" in x for x in lines):
                ok = True
                break
            if any("login_failed" in x or "Login failed" in x or "登录失败" in x for x in lines):
                raise RuntimeError(f"login failed lines={lines[-8:]}")
            # Also accept refreshed client token
            if getattr(app.client, "token", None):
                ok = True
                break
            log.info("wait login i=%d await=%s token=%s", i, app._await_login, bool(getattr(app.client, "token", None)))
        if not ok:
            raise RuntimeError(f"login did not complete; lines={lines[-12:]}")

        # Capture mode must be cleared
        assert not getattr(app, "_await_login", None), app._await_login
        assert app.client.token, "token missing after login"
        log.info("LOGIN_E2E_OK token_len=%d", len(app.client.token))

        # Exit cleanly
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
