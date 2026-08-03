"""
_migrate_launcher.py -- Extract ManagementHandler + _start_management_server from launcher.py.

Run:  python scripts/migrate_launcher.py

Produces:  src/opensquad/_launcher_api.py

Steps:
  1. Slice launcher.py lines 441-3078  (0-indexed 440:3078)
     - _start_management_server()      [441-3078]
  2. From that slice, extract lines 33-2601 (0-indexed 32:2601)
     - ManagementHandler body           [475=offset32, 3076=offset2601]
  3. Rewrite bare launcher globals -> self._ctx.attr
  4. Write the resulting file directly (plain Python, no string-escaping needed)
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

# Resolve paths relative to this script's location (scripts/)
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent

SRC = str(_PROJECT_ROOT / "src" / "opensquad" / "launcher.py")
DST = str(_PROJECT_ROOT / "src" / "opensquad" / "_launcher_api.py")


def _load_lines(path: str) -> list[str]:
    return Path(path).read_text(encoding="utf-8-sig").splitlines(keepends=True)


# ---------------------------------------------------------------------------
# Global -> _Ctx attribute mapping  (longest-first to avoid substring hits)
# ---------------------------------------------------------------------------
GLOBAL_TO_ATTR = [
    ("task_watch_stalled_notified", "task_watch_stalled_notified"),
    ("task_watch_heartbeats", "task_watch_heartbeats"),
    ("workspace_migration_tasks", "workspace_migration_tasks"),
    ("discover_plugin_services", "discover_plugin_services"),
    ("apply_config_defaults", "apply_config_defaults"),
    ("validate_agent_config", "validate_agent_config"),
    ("discover_agents", "discover_agents"),
    ("check_port_conflict", "check_port_conflict"),
    ("_resolve_discovery_port", "_resolve_discovery_port"),
    ("_cleanup_runtime_registry", "_cleanup_runtime_registry"),
    ("_read_json", "_read_json"),
    ("_plugin_services", "_plugin_services"),
    ("_processes", "_processes"),
    ("_shutdown_event", "_shutdown_event"),
    ("_log", "_log"),
    ("syscfg", "syscfg"),
    ("AGENTS_DIR", "AGENTS_DIR"),
    ("PLUGINS_DIR", "PLUGINS_DIR"),
    ("SKILLS_DIR", "SKILLS_DIR"),
    ("ROLE_CARDS_DIR", "ROLE_CARDS_DIR"),
    ("COLLAB_CARDS_DIR", "COLLAB_CARDS_DIR"),
    ("MODEL_CARDS_DIR", "MODEL_CARDS_DIR"),
    ("MANAGEMENT_PORT", "MANAGEMENT_PORT"),
    ("STALL_THRESHOLD", "STALL_THRESHOLD"),
]


def rewrite(text: str) -> str:
    for bare, attr in GLOBAL_TO_ATTR:
        pattern = rf"\b{re.escape(bare)}\b"
        replacement = f"self._ctx.{attr}"
        text = re.sub(pattern, replacement, text)
    return text


# ---------------------------------------------------------------------------
# Dynamic extraction helpers
# ---------------------------------------------------------------------------


def _find_line_index(lines: list[str], needle: str) -> int:
    for idx, line in enumerate(lines):
        if needle in line:
            return idx
    raise ValueError(f"Could not find marker: {needle}")


def _slice_between(lines: list[str], start_needle: str, end_needle: str) -> list[str]:
    start = _find_line_index(lines, start_needle)
    end = _find_line_index(lines, end_needle)
    if end <= start:
        raise ValueError(f"Invalid slice range: {start_needle!r} -> {end_needle!r}")
    return lines[start:end]


def _extract_handler_body(lines: list[str]) -> list[str]:
    start = _find_line_index(lines, "class ManagementHandler(BaseHTTPRequestHandler):")
    end = _find_line_index(lines, "# ── Task Watch Supervisor")
    if end <= start:
        raise ValueError("ManagementHandler body end marker appears before start")
    return lines[start:end]


def load_server_slice(path: str) -> list[str]:
    lines = _load_lines(path)
    return _slice_between(
        lines, "def _start_management_server(port: int = MANAGEMENT_PORT):", "# ── Task Watch Supervisor"
    )


def _find_handler_block_in_server_slice(server_lines: list[str]) -> list[str]:
    """Extract only the ManagementHandler class block from the server function slice."""
    start = _find_line_index(server_lines, "    class ManagementHandler(BaseHTTPRequestHandler):")
    end = _find_line_index(server_lines, '    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)')
    if end <= start:
        raise ValueError("ManagementHandler block end marker appears before start")
    return server_lines[start:end]


def load_handler_slice(path: str) -> list[str]:
    server_lines = load_server_slice(path)
    return _find_handler_block_in_server_slice(server_lines)


def dedent(lines: list[str], levels: int = 1) -> list[str]:
    """Remove exactly ``levels`` four-space indentation levels when present."""
    out = []
    prefix = " " * (4 * levels)
    for ln in lines:
        if not ln.strip():
            out.append("\n")
            continue
        if ln.startswith(prefix):
            out.append(ln[len(prefix) :])
        else:
            out.append(ln)
    return out


# ---------------------------------------------------------------------------
# Prefix / line filters for the handler body
# ---------------------------------------------------------------------------
SKIP_IMPORT_PREFIXES = (
    "from http.server import",
    "import urllib.parse",
    "import hashlib",
    "import secrets",
)
SKIP_PARAM_PREFIXES = (
    "task_watch_heartbeats=",
    "task_watch_stalled_notified=",
    "shutdown_event=",
    "workspace_migration_tasks=",
    "agents_dir=",
    "plugins_dir=",
    "skills_dir=",
    "role_cards_dir=",
    "collab_cards_dir=",
    "model_cards_dir=",
    "syscfg=",
    "logger=",
    "discover_agents=",
    "discover_plugin_services=",
    "AgentProcess=",
    "PluginServiceProcess=",
    "MANAGEMENT_PORT=",
    "STALL_THRESHOLD=",
    "_processes=",
    "_plugin_services=",
)
SKIP_LINES: set[str] = set()


def filter_handler_lines(lines: list[str]) -> list[str]:
    """Drop only wrapper-specific lines; preserve class method docstrings."""
    out = []
    for ln in lines:
        s = ln.strip()
        if s.startswith("class ManagementHandler(BaseHTTPRequestHandler):"):
            continue
        if any(s.startswith(p) for p in SKIP_IMPORT_PREFIXES):
            continue
        if any(s.startswith(p) for p in SKIP_PARAM_PREFIXES):
            continue
        if s in SKIP_LINES:
            continue
        out.append(ln)
    return out


# ---------------------------------------------------------------------------
# Header template
# ---------------------------------------------------------------------------
HEADER = (
    "# -*- coding: utf-8 -*-\n"
    "# AUTO-GENERATED from launcher.py dynamic server slice\n"
    "# DO NOT EDIT MANUALLY\n"
    "\n"
    '"""_launcher_api.py -- HTTP Management API handler for launcher.\n'
    "\n"
    "Extracted from launcher.py as part of P1 modularisation.\n"
    "The ManagementHandler class is produced by create_management_handler()\n"
    "which receives all launcher runtime state as constructor arguments.\n"
    '"""\n'
    "from __future__ import annotations\n"
    "\n"
    "from typing import Any, Dict, Type\n"
    "import hashlib\n"
    "import json\n"
    "import secrets\n"
    "import urllib.parse\n"
    "\n"
    "\n"
    "def create_management_handler(\n"
    "    *,\n"
    "    # Runtime state dicts\n"
    "    procesos: Dict[str, Any],\n"
    "    plug_svcs: Dict[str, Any],\n"
    "    task_hb: Dict[str, Dict[str, Any]],\n"
    "    task_sn: set,\n"
    "    shut_ev: Any,\n"
    "    ws_mig: Dict[str, Any],\n"
    "    # Directory paths\n"
    "    agents_dir: str,\n"
    "    plugins_dir: str,\n"
    "    skills_dir: str,\n"
    "    role_cards_dir: str,\n"
    "    collab_cards_dir: str,\n"
    "    model_cards_dir: str,\n"
    "    # Constants\n"
    "    mgmt_port: int,\n"
    "    stall_thresh: int,\n"
    "    # Singletons / modules\n"
    "    syscfg: Any,\n"
    "    logger: Any,\n"
    "    read_json: Any,\n"
    "    chk_port: Any,\n"
    "    res_disc_port: Any,\n"
    "    cln_reg: Any,\n"
    "    appl_def: Any,\n"
    "    val_cfg: Any,\n"
    "    disc_agents: Any,\n"
    "    disc_plug_svcs: Any,\n"
    "    AgentProcess: Type[Any],\n"
    "    PluginServiceProcess: Type[Any],\n"
    ") -> Type[Any]:\n"
    '    """Factory: build ManagementHandler bound to launcher runtime."""\n'
    "    from http.server import BaseHTTPRequestHandler\n"
    "\n"
    "    class _Ctx:\n"
    "        __slots__ = (\n"
    '            "_processes","_plugin_services","_task_watch_heartbeats",\n'
    '            "_task_watch_stalled_notified","_shutdown_event",\n'
    '            "_workspace_migration_tasks","_log","AGENTS_DIR","PLUGINS_DIR",\n'
    '            "SKILLS_DIR","ROLE_CARDS_DIR","COLLAB_CARDS_DIR","MODEL_CARDS_DIR",\n'
    '            "MANAGEMENT_PORT","STALL_THRESHOLD","syscfg","_read_json",\n'
    '            "check_port_conflict","_resolve_discovery_port","_cleanup_runtime_registry",\n'
    '            "apply_config_defaults","validate_agent_config",\n'
    '            "discover_agents","discover_plugin_services",\n'
    '            "AgentProcess","PluginServiceProcess",\n'
    "        )\n"
    "\n"
    "    _ctx = _Ctx()\n"
    "    _ctx._processes                    = procesos\n"
    "    _ctx._plugin_services              = plug_svcs\n"
    "    _ctx._task_watch_heartbeats       = task_hb\n"
    "    _ctx._task_watch_stalled_notified = task_sn\n"
    "    _ctx._shutdown_event              = shut_ev\n"
    "    _ctx._workspace_migration_tasks   = ws_mig\n"
    "    _ctx._log                         = logger\n"
    "    _ctx.AGENTS_DIR                  = agents_dir\n"
    "    _ctx.PLUGINS_DIR                 = plugins_dir\n"
    "    _ctx.SKILLS_DIR                  = skills_dir\n"
    "    _ctx.ROLE_CARDS_DIR              = role_cards_dir\n"
    "    _ctx.COLLAB_CARDS_DIR            = collab_cards_dir\n"
    "    _ctx.MODEL_CARDS_DIR             = model_cards_dir\n"
    "    _ctx.MANAGEMENT_PORT             = mgmt_port\n"
    "    _ctx.STALL_THRESHOLD             = stall_thresh\n"
    "    _ctx.syscfg                      = syscfg\n"
    "    _ctx._read_json                  = read_json\n"
    "    _ctx.check_port_conflict         = chk_port\n"
    "    _ctx._resolve_discovery_port     = res_disc_port\n"
    "    _ctx._cleanup_runtime_registry   = cln_reg\n"
    "    _ctx.apply_config_defaults       = appl_def\n"
    "    _ctx.validate_agent_config       = val_cfg\n"
    "    _ctx.discover_agents             = disc_agents\n"
    "    _ctx.discover_plugin_services    = disc_plug_svcs\n"
    "    _ctx.AgentProcess               = AgentProcess\n"
    "    _ctx.PluginServiceProcess       = PluginServiceProcess\n"
    "\n"
    "    ctx = _ctx\n"
    "\n"
    "    class ManagementHandler(BaseHTTPRequestHandler):\n"
    '        """Lightweight HTTP handler -- no FastAPI/uvicorn dependency."""\n'
    "        _ctx = ctx\n"
    "\n"
    "        def log_message(self, fmt, *args):\n"
    "            pass  # silence http.server spam\n"
    "\n"
    "        # ---- boilerplate / auth helpers ----\n"
    "\n"
    "        def _send_json(self, data: dict, status: int = 200):\n"
)


