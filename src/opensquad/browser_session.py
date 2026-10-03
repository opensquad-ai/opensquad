"""The built-in browser: one Playwright session the agent and the user share.

Why it lives here (in the launcher) and not in the panel: a view the agent can operate has to
be driven by something both sides can reach. The Electron `<webview>` is a view inside the
renderer, and an iframe is at the mercy of the site's `X-Frame-Options` — neither can be
clicked by a tool. Playwright can: it renders the page on this machine, and both the agent's
`browser_use` tools and the panel talk to *this* session, so "the browser the agent is using"
and "the browser you are looking at" are the same page. It also means pages that refuse to be
framed (Baidu, GitHub) display fine in the web build, because nothing is framed.

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
import queue
import threading
import uuid
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Keep one frame around so the panel can show the page without asking for a new screenshot on
# every poll.
MAX_SNAPSHOT_CHARS = 20_000
DEFAULT_TIMEOUT_MS = 20_000
VIEWPORT = {"width": 1280, "height": 800}


class _Job:
    """One call marshalled onto the session's thread."""

    def __init__(self, fn: Callable[[], Any]):
        self.fn = fn
        self.done = threading.Event()
        self.result: Any = None
        self.error: BaseException | None = None


class BrowserSession:
    """A Chromium page plus the thread that owns it."""

    def __init__(self, session_id: str, *, headless: bool = True):
        self.id = session_id
        self.headless = headless
        self.url = ""
        self.title = ""
        self.error = ""
        self.frame: str = ""  # last screenshot, base64 (what the panel shows)
        self.frame_at: float = 0.0
        self.closed = False
        self._jobs: queue.Queue[_Job | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._pw = None
        self._browser = None
        self._page = None

    # ── the worker thread ───────────────────────────────────────────────────
    def _run(self) -> None:
        try:
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=self.headless)
            self._page = self._browser.new_page(viewport=VIEWPORT)
            self._page.set_default_timeout(DEFAULT_TIMEOUT_MS)
        except Exception as exc:  # noqa: BLE001 - reported through the first call
            self.error = f"could not start a browser: {exc}"
            logger.warning("[Browser] %s failed to start: %s", self.id, exc)
            self._close_resources()
            return
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

    def _close_resources(self) -> None:
        for closer in (
            lambda: self._page and self._page.close(),
            lambda: self._browser and self._browser.close(),
            lambda: self._pw and self._pw.stop(),
        ):
            try:
                closer()
            except Exception:
                pass
        self._page = self._browser = self._pw = None

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
    def _sync_state(self) -> None:
        """Read url + title, retrying briefly: during a navigation a title can still be empty."""
        import time as _time

        deadline = _time.time() + 2.0
        while True:
            try:
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
            return True

        self._call(_type)
        return {"ok": True, "session_id": self.id, "url": self.url, "title": self.title}

    def press(self, key: str) -> dict[str, Any]:
        self._call(lambda: self._page.keyboard.press(str(key or "Enter")))
        return {"ok": True, "session_id": self.id, "url": self.url}

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

    def frame_now(self) -> dict[str, Any]:
        """The panel's poll: the last frame, or a fresh one if there is none yet."""
        if not self.frame:
            try:
                return self.screenshot()
            except Exception as exc:  # noqa: BLE001 - a poll must not raise
                return {"ok": False, "error": str(exc), "session_id": self.id}
        return {
            "ok": True,
            "session_id": self.id,
            "url": self.url,
            "title": self.title,
            "png": self.frame,
            "captured_at": self.frame_at,
        }

    def info(self) -> dict[str, Any]:
        return {
            "session_id": self.id,
            "url": self.url,
            "title": self.title,
            "running": not self.closed and not self.error,
            "error": self.error,
            "has_frame": bool(self.frame),
        }


# ── registry ────────────────────────────────────────────────────────────────

_SESSIONS: dict[str, BrowserSession] = {}
_LOCK = threading.Lock()


def open_session(*, session_id: str = "", headless: bool = True) -> dict[str, Any]:
    """Start (or reuse) the browser. One session per id, shared by agent and panel."""
    sid = str(session_id or "").strip() or uuid.uuid4().hex[:12]
    with _LOCK:
        existing = _SESSIONS.get(sid)
        if existing is not None and not existing.closed:
            return {"ok": True, "session_id": sid, "reused": True, **existing.info()}
        session = BrowserSession(sid, headless=headless)
        _SESSIONS[sid] = session
    session.start()
    # The first call also surfaces a start failure (a missing browser, for instance).
    if session._thread is not None:
        session._thread.join(timeout=0.05)
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
