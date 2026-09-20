"""Decode-safe ``text=True`` kwargs for subprocess calls.

Why this module exists
----------------------
``subprocess.run(..., text=True)`` without an explicit ``encoding`` decodes the
child's pipe with Python's *interpreter* default. On Windows that default is the
ANSI code page (cp936 on a Chinese system) — **unless** UTF-8 mode is on
(``PYTHONUTF8=1`` / ``-X utf8`` / ``PYTHONIOENCODING=utf-8``), in which case the
default becomes ``utf-8``. Windows native tools (``netstat`` / ``tasklist`` /
``wmic`` / ``powershell``) keep writing their *console* code page bytes, so the
two ends disagree and the reader thread dies::

    Exception in thread Thread-1 (_readerthread):
    UnicodeDecodeError: 'utf-8' codec can't decode byte 0xbb in position 2

The failure is silent-ish from the caller's point of view: ``subprocess.run``
raises ``ValueError``/returns empty output, ports look "not listening", and
service recycling logic misfires.

Rules of thumb
--------------
* Native OS tools           → :func:`native_text_kwargs`
* Tools that emit UTF-8     → :func:`utf8_text_kwargs`   (git, node, pnpm, pip)
* Child Python processes    → :func:`native_text_kwargs` (they inherit locale)

Both flavours set ``errors="replace"`` so a decode mismatch can never kill a
reader thread again — the worst case becomes a mangled character instead of a
lost subprocess result.
"""

from __future__ import annotations

import locale
import sys
from typing import Any

__all__ = [
    "merged",
    "native_encoding",
    "native_text_kwargs",
    "to_text",
    "utf8_text_kwargs",
]


def native_encoding() -> str:
    """Encoding used by native OS tools for piped output.

    ``locale.getencoding()`` (Python 3.11+) deliberately **ignores** UTF-8 mode,
    unlike ``locale.getpreferredencoding(False)`` which reports ``"utf-8"``
    whenever ``PYTHONUTF8=1`` — that difference is the whole bug. POSIX systems
    report their (almost always UTF-8) locale here.
    """
    if sys.platform == "win32":
        try:
            return locale.getencoding()
        except AttributeError:  # pragma: no cover - Python < 3.11
            return "mbcs"
    try:
        return locale.getencoding()
    except AttributeError:  # pragma: no cover - Python < 3.11
        return locale.getpreferredencoding(False) or "utf-8"


def native_text_kwargs() -> dict[str, Any]:
    """``text=True`` + native-code-page decoding for OS tools."""
    return {
        "text": True,
        "encoding": native_encoding(),
        "errors": "replace",
    }


def utf8_text_kwargs() -> dict[str, Any]:
    """``text=True`` + UTF-8 decoding for tools that emit UTF-8."""
    return {
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }


def merged(base: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """``merged(native_text_kwargs(), timeout=8)`` — read nicely at call sites."""
    out = dict(base)
    out.update(overrides)
    return out


def to_text(raw: Any, *, encoding: str | None = None) -> str:
    """Normalise a pipe read that may be ``str`` (text mode) or ``bytes``.

    ``Popen(stderr=PIPE, text=True)`` yields ``str`` while a default
    ``Popen(stderr=PIPE)`` yields ``bytes``; the two get mixed up when a shared
    process list holds both kinds, and ``bytes.decode`` on a ``str`` raises
    ``AttributeError`` that a bare ``except`` then silently swallows.
    """
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode(encoding or native_encoding(), errors="replace")
    return str(raw)
