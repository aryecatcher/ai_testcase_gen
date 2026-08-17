$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$HostAddress = if ($env:HOST) { $env:HOST } else { '127.0.0.1' }
$Port = if ($env:PORT) { $env:PORT } else { '8002' }
$Python = Join-Path $Root '.venv\Scripts\python.exe'

& $Python -m uvicorn src.api.main:app --host $HostAddress --port $Port
