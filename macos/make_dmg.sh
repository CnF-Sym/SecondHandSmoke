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

# The DMG is named from the repository's VERSION file, but it packages whatever
# .app is sitting in dist/. If the build above silently failed or was skipped,
# that produces a disk image named 0.4.0 containing 0.2.0 — a package that lies
# about its own version, handed to someone trusting it with their footage.
# Read the version back out of the thing actually being shipped, and refuse.
BUILT="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' \
         "$APP/Contents/Info.plist" 2>/dev/null || echo "unreadable")"
if [ "$BUILT" != "$VERSION" ]; then
  echo "REFUSING TO PACKAGE." >&2
  echo "  VERSION file says:   $VERSION" >&2
  echo "  the built app says:  $BUILT" >&2
  echo "  dist/ holds a different build than this checkout. Delete dist/ and" >&2
  echo "  build again from a clean tree before releasing." >&2
  exit 1
fi
echo "Packaging version $BUILT — matches the VERSION file."

STAGE="$(mktemp -d "${TMPDIR:-/tmp}/shs-dmg.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
cp -R "$APP" "$STAGE/Second Hand Smoke.app"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
hdiutil create -volname "Second Hand Smoke" -srcfolder "$STAGE" -ov -format UDZO "$DMG"

# A program that asks people to verify their footage should hand them something
# they can verify. The .sha256 sits beside the DMG and goes in the release.
shasum -a 256 "$DMG" | sed "s|$ROOT/dist/||" > "$DMG.sha256"
echo "Checksum: $(cat "$DMG.sha256")"

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
