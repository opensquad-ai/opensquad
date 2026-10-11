"""Parse a user's exported cookies into the shape Playwright's ``add_cookies`` takes.

Why a parser instead of reading the browser's own store: Chrome/Edge keep their ``Cookies``
SQLite encrypted (DPAPI + AES-GCM on Windows) and lock it while the browser runs, so the
panel asks the user for an *export* instead — a Netscape ``cookies.txt`` (what the usual
"export cookies" extensions write) or a JSON export (EditThisCookie / Cookie-Editor, or a
Playwright ``storage_state``, which is what our own websearch plugin exports).

Everything lands in one list of dicts the caller can hand straight to
``context.add_cookies``; entries that cannot be turned into a usable cookie are counted and
reported in ``skipped`` rather than silently dropped.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

#: A file with more cookies than this is not a cookie export.
MAX_COOKIES = 5000
#: How many skip reasons the caller is told about (``skipped_total`` is always exact).
MAX_REPORTED_SKIPS = 20

VALID_SAME_SITE = {"Strict", "Lax", "None"}
_SAME_SITE_ALIASES: dict[str, str | None] = {
    "strict": "Strict",
    "lax": "Lax",
    "none": "None",
    "no_restriction": "None",
    "norestriction": "None",
    "unspecified": None,
}


@dataclass
class ParsedCookies:
    """Cookies ready for ``context.add_cookies`` plus a record of what was skipped."""

    cookies: list[dict[str, Any]] = field(default_factory=list)
    #: Human-readable reasons, capped at :data:`MAX_REPORTED_SKIPS`.
    skipped: list[str] = field(default_factory=list)
    skipped_total: int = 0
    #: ``"netscape"`` or ``"json"`` — which reader produced the list.
    source: str = ""
    #: Set when the file as a whole was rejected (unsupported shape / not JSON).
    error: str = ""

    def _skip(self, reason: str) -> None:
        self.skipped_total += 1
        if len(self.skipped) < MAX_REPORTED_SKIPS:
            self.skipped.append(reason)

    def _add(self, cookie: dict[str, Any]) -> None:
        self.cookies.append(cookie)


def parse_cookies(text: str, *, filename: str = "") -> ParsedCookies:
    """Auto-detect the export format and normalise it.

    JSON is detected by its first non-space character; anything else is read as Netscape
    ``cookies.txt`` (that format is line-based and has no magic header). ``filename`` only
    ever shows up in error messages.
    """
    raw = (text or "").lstrip("\ufeff")
    if not raw.strip():
        return ParsedCookies(error="the file is empty")
    if raw.lstrip()[:1] in ("[", "{"):
        return _parse_json(raw, filename=filename)
    return _parse_netscape(raw)


# ── Netscape cookies.txt ────────────────────────────────────────────────────


def _parse_netscape(text: str) -> ParsedCookies:
    """``domain \t flag \t path \t secure \t expiry \t name \t value``, one per line.

    ``#HttpOnly_`` prefixes the domain (extension convention, not part of the domain);
    every other ``#`` line is a comment. ``flag=TRUE`` means the cookie also applies to
    subdomains, which is what a leading dot encodes for Playwright.
    """
    out = ParsedCookies(source="netscape")
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or (line.lstrip().startswith("#") and not line.startswith("#HttpOnly_")):
            continue
        http_only = False
        row = line
        if row.startswith("#HttpOnly_"):
            http_only = True
            row = row[len("#HttpOnly_") :]
        parts = row.split("\t")
        if len(parts) < 7:
            out._skip(f"line {lineno}: expected 7 tab-separated fields, got {len(parts)}")
            continue
        domain, flag, path, secure, expiry, name, value = (p.strip() for p in parts[:7])
        if flag.upper() in ("TRUE", "1", "YES") and domain and not domain.startswith("."):
            domain = f".{domain}"
        cookie = _cookie(
            name=name,
            value=value,
            domain=domain,
            path=path or "/",
            expires=_epoch_seconds(expiry),
            secure=secure.upper() in ("TRUE", "1", "YES"),
            http_only=http_only,
        )
        if cookie is None:
            out._skip(f"line {lineno}: missing name or domain")
            continue
        out._add(cookie)
        if len(out.cookies) >= MAX_COOKIES:
            out._skip(f"stopped at {MAX_COOKIES} cookies")
            break
    return out


# ── JSON exports ────────────────────────────────────────────────────────────


def _parse_json(text: str, *, filename: str) -> ParsedCookies:
    """An array of cookie objects, or ``{"cookies": [...]}`` (Playwright storage_state)."""
    out = ParsedCookies(source="json")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return ParsedCookies(source="json", error=f"invalid JSON: {exc}")
    if isinstance(data, dict):
        data = data.get("cookies")
    if not isinstance(data, list):
        return ParsedCookies(
            source="json",
            error="a JSON export must be a list of cookies, or an object with a 'cookies' list",
        )

    for index, item in enumerate(data):
        if not isinstance(item, dict):
            out._skip(f"item {index}: not an object")
            continue
        cookie = _cookie(
            name=str(item.get("name") or ""),
            value=_value_of(item.get("value")),
            domain=str(item.get("domain") or ""),
            url=str(item.get("url") or ""),
            path=str(item.get("path") or "/").strip() or "/",
            expires=_epoch_seconds(_first_present(item, "expires", "expirationDate", "expiry")),
            secure=bool(item.get("secure")),
            http_only=bool(item.get("httpOnly") or item.get("httponly")),
            same_site=_same_site(item.get("sameSite") or item.get("same_site")),
        )
        if cookie is None:
            out._skip(f"item {index}: missing name, value or domain/url")
            continue
        out._add(cookie)
        if len(out.cookies) >= MAX_COOKIES:
            out._skip(f"stopped at {MAX_COOKIES} cookies")
            break
    return out


# ── shared normalisation ────────────────────────────────────────────────────


def _cookie(
    *,
    name: str,
    value: str,
    domain: str = "",
    url: str = "",
    path: str = "/",
    expires: int | None = None,
    secure: bool = False,
    http_only: bool = False,
    same_site: str | None = None,
) -> dict[str, Any] | None:
    """One cookie in Playwright's shape, or None when it cannot be one.

    ``url`` wins when both are given: it is the form that carries scheme + host + path, and
    Playwright rejects a cookie that names neither.
    """
    name = (name or "").strip()
    domain = (domain or "").strip()
    url = (url or "").strip()
    if not name or (not domain and not url):
        return None
    cookie: dict[str, Any] = {"name": name, "value": value}
    if url:
        cookie["url"] = url
    else:
        cookie["domain"] = domain
        cookie["path"] = path or "/"
    if expires is not None:
        cookie["expires"] = expires
    if secure:
        cookie["secure"] = True
    if http_only:
        cookie["httpOnly"] = True
    if same_site:
        cookie["sameSite"] = same_site
    return cookie


def _value_of(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _first_present(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if item.get(key) is not None:
            return item[key]
    return None


def _epoch_seconds(value: Any) -> int | None:
    """Expiry as epoch seconds, or None for a session cookie.

    Netscape writes ``0`` for "session"; a few exporters write milliseconds, so anything
    past year 33658 is read as ms.
    """
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    if number > 1e12:
        number /= 1000.0
    return int(number)


def _same_site(value: Any) -> str | None:
    raw = str(value or "").strip()
    if raw in VALID_SAME_SITE:
        return raw
    return _SAME_SITE_ALIASES.get(raw.lower())
