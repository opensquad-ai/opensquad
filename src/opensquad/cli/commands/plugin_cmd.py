"""opensquad plugin — Manage plugins (install, uninstall, list)."""

import json
import os
import sys

try:
    import httpx
except ImportError:
    httpx = None


def _get_plugins_dir():
    """Return the builtin plugins directory (bundled with opensquad)."""
    _root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    from opensquad.system_config import syscfg

    return os.path.join(syscfg.get_builtin_root(), "plugins")


def _list_installed(plugins_dir):
    """List locally installed plugins from plugins/ directory."""
    result = []
    if not os.path.isdir(plugins_dir):
        return result
    for name in sorted(os.listdir(plugins_dir)):
        pj = os.path.join(plugins_dir, name, "plugin.json")
        if not os.path.isfile(pj):
            continue
        try:
            with open(pj, encoding="utf-8") as f:
                meta = json.load(f)
            display_name = meta.get("display_name") or meta.get("name") or name
            result.append(
                {
                    "id": name,
                    "name": display_name,
                    "version": meta.get("version", "?"),
                    "enabled": meta.get("enabled", True),
                }
            )
        except Exception:
            result.append({"id": name, "name": name, "version": "?", "enabled": True})
    return result


def _trigger_reload(plugins_dir):
    """Write .reload_ts to trigger plugin hot-reload."""
    import time

    try:
        with open(os.path.join(plugins_dir, ".reload_ts"), "w") as f:
            f.write(str(time.time()))
    except Exception:
        pass


def run_plugin(args):
    if not args.plugin_action:
        print("[plugin] Usage: opensquad plugin {install|uninstall|list|enable|disable|config|status}")
        sys.exit(1)

    plugins_dir = _get_plugins_dir()
    action = args.plugin_action

    if action == "list":
        # Prefer live Gateway list when logged in; fall back to local filesystem.
        try:
            from opensquad.cli.api_client import GatewayClient, load_credentials

            if load_credentials().get("token"):
                _cmd_list_api(GatewayClient(gateway_url=getattr(args, "gateway", None)))
                return
        except Exception:
            pass
        _cmd_list(plugins_dir)
    elif action == "install":
        _cmd_install(plugins_dir, args.plugin_id, args.mode)
    elif action == "uninstall":
        _cmd_uninstall(plugins_dir, args.plugin_id)
    elif action in ("enable", "disable", "config", "status"):
        _cmd_api(args)
    else:
        print(f"[plugin] Unknown action: {action}")
        sys.exit(1)


def _cmd_list_api(client):
    from opensquad.cli.api_client import handle_api_error, print_table

    try:
        data = client.admin_get("plugins")
    except Exception as e:
        handle_api_error(e)
        raise
    if isinstance(data, list):
        plugins = data
    elif isinstance(data, dict):
        plugins = data.get("plugins") or []
    else:
        plugins = []
    rows = []
    for p in plugins:
        rows.append(
            {
                "id": p.get("id") or p.get("name") or "",
                "name": p.get("display_name") or p.get("name") or "",
                "version": p.get("version") or "",
                "enabled": "yes" if p.get("enabled", True) else "no",
            }
        )
    print_table(
        rows,
        [("id", "ID"), ("name", "NAME"), ("version", "VER"), ("enabled", "ENABLED")],
    )


def _cmd_api(args):
    from opensquad.cli.api_client import GatewayClient, handle_api_error, print_json

    client = GatewayClient(gateway_url=getattr(args, "gateway", None))
    name = getattr(args, "plugin_id", None) or getattr(args, "name", None)
    try:
        if args.plugin_action == "enable":
            result = client.admin_put(f"plugins/{name}/enable", {})
            print(f"[plugin] enabled: {name}")
            print_json(result)
        elif args.plugin_action == "disable":
            result = client.admin_put(f"plugins/{name}/disable", {})
            print(f"[plugin] disabled: {name}")
            print_json(result)
        elif args.plugin_action == "status":
            data = client.admin_get("plugins")
            plugins = data.get("plugins") if isinstance(data, dict) else data
            if not isinstance(plugins, list):
                plugins = []
            match = next(
                (p for p in plugins if (p.get("id") or p.get("name")) == name),
                None,
            )
            if not match:
                print(f"[plugin] Not found: {name}")
                sys.exit(1)
            print_json(match)
        elif args.plugin_action == "config":
            if getattr(args, "set_json", None):
                with open(args.set_json, encoding="utf-8") as f:
                    body = json.load(f)
                result = client.admin_put(f"plugins/{name}/config", body)
                print(f"[plugin] config updated: {name}")
                print_json(result)
            else:
                data = client.admin_get(f"plugins/{name}/config")
                print_json(data)
    except Exception as e:
        handle_api_error(e)
        print(f"[plugin] {e}")
        sys.exit(1)


def _cmd_list(plugins_dir):
    plugins = _list_installed(plugins_dir)
    if not plugins:
        print("[plugin] No plugins installed.")
        return

    print(f"\n{'ID':<25} {'Name':<25} {'Version':<10} {'Enabled'}")
    print("-" * 70)
    for p in plugins:
        enabled = "Yes" if p["enabled"] else "No"
        print(f"{p['id']:<25} {p['name']:<25} {p['version']:<10} {enabled}")
    print(f"\n[plugin] {len(plugins)} plugin(s) installed.")


