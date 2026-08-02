param(
    [string[]]$Urls = @(
        "http://127.0.0.1:9555/health",
        "http://127.0.0.1:9720/health",
        "http://127.0.0.1:9600/api/ping"
    ),
    [int]$TimeoutSeconds = 90
)

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$ready = @{}
foreach ($url in $Urls) {
    $ready[$url] = $false
}

while ((Get-Date) -lt $deadline) {
    $allReady = $true
    foreach ($url in $Urls) {
        if ($ready[$url]) {
            continue
        }
        try {
            $resp = Invoke-RestMethod -Uri $url -TimeoutSec 2 -ErrorAction Stop
            $isOk = $false
            if ($url -like "*9555/health*") {
                $isOk = [bool]$resp.ready
            } elseif ($url -like "*9600*") {
                $isOk = ($resp.status -eq "ok")
            } else {
                $isOk = $true
            }
            if ($isOk) {
                $ready[$url] = $true
                Write-Host "[OK] Ready: $url"
            }
        } catch {
            # Not ready yet; keep polling.
        }
        if (-not $ready[$url]) {
            $allReady = $false
        }
    }
    if ($allReady) {
        exit 0
    }
    Start-Sleep -Milliseconds 500
}

$notReady = @($Urls | Where-Object { -not $ready[$_] })
Write-Host "[FAIL] Timeout after $TimeoutSeconds seconds waiting for: $($notReady -join ', ')" -ForegroundColor Red
exit 1
