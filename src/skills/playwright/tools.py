"""Playwright browser automation tools for OpenSquad agents.

Drives Chromium directly via the Playwright Python SDK — NO MCP server, NO
`npx` child process. Direct replacement for the old `@playwright/mcp`.

**Threading model (important):** Playwright's *sync* API is greenlet-bound and
*cannot* be called from a different thread than the one that started it. Every
public `browser_*` tool therefore submits its work to a **single dedicated
worker thread** via a task queue and blocks on the result. This makes the tools
safe for multi-threaded hosts (e.g. your AI agents calling them concurrently) —
a module-level lock would NOT work, because the greenlets themselves are
thread-affine.

**Persistence:** the browser uses `launch_persistent_context` with a fixed
user-data-dir, and cookies/localStorage are serialized to `storage_state.json`
— on clean close, on explicit `browser_save_state()`, and periodically in the
background — so logins (even session cookies) survive restarts.

Available tools (registered as ``skill_playwright__<name>``):
    browser_navigate(url, timeout, wait_until, ready_selector)
    browser_snapshot()
    browser_click(selector, timeout)
    browser_type(selector, text, submit=None)   # submit: True=Enter, str=click send button
    browser_press_key(key)
    browser_select(selector, value)
    browser_evaluate(script)
    browser_screenshot(path=None, full_page=True)
    browser_wait(milliseconds)
    browser_wait_for(selector, state="visible", timeout)  # wait_selector
    browser_wait_for_text(timeout, stable_for, selector)  # chat/generation completion
    browser_save_state()
    browser_go_back()
    browser_close()
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import threading
import time

logger = logging.getLogger("skills.playwright")

try:  # Guard import so a missing dependency doesn't break the whole skill library.
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright  # type: ignore
except Exception:  # pragma: no cover - dependency not installed
    sync_playwright = None
    PlaywrightTimeoutError = type("PlaywrightTimeoutError", (Exception,), {})  # type: ignore


# ── Single-worker routing ────────────────────────────────────────────────────
_task_queue: queue.Queue = queue.Queue()
_worker_started = False
_start_lock = threading.Lock()


def _ensure_worker() -> None:
    """Start the dedicated Playwright worker thread exactly once."""
    global _worker_started
    if _worker_started:
        return
    with _start_lock:
        if _worker_started:
            return
        t = threading.Thread(target=_worker_loop, name="playwright-worker", daemon=True)
        t.start()
        _worker_started = True


def _call_in_worker(fn):
    """Run ``fn`` on the single worker thread and return its result (re-raise on error)."""
    _ensure_worker()
    rq: queue.Queue = queue.Queue(maxsize=1)
    _task_queue.put((fn, rq))
    ok, payload = rq.get()
    if not ok:
        raise payload
    return payload


def _worker_loop() -> None:
    while True:
        fn, rq = _task_queue.get()
        try:
            result = fn()
            _autosave_state()
            rq.put((True, result))
        except Exception as exc:  # task-level failure, keep the worker alive
            rq.put((False, exc))


# ── Shared browser singleton (worker thread only) ───────────────────────────
_browser_context = None  # Persistent context (created & used only on worker thread)
_page = None
_pw = None

VIEWPORT = {"width": 1280, "height": 800}
SNAPSHOT_MAX_CHARS = 12000
DEFAULT_TIMEOUT = 30000
PROFILE_DIR_NAME = "browser_profile"
STATE_FILE_NAME = "storage_state.json"
AUTOSAVE_INTERVAL = 10.0  # seconds
_last_autosave = [0.0]


def _profile_root() -> str:
    """Machine-wide *shared* browser profile root, deliberately decoupled from the
    current cwd / ``OPENSQUAD_WORKSPACE`` so every agent on this box reuses the SAME
    logged-in browser profile instead of each building an empty one (which silently
    forces a login every time).

    Order: explicit ``PLAYWRIGHT_PROFILE_DIR`` env -> a fixed folder under the user's
    home directory. Screenshots / temp data still live in the workspace data dir; only
    the login profile (user-data-dir + storage_state) is shared machine-wide.
    """
    base = os.environ.get("PLAYWRIGHT_PROFILE_DIR")
    if base:
        return os.path.normpath(base)
    return os.path.join(os.path.expanduser("~"), ".opensquad_playwright")


def _profile_dir() -> str:
    return os.path.join(_profile_root(), PROFILE_DIR_NAME)


def _workspace_data_dir(*parts: str) -> str:
    """Resolve a workspace-local data directory.

    Order: explicit ``OPENSQUAD_WORKSPACE`` env first (so users can override),
    then opensquad's runtime config, then cwd.
    """
    base = os.environ.get("OPENSQUAD_WORKSPACE")
    if base:
        return os.path.join(base, "data", *parts)
    try:
        from opensquad import system_config as syscfg  # type: ignore

        return syscfg.workspace_data_dir(*parts)
    except Exception:
        base = os.environ.get("OPENSQUAD_WORKSPACE") or os.getcwd()
        return os.path.join(base, "data", *parts)


def _start_pw():
    """Start (and cache) a Playwright driver. Called on the worker thread only."""
    global _pw
    if _pw is not None:
        return _pw
    if sync_playwright is None:
        raise RuntimeError(
            "playwright is not installed. Run: pip install playwright playwright-stealth "
            "&& python -m playwright install chromium"
        )
    _pw = sync_playwright().start()
    logger.info("[playwright-skill] Playwright started")
    return _pw


def _headless() -> bool:
    """Whether to run Chromium headless (override with env PLAYWRIGHT_HEADLESS=0/1)."""
    return os.environ.get("PLAYWRIGHT_HEADLESS", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _state_path() -> str:
    return os.path.join(_profile_dir(), STATE_FILE_NAME)


def _persist_state() -> None:
    """Serialize cookies + localStorage so session cookies survive a restart."""
    ctx = _browser_context
    if ctx is None:
        return
    path = _state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ctx.storage_state(path=path)
    logger.info("[playwright-skill] Session state saved -> %s", path)


def _autosave_state() -> None:
    """Debounced state save, run after each task so a crash/OOM loses at most `AUTOSAVE_INTERVAL`."""
    now = time.time()
    if now - _last_autosave[0] < AUTOSAVE_INTERVAL or _browser_context is None:
        return
    try:
        _persist_state()
        _last_autosave[0] = now
    except Exception:
        pass


def _load_state() -> None:
    """Replay saved cookies + localStorage into a freshly created context."""
    path = _state_path()
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
        if state.get("cookies"):
            _browser_context.add_cookies(state["cookies"])
        origins = state.get("origins") or []
        if origins:
            try:
                _browser_context.add_init_script(_localstorage_js(origins))
            except Exception:
                pass
        logger.info("[playwright-skill] Loaded persisted session state")
    except Exception as exc:  # pragma: no cover
        logger.warning("[playwright-skill] Failed to load saved state: %s", exc)


def _localstorage_js(origins) -> str:
    """Build an init-script that replays saved localStorage per matching origin."""
    stmts = []
    for origin in origins or []:
        matches = origin.get("localStorage") or []
        if not matches:
            continue
        origin_js = json.dumps(origin.get("origin") or "", ensure_ascii=False)
        body = ";".join(
            f"localStorage.setItem({json.dumps(i.get('name', ''), ensure_ascii=False)},{json.dumps(i.get('value', ''), ensure_ascii=False)})"
            for i in matches
        )
        stmts.append(f"if(location.origin==={origin_js}){{{body};}}")
    return ";".join(stmts)


def _get_page():
    """Lazily create the persistent browser + page. Worker thread only."""
    global _browser_context, _page
    if _page is not None:
        try:
            if not _page.is_closed():
                return _page
        except Exception:
            pass
        _page = None

    if _browser_context is None:
        pw = _start_pw()
        profile_dir = _profile_dir()
        os.makedirs(profile_dir, exist_ok=True)
        _browser_context = pw.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=_headless(),
            viewport=VIEWPORT,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        _load_state()
        logger.info("[playwright-skill] Chromium launched (persistent profile)")

    pages = _browser_context.pages
    _page = pages[0] if pages else _browser_context.new_page()
    try:
        from playwright_stealth import stealth_sync

        stealth_sync(_page)
    except Exception:
        pass
    return _page


def _looks_like_css(selector: str) -> bool:
    """True when ``selector`` is almost certainly CSS, not visible text.

    Used so a stale ``textarea[placeholder*='任何话题']`` fails immediately
    instead of waiting ``timeout`` ms for that CSS string to appear as text.
    """
    s = (selector or "").strip()
    if not s:
        return False
    if s[0] in ".#[:":
        return True
    if any(ch in s for ch in "[]>*~=|^$"):
        return True
    return bool(re.match(r"^[a-zA-Z][\w-]*([.#:][\w-]+)*$", s))


def _locate(page, selector: str, timeout: int = DEFAULT_TIMEOUT):
    """Resolve a selector to a real, present element.

    NOT lazy: tries the string as CSS first; if it matches nothing, falls back
    to Playwright's *visible-text* locator and waits for it to appear. CSS-like
    selectors that match nothing fail immediately (no 10s text wait). Either
    an element is returned or a proper timeout is raised — no silent miss.
    """
    try:
        loc = page.locator(selector)
        if loc.count() > 0:
            return loc.first
        if _looks_like_css(selector):
            raise PlaywrightTimeoutError(f"No element matching CSS selector: {selector!r}")
    except PlaywrightTimeoutError:
        raise
    except Exception:
        pass
    text_loc = page.get_by_text(selector, exact=False).first
    text_loc.wait_for(timeout=timeout)
    return text_loc


# ── Public tools ─────────────────────────────────────────────────────────────
def browser_navigate(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    wait_until: str = "domcontentloaded",
    ready_selector: str | None = None,
) -> str:
    """Open a URL. Args: url (str, required); timeout (int, ms); wait_until (str: 'load'|'domcontentloaded'|'networkidle'|'commit'); ready_selector (str, optional) — wait for this selector before returning."""

    def _run() -> str:
        page = _get_page()
        page.goto(url, timeout=timeout, wait_until=wait_until)
        if ready_selector:
            page.wait_for_selector(ready_selector, timeout=timeout)
        return f"Navigated to {page.url}\nTitle: {page.title() or '(no title)'}"

    return _call_in_worker(_run)


def browser_snapshot() -> str:
    """Return the current page's visible text snapshot (URL + title + body text)."""

    def _run() -> str:
        page = _get_page()
        try:
            text = page.locator("body").inner_text(timeout=3000)
        except Exception:
            text = page.inner_text("body")
        text = (text or "").strip()
        total = len(text)
        if not text:
            return f"Current URL: {page.url}\nTitle: {page.title() or '(no title)'}\n\nThe page has no visible text."
        head = f"Current URL: {page.url}\nTitle: {page.title() or '(no title)'}\n\n"
        if total > SNAPSHOT_MAX_CHARS:
            text = text[:SNAPSHOT_MAX_CHARS]
            return f"{head}{text}\n...[truncated: keeping first {SNAPSHOT_MAX_CHARS}/{total} chars (dropped {total - SNAPSHOT_MAX_CHARS})]"
        return head + text

    return _call_in_worker(_run)


