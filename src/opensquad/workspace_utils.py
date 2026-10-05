"""
OpenSquad Workspace Utilities

Workspace management tools: detect, initialize, and record recently used workspaces.
"""

import hashlib
import json
import logging
import os
import platform
from pathlib import Path

logger = logging.getLogger(__name__)

# Global workspace config directory (across installation directories)
if platform.system() == "Windows":
    GLOBAL_CONFIG_DIR = Path(os.environ.get("USERPROFILE", "C:\\Users\\Default")) / ".opensquad"
else:
    GLOBAL_CONFIG_DIR = Path.home() / ".opensquad"

LAST_WORKSPACE_FILENAME = "last_workspace.json"
# The pre-isolation location, shared by every installation on the machine. Read
# once as a migration source, never written again.
LEGACY_LAST_WORKSPACE_FILE = GLOBAL_CONFIG_DIR / LAST_WORKSPACE_FILENAME

# Kept for callers that imported the old constant: it points at the legacy file,
# not at the per-installation one. New code wants ``last_workspace_path()``.
LAST_WORKSPACE_FILE = LEGACY_LAST_WORKSPACE_FILE


def instance_slug() -> str:
    """A stable short id for *this installation*.

    Two installations on one machine — a source checkout and the packaged desktop
    app — must not share "the last workspace I used". They previously did, because
    that pointer lives in ``~/.opensquad/last_workspace.json``, which is per user,
    not per installation: the desktop app wrote its choice there and the source
    install then booted into it (and the reverse).

    ``OPENSQUAD_INSTANCE`` overrides it; otherwise it is derived from the install
    root, which differs by construction (the repo, vs ``_internal`` in the
    packaged app).
    """
    raw = os.environ.get("OPENSQUAD_INSTANCE", "").strip()
    if raw:
        safe = "".join(c if (c.isalnum() or c in "-_") else "-" for c in raw)
        return safe[:40] or "default"
    try:
        from opensquad.system_config import syscfg

        root = syscfg.get_builtin_root()
    except Exception:
        root = str(Path(__file__).resolve().parents[1])
    digest = hashlib.sha1(os.path.normcase(os.path.abspath(str(root))).encode("utf-8")).hexdigest()
    return digest[:10]


def instance_config_dir() -> Path:
    """Per-installation directory for state that used to be global."""
    return GLOBAL_CONFIG_DIR / "instances" / instance_slug()


def last_workspace_path() -> Path:
    """This installation's workspace pointer file."""
    return instance_config_dir() / LAST_WORKSPACE_FILENAME


