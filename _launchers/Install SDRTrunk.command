#!/bin/bash
# Installs SDRTrunk (P25 / DMR decoder) for Apple silicon, drops the SDR Scanner playlist where it looks, and opens it.
cd "$(dirname "$0")"
LOG="sdrtrunk/install.log"; mkdir -p sdrtrunk; exec > >(tee "$LOG") 2>&1
echo "== SDRTrunk install $(date)"
DEST="$HOME/Applications"; mkdir -p "$DEST" "$HOME/SDRTrunk/playlist"
EXISTING=$(ls -d "$DEST"/sdr-trunk*/bin/sdr-trunk /Applications/sdr-trunk*/bin/sdr-trunk "$HOME/SDRTrunk/bin/sdr-trunk" 2>/dev/null | head -1)
if [ -z "$EXISTING" ]; then
  echo "finding the latest release..."
  URL=$(curl -sL -A "SDR Scanner" https://api.github.com/repos/DSheirer/sdrtrunk/releases/latest | python3 -c '
import json,sys
for a in json.load(sys.stdin)["assets"]:
    if "osx-aarch64" in a["name"] and a["name"].endswith(".zip"): print(a["browser_download_url"]); break')
  [ -z "$URL" ] && { echo "could not find the macOS build on GitHub - check your internet"; exit 1; }
  echo "downloading $URL"
  curl -L --progress-bar -o sdrtrunk/sdrtrunk.zip "$URL" || { echo "download failed"; exit 1; }
  echo "unpacking into $DEST"
  ditto -xk sdrtrunk/sdrtrunk.zip "$DEST" || unzip -q -o sdrtrunk/sdrtrunk.zip -d "$DEST"
  rm -f sdrtrunk/sdrtrunk.zip
  EXISTING=$(ls -d "$DEST"/sdr-trunk*/bin/sdr-trunk 2>/dev/null | head -1)
  [ -z "$EXISTING" ] && { echo "unpack failed"; ls "$DEST"; exit 1; }
  chmod +x "$EXISTING"; xattr -dr com.apple.quarantine "$(dirname "$(dirname "$EXISTING")")" 2>/dev/null
fi
echo "SDRTrunk: $EXISTING"
python3 sdrtrunk_playlist.py && cp sdrtrunk/SDR_Scanner.xml "$HOME/SDRTrunk/playlist/SDR_Scanner.xml" && echo "playlist copied to ~/SDRTrunk/playlist/SDR_Scanner.xml"
echo "$EXISTING" > sdrtrunk/path.txt
echo
echo "Done. The dashboard's 'Give dongle to SDRTrunk' now opens this copy."
echo "In SDRTrunk: Playlist Editor > Playlists > SDR_Scanner > Select, then Channels > 'Fire + Paramedics - West Simulcast' > Play."
sleep 3; osascript -e 'tell application "Terminal" to close (every window whose name contains "Install SDRTrunk")' &
exit 0
