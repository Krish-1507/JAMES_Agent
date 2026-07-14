# JAMES — Windows installer.
#
#   irm https://raw.githubusercontent.com/Krish-1507/Voice-Automated-Desktop-Agent-J.A.M.E.S/main/install.ps1 | iex
#
# Auto-clones the repo if you are not already inside it, creates a virtualenv,
# and installs JAMES with a sensible default extra set (orb GUI + MCP client).
# Flags:
#   -WithBrowser   Playwright + Chromium
#   -WithVoice     STT/TTS engines
#   -WithDesktop   pyautogui / clipboard / system control
#   -WithDocs      Word / PowerPoint / PDF generation
#   -WithAll       everything optional
#   -Minimal       core only, no GUI / MCP
param(
    [switch]$WithBrowser,
    [switch]$WithVoice,
    [switch]$WithDesktop,
    [switch]$WithDocs,
    [switch]$WithAll,
    [switch]$Minimal
)

$ErrorActionPreference = "Stop"
Write-Host "🤖 JAMES — Windows installer" -ForegroundColor Cyan

$REPO_URL = "https://github.com/Krish-1507/Voice-Automated-Desktop-Agent-J.A.M.E.S"
$REPO_DIR = "james"

# --- auto-clone if we're not already inside the project -------------------
if (-not (Test-Path pyproject.toml)) {
    if (Test-Path "$REPO_DIR\.git") {
        Write-Host "• Using existing clone at .\$REPO_DIR"
    } else {
        Write-Host "• Cloning JAMES…" -ForegroundColor Cyan
        git clone --depth 1 $REPO_URL $REPO_DIR
    }
    Set-Location $REPO_DIR
}

$py = "python"
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    if (-not (Get-Command python3 -ErrorAction SilentlyContinue)) {
        Write-Host "❌ Python 3.10+ is required. Install it, then re-run." -ForegroundColor Red
        exit 1
    }
    $py = "python3"
}

$ver = & $py -c "import sys;print('%d.%d'%sys.version_info[:2])"
if ([version]$ver -lt [version]"3.10") {
    Write-Host "❌ Python 3.10+ required (found $ver)." -ForegroundColor Red
    exit 1
}
Write-Host "• Using $py ($ver)"

# --- build the extras list ------------------------------------------------
if ($Minimal) {
    $extras = ""
} elseif ($WithAll) {
    $extras = "all"
} else {
    $extras = "ui,mcp"
    if ($WithBrowser) { $extras += ",browser" }
    if ($WithVoice)   { $extras += ",voice" }
    if ($WithDesktop) { $extras += ",desktop" }
    if ($WithDocs)    { $extras += ",docs" }
}

if (-not (Test-Path .venv)) {
    & $py -m venv .venv
}
$venvPy = Resolve-Path .venv\Scripts\python.exe

& $venvPy -m pip install -U pip | Out-Null
if ($extras) {
    Write-Host "• Installing JAMES [$extras]" -ForegroundColor Cyan
    & $venvPy -m pip install -e ".[$extras]" | Out-Null
} else {
    Write-Host "• Installing JAMES (core)" -ForegroundColor Cyan
    & $venvPy -m pip install -e . | Out-Null
}
Write-Host "✓ JAMES installed" -ForegroundColor Green

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "✓ Created .env — edit it with your API keys / model" -ForegroundColor Green
}

& $venvPy -m james --check

if ($WithBrowser) {
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
