#!/bin/bash
# SDR Scanner service loop - run by launchd (com.sdrscanner.dashboard). Keeps ONE dashboard alive, re-applies the hooks before
# every start, rotates the log, and keeps the Mac awake (caffeinate) while it runs. Output: dashboard.log in this folder.
cd "$(dirname "$0")/.."
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
export PYTHONUNBUFFERED=1
LOG="dashboard.log"
log(){ echo "$(date '+%m-%d %H:%M:%S')  service: $*" | tee -a events.log >> "$LOG"; }
# a Terminal-started Dashboard.command loop must not fight the service: ask it to stop
if pgrep -f "Dashboard.command" >/dev/null; then touch stop_loop; pkill -f "python3 dashboard2.py"; sleep 3; rm -f stop_loop; fi
pkill -f "python3 dashboard2.py" 2>/dev/null; sleep 1
log "started (pid $$)"
while true; do
  [ -f "$LOG" ] && [ "$(stat -f %z "$LOG" 2>/dev/null || echo 0)" -gt 20000000 ] && { tail -c 2000000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"; }
  python3 _helpers/ensure_monitor_hook.py >> "$LOG" 2>&1
  if [ -f service_stop ]; then rm -f service_stop; log "stopped (service_stop)"; exit 0; fi
  caffeinate -i -s python3 dashboard2.py >> "$LOG" 2>&1
  code=$?
  log "dashboard exited (code $code) - restarting in 3 s"
  sleep 3
done
