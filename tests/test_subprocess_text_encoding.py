"""Regression lock: subprocess text-mode decoding must never lose output.

The incident (2026-09-19, ``opensquad dev``)::

    Exception in thread Thread-1 (_readerthread):
    UnicodeDecodeError: 'utf-8' codec can't decode byte 0xbb in position 2

``subprocess.run(..., text=True)`` without an explicit ``encoding`` decodes the
child's pipe with the *interpreter* default. Windows native tools (``netstat`` /
``tasklist`` / ``wmic`` / ``powershell``) ignore ``PYTHONUTF8`` and keep writing
the console code page (cp936 here), so under UTF-8 mode the two ends disagree.

What makes this bug dangerous is that it is **silent**: the decode failure kills
the reader thread, ``Popen.communicate()`` then yields ``None``/``''`` and no
exception reaches the caller. ``_pid_on_port()`` therefore saw an empty netstat
dump, concluded "nothing is listening", and the CLI proceeded to recycle/duplicate
the service stack. See ``opensquad/proc_text.py`` for the fix and the rules for
choosing the native vs UTF-8 flavour.

This file locks it down twice:
  * statically — no bare ``text=True`` may reappear under ``src/``;
  * behaviourally — a real GBK-emitting child is decoded correctly, and the
    poisoned (bare ``text=True`` + UTF-8 mode) variant is proven to still be
    broken, so the static guard cannot pass on a test that asserts nothing.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from opensquad.proc_text import native_encoding, native_text_kwargs, to_text, utf8_text_kwargs

_SRC = Path(__file__).resolve().parents[1] / "src"
# Vendored bundles (PyInstaller _internal, frontend node_modules) are not ours.
_SKIP_PARTS = {
    "build",
    "node_modules",
    ".venv",
    "_internal",
    "__pycache__",
    ".mypy_cache",
    "nexuschat-pro",
}

_SUBPROCESS_MODULES = {"subprocess", "_subprocess", "sp", "subprocess_mod"}
_SPAWNING_FUNCS = {"run", "Popen", "call", "check_call", "check_output"}
_TEXT_FLAGS = ("text", "universal_newlines")

# Chinese "中文" as cp936/cp1252-ish raw bytes — the shape native Windows tools
# emit on a zh-CN system.
_GBK_BYTES = "中文".encode("cp936")
_GBK_CHILD = f"import sys; sys.stdout.buffer.write({_GBK_BYTES!r})"


def _dotted(node: ast.AST) -> str:
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        parts.reverse()
        return ".".join(parts)
    return ""


def _find_unprotected_text_calls(src: str) -> list[tuple[int, str]]:
    """Return [(lineno, call)] for ``text=True`` without ``encoding``/``errors``."""
    tree = ast.parse(src)
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        dotted = _dotted(node.func)
        if not dotted:
            continue
        head, _, func = dotted.rpartition(".")
        if head not in _SUBPROCESS_MODULES or func not in _SPAWNING_FUNCS:
            continue
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        if not any(isinstance(kw.get(flag), ast.Constant) and kw[flag].value is True for flag in _TEXT_FLAGS):
            continue
        if "encoding" in kw or "errors" in kw:
            continue
        hits.append((node.lineno, dotted))
    return hits


def _scan_repo() -> list[str]:
    out: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        if _SKIP_PARTS & set(path.parts):
            continue
        src = path.read_text(encoding="utf-8", errors="replace")
        try:
            hits = _find_unprotected_text_calls(src)
        except SyntaxError:
            continue
        rel = path.relative_to(_SRC.parent).as_posix()
        for lineno, call in hits:
            out.append(f"{rel}:{lineno}: {call}(text=True) 未指定 encoding/errors")
    return out


# --------------------------------------------------------------------------- #
# Static guard
# --------------------------------------------------------------------------- #


def test_no_unprotected_text_mode_subprocess_calls():
    """A bare ``text=True`` can silently discard a whole subprocess result."""
    hits = _scan_repo()
    assert not hits, (
        "subprocess 的 text=True 若不带 encoding/errors，在 UTF-8 模式下解码 GBK 输出会"
        "在 reader 线程里抛 UnicodeDecodeError，调用方只会拿到空输出（静默丢数据）。"
        "请改用 opensquad.proc_text 的 native_text_kwargs()（系统自带工具）"
        "或 utf8_text_kwargs()（git/node/pip 等输出 UTF-8 的工具）:\n" + "\n".join(hits)
    )


def test_guard_detects_bare_text_true():
    """Mutation check — the exact shape that broke `opensquad dev`."""
    broken = (
        "import subprocess\n"
        "\n"
        "def probe():\n"
        "    return subprocess.run(['netstat', '-ano'], capture_output=True, text=True)\n"
    )
    assert [c for _, c in _find_unprotected_text_calls(broken)] == ["subprocess.run"]


def test_guard_detects_aliased_module_and_universal_newlines():
    broken = "import subprocess as _subprocess\n\n_s = _subprocess.run(['x'], universal_newlines=True)\n"
    assert len(_find_unprotected_text_calls(broken)) == 1


def test_guard_allows_protected_variants():
    """Every shape the fix actually introduced must pass the guard."""
    fixed = (
        "import subprocess\n"
        "from opensquad.proc_text import native_text_kwargs, utf8_text_kwargs\n"
        "\n"
        "a = subprocess.run(['netstat', '-ano'], capture_output=True, **native_text_kwargs())\n"
        "b = subprocess.run(['git', 'status'], capture_output=True, **utf8_text_kwargs())\n"
        "c = subprocess.Popen(['x'], text=True, encoding='utf-8', errors='replace')\n"
        "d = subprocess.Popen(['x'], stdout=subprocess.PIPE)\n"
    )
    assert _find_unprotected_text_calls(fixed) == []


# --------------------------------------------------------------------------- #
# Behavioural proof
# --------------------------------------------------------------------------- #


def test_native_kwargs_decodes_cp936_output_without_dying():
    """Native tools emit the console code page; that must decode, not vanish."""
    result = subprocess.run(
        [sys.executable, "-c", _GBK_CHILD],
        capture_output=True,
        **native_text_kwargs(),
    )
    assert result.returncode == 0
    assert result.stdout is not None, "reader thread died → output silently lost"
    if native_encoding().lower().replace("_", "") in {"cp936", "gbk", "gb2312", "mbcs", "936"}:
        assert result.stdout == "中文"
    else:
        # UTF-8/latin locale: replacement chars are acceptable, silence is not.
        assert result.stdout != ""


def test_utf8_kwargs_decodes_utf8_child():
    result = subprocess.run(
        [sys.executable, "-c", "print('中文路径')"],
        capture_output=True,
        **utf8_text_kwargs(),
    )
    assert result.stdout.strip() == "中文路径"


def test_bare_text_true_is_proven_broken():
    """Mutation-check the *reason* the guard exists (CPython reader-thread bug).

    A grandchild emits cp936 bytes; the child decodes it with a bare
    ``text=True`` under UTF-8 mode. Historically that yields ``stdout is None``
    (thread died, no exception surfaced). If CPython ever starts raising instead,
    this test still fails loudly — either way the bare form must not be trusted.
    """
    probe = (
        "import subprocess, sys\n"
        f"child = [sys.executable, '-c', {_GBK_CHILD!r}]\n"
        "try:\n"
        "    r = subprocess.run(child, capture_output=True, text=True)\n"
        "    print('OK' if r.stdout == '中文' else 'LOST:%r' % (r.stdout,))\n"
        "except UnicodeDecodeError:\n"
        "    print('RAISED')\n"
    )
    out = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", probe],
        capture_output=True,
        **utf8_text_kwargs(),
    )
    verdict = (out.stdout or "").strip()
    assert verdict.startswith("LOST") or verdict == "RAISED", (
        "bare text=True unexpectedly decoded cp936 correctly — the guard's premise "
        f"changed, re-review opensquad/proc_text.py (got {verdict!r})"
    )
    assert "中文" not in verdict


# --------------------------------------------------------------------------- #
# Helper unit tests
# --------------------------------------------------------------------------- #


def test_to_text_normalises_bytes_str_and_none():
    assert to_text(None) == ""
    assert to_text("already text") == "already text"
    assert to_text(_GBK_BYTES) == "中文"
    assert to_text("中文".encode(), encoding="utf-8") == "中文"


def test_to_text_never_raises_on_undecodable_bytes():
    """This is what silently swallowed start_cmd's stderr diagnostics."""
    assert to_text(b"\xff\xfe\xff", encoding="utf-8") != ""
    assert to_text("str has no decode()") == "str has no decode()"


def test_kwargs_flavours_are_text_mode_with_replacement():
    for kw in (native_text_kwargs(), utf8_text_kwargs()):
        assert kw["text"] is True
        assert kw["errors"] == "replace"
        assert kw["encoding"]
    assert utf8_text_kwargs()["encoding"] == "utf-8"
    assert native_text_kwargs()["encoding"] == native_encoding()


@pytest.mark.skipif(sys.platform != "win32", reason="mbcs only exists on Windows")
def test_native_encoding_ignores_utf8_mode_on_windows():
    """`getpreferredencoding` flips to utf-8 under PYTHONUTF8=1 — that was the bug."""
    import locale

    assert native_encoding() == locale.getencoding()
