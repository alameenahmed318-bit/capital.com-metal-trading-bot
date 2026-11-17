# AL-ZOOL safe local readiness check (Windows PowerShell 5.1+).
# Does NOT enable trading or transmit secrets to a chat or log.
param(
  [string]$CredentialsPath = "",
  [switch]$InstallMissingModels,
  [switch]$StartRelay
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$relay = Join-Path $PSScriptRoot "ALZOOL_Local_Model_Relay.py"
$ea = Join-Path $PSScriptRoot "ALZOOL_Central_Client.mq5"
$base = "https://al-zool-central-engine.onrender.com"
$fail = 0
function Check($label,[bool]$good,$detail){
  $mark = if($good){"PASS"}else{"BLOCK"}
  $color = if($good){"Green"}else{"Yellow"}
  Write-Host ("[$mark] " + $label + " -- " + $detail) -ForegroundColor $color
  if(!$good){$script:fail++}
}
Write-Host "=== AL-ZOOL OWNER READINESS (SAFE MODE) ==="
Write-Host "All trading switches remain unchanged. No order is sent."
try {
  $response = Invoke-RestMethod "$base/health" -TimeoutSec 12
  Check "Render central engine" ($response.status -eq "UP") "Service responds"
}catch{
  Check "Render central engine" $false "Cannot reach /health"
}
Check "Central EA source" (Test-Path $ea) "MT5 EA source present"
Check "Qwen + DeepSeek relay" (Test-Path $relay) "Local relay script present"
$py = Get-Command python -ErrorAction SilentlyContinue
Check "Python" ($null -ne $py) "Python must be on PATH"
$mt = Get-Process terminal64 -ErrorAction SilentlyContinue
Check "MT5 terminal running" ($null -ne $mt) "Open MT5 and the XAUUSD M1 chart"
$models=@("qwen2.5:3b","deepseek-r1:1.5b")
try{
  $tags=(Invoke-RestMethod "http://127.0.0.1:11434/api/tags" -TimeoutSec 5).models
  $present=@($tags | ForEach-Object { $_.name })
  foreach($model in $models){
    $found=(@($present) -contains $model)
    if(!$found -and $InstallMissingModels){
      Write-Host ("Installing " + $model + " using Ollama...")
      & ollama pull $model
      $found=($LASTEXITCODE -eq 0)
    }
    Check ("Ollama model " + $model) $found $(if($found){"Installed"}else{"Missing; run: ollama pull " + $model})
  }
}catch{
  Check "Ollama local service" $false "Start Ollama; check port 11434"
}
$pairReady=$false
if($CredentialsPath -and (Test-Path -LiteralPath $CredentialsPath -PathType Leaf)){
 try{
  $pair=Get-Content -LiteralPath $CredentialsPath -Raw -Encoding UTF8 | ConvertFrom-Json
  $pairReady=($pair.InpOwnerMode -eq $true -and $pair.InpOwnerConnectionId -and
      $pair.InpOwnerPairingKey -and $pair.InpOwnerPairingKey.Length -ge 32)
  Check "Owner device pairing" $pairReady "Valid local JSON structure (secret hidden)"
  if($pairReady){
    try {
      $headers=@{ Authorization = "Bearer $($pair.InpOwnerPairingKey)" }
      $identity=[uri]::EscapeDataString([string]$pair.InpOwnerConnectionId)
      $context=Invoke-RestMethod "$base/v1/local-ai/context/$identity" -Headers $headers -TimeoutSec 15
      Check "Terminal-to-central analysis" ($null -ne $context.bar_time -and [long]$context.bar_time -gt 0) "Authenticated central analysis"
    }catch{
      # HTTP 409 means pairing worked but live market telemetry is not available.
      $code=""
      try{$code=[int]$_.Exception.Response.StatusCode}catch{}
      Check "Terminal-to-central analysis" $false ("No fresh terminal analysis; HTTP " + $code)
    }
  }
 }catch{ Check "Owner device pairing" $false "Could not parse JSON file" }
}else{
 Check "Owner device pairing" $false "Download owner pairing JSON from the AL-ZOOL owner dashboard"
}
if($ea -and (Test-Path $ea)){
 $source=Get-Content $ea -Raw
 Check "EA safe defaults" ($source.Contains("InpEnableCentralTrading = false") -and
      $source.Contains("InpEnableCentralPositionManagement = false")) "New entries default to OFF"
}
Write-Host ""
Write-Host ("CHECK COMPLETE: " + $fail + " unresolved checks.")
if($StartRelay){
 if(!$pairReady -or !$py -or !(Test-Path $relay)){
   Write-Host "Relay not started: pairing, Python or script missing." -ForegroundColor Yellow
   exit 2
 }
 Write-Host "Starting both local AI models. No broker orders will be placed by this relay."
 & $py.Source $relay --credentials $CredentialsPath
 exit $LASTEXITCODE
}
if($fail -gt 0){exit 2}
exit 0
