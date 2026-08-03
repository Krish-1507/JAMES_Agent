# JAMES — Windows installer.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/Krish-1507/JAMES_Agent/main/install.ps1 | iex"
#   (the wrapper works from both PowerShell and Command Prompt)
#
# Installs JAMES to a stable per-user location, creates a virtualenv with all
# core + optional (GUI/MCP) dependencies, and puts the `james` command on your
# PATH so you can run it from any terminal:
#   james              # desktop app
#   james --text       # terminal CLI
#
# Flags (only used when you run install.ps1 directly):
#   -WithBrowser   Playwright + Chromium
#   -WithVoice     STT/TTS engines
#   -WithDesktop   pyautogui / clipboard / system control
#   -WithDocs      Word / PowerPoint / PDF generation
#   -WithAll       everything optional
#   -Minimal       core only, no GUI / MCP
param(
    [string]$InstallDir = "",
    [switch]$WithBrowser,
    [switch]$WithVoice,
    [switch]$WithDesktop,
    [switch]$WithDocs,
    [switch]$WithAll,
    [switch]$Minimal
)

$ErrorActionPreference = "Stop"
Write-Host "🤖 JAMES — Windows installer" -ForegroundColor Cyan

$REPO_URL = "https://github.com/Krish-1507/JAMES_Agent"

# --- pick the install location ----------------------------------------------
# If we're already inside a checkout (pyproject.toml present) use it, otherwise
# install to a stable per-user folder so `james` keeps working no matter where
# the installer was launched from.
$InsideRepo = Test-Path pyproject.toml
if ($InsideRepo) {
    $INSTALL_DIR = (Get-Location).Path
    Write-Host "• Using current directory: $INSTALL_DIR"
} else {
    if (-not $InstallDir) {
        $InstallDir = Join-Path $HOME "james"
    }
    $INSTALL_DIR = $InstallDir
    if (Test-Path (Join-Path $INSTALL_DIR ".git")) {
        Write-Host "• Using existing install at $INSTALL_DIR"
    } else {
        if (-not (Test-Path $INSTALL_DIR)) {
            New-Item -ItemType Directory -Path $INSTALL_DIR | Out-Null
        }
        Write-Host "• Cloning JAMES to $INSTALL_DIR …"
        git clone --depth 1 $REPO_URL $INSTALL_DIR
    }
    Set-Location $INSTALL_DIR
}

# --- python detection --------------------------------------------------------
$py = $null
foreach ($candidate in @("python", "py")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) {
        try {
            $ver = & $candidate -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
            if ($ver -match '^\d+\.\d+$') {
                $py = $candidate
                break
            }
        } catch { }
    }
}
if (-not $py) {
    Write-Host "❌ Python 3.10+ is required but was not found." -ForegroundColor Red
    Write-Host "   Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then re-run this script." -ForegroundColor Yellow
    exit 1
}
$pyVer = & $py -c "import sys;print('%d.%d'%sys.version_info[:2])"
if ([version]$pyVer -lt [version]"3.10") {
    Write-Host "❌ Python 3.10+ required (found $pyVer)." -ForegroundColor Red
    exit 1
}
Write-Host "• Using Python $pyVer"

# --- build the extras list --------------------------------------------------
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

# --- virtualenv + install ---------------------------------------------------
if (-not (Test-Path .venv)) {
    Write-Host "• Creating virtualenv …"
    & $py -m venv .venv
}
$venvPy = Resolve-Path .venv\Scripts\python.exe

Write-Host "• Upgrading pip …"
& $venvPy -m pip install -U pip
if ($LASTEXITCODE -ne 0) { Write-Host "❌ pip upgrade failed." -ForegroundColor Red; exit 1 }

if ($extras) {
    Write-Host "• Installing JAMES [$extras] (this downloads dependencies) …" -ForegroundColor Cyan
    & $venvPy -m pip install -e ".[$extras]"
} else {
    Write-Host "• Installing JAMES (core) …" -ForegroundColor Cyan
    & $venvPy -m pip install -e .
}
if ($LASTEXITCODE -ne 0) { Write-Host "❌ Install failed. Re-run with a working internet connection." -ForegroundColor Red; exit 1 }
Write-Host "✓ JAMES installed" -ForegroundColor Green

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "✓ Created .env (paste your API key below)" -ForegroundColor Green
}

# --- make `james` available from any terminal -------------------------------
$venvScripts = Resolve-Path .venv\Scripts
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notmatch [regex]::Escape($venvScripts.Path)) {
    $newPath = "$userPath;$venvScripts"
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Write-Host "✓ Added JAMES to your user PATH (new terminals will have the `james` command)" -ForegroundColor Green
}

# --- desktop + start-menu shortcuts -----------------------------------------
$pythonw = Resolve-Path .venv\Scripts\pythonw.exe
$icon = Resolve-Path .venv\Scripts\james.exe
$ws = New-Object -ComObject WScript.Shell
$shortcutTargets = @()
try {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $shortcutTargets += (Join-Path $desktop "JAMES.lnk")
} catch { }
try {
    $programs = [Environment]::GetFolderPath("Programs")
    $menuDir = Join-Path $programs "JAMES"
    New-Item -ItemType Directory -Path $menuDir -Force | Out-Null
    $shortcutTargets += (Join-Path $menuDir "JAMES.lnk")
} catch { }
foreach ($target in $shortcutTargets) {
    $sc = $ws.CreateShortcut($target)
    $sc.TargetPath = $pythonw
    $sc.Arguments = "-m james"
    $sc.WorkingDirectory = (Get-Location).Path
    $sc.IconLocation = "$icon,0"
    $sc.Description = "JAMES — Just A Modular Executive System"
    $sc.Save()
    Write-Host "✓ Created shortcut: $target" -ForegroundColor Green
}

# --- one-time setup wizard --------------------------------------------------
$hasKey = Select-String -Path .env -Pattern '^[A-Z0-9_]+_API_KEY=.+' -Quiet -ErrorAction SilentlyContinue
if (-not $hasKey) {
    Write-Host ""
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
    Write-Host "  One-time setup — paste an API key and" -ForegroundColor Cyan
    Write-Host "  press Enter; JAMES detects the provider." -ForegroundColor Cyan
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
    & $venvPy -m james --setup
}

& $venvPy -m james --check

if ($WithBrowser) {
    & $venvPy -m playwright install chromium
    Write-Host "✓ Browser automation ready" -ForegroundColor Green
}

Write-Host ""
Write-Host "You're all set!" -ForegroundColor Green
Write-Host "  Open a NEW terminal (so PATH picks up `james`), then:"
Write-Host "    james              # desktop app"
Write-Host "    james --text       # terminal CLI"
Write-Host ""
Write-Host "Installed at: $INSTALL_DIR"
Write-Host "Note: allow microphone access in Windows Settings > Privacy > Microphone for voice mode."
