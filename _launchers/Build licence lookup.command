#!/bin/bash
# Downloads ISED's public licence database (who is licensed on which frequency) and builds licences_gta.csv
# for 50 km around Mississauga. Re-run every few months. Takes a few minutes (large downloads).
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:$PATH"
python3 tafl_lookup.py --show
echo; echo "Done - restart the dashboard (Restart dashboard.command) to use the new names."
sleep 10
exit 0