def read_last_workspace_path(path: Path | str | None = None) -> str | None:
    """The workspace recorded in a pointer file (this installation's by default).

    Also used by the ``syscfg`` config loader and the shipped plugins, which used
    to open ``~/.opensquad/last_workspace.json`` themselves — one copy of "where is
    the workspace" per installation, not four.
    """
    if path is None:
        target = last_workspace_path()
        if not target.exists():
            # Upgrade path: fall back to the machine-global file this pointer replaced, so
            # an existing install keeps the workspace it was already on and the plugins /
            # standalone adapters that only ever knew that file keep working.
            target = LEGACY_LAST_WORKSPACE_FILE
    else:
        target = Path(path)
    if not target.exists():
        return None
    try:
        with open(target, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    value = (data or {}).get("last_workspace")
    return str(value) if value else None


def _load_last_workspace_raw() -> dict:
    """Load raw workspace metadata with in-process caching.

    Reads this installation's file; falls back to the legacy global one exactly
    once (when the instance file does not exist yet), so an upgrade keeps the
    workspace the user was already on.
    """
    global _last_workspace_cache, _last_workspace_cache_loaded
    if _last_workspace_cache_loaded:
        return _last_workspace_cache
    source = last_workspace_path()
    if not source.exists():
        source = LEGACY_LAST_WORKSPACE_FILE
    if source.exists():
        try:
            with open(source, encoding="utf-8") as f:
                _last_workspace_cache = json.load(f)
        except Exception:
            _last_workspace_cache = {}
    _last_workspace_cache_loaded = True
    return _last_workspace_cache


# Desktop (Electron) app stores its workspace preference under Electron
# userData (OPENSQUAD_APP_DATA). The workspace itself may live elsewhere.
DESKTOP_WORKSPACE_CONFIG = "desktop-workspace.json"

# In-process cache: avoid repeated file reads for workspace metadata
_last_workspace_cache: dict = {}
_last_workspace_cache_loaded: bool = False


def _load_last_workspace_raw() -> dict:
    """Load raw workspace metadata with in-process caching."""
    global _last_workspace_cache, _last_workspace_cache_loaded
    if _last_workspace_cache_loaded:
        return _last_workspace_cache
    if LAST_WORKSPACE_FILE.exists():
        try:
            with open(LAST_WORKSPACE_FILE, encoding="utf-8") as f:
                _last_workspace_cache = json.load(f)
        except Exception:
            _last_workspace_cache = {}
    _last_workspace_cache_loaded = True
    return _last_workspace_cache


def get_default_workspace_path() -> str:
    """Return the default workspace path (OS-dependent)."""
    if platform.system() == "Windows":
        base_dir = Path(os.environ.get("USERPROFILE", "C:\\Users\\Default")) / "Documents"
    else:
        base_dir = Path.home() / "Documents"

    return str(base_dir / "OpenSquad-Workspace")


def load_last_workspace() -> str | None:
    """Load the most recently used workspace path (cached in-process)."""
    data = _load_last_workspace_raw()
    workspace_path = data.get("last_workspace")

    if workspace_path and os.path.exists(os.path.join(workspace_path, ".opensquad")):
        return workspace_path
    return None


def save_last_workspace(workspace_path: str, workspace_name: str | None = None, set_as_current: bool = True):
    """Save workspace record (write-guarded: only writes when data actually changes).

    set_as_current=True  -> also update last_workspace (used when switching)
    set_as_current=False -> only add to recent_workspaces list without changing the active workspace (used when adding/registering)
    """
    GLOBAL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # Load existing records (cached)
    data = dict(_load_last_workspace_raw())  # shallow copy so we can mutate
    data.setdefault("recent_workspaces", [])

    from datetime import datetime, timezone

    # Compute new values
    new_recent = data.get("recent_workspaces", [])
    new_last = workspace_path if set_as_current else data.get("last_workspace")

    new_recent = [w for w in new_recent if w["path"] != workspace_path]
    new_recent.insert(
        0,
        {
            "path": workspace_path,
            "name": workspace_name or os.path.basename(workspace_path),
            "last_opened": datetime.now(timezone.utc).isoformat() + "Z",
        },
    )
    new_recent = new_recent[:10]

    # Write-guarded: skip disk write if nothing changed
    if data.get("last_workspace") == new_last and data.get("recent_workspaces") == new_recent:
        return

    data["last_workspace"] = new_last
    data["recent_workspaces"] = new_recent

    # Update cache
    global _last_workspace_cache
    _last_workspace_cache = data

    # Write THIS installation's pointer, atomically. The legacy global file is
    # deliberately never written again: it is exactly what let a desktop launch
    # re-point a source install (see :func:`instance_slug`).
    target = last_workspace_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, target)


def detect_legacy_data(install_dir: str) -> bool:
    """
    Detect whether the installation directory contains legacy user data (pre-workspace era).

    Detection markers:
    - chat.db or sessions/ directory containing files
    - data/ directory containing user files
    - agents/ directory containing user agents
    """
    legacy_indicators = [
        os.path.join(install_dir, "gateway", "backend", "chat.db"),
        os.path.join(install_dir, "data", "uploads"),
        os.path.join(install_dir, "sessions"),
        os.path.join(install_dir, "agents"),
    ]

    for path in legacy_indicators:
        if os.path.exists(path):
            # If it's a directory, check if it's non-empty
            if os.path.isdir(path):
                try:
                    if any(os.scandir(path)):  # directory is non-empty
                        return True
                except PermissionError:
                    # No access permission, skip
                    continue
            else:
                # File exists = treat as having data
                return True

    return False


def get_desktop_app_data_dir() -> str | None:
    """Return Electron's fixed app config dir (OPENSQUAD_APP_DATA), if set."""
    raw = os.environ.get("OPENSQUAD_APP_DATA", "").strip()
    return os.path.abspath(raw) if raw else None


