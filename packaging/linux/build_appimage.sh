# JAMES — Linux AppImage build
#
# Usage (from the repo root, on Linux):
#   python -m PyInstaller james.spec
#   bash packaging/linux/build_appimage.sh [1.0.0]

set -euo pipefail

VERSION="${1:-1.0.0}"
STAGE="dist/appimage"
OUT="dist/JAMES-${VERSION}-linux-x86_64.AppImage"

rm -rf "$STAGE"
mkdir -p "$STAGE/usr/bin" "$STAGE/usr/share/applications" "$STAGE/usr/share/icons/hicolor/256x256/apps"

cp dist/JAMES "$STAGE/usr/bin/JAMES"
cp packaging/linux/JAMES.desktop "$STAGE/usr/share/applications/"
cp James.png "$STAGE/usr/share/icons/hicolor/256x256/apps/james.png"
cp James.png "$STAGE/james.png"

cat > "$STAGE/AppRun" <<'EOF'
#!/bin/sh
SELF="$(dirname "$(readlink -f "$0")")"
exec "$SELF/usr/bin/JAMES" "$@"
EOF
chmod +x "$STAGE/AppRun"

cat > "$STAGE/james.desktop" <<'EOF'
[Desktop Entry]
Name=JAMES
Comment=Just A Modular Executive System
Exec=james
Icon=james
Terminal=false
Type=Application
Categories=Utility;
EOF

cat > "$STAGE/.DirIcon" <<'EOF'
james.png
EOF

# Download appimagetool once, then package.
TOOL="${RUNNER_TEMP:-/tmp}/appimagetool"
if [ ! -x "$TOOL" ]; then
  curl -L -o "$TOOL" \
    https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage
  chmod +x "$TOOL"
fi
ARCH=x86_64 "$TOOL" "$STAGE" "$OUT"

echo "[+] Built $OUT"
