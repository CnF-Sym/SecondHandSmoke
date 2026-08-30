#!/bin/zsh
cd "${0:A:h}"
./macos/make_dmg.sh
echo ""
echo "Press return to close this window."
read
