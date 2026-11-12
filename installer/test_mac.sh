#!/bin/sh
# Mounts dist/<App>.dmg and checks it holds the app and an Applications shortcut. CI only.
set -eu
APP=$1
ROOT=$(cd "$(dirname "$0")/.." && pwd)
MNT=$(mktemp -d)
hdiutil attach -nobrowse -readonly -mountpoint "$MNT" "$ROOT/dist/$APP.dmg"
trap 'hdiutil detach "$MNT" -force >/dev/null 2>&1 || true' EXIT
ls -la "$MNT"
[ -x "$MNT/$APP.app/Contents/MacOS/$APP" ] || { echo "$APP.app missing from the disk image"; exit 1; }
[ "$(readlink "$MNT/Applications")" = /Applications ] || { echo "Applications shortcut missing"; exit 1; }
codesign --verify --deep "$MNT/$APP.app" 2>/dev/null && echo "signature intact" || echo "note: app is not signed (same as the zip)"
echo "disk image OK"