# ---------------------------------------------------------------------------
# Footer template
# ---------------------------------------------------------------------------
FOOTER = (
    "\n"
    "        @property\n"
    "        def _server_version(self) -> str:\n"
    '            return f"LauncherManagement/{_ctx.MANAGEMENT_PORT}"\n'
    "\n"
    "    return ManagementHandler\n"
    "\n"
    "\n"
    "# ---- Convenience: start the HTTP server in background thread ----\n"
    "\n"
    "def _start_management_server(\n"
    "    port: int,\n"
    "    *,\n"
    "    launcher_lock: Any,\n"
    "    procesos: Dict[str, Any],\n"
    "    plug_svcs: Dict[str, Any],\n"
    "    task_hb: Dict[str, Dict[str, Any]],\n"
    "    task_sn: set,\n"
    "    shut_ev: Any,\n"
    "    ws_mig: Dict[str, Any],\n"
    "    agents_dir: str,\n"
    "    plugins_dir: str,\n"
    "    skills_dir: str,\n"
    "    role_cards_dir: str,\n"
    "    collab_cards_dir: str,\n"
    "    model_cards_dir: str,\n"
    "    mgmt_port: int,\n"
    "    stall_thresh: int,\n"
    "    syscfg: Any,\n"
    "    logger: Any,\n"
    "    read_json: Any,\n"
    "    chk_port: Any,\n"
    "    res_disc_port: Any,\n"
    "    cln_reg: Any,\n"
    "    appl_def: Any,\n"
    "    val_cfg: Any,\n"
    "    disc_agents: Any,\n"
    "    disc_plug_svcs: Any,\n"
    "    AgentProcess: Type[Any],\n"
    "    PluginServiceProcess: Type[Any],\n"
    ") -> None:\n"
    '    """Start the HTTP management server in a dedicated daemon thread."""\n'
    "    from http.server import ThreadingHTTPServer\n"
    "    import threading\n"
    "\n"
    "    Handler = create_management_handler(\n"
    "        procesos=procesos,\n"
    "        plug_svcs=plug_svcs,\n"
    "        task_hb=task_hb,\n"
    "        task_sn=task_sn,\n"
    "        shut_ev=shut_ev,\n"
    "        ws_mig=ws_mig,\n"
    "        agents_dir=agents_dir,\n"
    "        plugins_dir=plugins_dir,\n"
    "        skills_dir=skills_dir,\n"
    "        role_cards_dir=role_cards_dir,\n"
    "        collab_cards_dir=collab_cards_dir,\n"
    "        model_cards_dir=model_cards_dir,\n"
    "        mgmt_port=mgmt_port,\n"
    "        stall_thresh=stall_thresh,\n"
    "        syscfg=syscfg,\n"
    "        logger=logger,\n"
    "        read_json=read_json,\n"
    "        chk_port=chk_port,\n"
    "        res_disc_port=res_disc_port,\n"
    "        cln_reg=cln_reg,\n"
    "        appl_def=appl_def,\n"
    "        val_cfg=val_cfg,\n"
    "        disc_agents=disc_agents,\n"
    "        disc_plug_svcs=disc_plug_svcs,\n"
    "        AgentProcess=AgentProcess,\n"
    "        PluginServiceProcess=PluginServiceProcess,\n"
    "    )\n"
    "\n"
    '    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)\n'
    "    t = threading.Thread(target=server.serve_forever, daemon=True,\n"
    '                         name="launcher-mgmt-server")\n'
    "    t.start()\n"
    '    logger.info(f"[Launcher] Management server started on port {port}")\n'
)


