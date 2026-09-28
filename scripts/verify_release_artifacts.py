"""Verify the built release artifacts before they are published.

Invoked by .github/workflows/release.yml — ``--tree`` before ``python -m build``
and the artifact check after it — and safe to run locally:

    python scripts/verify_release_artifacts.py --tree     # pre-build, scans the checkout
    python scripts/verify_release_artifacts.py [dist_dir] # post-build, scans the artifacts

Why this exists: the repo is public, and ``python -m build`` happily sweeps in
whatever is on disk. Three real leaks motivated it —

* ``npm install`` runs before the build in CI, so ``node_modules/`` (including
  vendored ``.py`` files) landed in the sdist and wheel;
* private plugins/skills (``news2theme``, ``j-space``, …) and local model cards
  exist on a developer machine but must never be published;
* the wheel used to ship without ``model_cards``/``plugins``, which made
  ``pip install opensquad`` uninstallable-in-practice.

The artifact check is the guarantee; ``--tree`` is the early-warning sibling that
catches a dirty checkout *before* a 3-minute build. Both were needed: the leak
shapes below only exist on a developer machine, so a check that only ever ran on
a clean CI checkout could not see them.

Checks, per artifact:
  1. leak     — no private path, model cache, node_modules, or build junk
  2. tracked  — every shipped file exists in ``git ls-files`` (skipped outside a
                git checkout)
  3. complete — the wheel carries the default resources and the built web UI
"""

from __future__ import annotations

import contextlib
import glob
import io
import os
import subprocess
import sys
import tarfile
import zipfile

# Substrings that must never appear in a published artifact.
LEAK_MARKERS = (
    "news2theme",
    "zt2theme",
    "j-space",
    "cursor-api",
    "vcs_collaboration",
    "skills/playwright/",
    # No trailing slash: a renamed backup (`node_modules_old_*/`) carries the
    # same vendored tree.
    "node_modules",
    "/reranker/models/",
    "models--",
    "__pycache__",
    "backend-win",
    # Plugin hot-reload marker, and an ad-hoc dump that holds a bridge key plus
    # absolute workspace paths.
    ".reload_ts",
    "debug_config.txt",
)
LEAK_SUFFIXES = (".pyc", ".pyo", ".log", ".safetensors", ".gguf", ".onnx")
# Exact basenames that must never ship.
LEAK_NAMES = (".env", ".env.local", ".env.production", ".DS_Store", "id_rsa", "credentials.json")

# Paths the wheel must contain for `pip install opensquad` to seed a workspace
# and serve the UI.
WHEEL_REQUIRED = (
    "opensquad/gateway/config.json",
    "opensquad/gateway/nexuschat-pro/dist/index.html",
    "opensquad/launcher/pkg_import_map.json",
    "model_cards/deepseek-v4-flash.json",
    "pymcp/config_basic.json",
    "agents/pm/config.json",
    # Every agent boot reads one of these (base|thought × fc|xml, picked from
    # the model's tool-call capability); without the dir `agents_boot` raises
    # "Base prompt not found" and no agent can start.
    "prompts/base_fc.md",
    "prompts/thought_fc.md",
)
# Minimum per-directory file counts (guards against silently shipping empty dirs).
WHEEL_MIN_FILES = {
    "plugins/": 100,
    "skills/": 10,
    "model_cards/": 1,
    "agents/": 1,
    # 4 templates + 49 `parts/` fragments the templates include by name.
    "prompts/": 50,
}

# Generated at build time; not expected to be tracked by git.
GENERATED = (
    "PKG-INFO",
    "setup.cfg",
    ".egg-info/",
    ".dist-info/",
    # The web UI is built by the `Build frontend` step before `python -m build`.
    "gateway/nexuschat-pro/dist/",
    # Plugin UI bundles are React builds (gitignored via
    # `src/plugins/*/ui/index.js`), shipped so a pip install serves them.
    "/ui/index.js",
)


def _is_generated(path: str) -> bool:
    return any(marker in path for marker in GENERATED)


def _leaks(path: str) -> str | None:
    for marker in LEAK_MARKERS:
        if marker in path:
            return marker
    for suffix in LEAK_SUFFIXES:
        if path.endswith(suffix):
            return suffix
    if os.path.basename(path) in LEAK_NAMES:
        return os.path.basename(path)
    return None


def _tracked_files() -> set[str] | None:
    try:
        out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


