#!/bin/bash
# Builds "Second Hand Smoke.app". Run on macOS 13+ (Apple Silicon) with the
# Xcode Command Line Tools installed.
#
# Signing is optional. With no CODESIGN_IDENTITY the app is ad-hoc signed,
# which is enough to run on this machine and not enough to hand to anyone else.
#
#   ./macos/build_macos_app.sh
#   CODESIGN_IDENTITY="Developer ID Application: NAME (TEAMID)" ./macos/build_macos_app.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

APP="$ROOT/dist/Second Hand Smoke.app"
CONTENTS="$APP/Contents"
BUILD_VENV="$ROOT/macos/.build-venv"

command -v xcrun >/dev/null || {
  echo "Install the Xcode Command Line Tools first: xcode-select --install"; exit 1; }
command -v python3 >/dev/null || {
  echo "python3 is required to build the server."; exit 1; }

if [ "$(uname -m)" != "arm64" ]; then
  echo "This build script targets Apple Silicon. The app it produces will only"
  echo "run on the architecture it was built on."
fi

VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
echo "=== Second Hand Smoke $VERSION ==="

# 1. A build environment separate from .venv, so a failed build can never leave
#    the environment the operator actually runs the tool with in a broken state.
echo "--- Build environment"
if [ ! -x "$BUILD_VENV/bin/python" ]; then
  python3 -m venv "$BUILD_VENV"
fi
"$BUILD_VENV/bin/pip" install --quiet --upgrade pip
"$BUILD_VENV/bin/pip" install --quiet -r "$ROOT/requirements.txt"
"$BUILD_VENV/bin/pip" install --quiet -r "$ROOT/macos/requirements-build.txt"

# 2. Freeze the server. Everything the Python side needs ends up in dist/server.
echo "--- Server"
rm -rf "$ROOT/build" "$ROOT/dist/server" "$APP"
export PYINSTALLER_CONFIG_DIR="${PYINSTALLER_CONFIG_DIR:-$ROOT/macos/.pyinstaller-cache}"
"$BUILD_VENV/bin/python" -m PyInstaller "$ROOT/SecondHandSmoke.spec" --noconfirm --clean \
  --distpath "$ROOT/dist" --workpath "$ROOT/build"

test -x "$ROOT/dist/server/SecondHandSmoke-server" || {
  echo "ERROR: PyInstaller did not produce dist/server/SecondHandSmoke-server."; exit 1; }

# 3. Assemble the bundle.
echo "--- Bundle"
mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources"
cp "$ROOT/macos/Info.plist" "$CONTENTS/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$CONTENTS/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $(echo "$VERSION" | tr -d '.')" "$CONTENTS/Info.plist"
cp -R "$ROOT/dist/server" "$CONTENTS/Resources/server"
cp "$ROOT/LICENSE" "$CONTENTS/Resources/LICENSE"

if [ -f "$ROOT/macos/AppIcon.icns" ]; then
  cp "$ROOT/macos/AppIcon.icns" "$CONTENTS/Resources/AppIcon.icns"
  /usr/libexec/PlistBuddy -c 'Add :CFBundleIconFile string AppIcon.icns' "$CONTENTS/Info.plist" \
    2>/dev/null || true
fi

# 4. The native shell. main.swift owns NSApplication startup so the window is
#    created and activated reliably whether this is launched from Terminal or
#    double-clicked.
echo "--- Wrapper"
xcrun swiftc -O -target arm64-apple-macos13.0 -framework Cocoa -framework WebKit \
  "$ROOT/macos/SecondHandSmokeApp.swift" "$ROOT/macos/main.swift" \
  -o "$CONTENTS/MacOS/SecondHandSmoke"

# 5. Sign. On Apple Silicon an unsigned binary will not launch at all, so the
#    unsigned case is ad-hoc signed rather than left alone. Nested code is
#    signed before the bundle that contains it; --deep is deprecated by Apple
#    and gets the order wrong often enough to matter.
echo "--- Signing"
if [ -n "${CODESIGN_IDENTITY:-}" ]; then
  SIGN=(codesign --force --options runtime --timestamp
        --entitlements "$ROOT/macos/entitlements.plist"
        --sign "$CODESIGN_IDENTITY")
  SIGNED=1
else
  SIGN=(codesign --force --sign -)
  SIGNED=0
fi

while IFS= read -r -d '' target; do
  "${SIGN[@]}" "$target" >/dev/null
done < <(find "$CONTENTS/Resources/server" -type f \
           \( -name '*.so' -o -name '*.dylib' -o -perm -u+x \) -print0)

"${SIGN[@]}" "$CONTENTS/Resources/server/SecondHandSmoke-server" >/dev/null
"${SIGN[@]}" "$CONTENTS/MacOS/SecondHandSmoke" >/dev/null
"${SIGN[@]}" "$APP" >/dev/null
codesign --verify --strict --verbose=2 "$APP"

echo ""
if [ "$SIGNED" = "1" ]; then
  echo "Built and Developer ID signed: $APP"
  echo "Notarize it with macos/make_dmg.sh and NOTARY_PROFILE set, or anyone who"
  echo "downloads it will be told the app is damaged."
else
  echo "Built ad-hoc signed (this Mac only): $APP"
  echo "First launch still needs right-click > Open, once."
  echo "For a release: CODESIGN_IDENTITY=\"Developer ID Application: NAME (TEAMID)\" $0"
fi