def main():
    # 1. Load the full _start_management_server function slice
    server_lines = load_server_slice(SRC)

    # 2. Extract just the ManagementHandler block from that server slice
    handler_lines = _find_handler_block_in_server_slice(server_lines)

    # 3. Dedent handler body by 1 level
    handler_dedented = dedent(handler_lines, 1)

    # 4. Filter boilerplate lines
    handler_filtered = filter_handler_lines(handler_dedented)

    # 5. Rewrite global references -> self._ctx.attr
    handler_body = rewrite("".join(handler_filtered))

    # 6. Assemble the file
    src = HEADER + handler_body + FOOTER

    # Ensure output directory exists
    Path(DST).parent.mkdir(parents=True, exist_ok=True)

    with open(DST, "w", encoding="utf-8") as fh:
        fh.write(src)

    line_count = src.count("\n")
    print(f"Wrote {len(src):,} chars ({line_count} lines) -> {DST}")

    # 7. Verify syntax
    try:
        ast.parse(src)
        print("Syntax: OK")
    except SyntaxError as e:
        print(f"SyntaxError at line {e.lineno}: {e.msg}")
        lines = src.splitlines()
        for i in range(max(0, e.lineno - 3), min(len(lines), e.lineno + 2)):
            mark = ">>>" if i + 1 == e.lineno else "   "
            print(f"  {mark} {i + 1:4d}  {lines[i]}")
        sys.exit(1)


if __name__ == "__main__":
    main()
