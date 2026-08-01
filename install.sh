#!/usr/bin/env bash
# JAMES — one-line installer.
#
#   curl -fsSL https://raw.githubusercontent.com/Krish-1507/JAMES_Agent/main/install.sh | bash
#
# The script auto-clones the repo if you are not already inside it, creates a
# virtualenv, and installs JAMES with a sensible default extra set
# (orb GUI + MCP client). Add flags to pull more capabilities:
#
#   bash install.sh --with-browser   # Playwright + Chromium
#   bash install.sh --with-voice     # STT/TTS engines
#   bash install.sh --with-desktop   # pyautogui / clipboard / system control
#   bash install.sh --with-docs      # Word / PowerPoint / PDF generation
#   bash install.sh --with-mcp       # MCP client (on by default)
#   bash install.sh --with-all       # everything optional
#   bash install.sh --minimal        # core only, no GUI / MCP
set -euo pipefail

REPO_URL="https://github.com/Krish-1507/JAMES_Agent"
REPO_DIR="james"

echo "🤖 JAMES — one-line installer"

# --- auto-clone if we're not already inside the project -------------------
if [ ! -f pyproject.toml ]; then
  if [ -d "$REPO_DIR/.git" ]; then
    echo "• Using existing clone at ./$REPO_DIR"
  else
    echo "• Cloning JAMES…"
    git clone --depth 1 "$REPO_URL" "$REPO_DIR"
  fi
  cd "$REPO_DIR"
fi

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "❌ Python 3.10+ is required. Install it then re-run this script." >&2
  exit 1
fi

ver=$("$PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])')
if [ "$(printf '%s\n' 3.10 "$ver" | sort -V | head -n1)" != "3.10" ]; then
  echo "❌ Python 3.10+ required (found $ver)." >&2
  exit 1
fi
echo "• Using $PY ($ver)"

# --- translate flags into an extras list ----------------------------------
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

if [ ! -d .venv ]; then
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

pip install -U pip >/dev/null
if [ -n "$EXTRAS" ]; then
  echo "• Installing JAMES [${EXTRAS}]"
  pip install -e ".[$EXTRAS]" >/dev/null
else
  echo "• Installing JAMES (core)"
  pip install -e . >/dev/null
fi
echo "✓ JAMES installed"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "✓ Created .env — edit it with your API keys / model"
fi

python -m james --check || true

if [ "$WITH_BROWSER" -eq 1 ]; then
  playwright install chromium
  echo "✓ Browser automation ready"
fi

echo
echo "Next steps:"
echo "  1. edit .env  (set LLM_PROVIDER + your API key, or LLM_PROVIDER=custom for Ollama)"
echo "  2. source .venv/bin/activate"
echo "  3. python -m james --text      # or: python -m james --ui"
echo
echo "Want JAMES to teach itself new skills & connect MCP servers? See README.md"
