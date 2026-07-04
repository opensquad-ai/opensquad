# Build all plugin UI bundles (ui/index.js) for plugins that ship a web view.
#
# Each src/plugins/<name>/ui/ directory is expected to contain:
#   - package.json with a "build" script (typically tsc --noEmit + esbuild)
#   - src/index.tsx (entry)
# Output: ui/index.js (ESM bundle exporting mount()/unmount())
#
# Used by:
#   - scripts/build_backend.bat (before PyInstaller, so index.js is bundled)
#   - CI workflow (so frozen releases ship plugin visualizations)
#
# Why a dedicated script: each plugin's ui/index.js is gitignored (build
# artifact), so a fresh clone or CI run has no index.js until this runs.
# Without it, the front-end falls back to GenericPluginView (raw JSON tree)
# and the user sees "just a database" instead of charts for Token Analytics,
# Quick Note, Task Watch, Email Assistant, etc.

$ErrorActionPreference = "Stop"

$ROOT = Split-Path -Parent $PSScriptRoot
$PLUGINS_DIR = Join-Path $ROOT "src\plugins"

$uiDirs = Get-ChildItem -Path $PLUGINS_DIR -Directory | Where-Object {
    Test-Path (Join-Path $_.FullName "ui\package.json")
}

if (-not $uiDirs) {
    Write-Host "[build_plugin_ui] No plugin UI directories found."
    exit 0
}

Write-Host "[build_plugin_ui] Found $($uiDirs.Count) plugin UI(s) to build:"
foreach ($d in $uiDirs) { Write-Host "  - $($d.Name)" }
Write-Host ""

foreach ($dir in $uiDirs) {
    $pluginName = $dir.Name
    $uiDir = Join-Path $dir.FullName "ui"
    Write-Host "[build_plugin_ui] Building $pluginName ..."
    Push-Location $uiDir
    try {
        # Prefer pnpm if available (matches lockfile), fall back to npm.
        $pkgManager = if (Get-Command pnpm -ErrorAction SilentlyContinue) { "pnpm" } else { "npm" }
        Write-Host "  using $pkgManager"
        # Install deps if node_modules missing (CI / fresh clone)
        if (-not (Test-Path "node_modules")) {
            & $pkgManager install --silent 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "$pkgManager install failed for $pluginName" }
        }
        & $pkgManager run build
        if ($LASTEXITCODE -ne 0) { throw "build failed for $pluginName" }
        $bundle = Join-Path $uiDir "index.js"
        if (-not (Test-Path $bundle)) { throw "index.js not produced for $pluginName" }
        $size = (Get-Item $bundle).Length
        Write-Host "  OK: index.js ($([math]::Round($size / 1KB, 1)) KB)"
    } finally {
        Pop-Location
    }
}

Write-Host ""
Write-Host "[build_plugin_ui] All plugin UIs built successfully."
