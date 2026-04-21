$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Test-Path '.venv')) {
  python -m venv .venv
}

$Python = Join-Path $Root '.venv\Scripts\python.exe'
& $Python -m pip install --upgrade pip
& $Python -m pip install -r requirements.txt

Set-Location (Join-Path $Root 'frontend')
npm install

Write-Host 'Bootstrap complete.'
