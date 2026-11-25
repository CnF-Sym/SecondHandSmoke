#!/bin/bash
# Packages the existing signed app. Run the build command first.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/dist/Second Hand Smoke.app"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
DMG="$ROOT/dist/SecondHandSmoke-$VERSION.dmg"

# Read the version back out of the bundle that will actually ship.
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

/bin/bash "$ROOT/macos/verify_release_app.sh" "$APP"

# Default to the same keychain profile as the shared app signing command.
PROFILE="${NOTARY_PROFILE:-cnf-notary}"
IDENTITY="${CODESIGN_IDENTITY:-$(codesign -dvv "$APP" 2>&1 | sed -n 's/^Authority=\(Developer ID Application: .*\)$/\1/p')}"
case "$IDENTITY" in
  "Developer ID Application: "*) ;;
  *) echo "A Developer ID Application certificate is required to sign the DMG." >&2; exit 1 ;;
esac

# Keep the previous release files until the new image and checksum are ready.
STAGE="$(mktemp -d "$ROOT/dist/.shs-dmg.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
VOLUME="$STAGE/volume"
mkdir "$VOLUME"
ditto "$APP" "$VOLUME/Second Hand Smoke.app"
ln -s /Applications "$VOLUME/Applications"
NAME="$(basename "$DMG")"
IMAGE="$STAGE/$NAME"
hdiutil create -volname "Second Hand Smoke" -srcfolder "$VOLUME" -format UDZO "$IMAGE"
codesign --sign "$IDENTITY" --timestamp "$IMAGE"
xcrun notarytool submit "$IMAGE" --keychain-profile "$PROFILE" --wait
xcrun stapler staple "$IMAGE"
xcrun stapler validate "$IMAGE"
(cd "$STAGE" && shasum -a 256 "$NAME" > "$NAME.sha256")
mv -f "$IMAGE" "$DMG"
mv -f "$STAGE/$NAME.sha256" "$DMG.sha256"
echo "Release files: $DMG and $DMG.sha256"
