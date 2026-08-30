#!/bin/zsh
cd "${0:A:h}"
./macos/build_macos_app.sh
echo ""
echo "Press return to close this window."
read