def browser_click(selector: str, timeout: int = 10000) -> str:
    """Click an element. Args: selector (str, required) — a CSS selector or unique visible text. Raises a clear error if nothing matches."""

    def _run() -> str:
        page = _get_page()
        _locate(page, selector, timeout).click(timeout=timeout)
        return f"Clicked element matching '{selector}'"

    return _call_in_worker(_run)


def browser_type(selector: str, text: str, submit=None, timeout: int = 10000) -> str:
    """Type into an input. Args: selector (str), text (str); submit (optional): True=press Enter, a CSS/text selector=click that send button."""

    def _run() -> str:
        page = _get_page()
        loc = _locate(page, selector, timeout)
        loc.click()
        loc.fill(text)
        if submit is True:
            loc.press("Enter")
        elif isinstance(submit, str) and submit:
            _locate(page, submit, timeout).click()
        return f"Typed into element matching '{selector}'"

    return _call_in_worker(_run)


def browser_press_key(key: str) -> str:
    """Press the given keyboard key on the focused element/page (e.g. 'Enter', 'Tab', 'Escape')."""

    def _run() -> str:
        _get_page().keyboard.press(key)
        return f"Pressed key '{key}'"

    return _call_in_worker(_run)


def browser_select(selector: str, value: str) -> str:
    """Select an <option> by value inside a <select>. Args: selector (str), value (str)."""

    def _run() -> str:
        page = _get_page()
        page.select_option(selector, value)
        return f"Selected '{value}' in '{selector}'"

    return _call_in_worker(_run)


