#!/bin/bash
# Pearson arrivals + tower and the FRS/GMRS channels, 2 hour watch, ranked by how strong and how busy each one is at the car antenna.
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:$PATH"
KNOWN=""
for f in *.csv ~/.chirp/*.csv ~/Desktop/*.csv; do
  [ -f "$f" ] && head -1 "$f" 2>/dev/null | grep -q "^Location,Name,Frequency" && KNOWN="$f" && break
done
[ -n "$KNOWN" ] && echo "Comparing against existing CHIRP file: $KNOWN" && KA="--known $KNOWN"
python3 sdr_scan.py scan --hours 2 --bands airfrs --location "Mississauga" $KA
read -n 1 -s -r -p "Press any key to close."
