"""The built-in browser: one Playwright session the agent and the user share.

Why it lives here (in the launcher) and not in the panel: a view the agent can operate has to
be driven by something both sides can reach. The Electron `<webview>` is a view inside the
renderer, and an iframe is at the mercy of the site's `X-Frame-Options` — neither can be
clicked by a tool. Playwright can: it renders the page on this machine, and both the agent's
`browser_use` tools and the panel talk to *this* session, so "the browser the agent is using"
and "the browser you are looking at" are the same page. It also means pages that refuse to be
framed (Baidu, GitHub) display fine in the web build, because nothing is framed.

The same page is also handed to the Playwright MCP server: each session launches Chromium with a
CDP port (``cdp_port``) and the MCP is pointed at it with ``--cdp-endpoint``, so a plugin that
believes it drives a browser of its own is in fact driving this one. That is why the url is read
back from the page rather than from Playwright's cache — see ``_sync_state``.

Threading: Playwright's sync objects belong to the thread that created them, so every session
owns one worker thread and all calls are marshalled onto it through a queue. Callers block on a
per-job event; nothing else touches the page.

No SSRF guard on URLs: this is a browser the user/agent drives on their own machine, and the
same agent already has a shell here — refusing `http://localhost:5173` would remove the most
useful target (previewing the app being built) without preventing anything.
"""

from __future__ import annotations

import base64
import logging
import os
import queue
import socket
import threading
import uuid
from typing import Any, Callable

logger = logging.getLogger(__name__)

# The panel's poll shows the last captured frame instead of rendering on every tick — but a cache
# alone is not enough: a CDP client (the Playwright MCP) drives this page without going through any
# method here, so nothing would ever mark it dirty and the panel would freeze on a stale picture.
# A poll whose frame is older than this re-captures. See BrowserSession.frame_now.
FRAME_TTL_S = 0.5
MAX_SNAPSHOT_CHARS = 20_000
DEFAULT_TIMEOUT_MS = 20_000
# How long a caller waits to learn whether the session got a window (see BrowserSession.ready).
START_TIMEOUT_S = 30.0
VIEWPORT = {"width": 1280, "height": 800}


def _free_port() -> int:
    """A port Chromium can bind for CDP. Racy in principle; a miss just costs the MCP its
    attachment, never the browser itself."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])
    except Exception:  # noqa: BLE001 - no port simply means "no MCP sharing"
        return 0


class _Job:
    """One call marshalled onto the session's thread."""

    def __init__(self, fn: Callable[[], Any]):
        self.fn = fn
        self.done = threading.Event()
        self.result: Any = None
        self.error: BaseException | None = None


