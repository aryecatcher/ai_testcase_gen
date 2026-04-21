$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Port = if ($env:PORT) { $env:PORT } else { '8504' }
$Streamlit = Join-Path $Root '.venv\Scripts\streamlit.exe'

& $Streamlit run ui/main.py --server.port $Port
