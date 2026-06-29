import json
import os
import sys
import time
import platform
import shutil

CONFIG_FILE = "system_config.json"

def find_config_path() -> str:
    """
    按优先级查找 system_config.json：
    1. 当前目录（兼容旧用法）
    2. ~/.opensquad/last_workspace.json 指向的工作区
    3. 找不到则报错退出
    """
    # 1. 当前目录
    if os.path.exists(CONFIG_FILE):
        return os.path.abspath(CONFIG_FILE)

    # 2. 从上次使用的工作区查找
    try:
        import pathlib
        last_ws_file = pathlib.Path.home() / ".opensquad" / "last_workspace.json"
        if last_ws_file.exists():
            data = json.loads(last_ws_file.read_text(encoding="utf-8"))
            ws_path = data.get("last_workspace", "")
            if ws_path:
                candidate = os.path.join(ws_path, CONFIG_FILE)
                if os.path.exists(candidate):
                    print(f"[*] Config loaded from workspace: {ws_path}")
                    return os.path.abspath(candidate)
    except Exception as e:
        print(f"[!] Warning: failed to read last workspace: {e}")

    print(f"Error: {CONFIG_FILE} not found in current directory or last workspace.")
    print("       Run from your workspace directory, or switch workspace in the Web UI first.")
    sys.exit(1)

def load_config():
    config_path = find_config_path()
    # 切换到配置文件所在目录，使配置中的相对路径正确解析
    config_dir = os.path.dirname(config_path)
    if config_dir and config_dir != os.getcwd():
        # 仅切换以便路径解析，服务脚本路径相对于安装目录——保存安装目录供后续使用
        pass  # 不 chdir，config_dir 仅用于读取文件
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error reading config: {e}")
        sys.exit(1)

def run_command_in_new_window(cmd, title):
    current_os = platform.system()
    
    if current_os == "Windows":
        # Windows: start "Title" cmd
        os.system(f'start "{title}" {cmd}')
        
    elif current_os == "Darwin": # macOS
        os.system(f"""osascript -e 'tell application "Terminal" to do script "{cmd}"'""")
        
    else: # Linux
        try:
            os.system(f'gnome-terminal --title="{title}" -- {cmd}')
        except:
            print(f"    (Linux GUI terminal not detected, running in background: {title})")
            import subprocess
            subprocess.Popen(cmd, shell=True)

