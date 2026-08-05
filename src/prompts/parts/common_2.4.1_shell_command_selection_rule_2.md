### 2.4.1 Shell Command Selection Rule

- **Short/interactive shell commands** (e.g. `git status`, `pip install`, short Python scripts, `curl`, `ffmpeg` one-shot): use `system.run_session_job`.
- **Long-running/background tasks** (e.g. dev server, watcher, long build/test): use `system.start_job` in non-blocking mode and follow with `system.check_job` polling.
- **CRITICAL — `run_session_job` blocks the shell**: `run_session_job` uses a persistent shell session. If a previous command in that session started a foreground process (e.g. `npm run dev`, `python server.py`), ALL subsequent `run_session_job` calls will be queued behind it and time out. NEVER start a long-running service with `run_session_job` — always use `start_job` instead.
- **Polling discipline**: after `system.start_job`, estimate likely completion time and poll using `system.check_job` after a reasonable delay. Don't poll faster than the job can finish; short jobs need short waits, long jobs need longer waits.
- **Process-kill safety (mandatory)**: never execute global kill commands that terminate all Node.js/Python processes (e.g. `taskkill /IM python.exe`, `taskkill /IM node.exe`, `pkill python`, `pkill node`, `Stop-Process -Name python/node`).
- Only perform **targeted** termination by explicit PID/port/specific service.
- If a global cleanup seems necessary, you must first ask the user and obtain explicit approval.
- Never use blocking mode for long-running services.
