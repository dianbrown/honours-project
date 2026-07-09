[CmdletBinding()]
param(
    [string]$BindHost = "0.0.0.0",
    [int]$Port = 8080,
    [string]$Uri = "tmr:///COM3",
    [int]$Antenna = 1,
    [int]$ReadPower = 1900,
    [string]$ReadAsyncPath = ""
)

$ErrorActionPreference = "Stop"

if (-not $ReadAsyncPath) {
    $candidates = @(
        "c\projVS2019\Samples\ReadAsync-Release\ReadAsync.exe",
        "c\proj\Samples\ReadAsync-Release\ReadAsync.exe",
        "c\src\api\readasync.exe"
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) {
            $ReadAsyncPath = (Resolve-Path $p).Path
            break
        }
    }
}

if (-not $ReadAsyncPath) {
    throw "readasync executable not found. Build it first with run-hecto-live.cmd."
}

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pthreadDir = Join-Path $root "c\src\arch\win32\lib"
if (Test-Path $pthreadDir) {
    $env:PATH = "$pthreadDir;$env:PATH"
}
$readAsyncDir = Split-Path -Parent $ReadAsyncPath
if (Test-Path $readAsyncDir) {
    $env:PATH = "$readAsyncDir;$env:PATH"
}

python -c "import flask" | Out-Null

Write-Host "Starting kiosk server..."
Write-Host "URL: http://localhost:$Port"
Write-Host "URI: $Uri"
Write-Host "Antenna: $Antenna"
Write-Host "Read power: $ReadPower cdBm"
Write-Host "readasync: $ReadAsyncPath"
Write-Host "Press Ctrl+C to stop.`n"

python attendance_kiosk.py `
  --host $BindHost `
  --port $Port `
  --uri $Uri `
  --antenna $Antenna `
  --read-power $ReadPower `
  --readasync-bin $ReadAsyncPath
