"""Regression lock: the TUI chrome is repainted through the diffing helper.

Why this exists
---------------
``_refresh_chrome`` is on the hot path: it runs on every throttled side-stream
tick (``_on_side_chunk``, ~12/s) as well as on most state transitions. Three of
its four paints already go through a content diff — ``#header-bar`` and
``#footer-path`` via ``_static_set``, ``#prompt-meta`` via ``_paint_cache``,
and the placeholder via ``_placeholder_cache`` — but the footer's *non-wait*
branch used a bare ``Static.update(...)``: the markup was rebuilt and the row
repainted on every tick even though the footer only changes when cwd or theme
changes. The wait branch had already been converted; the other one was missed.

Rules:
  R1  both branches of ``_refresh_chrome`` paint the footer through
      ``_static_set`` — an unchanged cwd / theme must not repaint the row.
  R2  ``_refresh_chrome`` performs no direct widget ``.update(...)`` at all, so
      a paint added there later cannot silently skip the diff.
  R3  nothing writes ``#footer-path`` with a bare ``update()`` (split across
      lines or on one line) — a second writer would desync the cache the diff
      depends on.

Mutation check: restoring the old non-wait branch
(``fpath = self.query_one("#footer-path", Static)`` / ``fpath.update(...)``)
fails R1 and R3.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "src" / "opensquad" / "cli" / "tui" / "app.py"
SRC = APP.read_text(encoding="utf-8")

# `#footer-path` followed by a `.update(` on the same line, or on the next one
# (the shape the old code used: query_one on one line, update on the next).
FOOTER_DIRECT_UPDATE = re.compile(r"#footer-path[^\n]*\.update\(")
FOOTER_SPLIT_UPDATE = re.compile(r"#footer-path[^\n]*\n\s*[^\n]*\.update\(")


def _function_sources(name: str) -> list[str]:
    """Source of every ``def <name>`` in the module, nested definitions included."""
    out: list[str] = []
    for node in ast.walk(ast.parse(SRC)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            out.append(ast.get_source_segment(SRC, node) or "")
    return out


def test_r1_refresh_chrome_diffs_the_footer_in_both_branches():
    bodies = _function_sources("_refresh_chrome")
    assert len(bodies) == 1, "expected exactly one _refresh_chrome definition"
    body = bodies[0]
    assert body.count('_static_set("#footer-path"') == 2, (
        f"the wait branch and the normal branch must both paint the footer through _static_set:\n{body}"
    )


def test_r2_refresh_chrome_has_no_direct_update():
    body = _function_sources("_refresh_chrome")[0]
    assert ".update(" not in body, (
        f"_refresh_chrome runs on every throttled tick; a direct .update() there repaints unconditionally:\n{body}"
    )


def test_r3_footer_path_has_no_direct_writer():
    for pattern in (FOOTER_DIRECT_UPDATE, FOOTER_SPLIT_UPDATE):
        found = pattern.search(SRC)
        assert found is None, f"bare update on the footer: {found.group(0)!r}"
