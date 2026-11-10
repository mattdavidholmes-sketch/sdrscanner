#!/bin/bash
# SDR Scanner - the one Terminal window. Keep it open: closing it stops the app (the login watchdog re-opens it within 3 minutes).
# If the dashboard program ever crashes it is started again after 2 s (an on-purpose restart from the app does its own re-exec).
# Everything the app prints also goes to dashboard.log. caffeinate keeps the Mac awake while the scanner runs.
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:$PATH"
export PYTHONUNBUFFERED=1
# only ONE loop: if another Dashboard.command window is already running, this window just follows the log
others=$(pgrep -f "Dashboard.command" | grep -v "^$$\$" | grep -v "^$PPID\$" | wc -l | tr -d ' ')
if [ "$others" -gt 0 ] || curl -s -m 2 -o /dev/null http://127.0.0.1:8765/meter; then
  echo "SDR Scanner is already running in another window - this one follows the log (Ctrl-C closes only this view)."
  exec tail -n 30 -f dashboard.log
fi
pkill -f "dashboard2.py" 2>/dev/null; sleep 1
while true; do
  [ -f dashboard.log ] && [ "$(stat -f %z dashboard.log 2>/dev/null || echo 0)" -gt 20000000 ] && { tail -c 2000000 dashboard.log > dashboard.log.tmp && mv dashboard.log.tmp dashboard.log; }
  python3 _helpers/ensure_monitor_hook.py 2>/dev/null | tee -a dashboard.log     # keeps the Monitor card / fastscan / follow hooks in place
  caffeinate -i -s python3 dashboard2.py 2>&1 | tee -a dashboard.log
  code=${PIPESTATUS[0]}
  [ -f stop_loop ] && { rm -f stop_loop; echo "dashboard stopped (stop_loop)"; break; }
  echo "$(date '+%m-%d %H:%M:%S')  dashboard exited (code $code) - restarting in 2 s" | tee -a events.log dashboard.log
  sleep 2
done
