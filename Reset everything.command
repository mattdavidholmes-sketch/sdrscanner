#!/bin/bash
# Full reset: stops everything radio-related (the dashboard, SDRTrunk, sweeps, receivers, players), clears the request and pause
# flags, checks the dongle, then starts ONE dashboard in ONE Terminal window (Dashboard.command). No data is deleted.
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:$PATH"
echo "=== $(date '+%H:%M:%S') Stopping everything radio-related ==="
touch stop_loop                                   # a running Dashboard.command loop ends instead of starting the dashboard again
pkill -f "sdr-trunk"; pkill -f "sdr_scan.py"; pkill -f "fastscan.py"; pkill -f "dashboard2.py"
pkill -x rtl_fm; pkill -x rtl_tcp; pkill -x rtl_power; pkill -x rtl_sdr; pkill -x sox; pkill -x play; pkill -x whisper-cli; pkill -x icecast
for i in $(seq 1 20); do pgrep -f "dashboard2.py|sdr-trunk" >/dev/null || break; sleep 0.5; done
pkill -9 -f "sdr-trunk" 2>/dev/null; pkill -9 -f "dashboard2.py" 2>/dev/null; pkill -9 -x rtl_fm 2>/dev/null; pkill -9 -x rtl_tcp 2>/dev/null; pkill -9 -x rtl_power 2>/dev/null
for i in $(seq 1 10); do pgrep -f "Dashboard.command" >/dev/null || break; sleep 0.5; done       # the old loop has seen stop_loop and ended
rm -f stop_loop scan_*/PAUSE restart_request.txt reset_request.txt open_request.txt sdrt_request.txt scan_request.txt diag_request.txt SDRTRUNK_HAS_DONGLE DMR_SCAN_ON
echo "Still running (should be none): $(pgrep -lf 'rtl_|sdr_scan|fastscan|dashboard2|sdr-trunk' | cut -c1-60 | tr '\n' ';')"
echo "=== Dongle check ==="
if command -v rtl_test >/dev/null; then
  if perl -e 'alarm 8; exec @ARGV' rtl_test -t 2>&1 | grep -q "Found 1"; then echo "dongle OK"; else echo "NO DONGLE FOUND - unplug it, plug it back in, then run this again (the dashboard starts anyway)"; fi
fi
echo "=== Starting the dashboard ==="
/usr/bin/open -a Terminal "$PWD/Dashboard.command"
ok=0; for i in $(seq 1 60); do curl -s -m 2 -o /dev/null http://127.0.0.1:8765/meter && { ok=1; break; }; sleep 1; done
[ "$ok" = 1 ] && echo "$(date '+%H:%M:%S') Done. Dashboard: http://localhost:8765" || echo "The dashboard did not answer within a minute - look at its Terminal window."
# the window of the loop that was just stopped is finished: close it (only windows that show that loop's last line), so ONE window is left
perl -e 'alarm 6; exec @ARGV' osascript -e 'tell application "Terminal"' -e 'repeat with w in (every window)' -e 'try' -e 'set t to selected tab of w' \
  -e 'if (busy of t is false) and (contents of t contains "dashboard stopped (stop_loop)") then close w' -e 'end try' -e 'end repeat' -e 'end tell' >/dev/null 2>&1
exit 0
