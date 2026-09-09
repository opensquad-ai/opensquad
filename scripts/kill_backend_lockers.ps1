# kill_backend_lockers.ps1
# ============================================================
# 在构建前终止所有持有已打包后端构建产物锁的残留进程。
#
# 背景：PyInstaller 的 --clean / COLLECT 阶段需要删除 build\backend-win\run\
# 里的 run.exe 与 _internal\*.dll/.pyd。如果旧版 launcher/gateway（run.exe）
# 还活着，或某个插件服务子进程（如 external_api，用 runtime python 从
# _internal 加载模块）仍存活，这些文件会被占用，删除时报
# “PermissionError: [WinError 5] 拒绝访问”，导致构建失败。
#
# 本脚本按三条路径探测并强制终止，随后最多等待 10s 等句柄释放：
#   1) 可执行文件路径位于构建目录下（run.exe 等）
#   2) 命令行引用了构建目录下的文件（插件服务子进程，脚本从 _internal 运行）
#   3) 已加载的模块文件位于构建目录下（防御未知宿主进程）
# ============================================================

param(
    # 默认指向 scripts\..\build\backend-win
    [string]$BuildRoot = (Join-Path $PSScriptRoot "..\build\backend-win")
)

$ErrorActionPreference = 'SilentlyContinue'
$root = [System.IO.Path]::GetFullPath($BuildRoot)

if (-not (Test-Path $root)) {
    Write-Host "[kill-backend-lockers] no existing build at $root (nothing to clean)."
    exit 0
}

Write-Host "[kill-backend-lockers] scanning processes under: $root"
$victims = [ordered]@{}

# 1) + 2) 一次性取全部进程，按 可执行路径 / 命令行 命中构建目录 判定
$all = Get-CimInstance Win32_Process
foreach ($line in $all) {
    $exe = [string]$line.ExecutablePath
    $cmd = [string]$line.CommandLine
    $under = $false
    if ($exe -and $exe.IndexOf($root, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) { $under = $true }
    if (-not $under -and $cmd -and $cmd.IndexOf($root, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) { $under = $true }
    if ($under) { $victims["$($line.ProcessId)"] = "$($line.Name)" }
}

# 3) 兜底：加载了构建目录内模块的进程（宿主进程可能不在该目录下）
foreach ($p in Get-Process -ErrorAction SilentlyContinue) {
    if ($victims.Contains("$($p.Id)")) { continue }
    try {
        $held = $p.Modules | Where-Object { $_.FileName -and $_.FileName.IndexOf($root, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 } | Select-Object -First 1
        if ($held) { $victims["$($p.Id)"] = "$($p.ProcessName)" }
    } catch {}
}

# 永远不要杀掉承载本脚本的进程
$victims.Remove("$($PID)")

if ($victims.Count -eq 0) {
    Write-Host "[kill-backend-lockers] no locking processes found."
    exit 0
}

foreach ($kv in $victims.GetEnumerator()) {
    Write-Host ("[kill-backend-lockers] stopping PID {0} ({1})" -f $kv.Key, $kv.Value)
    Stop-Process -Id ([int]$kv.Key) -Force
}

# Windows 结束进程后句柄释放会有延迟，最多等待 10s
$deadline = (Get-Date).AddSeconds(10)
while ((Get-Date) -lt $deadline) {
    $still = @(foreach ($k in $victims.Keys) { if (Get-Process -Id ([int]$k) -ErrorAction SilentlyContinue) { $k } })
    if ($still.Count -eq 0) {
        Write-Host "[kill-backend-lockers] all lockers exited."
        exit 0
    }
    Start-Sleep -Milliseconds 500
}
Write-Host ("[kill-backend-lockers] WARNING: still terminating: {0}" -f ($still -join ', '))
exit 0
