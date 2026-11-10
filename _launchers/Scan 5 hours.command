#!/bin/bash
# YYZ arrivals + tower and every Toronto/Mississauga 2m & 70cm repeater, 5 hour watch. Ctrl-C ends early and still writes results.
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:$PATH"
KNOWN=""
for f in *.csv ~/.chirp/*.csv ~/Desktop/*.csv; do
  [ -f "$f" ] && head -1 "$f" 2>/dev/null | grep -q "^Location,Name,Frequency" && KNOWN="$f" && break
done
[ -n "$KNOWN" ] && echo "Comparing against existing CHIRP file: $KNOWN" && KA="--known $KNOWN"
python3 sdr_scan.py scan --hours 5 --bands toronto --location "Toronto" $KA
read -n 1 -s -r -p "Press any key to close."