def browser_evaluate(script: str) -> str:
    """Run JavaScript in the page and return the (stringified) result. Args: script (str, required)."""

    def _run() -> str:
        return str(_get_page().evaluate(script))

    return _call_in_worker(_run)


def browser_screenshot(path: str | None = None, full_page: bool = True) -> str:
    """Save a screenshot. Args: path (str, optional); full_page (bool, default True) — capture scrolled content too."""

    def _run() -> str:
        page = _get_page()
        target = path or None  # capture closure; never rebind `path` (would become a local)
        if not target:
            out_dir = _workspace_data_dir("browser_screenshots")
            os.makedirs(out_dir, exist_ok=True)
            target = os.path.join(out_dir, "screenshot.png")
        else:
            # Resolve exactly as given (relative -> cwd), no forced nesting.
            target = os.path.abspath(target)
            os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        page.screenshot(path=target, full_page=full_page)
        return f"Screenshot saved to {target}"

    return _call_in_worker(_run)


def browser_wait(milliseconds: int = 1000) -> str:
    """Wait/sleep so page content can settle. Args: milliseconds (int, default 1000)."""
    time.sleep(max(0, int(milliseconds)) / 1000.0)
    return f"Waited {milliseconds}ms"


def browser_wait_for(selector: str, state: str = "visible", timeout: int = DEFAULT_TIMEOUT) -> str:
    """Wait for a selector to reach a state. Args: selector (str); state (str: 'attached'|'detached'|'visible'|'hidden'); timeout (int, ms)."""

    def _run() -> str:
        page = _get_page()
        page.wait_for_selector(selector, state=state, timeout=timeout)
        return f"Selector reached '{state}': {selector}"

    return _call_in_worker(_run)


