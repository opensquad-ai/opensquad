import os
import subprocess
import asyncio
import logging
import datetime
from typing import Dict
from opensquad.system_config import syscfg

logger = logging.getLogger("plugin_builder")

# On Windows, npm/node are .cmd scripts and require shell=True (or explicit .cmd suffix).
_IS_WINDOWS = os.name == "nt"


def _run_cmd(cmd: list[str], cwd: str, log_file) -> int:
    """
    Run a command synchronously, streaming output line-by-line to log_file.
    Uses shell=True on Windows so that .cmd scripts (npm.cmd, node.cmd) are found.
    Returns the exit code.
    """
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=_IS_WINDOWS,
        )
        for line in proc.stdout:
            log_file.write(line)
            log_file.flush()
        proc.wait()
        return proc.returncode
    except FileNotFoundError:
        log_file.write(f"[ERROR] Command not found: {cmd[0]}\n")
        return 1
    except Exception as e:
        log_file.write(f"[ERROR] Failed to run {cmd}: {e}\n")
        return 1


class PluginBuilder:
    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root
        self.active_builds: Dict[str, str] = {}  # plugin_id -> status

    def get_log_path(self, plugin_id: str) -> str:
        log_dir = os.path.join(self.workspace_root, "data", "logs", "builds")
        os.makedirs(log_dir, exist_ok=True)
        return os.path.join(log_dir, f"{plugin_id}.log")

    async def check_env(self) -> dict:
        """Check if node and npm are available."""
        def _check():
            results = {}
            for tool in ["node", "pnpm"]:
                try:
                    r = subprocess.run(
                        [tool, "--version"],
                        capture_output=True, text=True,
                        shell=_IS_WINDOWS, timeout=10
                    )
                    results[tool] = r.returncode == 0
                    results[f"{tool}_version"] = r.stdout.strip() if r.returncode == 0 else None
                except Exception as e:
                    results[tool] = False
                    results[f"{tool}_version"] = None
                    results["error"] = str(e)
            # keep 'npm' key for backwards compat with frontend that checks env.npm
            results["npm"] = results.get("pnpm", False)
            results["npm_version"] = results.get("pnpm_version")
            return results

        return await asyncio.to_thread(_check)

    async def run_build_task(self, plugin_id: str):
        """Internal build task runner (runs in a thread to avoid SelectorEventLoop limits)."""
        plugin_ui_dir = os.path.join(self.workspace_root, "plugins", plugin_id, "ui")
        log_path = self.get_log_path(plugin_id)

        if not os.path.exists(plugin_ui_dir):
            self.active_builds[plugin_id] = "error: ui_dir_not_found"
            return

        self.active_builds[plugin_id] = "building"

        def _build():
            try:
                with open(log_path, "w", encoding="utf-8") as log_file:
                    ts = datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S")
                    log_file.write(f"--- OpenSquad Plugin Build: {plugin_id} ---\n")
                    log_file.write(f"Timestamp: {ts}\n\n")

                    # Step 1: pnpm install (uses global content-addressable store,
                    # packages are hard-linked so each plugin shares the same on-disk copy)
                    log_file.write("[1/2] pnpm install\n")
                    log_file.flush()
                    rc = _run_cmd(["pnpm", "install", "--no-frozen-lockfile"], plugin_ui_dir, log_file)
                    if rc != 0:
                        log_file.write(f"\n[FAIL] pnpm install exited with code {rc}\n")
                        return "error"

                    # Step 2: pnpm run build
                    log_file.write("\n[2/2] pnpm run build\n")
                    log_file.flush()
                    rc = _run_cmd(["pnpm", "run", "build"], plugin_ui_dir, log_file)
                    if rc == 0:
                        log_file.write("\n[DONE] Plugin built successfully!\n")
                        return "success"
                    else:
                        log_file.write(f"\n[FAIL] npm run build exited with code {rc}\n")
                        return "error"

            except Exception as e:
                logger.error(f"Build task crashed for {plugin_id}: {e}")
                try:
                    with open(log_path, "a", encoding="utf-8") as lf:
                        lf.write(f"\n[CRITICAL] Internal error: {type(e).__name__}: {e}\n")
                except Exception:
                    pass
                return f"error: {e}"

        status = await asyncio.to_thread(_build)
        self.active_builds[plugin_id] = status

    def start_build(self, plugin_id: str):
        """Fire and forget build task."""
        asyncio.create_task(self.run_build_task(plugin_id))
        return {"status": "started", "log_path": self.get_log_path(plugin_id)}


# Singleton
builder = PluginBuilder(syscfg.project_root())
