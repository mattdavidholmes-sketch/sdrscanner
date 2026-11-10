#!/bin/bash
# Continues the newest scan folder (appends to its data) - use after a restart or a code update.
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:$PATH"
F=$(ls -td scan_*/ 2>/dev/null | head -1 | tr -d /)
[ -z "$F" ] && { echo "No scan folder yet - run Scan everything 5 hours.command"; read -n 1; exit 1; }
rm -f "$F/PAUSE"
echo "Resuming $F"
python3 sdr_scan.py scan --hours 5 --bands all --location GTA --out "$F" --known gta_nets_chirp.csv
read -n 1 -s -r -p "Press any key to close."
