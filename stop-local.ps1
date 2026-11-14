$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidFile = Join-Path $Root ".alzool-local.pid"
if (-not (Test-Path $PidFile)) {
    Write-Host "No AL-ZOOL local PID file was found."
    exit 0
}
$serverPid = Get-Content $PidFile -ErrorAction SilentlyContinue
if ($serverPid) {
    Stop-Process -Id ([int]$serverPid) -ErrorAction SilentlyContinue
}
Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
Write-Host "AL-ZOOL local server stopped."
