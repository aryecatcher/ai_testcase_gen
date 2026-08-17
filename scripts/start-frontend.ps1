$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $Root 'frontend')

$env:VITE_BACKEND_URL = if ($env:VITE_BACKEND_URL) { $env:VITE_BACKEND_URL } else { 'http://127.0.0.1:8002' }
$HostAddress = if ($env:HOST) { $env:HOST } else { '127.0.0.1' }
$Port = if ($env:PORT) { $env:PORT } else { '5173' }

npm run dev -- --host $HostAddress --port $Port