def browser_wait_for_text(
    selector: str | None = None,
    stable_for: int = 2000,
    timeout: int = 90000,
    poll: int = 600,
    stop_selector: str | None = None,
) -> str:
    """Wait until the *latest* matching element's text stops changing (chat/streaming completion).

    Args:
      selector (str, optional) — watch the **last** element matching this (i.e. the newest
        answer, not the oldest). If omitted, watches the whole body length.
      stable_for (int, ms) — how long the length must stay unchanged before we consider it done.
      timeout (int, ms) — give up.
      poll (int, ms) — sampling interval.
      stop_selector (str, optional) — a selector for the "stop generating" control; if given,
        completion ALSO requires it to disappear, closing the window where a long "thinking"
        pause could be misread as done.

    Stronger than a fixed sleep: it waits for content length to be stable, and optionally for a
    stop control to vanish, so a streaming answer is not grabbed halfway.
    """

    def _latest_len(page) -> int:
        try:
            if selector:
                loc = page.locator(selector)
                if loc.count() == 0:
                    return -1
                return len((loc.last.inner_text(timeout=2000) or "").strip())
            return len((page.locator("body").inner_text(timeout=2000) or "").strip())
        except Exception:
            return -1

    def _still_generating(page) -> bool:
        if not stop_selector:
            return False
        try:
            loc = page.locator(stop_selector)
            return loc.count() > 0 and loc.first.is_visible()
        except Exception:
            return False

    def _run() -> str:
        page = _get_page()
        last_len = None
        stable_since = None
        deadline = time.time() + timeout / 1000.0
        while time.time() < deadline:
            cur = _latest_len(page)
            generating = _still_generating(page)
            if cur == last_len and cur > 0:
                if stable_since is None:
                    stable_since = time.time()
                if (time.time() - stable_since) * 1000 >= stable_for and not generating:
                    note = f"latest={cur} chars stable for {stable_for}ms"
                    if stop_selector:
                        note += f" and stop control ('{stop_selector}') gone"
                    return f"Text stabilized ({note})."
            else:
                stable_since = None
                last_len = cur
            time.sleep(poll / 1000.0)
        last = _latest_len(page)
        raise TimeoutError(f"Text never stabilized within {timeout}ms (last len={last}).")

    return _call_in_worker(_run)


def browser_save_state() -> str:
    """Explicitly persist the current session (cookies + localStorage) to disk now."""

    def _run() -> str:
        _persist_state()
        return f"Session state saved -> {_state_path()}"

    return _call_in_worker(_run)


def browser_go_back() -> str:
    """Navigate back to the previous page."""

    def _run() -> str:
        page = _get_page()
        page.go_back()
        return f"Went back to {page.url}"

    return _call_in_worker(_run)


def browser_close() -> str:
    """Close the browser. State is serialized first so logins survive a restart."""
    global _browser_context, _page, _pw

    def _run() -> str:
        global _browser_context, _page, _pw
        try:
            _persist_state()
        except Exception as exc:
            logger.warning("[playwright-skill] Failed to save state on close: %s", exc)
        for obj in (_page, _browser_context):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
        _page = _browser_context = None
        if _pw is not None:
            try:
                _pw.stop()
            except Exception:
                pass
            _pw = None
        return "Browser closed (session persisted)."

    return _call_in_worker(_run)
