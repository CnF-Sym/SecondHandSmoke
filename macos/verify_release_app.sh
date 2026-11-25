#!/bin/bash
# Check the actual signatures. The shared signer can continue after a nested
# signing error, even though that binary may fail at runtime.
#
# Two codesign details this depends on:
#   * `codesign -dv` does not print the Authority= lines. Only -dvv and up do.
#   * `codesign -d --entitlements -` prints a human-readable dump on current
#     macOS. --xml gives a real plist that PlistBuddy can read.
#
# Entitlements only mean something on executables. The bundled Python server,
# the Swift wrapper and the bundle itself must carry both. Nested .so/.dylib
# files only need a valid signature from the same Developer ID.
set -euo pipefail

APP="$1"
fail() { echo "REFUSING RELEASE: $*" >&2; exit 1; }

signature="$(codesign -dvv "$APP" 2>&1)" || fail "App signature is missing."
authority="$(sed -n '/^Authority=Developer ID Application: /{s/^Authority=//;p;}' <<< "$signature")"
[ -n "$authority" ] || fail "The app needs a Developer ID Application signature."

entitlements="$(mktemp)"
trap 'rm -f "$entitlements"' EXIT

signed_by_us() {
  local item="$1" signed
  signed="$(codesign -dvv "$item" 2>&1)" || fail "Missing signature on $item"
  grep -Fxq "Authority=$authority" <<< "$signed" || fail "Wrong signing identity on $item"
  codesign --verify --strict "$item" || fail "Invalid signature on $item"
  printf '%s\n' "$signed"
}

# Executables: identity, hardened runtime and both Python entitlements.
verify() {
  local item="$1" signed key
  signed="$(signed_by_us "$item")"
  grep -Eq 'flags=.*\(.*runtime' <<< "$signed" || fail "Hardened runtime is missing on $item"
  codesign -d --entitlements - --xml "$item" > "$entitlements" 2>/dev/null \
    || fail "Cannot read entitlements on $item"
  for key in com.apple.security.cs.disable-library-validation \
             com.apple.security.cs.allow-dyld-environment-variables; do
    [ "$(/usr/libexec/PlistBuddy -c "Print :$key" "$entitlements" 2>/dev/null)" = true ] \
      || fail "Missing $key on $item"
  done
}

verify "$APP/Contents/Resources/server/SecondHandSmoke-server"
verify "$APP/Contents/MacOS/SecondHandSmoke"
# Libraries: signed, valid, same Developer ID. No entitlements expected.
find "$APP/Contents" -type f \( -name '*.so' -o -name '*.dylib' \) -print0 |
  while IFS= read -r -d '' item; do signed_by_us "$item" > /dev/null; done
verify "$APP"
xcrun stapler validate "$APP" || fail "The app needs a valid notarization staple."
echo "Release checks passed: $authority"
