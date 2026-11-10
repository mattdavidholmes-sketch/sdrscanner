#!/bin/bash
# Restarts only the dashboard (the scan keeps running) in ONE Terminal window via Dashboard.command (crash-restart loop).
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:$PATH"
touch stop_loop; pkill -f "python3 dashboard2.py"; pkill -x rtl_fm; pkill -x sox; sleep 2; rm -f stop_loop
osascript -e 'tell application "Terminal" to do script "bash \"'"$PWD"'/Dashboard.command\""' >/dev/null
sleep 1; exit 0
