# build_desktop.ps1
# ============================================================
# Full Windows desktop build: backend (PyInstaller) + Electron installer.
#
# Usage (from repo root):
#   powershell -ExecutionPolicy Bypass -File scripts\build_desktop.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\build_desktop.ps1 -SkipElectron
#   powershell -ExecutionPolicy Bypass -File scripts\build_desktop.ps1 -SkipBackend
#
# Outputs:
#   build\backend-win\run\run.exe
#   build\release\OpenSquad-*-win-*-Setup.exe  (or Portable)
# ============================================================

param(
    [switch]$SkipBackend,
    [switch]$SkipElectron,
    [switch]$SkipNpmCi
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

Write-Host "============================================================"
Write-Host " OpenSquad Desktop - full Windows build"
Write-Host " Root: $Root"
Write-Host "============================================================"

if (-not $SkipBackend) {
    Write-Host "`n[1/3] Building backend (scripts\build_backend.bat)..."
    & cmd /c "scripts\build_backend.bat"
    if ($LASTEXITCODE -ne 0) {
        throw "build_backend.bat failed with exit code $LASTEXITCODE"
    }
} else {
    Write-Host "`n[1/3] Skipping backend build (-SkipBackend)"
    & uv run --python 3.11 python scripts/check_backend_bundle.py --bundle (Join-Path $Root "build\backend-win\run")
    if ($LASTEXITCODE -ne 0) {
        throw "Existing backend bundle failed pollution check"
    }
}

$Frontend = Join-Path $Root "src\opensquad\gateway\nexuschat-pro"
if (-not $SkipElectron) {
    Write-Host "`n[2/3] Building Electron installer..."
    Set-Location $Frontend
    if (-not $SkipNpmCi) {
        if (Test-Path "package-lock.json") {
            Write-Host "  npm ci..."
            npm ci
            if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
        } else {
            Write-Host "  npm install (no package-lock)..."
            npm install --no-package-lock
            if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
        }
    }
    $env:CSC_IDENTITY_AUTO_DISCOVERY = "false"
    npm run electron:win
    if ($LASTEXITCODE -ne 0) {
        throw "npm run electron:win failed with exit code $LASTEXITCODE"
    }
} else {
    Write-Host "`n[2/3] Skipping Electron (-SkipElectron)"
}

Set-Location $Root
Write-Host "`n[3/3] Artifacts:"
$backendExe = Join-Path $Root "build\backend-win\run\run.exe"
if (Test-Path $backendExe) {
    Write-Host "  Backend: $backendExe"
} else {
    Write-Host "  Backend: (missing) $backendExe"
}
$releaseDir = Join-Path $Root "build\release"
if (Test-Path $releaseDir) {
    Get-ChildItem $releaseDir -File | ForEach-Object {
        Write-Host ("  Release: {0} ({1:N1} MB)" -f $_.FullName, ($_.Length / 1MB))
    }
} else {
    Write-Host "  Release: (no build\release yet)"
}

Write-Host "`nDone."
