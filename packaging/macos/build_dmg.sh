# JAMES — macOS app bundle + signed DMG
#
# Usage (from the repo root, on macOS):
#   python -m PyInstaller james.spec          # builds dist/JAMES.app
#   bash packaging/macos/build_dmg.sh [1.0.0]
#
# Signing/notarization are applied only when the required environment
# variables are present (CI passes them from secrets):
#   MAC_CERTIFICATE_BASE64 / MAC_CERTIFICATE_PASSWORD / MAC_SIGNING_IDENTITY
#   APPLE_ID / APPLE_TEAM_ID / APPLE_APP_PASSWORD

set -euo pipefail

VERSION="${1:-1.0.0}"
APP="dist/JAMES.app"
DMG="dist/JAMES-${VERSION}-macos-x86_64.dmg"

if [ ! -d "$APP" ]; then
  echo "[!] $APP not found. Run: python -m PyInstaller james.spec" >&2
  exit 1
fi

# --- codesign (ad-hoc or real identity) -------------------------------------
IDENTITY="${MAC_SIGNING_IDENTITY:- -}"
if [ -n "${MAC_CERTIFICATE_BASE64:-}" ]; then
  CERT="$RUNNER_TEMP/mac_cert.p12"
  echo "$MAC_CERTIFICATE_BASE64" | base64 --decode > "$CERT"
  KEYCHAIN="$RUNNER_TEMP/james.keychain"
  security create-keychain -p james "$KEYCHAIN"
  security default-keychain -s "$KEYCHAIN"
  security unlock-keychain -p james "$KEYCHAIN"
  security import "$CERT" -P "$MAC_CERTIFICATE_PASSWORD" -A -t cert -f pkcs12 -k "$KEYCHAIN"
  security set-key-partition-list -S apple-tool:,apple: -s -k james "$KEYCHAIN"
fi

codesign --deep --force --options runtime --sign "$IDENTITY" \
  --identifier "ai.james.assistant" \
  --entitlements packaging/macos/entitlements.plist "$APP" 2>/dev/null || \
codesign --deep --force --sign "$IDENTITY" \
  --identifier "ai.james.assistant" "$APP"

# --- dmg --------------------------------------------------------------------
if command -v create-dmg >/dev/null 2>&1; then
  create-dmg --volname "JAMES ${VERSION}" --window-pos 200 120 \
    --window-size 600 400 --icon-size 100 --app-drop-link 480 250 \
    --icon "JAMES.app" 120 250 "$DMG" "$APP"
else
  hdiutil create -volname "JAMES ${VERSION}" -srcfolder "$APP" -ov -format UDZO "$DMG"
fi

codesign --force --sign "$IDENTITY" "$DMG" 2>/dev/null || true

# --- notarization (only with Apple credentials) -----------------------------
if [ -n "${APPLE_ID:-}" ] && [ -n "${APPLE_TEAM_ID:-}" ]; then
  xcrun notarytool submit "$DMG" --apple-id "$APPLE_ID" \
    --team-id "$APPLE_TEAM_ID" --password "$APPLE_APP_PASSWORD" \
    --wait
  xcrun stapler staple "$DMG"
fi

echo "[+] Built $DMG"
