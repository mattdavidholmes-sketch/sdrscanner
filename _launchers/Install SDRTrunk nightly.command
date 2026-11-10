#!/bin/bash
# Installs the SDRTrunk NIGHTLY build (adds the NXDN decoder: GO Transit rail traffic control) next to the stable 0.6.1 - nothing is replaced.
# The app prefers the nightly once this file exists: ~/Applications/sdr-trunk-nightly/bin/sdr-trunk. Delete that folder to go back.
cd "$(dirname "$0")/.."; export PATH="/opt/homebrew/bin:$PATH"
DEST="$HOME/Applications"; mkdir -p "$DEST"; cd "$DEST" || exit 1
echo "== downloading sdr-trunk-osx-aarch64-vnightly.zip (~80 MB)"
curl -L --fail --progress-bar -o sdr-trunk-nightly.zip "https://github.com/DSheirer/sdrtrunk/releases/download/nightly/sdr-trunk-osx-aarch64-vnightly.zip" || { echo "download failed"; exit 1; }
rm -rf sdr-trunk-nightly.tmp; mkdir sdr-trunk-nightly.tmp; unzip -q sdr-trunk-nightly.zip -d sdr-trunk-nightly.tmp || { echo "unzip failed"; exit 1; }
INNER=$(dirname "$(find sdr-trunk-nightly.tmp -maxdepth 4 -type f -name sdr-trunk | head -1)")/..
[ -n "$INNER" ] || { echo "unexpected archive layout"; exit 1; }
rm -rf sdr-trunk-nightly; mv "$INNER" sdr-trunk-nightly; rm -rf sdr-trunk-nightly.tmp sdr-trunk-nightly.zip
chmod +x sdr-trunk-nightly/bin/sdr-trunk; xattr -dr com.apple.quarantine sdr-trunk-nightly 2>/dev/null
echo "== installed: $DEST/sdr-trunk-nightly/bin/sdr-trunk"; ls sdr-trunk-nightly/bin | head
echo "nightly $(date '+%Y-%m-%d %H:%M')" > "$HOME/SDRScanner/sdrtrunk/nightly_installed.txt"
echo "== done"
