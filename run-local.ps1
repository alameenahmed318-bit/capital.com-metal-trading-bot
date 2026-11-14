$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$EnvFile = Join-Path $Root ".env.local"
if (-not (Test-Path $EnvFile)) {
    Write-Host "Missing .env.local. Copy .env.local.example to .env.local and fill it with your existing Supabase values." -ForegroundColor Yellow
    exit 1
}

Get-Content $EnvFile | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) { return }
    $parts = $line -split "=", 2
    $key = $parts[0].Trim()
    $value = $parts[1].Trim().Trim('"').Trim("'")
    if ($key -match '^[A-Za-z_][A-Za-z0-9_]*$') {
        [Environment]::SetEnvironmentVariable($key, $value, "Process")
    }
}

$required = @("SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY", "OWNER_EMAIL")
$missing = @($required | Where-Object { -not [Environment]::GetEnvironmentVariable($_, "Process") })
if ($missing.Count -gt 0) {
    Write-Host ("Missing required settings in .env.local: " + ($missing -join ", ")) -ForegroundColor Red
    exit 1
}

$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) {
    Write-Host "Python was not found. Install Python 3.11 or newer, then retry." -ForegroundColor Red
    exit 1
}

$Port = if ($env:PORT) { $env:PORT } else { "10000" }
$env:PORT = $Port
$PidFile = Join-Path $Root ".alzool-local.pid"
if (Test-Path $PidFile) {
    $oldPid = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($oldPid -and (Get-Process -Id ([int]$oldPid) -ErrorAction SilentlyContinue)) {
        Write-Host "AL-ZOOL local server already appears to be running (PID $oldPid)." -ForegroundColor Yellow
        Start-Process "http://127.0.0.1:$Port"
        exit 0
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

$server = Start-Process -FilePath $Python.Source -ArgumentList @("marketplace/backend/app.py") -WorkingDirectory $Root -PassThru -WindowStyle Minimized
Set-Content -Path $PidFile -Value $server.Id
$healthy = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 1
    try {
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
        if ($response.ok -eq $true) { $healthy = $true; break }
    } catch {}
    if ($server.HasExited) { break }
}
if ($healthy) {
    Write-Host "AL-ZOOL local server is running. Health check passed." -ForegroundColor Green
    Start-Process "http://127.0.0.1:$Port"
} else {
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    Write-Host "AL-ZOOL did not pass its local health check. Check the server window for the error. No cloud service was changed." -ForegroundColor Red
    exit 1
}