def _manifest_selection() -> set[str] | None:
    """Replay MANIFEST.in over the current checkout.

    This is the pre-build twin of the sdist check: it asks which files
    ``python -m build`` would sweep in *right now*, so a dirty checkout fails in
    a minute instead of after a multi-minute build. Returns ``None`` when
    MANIFEST.in or setuptools is unavailable.
    """
    try:
        from setuptools._distutils.filelist import FileList
    except ImportError:  # pragma: no cover - setuptools is a build dep
        return None
    if not os.path.exists("MANIFEST.in"):
        return None

    # `graft`/`prune` warn about patterns that match nothing (e.g. an optional
    # lockfile); that is expected here and would only add noise.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        filelist = FileList()
        filelist.findall()
        with open("MANIFEST.in", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line and not line.startswith("#"):
                    try:
                        filelist.process_template_line(line)
                    except Exception:  # noqa: BLE001 - a broken directive is the build's problem
                        continue
    return {path.replace(os.sep, "/") for path in filelist.files}


def _tree_errors(tracked: set[str] | None) -> list[str]:
    """Pre-build check: what would a build sweep in that git does not track?"""
    if tracked is None:
        return []
    selection = _manifest_selection()
    if selection is None:
        return []
    return [
        f"[tree] a build would package this untracked file: {path}"
        for path in sorted(selection)
        if path not in tracked and not _is_generated(path)
    ]


def _wheel_entries(whl: str) -> list[str]:
    with zipfile.ZipFile(whl) as z:
        return [n for n in z.namelist() if not n.endswith("/")]


def _sdist_entries(tar_path: str) -> list[str]:
    with tarfile.open(tar_path) as t:
        members = t.getmembers()
    root = members[0].name.split("/")[0] if members else ""
    out = []
    for m in members:
        if m.isdir():
            continue
        rel = m.name[len(root) + 1 :] if root and m.name.startswith(root + "/") else m.name
        out.append(rel)
    return out


def _check(entries: list[str], label: str, tracked: set[str] | None, *, wheel: bool) -> list[str]:
    errors: list[str] = []

    # 1. leak
    for path in entries:
        marker = _leaks(path)
        if marker:
            errors.append(f"[{label}] leaked non-public content ({marker!r}): {path}")

    # 2. tracked
    if tracked is not None:
        for path in entries:
            if _is_generated(path):
                continue
            repo_path = ("src/" + path) if wheel else path
            if repo_path not in tracked:
                errors.append(f"[{label}] not tracked by git ({repo_path}): {path}")

    return errors


def _check_wheel_completeness(entries: list[str]) -> list[str]:
    errors: list[str] = []
    for required in WHEEL_REQUIRED:
        if required not in entries:
            errors.append(f"[wheel] missing required file: {required}")
    for prefix, minimum in WHEEL_MIN_FILES.items():
        count = sum(1 for n in entries if n.startswith(prefix))
        if count < minimum:
            errors.append(f"[wheel] {prefix} has {count} files, expected at least {minimum}")
    return errors


def main() -> int:
    if "--tree" in sys.argv[1:]:
        tracked = _tracked_files()
        if tracked is None:
            print("verify --tree: not a git checkout - nothing to compare against", file=sys.stderr)
            return 0
        errors = _tree_errors(tracked)
        if errors:
            print(f"verify --tree: FAILED with {len(errors)} problem(s):", file=sys.stderr)
            for err in errors[:40]:
                print(f"  {err}", file=sys.stderr)
            if len(errors) > 40:
                print(f"  ... and {len(errors) - 40} more", file=sys.stderr)
            print(
                "\nEither track the file, delete it, or add it to the never-ship block in MANIFEST.in.", file=sys.stderr
            )
            return 1
        print("verify --tree: OK - a build would only package tracked files")
        return 0

    dist_dir = sys.argv[1] if len(sys.argv) > 1 else "dist"
    wheels = sorted(glob.glob(os.path.join(dist_dir, "*.whl")))
    sdists = sorted(glob.glob(os.path.join(dist_dir, "*.tar.gz")))
    if not wheels or not sdists:
        print(f"verify: expected a wheel and an sdist in {dist_dir!r}", file=sys.stderr)
        return 2

    tracked = _tracked_files()
    if tracked is None:
        print("verify: not a git checkout - skipping the tracked-files check", file=sys.stderr)

    errors: list[str] = []
    for whl in wheels:
        entries = _wheel_entries(whl)
        print(f"verify: {os.path.basename(whl)}  ({len(entries)} files)")
        errors += _check(entries, "wheel", tracked, wheel=True)
        errors += _check_wheel_completeness(entries)
    for sdist in sdists:
        entries = _sdist_entries(sdist)
        print(f"verify: {os.path.basename(sdist)}  ({len(entries)} files)")
        errors += _check(entries, "sdist", tracked, wheel=False)

    if errors:
        print(f"\nverify: FAILED with {len(errors)} problem(s):", file=sys.stderr)
        for err in errors[:40]:
            print(f"  {err}", file=sys.stderr)
        if len(errors) > 40:
            print(f"  ... and {len(errors) - 40} more", file=sys.stderr)
        return 1

    print("verify: OK - no private content, everything tracked, wheel is complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
