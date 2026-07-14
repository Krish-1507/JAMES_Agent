# JAMES — Windows installer. Usage (PowerShell):
#   irm https://raw.githubusercontent.com/Krish-1507/Voice-Automated-Desktop-Agent-J.A.M.E.S/main/install.ps1 | iex
# Optional browser automation:  powershell -ExecutionPolicy Bypass -File install.ps1 -WithBrowser
param(
    [switch]$WithBrowser
)

$ErrorActionPreference = "Stop"
Write-Host "🤖 JAMES — Windows installer" -ForegroundColor Cyan

$py = "python"
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    if (-not (Get-Command python3 -ErrorAction SilentlyContinue)) {
        Write-Host "❌ Python 3.10+ is required. Install it, then re-run." -ForegroundColor Red
        exit 1
    }
    $py = "python3"
}

$ver = & $py -c "import sys;print('%d.%d'%sys.version_info[:2])"
Write-Host "• Using $py ($ver)"

if (-not (Test-Path .venv)) {
    & $py -m venv .venv
}
$venvPy = Resolve-Path .venv\Scripts\python.exe

& $venvPy -m pip install -U pip | Out-Null
& $venvPy -m pip install -e . | Out-Null
Write-Host "✓ JAMES installed" -ForegroundColor Green

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "✓ Created .env — edit it with your API keys / model" -ForegroundColor Green
}

& $venvPy -m james --check

if ($WithBrowser) {
    & $venvPy -m pip install playwright | Out-Null
    & $venvPy -m playwright install chromium
    Write-Host "✓ Browser automation ready" -ForegroundColor Green
}

Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. edit .env  (set LLM_PROVIDER + your API key, or LLM_PROVIDER=custom for Ollama)"
Write-Host "  2. .venv\Scripts\Activate.ps1"
Write-Host "  3. python -m james --text      # or: python -m james --ui"
Write-Host ""
Write-Host "Note: allow microphone access in Windows Settings > Privacy > Microphone for voice mode."