class BrowserSession:
    """A Chromium page plus the thread that owns it."""

    def __init__(self, session_id: str, *, headless: bool | None = None):
        self.id = session_id
        # None means "prefer a real window" — see _launch: a browser you can use beats a picture
        # of one, and a machine with no desktop falls back to headless by itself.
        self.want_headless = headless
        self.headless = bool(headless)
        self.headed = False
        self.window_note = ""
        self.profile_dir = _profile_dir(session_id)
        # Allocated once, not per launch attempt: the MCP is told this port when the session
        # opens, and a headed launch that falls back to headless must not move it.
        self.cdp_port = _free_port()
        self.url = ""
        self.title = ""
        self.error = ""
        self.frame: str = ""  # last screenshot, base64 (what the panel shows)
        self.frame_at: float = 0.0
        self.closed = False
        # Set once the launch has resolved (a window, or the headless fallback, or an error):
        # a headed launch can take seconds to fail, and until it has, "which shape did we get"
        # is not yet knowable — reporting too early showed "preview only" for sessions that were
        # in fact about to get a real window.
        self.ready = threading.Event()
        self._jobs: queue.Queue[_Job | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._pw = None
        self._context = None
        self._page = None

    # ── the worker thread ───────────────────────────────────────────────────
    def _run(self) -> None:
        try:
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
            self._launch()
            self._page.set_default_timeout(DEFAULT_TIMEOUT_MS)
        except Exception as exc:  # noqa: BLE001 - reported through the first call
            self.error = f"could not start a browser: {exc}"
            logger.warning("[Browser] %s failed to start: %s", self.id, exc)
            self._close_resources()
            return
        finally:
            self.ready.set()
        while True:
            job = self._jobs.get()
            if job is None:  # stop signal
                break
            try:
                job.result = job.fn()
            except BaseException as exc:  # noqa: BLE001 - handed to the caller
                job.error = exc
            finally:
                job.done.set()
        self._close_resources()

    def _launch(self) -> None:
        """A real window when one can be shown, headless when it cannot.

        A window is the better half of the deal: the user gets a browser they can actually use —
        hover, caret, selection, right-click — while the agent drives that same window through
        the same Playwright session, so "the browser the agent uses" is still one page. Headless
        remains for machines with no desktop, and for callers that ask for it.
        """
        if not self.want_headless:
            try:
                self._start_context(headless=False)
                self.headed = True
                return
            except Exception as exc:  # noqa: BLE001 - falling back is the point
                self.window_note = f"this machine cannot show a window ({type(exc).__name__}: {exc})"
                logger.info("[Browser] %s: no window (%s); using headless", self.id, exc)
        self._start_context(headless=True)

    def _start_context(self, *, headless: bool) -> None:
        """One persistent profile per session — that is what keeps you logged in across runs."""
        args = ["--no-first-run", "--no-default-browser-check"]
        if self.cdp_port:
            # Playwright drives this browser over its own pipe; the port is a second, additive
            # door so the Playwright MCP can drive the very same page.
            args.append(f"--remote-debugging-port={self.cdp_port}")
        self._context = self._pw.chromium.launch_persistent_context(
            user_data_dir=self.profile_dir,
            headless=headless,
            viewport=VIEWPORT,
            args=args,
        )
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self.headless = headless

    def _close_resources(self) -> None:
        for closer in (
            lambda: self._page and self._page.close(),
            lambda: self._context and self._context.close(),
            lambda: self._pw and self._pw.stop(),
        ):
            try:
                closer()
            except Exception:
                pass
        self._page = self._context = self._pw = None

    def _call(self, fn: Callable[[], Any]) -> Any:
        if self.closed:
            raise RuntimeError("browser session is closed")
        if self.error:
            raise RuntimeError(self.error)
        job = _Job(fn)
        self._jobs.put(job)
        if not job.done.wait(timeout=(DEFAULT_TIMEOUT_MS / 1000) + 30):
            raise TimeoutError("the browser did not answer in time")
        if job.error is not None:
            raise job.error
        return job.result

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name=f"browser-{self.id}", daemon=True)
            self._thread.start()

    def close(self) -> dict[str, Any]:
        if not self.closed:
            self.closed = True
            self._jobs.put(None)
            if self._thread is not None:
                self._thread.join(timeout=10)
        return {"ok": True, "session_id": self.id}

    # ── actions ─────────────────────────────────────────────────────────────
    def _sync_state(self, *, retry: bool = True) -> None:
        """Read url + title, retrying briefly: during a navigation a title can still be empty.

        The url comes from the document (`location.href`), not from `page.url`: when the
        Playwright MCP drives this same browser over CDP, Playwright's cached url is not always
        refreshed by a navigation it did not initiate — the title is live, the cache is not.

        ``retry=False`` is the poll's path: one attempt, no 2 s wait for a title. A page with no
        `<title>` would otherwise make every poll of the panel take two seconds.
        """
        import time as _time

        deadline = _time.time() + (2.0 if retry else 0.0)
        while True:
            try:
                try:
                    self.url = str(self._page.evaluate("() => location.href"))
                except Exception:
                    self.url = str(self._page.url)
                title = str(self._page.title())
                self.title = title
                if title or _time.time() >= deadline:
                    return
            except Exception:
                if _time.time() >= deadline:
                    return
            _time.sleep(0.05)

    def navigate(self, url: str) -> dict[str, Any]:
        target = str(url or "").strip()
        if not target:
            return {"ok": False, "error": "url is required"}
        if "://" not in target:
            target = ("http://" + target) if target.startswith(("localhost", "127.0.0.1")) else ("https://" + target)

        def _go():
            self._page.goto(target, wait_until="domcontentloaded")
            self._sync_state()
            self._capture()
            return True

        self._call(_go)
        return {"ok": True, "session_id": self.id, "url": self.url, "title": self.title}

    def back(self) -> dict[str, Any]:
        def _back():
            self._page.go_back(wait_until="domcontentloaded")
            self._sync_state()
            self._capture()
            return True

        self._call(_back)
        return {"ok": True, "session_id": self.id, "url": self.url, "title": self.title}

    def click(self, *, selector: str = "", x: float | None = None, y: float | None = None) -> dict[str, Any]:
        def _click():
            if selector:
                self._page.click(selector)
            elif x is not None and y is not None:
                self._page.mouse.click(float(x), float(y))
            else:
                raise ValueError("click needs a selector or x/y")
            self._sync_state()
            self._capture()
            return True

        self._call(_click)
        return {"ok": True, "session_id": self.id, "url": self.url, "title": self.title}

    def type_text(self, *, selector: str, text: str, submit: bool = False) -> dict[str, Any]:
        def _type():
            page = self._page
            if selector:
                page.fill(selector, str(text or ""))
                if submit:
                    page.press(selector, "Enter")
            else:
                page.keyboard.type(str(text or ""))
                if submit:
                    page.keyboard.press("Enter")
            self._sync_state()
            self._capture()
            return True

        self._call(_type)
        return {"ok": True, "session_id": self.id, "url": self.url, "title": self.title}

    def press(self, key: str) -> dict[str, Any]:
        def _press():
            self._page.keyboard.press(str(key or "Enter"))
            self._sync_state()
            self._capture()
            return True

        self._call(_press)
        return {"ok": True, "session_id": self.id, "url": self.url, "title": self.title}

    def snapshot(self) -> dict[str, Any]:
        """What is on the page, as text — the cheap thing for a model to read."""

        def _snap():
            body = ""
            for getter in (
                lambda: self._page.inner_text("body"),
                lambda: self._page.content(),
            ):
                try:
                    body = str(getter() or "").strip()
                except Exception:
                    body = ""
                if body:
                    break
            links = []
            try:
                links = [str(el) for el in self._page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")][
                    :40
                ]
            except Exception:
                links = []
            self._sync_state()
            return body, links

        body, links = self._call(_snap)
        return {
            "ok": True,
            "session_id": self.id,
            "url": self.url,
            "title": self.title,
            "text": body[:MAX_SNAPSHOT_CHARS],
            "truncated": len(body) > MAX_SNAPSHOT_CHARS,
            "links": links,
        }

    def _capture(self, full_page: bool = False) -> str:
        """Take a frame and remember it (the panel's view is this image)."""
        png = self._page.screenshot(full_page=full_page, type="png")
        self.frame = base64.b64encode(png).decode("ascii")
        import time as _time

        self.frame_at = _time.time()
        return self.frame

    def screenshot(self, *, full_page: bool = False) -> dict[str, Any]:
        png_b64 = self._call(lambda: self._capture(full_page))
        return {
            "ok": True,
            "session_id": self.id,
            "url": self.url,
            "title": self.title,
            "png": png_b64,
            "bytes": len(png_b64),
        }

    def _frame_is_stale(self) -> bool:
        """True when the panel should be shown a fresh render rather than the cached one."""
        import time as _time

        return (not self.frame) or (_time.time() - self.frame_at) > FRAME_TTL_S

    def frame_now(self) -> dict[str, Any]:
        """The panel's poll: the last frame, re-captured once it has gone stale.

        `navigate`/`click`/`type_text`/`press` refresh the cache themselves, but the Playwright
        MCP drives this same page over CDP and calls none of them — with the cache as the only
        source, a plugin's work would never appear in the panel. Hence the TTL: a poll older than
        `FRAME_TTL_S` re-reads the url and renders again, so whoever is driving, the preview keeps
        up. The cache still does its job — a burst of polls inside the TTL renders once, not once
        per tick.
        """
        if self._frame_is_stale():
            try:
                self._call(lambda: (self._sync_state(retry=False), self._capture()))
            except Exception as exc:  # noqa: BLE001 - a poll must not raise
                # Only a poll with nothing to show reports the failure. A render can lose the race
                # with a navigation (the plugin's, or the agent's) and throw for one tick; turning
                # that into an error would flash the panel amber every time the page moves.
                if not self.frame:
                    return {"ok": False, "error": str(exc), "session_id": self.id}
        return {
            "ok": True,
            "session_id": self.id,
            "url": self.url,
            "title": self.title,
            "png": self.frame,
            "captured_at": self.frame_at,
            # carried on the poll so the panel keeps showing the right mode even when the agent
            # is the one that opened the browser
            "headed": self.headed,
            "window_note": self.window_note,
        }

    def info(self) -> dict[str, Any]:
        return {
            "session_id": self.id,
            "url": self.url,
            "title": self.title,
            "running": not self.closed and not self.error,
            "error": self.error,
            "has_frame": bool(self.frame),
            # which half of the deal this session got: a real window, or only a preview
            "headed": self.headed,
            "headless": self.headless,
            "profile_dir": self.profile_dir,
            # where the Playwright MCP attaches to drive this same page
            "cdp_port": self.cdp_port,
            "window_note": self.window_note,
        }


