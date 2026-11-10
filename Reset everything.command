#!/bin/bash
# Hard reset: stops every scanner/dashboard/radio process, clears pause flags, trims junk data, then starts ONE scan + ONE dashboard.
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:$PATH"
echo "=== Stopping everything radio-related ==="
pkill -x rtl_fm; pkill -x rtl_power; pkill -x rtl_tcp; pkill -x play; pkill -x whisper-cli
pkill -f "sdr_scan.py scan"; pkill -f "dashboard"; pkill -x sox
sleep 2
# every other Terminal window is now idle: close them all (this one stays until the end)
osascript -e 'tell application "Terminal" to close (every window whose busy is false)' >/dev/null 2>&1
echo "Still running (should be empty):"; pgrep -lf "rtl_|sdr_scan|dashboard" || echo "  none"
rm -f scan_*/PAUSE
echo
echo "=== Dongle check ==="
if rtl_test -t 2>&1 | grep -q "Found 1"; then echo "dongle OK"; else echo "NO DONGLE - unplug it, plug it back in, run this again"; read -n 1 -s -r -p "Press any key to close."; exit 1; fi
echo
F="scan_$(date +%Y-%m-%d_%H%M)"
echo "=== Starting a FRESH scan in $F (old folders are kept) and the dashboard ==="
osascript -e 'tell application "Terminal" to do script "cd \"'"$PWD"'\"; export PATH=/opt/homebrew/bin:$PATH; python3 sdr_scan.py scan --hours 5 --bands all --slice 1 --location GTA --out \"'"$F"'\" --known gta_nets_chirp.csv"' >/dev/null
sleep 3
osascript -e 'tell application "Terminal" to do script "cd \"'"$PWD"'\"; export PATH=/opt/homebrew/bin:$PATH; python3 dashboard2.py"' >/dev/null
sleep 2
echo "Running now:"; pgrep -lf "sdr_scan.py scan|dashboard.py|rtl_power" | sed 's/^/  /'
echo
echo "Done. Dashboard: http://localhost:8765"
# close this window too, a moment after the script ends
(sleep 2; osascript -e 'tell application "Terminal" to close (every window whose busy is false)') >/dev/null 2>&1 &
exit 0
