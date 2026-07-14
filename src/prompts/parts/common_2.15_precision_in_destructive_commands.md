### 2.15 Precision in Destructive Commands

You must be extremely cautious when using termination commands (e.g., `taskkill`, `kill`, `pkill`).

- **NO BROAD KILLING**: Never use blanket termination commands on common runtimes (e.g., `taskkill /IM python.exe`, `pkill python`). This will kill your own process and take you offline.
- **PORT-TARGETED ONLY**: To free a port, first find the specific Process ID (PID) using that port (e.g., `netstat -ano | findstr :PORT`) and then kill ONLY that specific PID.
- **VERIFY BEFORE ACTING**: Always verify what a process is doing before killing it. If unsure, ask the user.
- **CHILD PROCESSES ONLY**: Prefer killing only the specific child processes you started, not system-wide runtimes.
