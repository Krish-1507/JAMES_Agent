#!/usr/bin/env bash
# JAMES — one-line installer.
#
#   curl -fsSL https://raw.githubusercontent.com/Krish-1507/JAMES_Agent/main/install.sh | bash
#
# Installs JAMES to a stable per-user location (~/james), creates a virtualenv
# with all core + optional (GUI/MCP) dependencies, and exposes the `james`
# command on your PATH so you can run it from any terminal:
#   james              # desktop app
#   james --text       # terminal CLI
#
# Flags (only used when you run install.sh directly):
#   bash install.sh --with-browser   # Playwright + Chromium
#   bash install.sh --with-voice     # STT/TTS engines
#   bash install.sh --with-desktop   # pyautogui / clipboard / system control
#   bash install.sh --with-docs      # Word / PowerPoint / PDF generation
#   bash install.sh --with-mcp       # MCP client (on by default)
#   bash install.sh --with-all       # everything optional
#   bash install.sh --minimal        # core only, no GUI / MCP
set -euo pipefail

REPO_URL="https://github.com/Krish-1507/JAMES_Agent"
INSTALL_DIR="${JAMES_INSTALL_DIR:-$HOME/james}"

echo "🤖 JAMES — one-line installer"

# --- pick the install location ----------------------------------------------
# If we're already inside a checkout use it, otherwise install to ~/james so the
# command works no matter where the installer was launched from.
if [ -f pyproject.toml ]; then
  INSTALL_DIR="$(pwd)"
  echo "• Using current directory: $INSTALL_DIR"
else
  if [ -d "$INSTALL_DIR/.git" ]; then
    echo "• Using existing install at $INSTALL_DIR"
  else
    mkdir -p "$(dirname "$INSTALL_DIR")"
    echo "• Cloning JAMES to $INSTALL_DIR …"
    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
  fi
  cd "$INSTALL_DIR"
fi

# --- python detection --------------------------------------------------------
PY=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys' >/dev/null 2>&1; then
      PY="$candidate"
      break
    fi
  fi
done
if [ -z "$PY" ]; then
  echo "❌ Python 3.10+ is required but was not found." >&2
  echo "   Install it (apt install python3 / brew install python3), then re-run." >&2
  exit 1
fi
ver=$("$PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])')
if [ "$(printf '%s\n' 3.10 "$ver" | sort -V | head -n1)" != "3.10" ]; then
  echo "❌ Python 3.10+ required (found $ver)." >&2
  exit 1
fi
echo "• Using Python $ver"

# --- translate flags into an extras list ------------------------------------
EXTRAS="ui,mcp"
WITH_BROWSER=0
while [ $# -gt 0 ]; do
  case "$1" in
    --minimal)      EXTRAS="" ;;
    --with-mcp)     EXTRAS="${EXTRAS:+$EXTRAS,}mcp" ;;
    --with-browser) EXTRAS="${EXTRAS:+$EXTRAS,}browser"; WITH_BROWSER=1 ;;
    --with-voice)   EXTRAS="${EXTRAS:+$EXTRAS,}voice" ;;
    --with-desktop) EXTRAS="${EXTRAS:+$EXTRAS,}desktop" ;;
    --with-docs)    EXTRAS="${EXTRAS:+$EXTRAS,}docs" ;;
    --with-all)     EXTRAS="all" ;;
    *) echo "⚠ Ignoring unknown flag: $1" ;;
  esac
  shift
done

# --- virtualenv + install ---------------------------------------------------
if [ ! -d .venv ]; then
  echo "• Creating virtualenv …"
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "• Upgrading pip …"
pip install -U pip

if [ -n "$EXTRAS" ]; then
  echo "• Installing JAMES [$EXTRAS] (this downloads dependencies) …"
  pip install -e ".[$EXTRAS]"
else
  echo "• Installing JAMES (core) …"
  pip install -e .
fi
echo "✓ JAMES installed"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "✓ Created .env (paste your API key below)"
fi

# --- make `james` available from any terminal --------------------------------
# Symlink the console script into ~/.local/bin and add it to PATH in the shell
# profile, so a fresh terminal has the `james` command.
mkdir -p "$HOME/.local/bin"
if [ -f "$(pwd)/.venv/bin/james" ]; then
  ln -sf "$(pwd)/.venv/bin/james" "$HOME/.local/bin/james"
  echo "✓ Linked james -> $HOME/.local/bin/james"
fi
if ! grep -qs "$HOME/.local/bin" "$HOME/.bashrc" 2>/dev/null; then
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
  echo "• Added ~/.local/bin to PATH in ~/.bashrc"
fi
if [ -f "$HOME/.zshrc" ] && ! grep -qs "$HOME/.local/bin" "$HOME/.zshrc" 2>/dev/null; then
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.zshrc"
  echo "• Added ~/.local/bin to PATH in ~/.zshrc"
fi

# --- desktop launcher --------------------------------------------------------
if [ -d "$HOME/.local/share/applications" ]; then
  cat > "$HOME/.local/share/applications/james.desktop" <<EOF
[Desktop Entry]
Name=JAMES
Comment=Just A Modular Executive System
Exec=$(pwd)/.venv/bin/james
Icon=$(pwd)/James.png
Terminal=false
Type=Application
Categories=Utility;
EOF
  chmod +x "$HOME/.local/share/applications/james.desktop"
  echo "✓ Created desktop launcher"
fi

# --- one-time setup wizard --------------------------------------------------
if ! grep -Eq '^[A-Z0-9_]+_API_KEY=.+' .env 2>/dev/null; then
  echo
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  One-time setup — paste an API key and"
  echo "  press Enter; JAMES detects the provider."
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  python -m james --setup || true
fi

python -m james --check || true

if [ "$WITH_BROWSER" -eq 1 ]; then
  playwright install chromium
  echo "✓ Browser automation ready"
fi

echo
echo "You're all set!"
echo "  Open a NEW terminal (so PATH picks up james), then:"
echo "    james              # desktop app"
echo "    james --text       # terminal CLI"
echo
echo "Installed at: $INSTALL_DIR"
