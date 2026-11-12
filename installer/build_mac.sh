#!/bin/sh
# Builds the macOS disk image from dist/<App>.app:
#   installer/build_mac.sh Tag
# Writes dist/<App>.dmg (the app next to a shortcut to Applications, so it
# installs by dragging) and dist/<App>.dmg.sha256.
set -eu
APP=$1
ROOT=$(cd "$(dirname "$0")/.." && pwd)
DIST=$ROOT/dist
[ -d "$DIST/$APP.app" ] || { echo "dist/$APP.app not found: build the app first"; exit 1; }

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
ditto "$DIST/$APP.app" "$STAGE/$APP.app"
ln -s /Applications "$STAGE/Applications"

rm -f "$DIST/$APP.dmg"
# hdiutil occasionally fails with "Resource busy" on CI runners; try a few times.
for i in 1 2 3 4 5; do
  hdiutil create -volname "$APP" -srcfolder "$STAGE" -fs HFS+ -format UDZO -imagekey zlib-level=9 \
    -ov "$DIST/$APP.dmg" && break
  [ "$i" = 5 ] && exit 1
  sleep 5
done

cd "$DIST"
shasum -a 256 "$APP.dmg" > "$APP.dmg.sha256"
cat "$APP.dmg.sha256"