# ── registry ────────────────────────────────────────────────────────────────

_SESSIONS: dict[str, BrowserSession] = {}
_LOCK = threading.Lock()


def _profile_dir(session_id: str) -> str:
    """Where this session's cookies live. Persistent, so a login survives a restart."""
    base = str(os.environ.get("OPENSQUAD_WORKSPACE") or os.environ.get("OPENSQUAD_USER_DATA") or "")
    if not base:
        try:
            from opensquad.system_config import syscfg

            base = str(syscfg.workspace_dir() or "")
        except Exception:
            base = ""
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".opensquad")
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in str(session_id or "default"))[:64]
    return os.path.join(base, "browser_profiles", safe or "default")


def _env_prefers_headless() -> bool:
    """``OPENSQUAD_BROWSER_HEADLESS=1`` keeps every session headless.

    For test suites and servers: a window per session is fine for a person at a desk and wrong
    for a suite that opens eight of them (and for a machine with nobody watching).
    """
    return str(os.environ.get("OPENSQUAD_BROWSER_HEADLESS") or "").strip().lower() in ("1", "true", "yes", "on")


def open_session(*, session_id: str = "", headless: bool | None = None) -> dict[str, Any]:
    """Start (or reuse) the browser. One session per id, shared by agent and panel.

    ``headless=None`` (the default callers get) prefers a real window and falls back to headless
    if this machine cannot show one; ``headless=True`` is for remote/CI callers that only want
    frames.
    """
    sid = str(session_id or "").strip() or uuid.uuid4().hex[:12]
    if headless is None and _env_prefers_headless():
        headless = True
    with _LOCK:
        existing = _SESSIONS.get(sid)
        if existing is not None and not existing.closed:
            return {"ok": True, "session_id": sid, "reused": True, **existing.info()}
        session = BrowserSession(sid, headless=headless)
        _SESSIONS[sid] = session
    session.start()
    # Wait for the launch to resolve: the caller (panel or tool) is told which shape this session
    # got, and "preview only" while Chromium is still starting would be a lie. Headless comes up
    # in about a second; a headed launch that cannot show a window takes a few seconds to fail.
    session.ready.wait(timeout=START_TIMEOUT_S)
    if session.error:
        with _LOCK:
            _SESSIONS.pop(sid, None)
        return {"ok": False, "session_id": sid, "error": session.error}
    return {"ok": True, "session_id": sid, **session.info()}


