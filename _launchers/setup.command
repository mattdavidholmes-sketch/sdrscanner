#!/bin/bash
# One-time setup: installs the RTL-SDR tools and sox (audio) via Homebrew.
cd "$(dirname "$0")"
echo "=== SDR Scanner setup ==="
if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew not found - installing it first (you will be asked for your Mac password)."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  eval "$(/opt/homebrew/bin/brew shellenv)"
fi
brew list librtlsdr >/dev/null 2>&1 || brew install librtlsdr
brew list sox >/dev/null 2>&1 || brew install sox
echo
echo "Checking for the dongle..."
if rtl_test -t 2>&1 | grep -q "Found"; then
  rtl_test -t 2>&1 | grep -E "Found|Using"
  echo
  echo "All good. Double-click 'Scan 5 hours.command' to start."
else
  echo "No RTL-SDR seen. Plug it in (try a different USB port, no hub) and run this again."
fi
echo
read -n 1 -s -r -p "Press any key to close."