def main():
    print("==================================================")
    print("   OpenSquad Multi-Agent Launcher (v3)             ")
    print("==================================================")

    config = load_config()
    python_exe = sys.executable
    
    # 1. Start Infrastructure Services
    services = config.get("services", {})
    
    print("\n--- Starting Infrastructure Services ---")
    for svc_id, svc_info in services.items():
        if not svc_info.get("enabled", False):
            continue
            
        print(f"[*] Launching Service: {svc_info['name']}...")
        
        svc_type = svc_info.get("type", "python")
        
        if svc_type == "python":
            script_path = svc_info['path']
            if not os.path.exists(script_path):
                print(f"    [ERROR] Script not found: {script_path}")
                continue
            work_dir = os.path.dirname(script_path)
            # Fix: if work_dir is empty (script is in root), use current dir "."
            if not work_dir:
                work_dir = "."
            script_name = os.path.basename(script_path)
            
            # Support additional arguments
            extra_args = svc_info.get("args", [])
            args_str = " ".join(extra_args) if extra_args else ""
            
            if platform.system() == "Windows":
                 # Windows cmd /k quote handling needs care.
                 # Structure: cmd /k "chcp 65001 >nul && cd /d "PATH" && "PYTHON" "SCRIPT" [args]"
                 # chcp 65001 sets UTF-8 code page to prevent Chinese character garbling.
                 if args_str:
                     cmd = f'cmd /k "chcp 65001 >nul && cd /d "{work_dir}" && "{python_exe}" "{script_name}" {args_str}"'
                 else:
                     cmd = f'cmd /k "chcp 65001 >nul && cd /d "{work_dir}" && "{python_exe}" "{script_name}""'
            else:
                 if args_str:
                     cmd = f'bash -c "cd {work_dir} && "{python_exe}" {script_name} {args_str}; exec bash"'
                 else:
                     cmd = f'bash -c "cd {work_dir} && "{python_exe}" {script_name}; exec bash"'

        elif svc_type == "python_module":
            # Runs: python -m <module> [args]  from project root
            module = svc_info.get("module", "")
            if not module:
                print(f"    [ERROR] 'module' field missing for service: {svc_id}")
                continue
            extra_args = svc_info.get("args", [])
            args_str = " ".join(extra_args) if extra_args else ""
            root_dir = os.getcwd()
            if platform.system() == "Windows":
                if args_str:
                    cmd = f'cmd /k "chcp 65001 >nul && cd /d "{root_dir}" && "{python_exe}" -m {module} {args_str}"'
                else:
                    cmd = f'cmd /k "chcp 65001 >nul && cd /d "{root_dir}" && "{python_exe}" -m {module}"'
            else:
                if args_str:
                    cmd = f'bash -c "cd {root_dir} && {python_exe} -m {module} {args_str}; exec bash"'
                else:
                    cmd = f'bash -c "cd {root_dir} && {python_exe} -m {module}; exec bash"'

        elif svc_type == "exe":
            # Runs a native executable (e.g. NapCat) from a specified cwd.
            # Windows-only; skipped on other platforms.
            if platform.system() != "Windows":
                print(f"    [SKIP] type=exe is Windows-only, skipping: {svc_info['name']}")
                continue
            exe_path = svc_info.get("exe", "")
            work_dir = svc_info.get("cwd", ".")
            if not os.path.exists(work_dir):
                print(f"    [ERROR] Directory not found: {work_dir}")
                continue
            # Use start "" to launch detached (no /wait), same as start_napcat.bat
            cmd = f'cmd /k "chcp 65001 >nul && cd /d "{work_dir}" && start \\"\\" \\"{exe_path}\\""'

        elif svc_type == "shell":
            work_dir = svc_info['cwd']
            shell_cmd = svc_info['cmd']
            
            if not os.path.exists(work_dir):
                print(f"    [ERROR] Directory not found: {work_dir}")
                continue
                
            # Check if npm exists for frontend
            if "npm" in shell_cmd and not shutil.which("npm"):
                 print(f"    [WARNING] 'npm' not found in PATH. Skipping frontend start.")
                 continue

            if platform.system() == "Windows":
                 # Structure: cmd /k "chcp 65001 >nul && cd /d "PATH" && COMMAND"
                 cmd = f'cmd /k "chcp 65001 >nul && cd /d "{work_dir}" && {shell_cmd}"'
            else:
                 cmd = f'bash -c "cd {work_dir} && {shell_cmd}; exec bash"'

        else:
            print(f"    [ERROR] Unknown service type '{svc_type}' for: {svc_id}")
            continue

        run_command_in_new_window(cmd, f"OpenSquad Service - {svc_info['name']}")
        delay = svc_info.get("start_delay", 2)
        time.sleep(delay)

    # 2. Agent startup
    # In V3 architecture, launcher.py reads auto_start from system_config.json
    # and manages all agent processes. No manual agent startup needed here.
    launcher_enabled = config.get("services", {}).get("launcher", {}).get("enabled", False)
    auto_start_list = config.get("auto_start", [])

    if launcher_enabled:
        print("\n--- Agent Startup Handled by Launcher ---")
        print(f"  Launcher will auto-start agents from config: {', '.join(auto_start_list) if auto_start_list else '(all discovered)'}")
    else:
        print("\n--- WARNING: Launcher is disabled, no agents will be started ---")
        print("  Enable launcher in services or start agents manually.")


    print("-" * 50)
    print("[OK] System startup sequence initiated.")
    print("==================================================")

if __name__ == "__main__":
    main()
