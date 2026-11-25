#!/bin/zsh
CNF_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"   # for the sign + notarize step at the end
cd "${0:A:h}"
./macos/build_macos_app.sh; CNF_BUILD_STATUS=$?
# ── Sign + notarize (CnF: every build ends signed) ──────────────────────────────────────────────────────
# Runs the shared release.command (found in this folder or any folder above it) on the app this build just
# made: Developer ID signature, Apple notarization, staple, and a zip beside the app (the zip to carry to the
# Mac mini). Needs internet; Apple usually answers in 1–5 minutes.
# CNF_SKIP_RELEASE=1 skips it for a quick test build (the app is then only ad-hoc signed).
CNF_BUILT_APP="$CNF_SCRIPT_DIR/dist/Second Hand Smoke.app"
CNF_RELEASE_OK=0
if [ "${CNF_BUILD_STATUS:-0}" != 0 ]; then
  echo; echo "The build failed, so nothing was signed."
elif [ -n "${CNF_SKIP_RELEASE:-}" ]; then
  echo; echo "Signing skipped (CNF_SKIP_RELEASE is set). Run release.command on the app before it leaves this Mac."
else
  CNF_RELEASE=""; CNF_D="$CNF_SCRIPT_DIR"
  while [ -n "$CNF_D" ] && [ "$CNF_D" != "/" ]; do
    if [ -f "$CNF_D/release.command" ]; then CNF_RELEASE="$CNF_D/release.command"; break; fi
    CNF_D="$(dirname "$CNF_D")"
  done
  if [ -z "$CNF_RELEASE" ]; then
    echo; echo "NOT SIGNED: release.command wasn't found in this folder or any folder above it."
  elif [ ! -d "$CNF_BUILT_APP" ]; then
    echo; echo "NOT SIGNED: the build didn't leave an app at $CNF_BUILT_APP"
  else
    echo; echo "=== Signing + notarizing with $CNF_RELEASE ==="
    # The shared signer discovers macos/entitlements.plist one level above dist/.
    if CNF_NO_PAUSE=1 /bin/bash "$CNF_RELEASE" "$CNF_BUILT_APP" &&
       /bin/bash "$CNF_SCRIPT_DIR/macos/verify_release_app.sh" "$CNF_BUILT_APP"; then
      CNF_RELEASE_OK=1
    else
      echo; echo "RELEASE FAILED: signing, notarization, or signature validation failed. See above."
    fi
  fi
fi

echo ""
echo "Press return to close this window."
read
[[ "$CNF_BUILD_STATUS" == 0 && ( -n "${CNF_SKIP_RELEASE:-}" || "$CNF_RELEASE_OK" == 1 ) ]]
