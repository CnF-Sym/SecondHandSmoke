#!/bin/bash
# Builds the app, then a drag-to-Applications DMG beside it.
# Notarization happens when NOTARY_PROFILE names an existing notarytool
# keychain profile, and is skipped otherwise.
#
#   CODESIGN_IDENTITY="Developer ID Application: NAME (TEAMID)" \
#   NOTARY_PROFILE=cnfworks ./macos/make_dmg.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/dist/Second Hand Smoke.app"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
DMG="$ROOT/dist/SecondHandSmoke-$VERSION.dmg"

"$ROOT/macos/build_macos_app.sh"

STAGE="$(mktemp -d "${TMPDIR:-/tmp}/shs-dmg.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
cp -R "$APP" "$STAGE/Second Hand Smoke.app"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
hdiutil create -volname "Second Hand Smoke" -srcfolder "$STAGE" -ov -format UDZO "$DMG"

if [ -n "${NOTARY_PROFILE:-}" ]; then
  if [ -z "${CODESIGN_IDENTITY:-}" ]; then
    echo "NOTARY_PROFILE is set but CODESIGN_IDENTITY is not."
    echo "Notarization requires a Developer ID signature; an ad-hoc one is rejected."
    exit 1
  fi
  xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG"
  xcrun stapler validate "$DMG"
  echo "Notarized and stapled. This opens cleanly on any Mac."
else
  echo ""
  echo "NOT notarized. Set NOTARY_PROFILE to finish a public release:"
  echo '  xcrun notarytool store-credentials "cnfworks" --apple-id ... --team-id ... --password ...'
fi

shasum -a 256 "$DMG" > "$DMG.sha256"
echo "Release files: $DMG and $DMG.sha256"
