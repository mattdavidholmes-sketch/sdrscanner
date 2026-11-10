#!/bin/bash
# Installs SDR++ (Apple Silicon nightly build) into /Applications and opens it.
set -e
cd "$HOME/Downloads"
echo "=== Installing SDR++ ==="
echo "Downloading sdrpp_macos_arm.zip ..."
curl -L -# -o sdrpp_macos_arm.zip "https://github.com/AlexandreRouma/SDRPlusPlus/releases/download/nightly/sdrpp_macos_arm.zip"
rm -rf sdrpp_unpack && mkdir sdrpp_unpack
ditto -x -k sdrpp_macos_arm.zip sdrpp_unpack
APP=$(find sdrpp_unpack -maxdepth 3 -name "SDR++.app" | head -1)
if [ -z "$APP" ]; then echo "Could not find SDR++.app in the download."; ls -R sdrpp_unpack | head; read -n 1 -s -r -p "Press any key to close."; exit 1; fi
rm -rf "/Applications/SDR++.app"
ditto "$APP" "/Applications/SDR++.app"
xattr -dr com.apple.quarantine "/Applications/SDR++.app" 2>/dev/null || true
rm -rf sdrpp_unpack sdrpp_macos_arm.zip
echo
echo "SDR++ is in /Applications. Opening it now."
echo "First time: Source = RTL-SDR, pick the dongle, click the play button, then tune."
echo "To load the scanner hits: Module Manager > add 'frequency_manager' if not there > Import > choose sdrpp_bookmarks.json from a scan folder."
open -a "SDR++"
echo
read -n 1 -s -r -p "Press any key to close."