def load_desktop_workspace_path(app_data_dir: str) -> str | None:
    """Read the user-chosen workspace path from desktop-workspace.json."""
    cfg_path = os.path.join(app_data_dir, DESKTOP_WORKSPACE_CONFIG)
    if not os.path.isfile(cfg_path):
        return None
    try:
        with open(cfg_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    raw = (data.get("workspace_path") or "").strip()
    return os.path.abspath(raw) if raw else None


def save_desktop_workspace_path(app_data_dir: str, workspace_path: str) -> None:
    """Persist the active desktop workspace path for the next app launch."""
    from datetime import datetime, timezone

    os.makedirs(app_data_dir, exist_ok=True)
    cfg_path = os.path.join(app_data_dir, DESKTOP_WORKSPACE_CONFIG)
    payload = {
        "workspace_path": os.path.abspath(workspace_path),
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def _is_valid_workspace_dir(workspace_path: str) -> bool:
    return os.path.isdir(workspace_path) and os.path.isdir(os.path.join(workspace_path, ".opensquad"))


def resolve_desktop_workspace() -> tuple[str, str]:
    """
    Resolve (app_data_dir, workspace_path) for the packaged desktop app.

    app_data_dir is Electron userData — always writable, holds app prefs.
    workspace_path is where chat.db / uploads / agents live; defaults to
    app_data_dir on first run but can be changed in System Settings.
    """
    app_data = get_desktop_app_data_dir() or os.environ.get("OPENSQUAD_USER_DATA", "").strip()
    if not app_data:
        raise RuntimeError("Desktop app requires OPENSQUAD_APP_DATA or OPENSQUAD_USER_DATA")
    app_data = os.path.abspath(app_data)
    os.makedirs(app_data, exist_ok=True)

    configured = load_desktop_workspace_path(app_data)
    if configured and _is_valid_workspace_dir(configured):
        return app_data, configured

    if configured and not _is_valid_workspace_dir(configured):
        print(
            f"[Workspace] Configured desktop workspace missing or invalid: {configured}\n"
            f"[Workspace] Falling back to app data dir: {app_data}"
        )
        save_desktop_workspace_path(app_data, app_data)

    return app_data, app_data


def bootstrap_desktop_workspace() -> str:
    """
    Initialize or reuse the desktop workspace on frozen (packaged) startup.
    Called from both the gateway (main.py) and the launcher.

    Uses a cross-process file lock so Gateway + Launcher can start in parallel
    without racing on first-run ``init_workspace`` / resource copy.
    """
    import sys as _sys

    from opensquad import system_config as syscfg

    if not getattr(_sys, "frozen", False):
        raise RuntimeError("bootstrap_desktop_workspace() is for frozen/desktop mode only")

    _app_data, workspace_path = resolve_desktop_workspace()
    os.makedirs(workspace_path, exist_ok=True)

    with workspace_bootstrap_lock(workspace_path):
        syscfg.set_workspace(workspace_path)

        meta = os.path.join(workspace_path, ".opensquad", "workspace.json")
        if not os.path.exists(meta):
            print(f"[Workspace] First run — initializing desktop workspace: {workspace_path}")
            syscfg.init_workspace(workspace_path, copy_config=True)
            _copy_default_resources(workspace_path, syscfg.get_builtin_root())
        else:
            print(f"[Workspace] Reusing desktop workspace: {workspace_path}")

        save_last_workspace(workspace_path)
    return workspace_path


class _BootstrapLock:
    """Simple cross-process exclusive lock for workspace bootstrap."""

    def __init__(self, lock_path: str, timeout_s: float = 120.0):
        self.lock_path = lock_path
        self.timeout_s = timeout_s
        self._fh = None

    def __enter__(self):
        import time

        os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
        deadline = time.time() + self.timeout_s
        self._fh = open(self.lock_path, "a+", encoding="utf-8")
        while True:
            try:
                if platform.system() == "Windows":
                    import msvcrt

                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() >= deadline:
                    raise TimeoutError(f"workspace bootstrap lock timeout: {self.lock_path}")
                time.sleep(0.1)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._fh is None:
            return
        try:
            if platform.system() == "Windows":
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            self._fh.close()
        except Exception:
            pass
        self._fh = None


def _try_lock_file(path: str):
    """Take a non-blocking exclusive OS lock on *path*; the handle returned holds it.

    Holding the handle for the process lifetime is the liveness signal: the OS drops it
    when the process dies, so a crashed owner never leaves a lock that has to be cleared
    by hand (which is why this file previously had a pid check to get wrong).
    """
    handle = open(path, "a+", encoding="utf-8")
    try:
        if platform.system() == "Windows":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


class WorkspaceRunLock:
    """Ownership of one workspace by one *installation*, held for the process lifetime.

    The bootstrap lock above is short-lived: it only serialises first-run init. This one
    answers a different question — "is another OpenSquad installation already running
    against this workspace?" — because sharing one is not a feature: the two fight over
    the same ports (9555/9600/9720), the same ``chat.db`` and the same plugin data
    (websearch's browser_profile, the sensevoice model), and the loser half-starts with
    silently failing plugin services instead of an error.

    Sibling services of the same installation (gateway, launcher, registry) are allowed:
    they share a slug. A different installation is refused, with the owner named.
    """

    OWNER_FILE = "instance.json"
    LOCK_FILE = "instance.lock"

    def __init__(self, workspace_path: str):
        self.workspace_path = os.path.abspath(str(workspace_path))
        self.meta_dir = os.path.join(self.workspace_path, ".opensquad")
        self.owner_file = os.path.join(self.meta_dir, self.OWNER_FILE)
        self.lock_file = os.path.join(self.meta_dir, self.LOCK_FILE)
        self._fh = None

    def owner(self) -> dict:
        try:
            with open(self.owner_file, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def acquire(self) -> None:
        os.makedirs(self.meta_dir, exist_ok=True)
        slug = instance_slug()
        owner = self.owner()
        if owner.get("slug") == slug:
            return  # our own sibling service: same installation, same workspace

        handle = _try_lock_file(self.lock_file)
        if handle is None:
            who = str(owner.get("slug") or "an unknown installation")
            pid = owner.get("pid") or "?"
            raise RuntimeError(
                f"[Workspace] Refusing to start: {self.workspace_path} is already in use by "
                f"another OpenSquad installation (instance {who}, pid {pid}).\n"
                f"[Workspace] This install is instance {slug}.\n"
                "[Workspace] Two installations sharing one workspace collide on ports "
                "(9555/9600/9720), chat.db and plugin data. Do one of:\n"
                "  - stop the other stack, or\n"
                "  - point this install at its own workspace (Settings -> Workspace), or\n"
                "  - run it as a separate instance: OPENSQUAD_INSTANCE=<name> with its own workspace."
            )
        self._fh = handle

        from datetime import datetime, timezone

        payload = {
            "slug": slug,
            "pid": os.getpid(),
            "workspace": self.workspace_path,
            "since": datetime.now(timezone.utc).isoformat(),
        }
        tmp = self.owner_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.owner_file)


_RUN_LOCKS: dict[str, WorkspaceRunLock] = {}


def ensure_workspace_owner(workspace_path: str) -> None:
    """Take ownership of *workspace_path* for this process, once, or refuse to start."""
    key = os.path.normcase(os.path.abspath(str(workspace_path)))
    if key in _RUN_LOCKS:
        return
    lock = WorkspaceRunLock(workspace_path)
    lock.acquire()
    _RUN_LOCKS[key] = lock


def workspace_bootstrap_lock(workspace_path: str):
    """Lock ``{workspace}/.opensquad/bootstrap.lock`` and claim the workspace.

    Every bootstrap path goes through here, so this is also where the per-installation
    ownership check happens (see :class:`WorkspaceRunLock`): a second installation on the
    same workspace is refused loudly rather than allowed to half-start.
    """
    ensure_workspace_owner(workspace_path)
    lock_path = os.path.join(workspace_path, ".opensquad", "bootstrap.lock")
    return _BootstrapLock(lock_path)


def persist_desktop_workspace_switch(workspace_path: str) -> None:
    """Record a workspace switch so Electron picks it up on next launch."""
    app_data = get_desktop_app_data_dir()
    if app_data:
        save_desktop_workspace_path(app_data, workspace_path)


# Builtin model cards that should always be available in workspace lists
# (never overwrite an existing same-named file the user may have edited).
# Every name here is looked up in the *installed* ``model_cards/`` dir, so each
# one must be tracked by git and listed in MANIFEST.in + `package-data`
# (tests/test_verify_release_artifacts.py enforces the allowlist; the missing
# card is otherwise silent and the ASR dropdown simply has no built-in option).
BUILTIN_MODEL_CARD_FILES = ("builtin-sensevoice-asr.json",)


def ensure_builtin_model_cards(
    workspace_path: str | None = None,
    install_dir: str | None = None,
) -> list[str]:
    """Copy missing builtin model cards into the workspace ``model_cards/``.

    Returns the list of card filenames that were newly copied.
    Existing files are left untouched.
    """
    import shutil

    from opensquad.system_config import syscfg

    root = install_dir or syscfg.get_builtin_root()
    src_dir = os.path.join(root, "model_cards")
    if workspace_path:
        dst_dir = os.path.join(os.path.abspath(workspace_path), "model_cards")
    else:
        dst_dir = syscfg.workspace_model_cards_dir()

    if not os.path.isdir(src_dir):
        logger.warning(
            "[workspace] built-in model cards dir is missing (%s); no built-in model card was seeded",
            src_dir,
        )
        return []

    os.makedirs(dst_dir, exist_ok=True)
    copied: list[str] = []
    for card_name in BUILTIN_MODEL_CARD_FILES:
        src = os.path.join(src_dir, card_name)
        dst = os.path.join(dst_dir, card_name)
        if not os.path.isfile(src):
            # A packaging regression, not a user problem: the artifact must
            # carry every card named above, or a fresh install has no built-in
            # ASR (both the agent voice picker and group voice read these).
            logger.warning("[workspace] built-in model card not shipped: %s", src)
            continue
        if os.path.isfile(dst):
            continue
        try:
            shutil.copy2(src, dst)
            copied.append(card_name)
        except OSError as e:
            logger.warning("[workspace] could not seed built-in model card %s: %s", card_name, e)
    return copied


def _copy_default_resources(workspace_path: str, install_dir: str):
    """Copy default model cards, MCP config and agent to a new workspace."""
    import shutil

    # Copy model cards
    src_model_cards = os.path.join(install_dir, "model_cards")
    ws_model_cards = os.path.join(workspace_path, "model_cards")
    if os.path.isdir(src_model_cards):
        os.makedirs(ws_model_cards, exist_ok=True)
        seed_cards = (
            "deepseek-v4-flash.json",
            "deepseek-v4-pro.json",
            *BUILTIN_MODEL_CARD_FILES,
        )
        for card_name in seed_cards:
            src = os.path.join(src_model_cards, card_name)
            dst = os.path.join(ws_model_cards, card_name)
            if os.path.isfile(src) and not os.path.isfile(dst):
                shutil.copy2(src, dst)
                print(f"[Workspace] Copied model card: {card_name}")

    # Copy default MCP config to workspace data/
    src_mcp = os.path.join(install_dir, "pymcp", "config_basic.json")
    ws_data = os.path.join(workspace_path, "data")
    ws_mcp = os.path.join(ws_data, "mcp_config.json")
    if os.path.isfile(src_mcp) and not os.path.isfile(ws_mcp):
        os.makedirs(ws_data, exist_ok=True)
        shutil.copy2(src_mcp, ws_mcp)
        print("[Workspace] Created default MCP config: data/mcp_config.json")

    # Copy seed agents into the workspace.
    # pm/coder/qa: the multi-agent collaboration team that ships out of the
    # box. Workspace DB init (init_data.init_default_data) pre-registers
    # their group_chat accounts and a default group so a fresh deploy can
    # experience multi-agent collaboration after the user fills in a model
    # card api_key and starts them.
    src_agents = os.path.join(install_dir, "agents")
    ws_agents = os.path.join(workspace_path, "agents")
    for agent_name in ("pm", "coder", "qa"):
        agent_src = os.path.join(src_agents, agent_name)
        agent_dst = os.path.join(ws_agents, agent_name)
        if os.path.isdir(agent_src) and not os.path.isdir(agent_dst):
            shutil.copytree(agent_src, agent_dst)
            print(f"[Workspace] Created seed agent: agents/{agent_name}/")


def bootstrap_workspace() -> str:
    """
    Workspace initialization flow at startup.
    Returns the finalized workspace path.
    """
    import sys as _sys

    from opensquad import system_config as syscfg

    # ── Frozen (desktop app): workspace path comes from Electron via
    # OPENSQUAD_USER_DATA; the fixed app config dir is OPENSQUAD_APP_DATA.
    # Users can point the workspace elsewhere via System Settings → Workspace.
    if getattr(_sys, "frozen", False) and (
        os.environ.get("OPENSQUAD_APP_DATA") or os.environ.get("OPENSQUAD_USER_DATA")
    ):
        return bootstrap_desktop_workspace()

    # 1. Try to load the last-used workspace
    last_workspace = load_last_workspace()
    if last_workspace:
        with workspace_bootstrap_lock(last_workspace):
            syscfg.set_workspace(last_workspace)
            print(f"[Workspace] Loaded: {last_workspace}")
            save_last_workspace(last_workspace)  # update last-opened time
        return last_workspace

    # 2. Detect legacy data
    install_dir = syscfg.get_builtin_root()
    has_legacy_data = detect_legacy_data(install_dir)

    if has_legacy_data:
        # Trigger migration flow (use installation dir as workspace temporarily, pending user migration)
        print("[Workspace] Detected legacy data in installation directory")
        print("[Workspace] Using installation directory as workspace (legacy mode)")
        print("[Workspace] Please run migration wizard to move data to a dedicated workspace")
        workspace_path = install_dir
        with workspace_bootstrap_lock(workspace_path):
            syscfg.set_workspace(workspace_path)
    else:
        # Create default workspace with full initialization
        workspace_path = get_default_workspace_path()
        print(f"[Workspace] Creating default workspace at: {workspace_path}")
        with workspace_bootstrap_lock(workspace_path):
            syscfg.init_workspace(workspace_path, copy_config=True)
            _copy_default_resources(workspace_path, install_dir)

    # 3. Save workspace path
    save_last_workspace(workspace_path)
    return workspace_path
