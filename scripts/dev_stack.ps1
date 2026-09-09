# dev_stack.ps1
# ============================================================
# Keep a single local OpenSquad stack for development.
#
# - Kills duplicate gateway/launcher processes fighting for 9555/9600
# - Verifies `import opensquad` resolves to this repo's src/opensquad
# - Optionally starts the stack via `opensquad start` (uv tool / PATH)
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\dev_stack.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\dev_stack.ps1 -Start
#   powershell -ExecutionPolicy Bypass -File scripts\dev_stack.ps1 -Workspace C:\ai_work\pro0\opensquad_runtime_deploy
#
# After editing builtin tools (im / bridge / collaboration), restart agents:
#   POST http://127.0.0.1:9600/api/agents/{name}/restart
# Plugin hot-reload alone does not always reload already-imported modules.
# ============================================================

param(
    [switch]$Start,
    [string]$Workspace = "",
    [int]$GatewayPort = 9555,
    [int]$LauncherPort = 9600
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$ExpectedOpensquad = (Join-Path $Root "src\opensquad").ToLowerInvariant()

function Get-Listeners([int]$Port) {
    # Returns PIDs listening on 0.0.0.0:Port or 127.0.0.1:Port
    $lines = netstat -ano | Select-String -Pattern ":$Port\s+.*LISTENING"
    $pids = @()
    foreach ($line in $lines) {
        if ($line -match "\s+(\d+)\s*$") { $pids += [int]$Matches[1] }
    }
    return $pids | Select-Object -Unique
}

function Stop-PidSafe([int]$ProcId) {
    if ($ProcId -le 0 -or $ProcId -eq $PID) { return }
    try {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcId" -ErrorAction SilentlyContinue
        if (-not $p) { return }
        $cmd = [string]$p.CommandLine
        $name = [string]$p.Name
        # Only touch OpenSquad-related processes (avoid killing unrelated listeners)
        $looksLikeOurs = ($cmd -match "opensquad|launcher_main|gateway\\backend\\run|agents_boot|run\.exe") `
            -or ($name -match "run\.exe")
        if (-not $looksLikeOurs) {
            Write-Host "[dev_stack] leave PID $ProcId ($name) — not an OpenSquad command line"
            return
        }
        Write-Host "[dev_stack] stopping PID $ProcId :: $name"
        Write-Host "           $cmd"
        Stop-Process -Id $ProcId -Force -ErrorAction SilentlyContinue
    } catch {
        Write-Host "[dev_stack] could not stop PID $ProcId: $_"
    }
}

Write-Host "[dev_stack] repo root: $Root"

# 1) Deduplicate listeners on gateway/launcher ports
foreach ($port in @($GatewayPort, $LauncherPort)) {
    $pids = @(Get-Listeners $port)
    if ($pids.Count -eq 0) {
        Write-Host "[dev_stack] port $port: free"
        continue
    }
    Write-Host "[dev_stack] port $port listeners: $($pids -join ', ')"
    # Keep the first OpenSquad listener; kill later duplicates if multiple ours
    $ours = @()
    foreach ($procId in $pids) {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
        if (-not $p) { continue }
        $cmd = [string]$p.CommandLine
        if ($cmd -match "opensquad|launcher_main|gateway\\backend\\run|agents_boot|run\.exe") {
            $ours += $procId
        }
    }
    if ($ours.Count -gt 1) {
        Write-Host "[dev_stack] multiple OpenSquad listeners on $port — keeping $($ours[0]), stopping the rest"
        for ($i = 1; $i -lt $ours.Count; $i++) { Stop-PidSafe $ours[$i] }
    } elseif ($ours.Count -eq 0 -and $pids.Count -gt 0) {
        Write-Host "[dev_stack] WARNING: port $port held by non-OpenSquad process(es): $($pids -join ', ')"
    }
}

# 2) Verify opensquad import path
$probe = @"
import opensquad, pathlib
p = pathlib.Path(opensquad.__file__).resolve().parent
print(p)
"@
$importPath = (& python -c $probe 2>$null | Select-Object -Last 1)
if (-not $importPath) {
    # Fall back to uv tool / project
    $importPath = (& uv run --python 3.11 python -c $probe 2>$null | Select-Object -Last 1)
}
if (-not $importPath) {
    Write-Host "[dev_stack] ERROR: cannot import opensquad"
    exit 1
}
$normalized = $importPath.Trim().ToLowerInvariant()
Write-Host "[dev_stack] opensquad.__file__ parent: $importPath"
if ($normalized -ne $ExpectedOpensquad) {
    Write-Host "[dev_stack] WARNING: expected editable package at:"
    Write-Host "             $ExpectedOpensquad"
    Write-Host "           Fix with:  uv tool install -e `"$Root`"   or   pip install -e `"$Root`""
} else {
    Write-Host "[dev_stack] OK: editable src/opensquad"
}

# 3) Optional start
if ($Start) {
    if ($Workspace) {
        $env:OPENSQUAD_WORKSPACE = (Resolve-Path $Workspace).Path
        $env:OPENSQUAD_USER_DATA = $env:OPENSQUAD_WORKSPACE
        Write-Host "[dev_stack] OPENSQUAD_WORKSPACE=$($env:OPENSQUAD_WORKSPACE)"
    }
    Write-Host "[dev_stack] starting: opensquad start"
    Set-Location $Root
    & opensquad start
} else {
    Write-Host "[dev_stack] dry-run complete. Pass -Start to launch ``opensquad start``."
    Write-Host "[dev_stack] After builtin tool edits, restart agents via launcher API."
}