def _cmd_install(plugins_dir, plugin_id, mode):
    if httpx is None:
        print("[plugin] Error: httpx is required. Install with: pip install httpx", file=sys.stderr)
        sys.exit(1)

    # Check if it's a Git URL
    if plugin_id.startswith("http://") or plugin_id.startswith("https://") or plugin_id.startswith("git@"):
        _install_from_git(plugins_dir, plugin_id, mode)
    else:
        _install_from_store(plugins_dir, plugin_id, mode)


def _install_from_store(plugins_dir, plugin_id, mode):
    """Install a plugin from the registry."""
    REGISTRY_URL = "https://raw.githubusercontent.com/opensquad-ai/opensquad-plugins/main/index.json"

    print("[plugin] Fetching registry...")
    try:
        resp = httpx.get(REGISTRY_URL, timeout=15)
        resp.raise_for_status()
        all_plugins = resp.json()
    except Exception as e:
        print(f"[plugin] Error: Failed to fetch registry: {e}", file=sys.stderr)
        sys.exit(1)

    plugin_meta = next((p for p in all_plugins if p.get("id") == plugin_id), None)
    if not plugin_meta:
        print(f"[plugin] Error: Plugin '{plugin_id}' not found in registry.", file=sys.stderr)
        sys.exit(1)

    print(f"[plugin] Found: {plugin_meta['name']} v{plugin_meta['version']} by {plugin_meta['author']}")

    download_url = plugin_meta.get("download_url")
    git_url = plugin_meta.get("git_url")

    if not download_url and git_url:
        print(f"[plugin] No download URL, using Git: {git_url}")
        _install_from_git(plugins_dir, git_url, mode, plugin_id=plugin_id)
        return

    if not download_url:
        print("[plugin] Error: Plugin has no download_url and no git_url.", file=sys.stderr)
        sys.exit(1)

    # Download and extract
    import io
    import shutil
    import zipfile

    print(f"[plugin] Downloading from {download_url}...")
    try:
        resp = httpx.get(download_url, timeout=30)
        resp.raise_for_status()
        zip_bytes = resp.content
    except Exception as e:
        print(f"[plugin] Error: Download failed: {e}", file=sys.stderr)
        sys.exit(1)

    plugin_dest = os.path.join(plugins_dir, plugin_id)

    # Preserve existing plugin.py if present
    existing_plugin_py_path = os.path.join(plugin_dest, "plugin.py")
    existing_plugin_py = None
    if os.path.isfile(existing_plugin_py_path):
        with open(existing_plugin_py_path, "rb") as f:
            existing_plugin_py = f.read()

    # Extract
    print(f"[plugin] Extracting to {plugin_dest}...")
    os.makedirs(plugins_dir, exist_ok=True)
    try:
        buf = io.BytesIO(zip_bytes)
        with zipfile.ZipFile(buf) as zf:
            for member in zf.infolist():
                parts = member.filename.split("/")
                relative = "/".join(parts[1:]) if len(parts) > 1 else parts[0]
                if not relative:
                    continue
                dest_path = os.path.join(plugin_dest, relative)
                if not os.path.abspath(dest_path).startswith(os.path.abspath(plugin_dest)):
                    continue
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                if not member.is_dir():
                    with zf.open(member) as src, open(dest_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
    except Exception as e:
        print(f"[plugin] Error: Extraction failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Restore plugin.py
    if existing_plugin_py is not None:
        with open(existing_plugin_py_path, "wb") as f:
            f.write(existing_plugin_py)

    _trigger_reload(plugins_dir)
    print(f"[plugin] Plugin '{plugin_id}' installed successfully.")


def _install_from_git(plugins_dir, git_url, mode, plugin_id=None):
    """Install a plugin from a Git repository."""
    import shutil
    import subprocess
    import tempfile

    if plugin_id is None:
        # Derive plugin_id from URL
        plugin_id = git_url.rstrip("/").split("/")[-1].replace(".git", "")

    print(f"[plugin] Cloning {git_url}...")
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            subprocess.run(["git", "clone", "--depth", "1", git_url, tmpdir], check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            print(f"[plugin] Error: Git clone failed: {e.stderr.decode()}", file=sys.stderr)
            sys.exit(1)
        except FileNotFoundError:
            print("[plugin] Error: Git not found. Install git first.", file=sys.stderr)
            sys.exit(1)

        plugin_dest = os.path.join(plugins_dir, plugin_id)
        os.makedirs(plugins_dir, exist_ok=True)

        # Copy to plugins dir
        if os.path.exists(plugin_dest):
            shutil.rmtree(plugin_dest)
        shutil.copytree(tmpdir, plugin_dest)

    _trigger_reload(plugins_dir)
    print(f"[plugin] Plugin '{plugin_id}' installed from Git successfully.")


def _cmd_uninstall(plugins_dir, plugin_id):
    import shutil

    plugin_dest = os.path.join(plugins_dir, plugin_id)
    if not os.path.isdir(plugin_dest):
        print(f"[plugin] Error: Plugin '{plugin_id}' not found at {plugin_dest}", file=sys.stderr)
        sys.exit(1)

    # Safety check
    if not os.path.abspath(plugin_dest).startswith(os.path.abspath(plugins_dir)):
        print("[plugin] Error: Path traversal detected. Aborting.", file=sys.stderr)
        sys.exit(1)

    print(f"[plugin] Removing {plugin_dest}...")
    shutil.rmtree(plugin_dest, onerror=_remove_readonly)
    _trigger_reload(plugins_dir)
    print(f"[plugin] Plugin '{plugin_id}' uninstalled successfully.")


def _remove_readonly(func, path, exc):
    """Handle read-only files on Windows."""
    import stat

    os.chmod(path, stat.S_IWRITE)
    func(path)
