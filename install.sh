#!/usr/bin/env bash
# JAMES — one-line installer. Usage:  curl -fsSL https://raw.githubusercontent.com/Krish-1507/Voice-Automated-Desktop-Agent-J.A.M.E.S/main/install.sh | bash
# Optional:  bash install.sh --with-browser   (adds Playwright + Chromium)
set -euo pipefail

echo "🤖 JAMES — one-line installer"
PY="${PYTHON:-python3}"

if ! command -v "$PY" >/dev/null 2>&1; then
  echo "❌ Python 3.10+ is required. Install it then re-run this script." >&2
  exit 1
fi

ver=$("$PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])')
echo "• Using $PY ($ver)"

if [ ! -d .venv ]; then
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

pip install -U pip >/dev/null
pip install -e . >/dev/null
echo "✓ JAMES installed"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "✓ Created .env — edit it with your API keys / model"
fi

python -m james --check || true

if [ "${1:-}" = "--with-browser" ]; then
  pip install playwright >/dev/null
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