def _get(session_id: str) -> BrowserSession:
    with _LOCK:
        session = _SESSIONS.get(str(session_id or "").strip())
    if session is None:
        raise KeyError(f"unknown browser session: {session_id}")
    return session


def _action(session_id: str, fn: Callable[[BrowserSession], dict[str, Any]]) -> dict[str, Any]:
    """Run an action, reporting a stale id or a page error instead of raising at the caller."""
    try:
        session = _get(session_id)
    except KeyError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        return fn(session)
    except Exception as exc:  # noqa: BLE001 - the panel/tool gets the reason
        return {"ok": False, "session_id": session.id, "error": f"{type(exc).__name__}: {exc}"}


def navigate(session_id: str, url: str) -> dict[str, Any]:
    return _action(session_id, lambda s: s.navigate(url))


def back(session_id: str) -> dict[str, Any]:
    return _action(session_id, lambda s: s.back())


def click(session_id: str, selector: str = "", x: float | None = None, y: float | None = None) -> dict[str, Any]:
    return _action(session_id, lambda s: s.click(selector=selector, x=x, y=y))


def type_text(session_id: str, selector: str, text: str, submit: bool = False) -> dict[str, Any]:
    return _action(session_id, lambda s: s.type_text(selector=selector, text=text, submit=submit))


def press(session_id: str, key: str) -> dict[str, Any]:
    return _action(session_id, lambda s: s.press(key))


def snapshot(session_id: str) -> dict[str, Any]:
    return _action(session_id, lambda s: s.snapshot())


def screenshot(session_id: str, full_page: bool = False) -> dict[str, Any]:
    return _action(session_id, lambda s: s.screenshot(full_page=full_page))


def frame(session_id: str) -> dict[str, Any]:
    return _action(session_id, lambda s: s.frame_now())


def cdp_endpoint(session_id: str) -> str:
    """Where a CDP client (the Playwright MCP) can reach this session's browser, or ""."""
    try:
        session = _get(session_id)
    except KeyError:
        return ""
    port = int(getattr(session, "cdp_port", 0) or 0)
    return f"http://127.0.0.1:{port}" if port else ""


def close_session(session_id: str) -> dict[str, Any]:
    with _LOCK:
        session = _SESSIONS.pop(str(session_id or "").strip(), None)
    if session is None:
        return {"ok": True, "already_closed": True}
    return session.close()


def list_sessions() -> list[dict[str, Any]]:
    with _LOCK:
        return [s.info() for s in _SESSIONS.values()]


def close_all() -> int:
    """Stop every browser (shutdown / tests)."""
    with _LOCK:
        sessions = list(_SESSIONS.values())
        _SESSIONS.clear()
    for session in sessions:
        try:
            session.close()
        except Exception:
            logger.debug("[Browser] close_all: %s failed", session.id, exc_info=True)
    return len(sessions)
