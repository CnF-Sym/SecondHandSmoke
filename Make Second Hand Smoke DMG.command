#!/bin/zsh
cd "${0:A:h}"
/bin/bash ./macos/make_dmg.sh
DMG_STATUS=$?
echo ""
echo "Press return to close this window."
read
exit "$DMG_STATUS"
