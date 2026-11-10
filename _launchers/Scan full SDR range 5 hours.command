#!/bin/bash
# Whole RTL-SDR range, 24 MHz to 1.7 GHz, 5 hour watch. Finds everything the dongle can hear; UV-32-tunable hits go to the radio files, the rest to SDR++ bookmarks.
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:$PATH"
KNOWN=""
for f in *.csv ~/.chirp/*.csv ~/Desktop/*.csv; do
  [ -f "$f" ] && head -1 "$f" 2>/dev/null | grep -q "^Location,Name,Frequency" && KNOWN="$f" && break
done
[ -n "$KNOWN" ] && echo "Comparing against existing CHIRP file: $KNOWN" && KA="--known $KNOWN"
python3 sdr_scan.py scan --hours 5 --bands full --location "GTA" $KA
read -n 1 -s -r -p "Press any key to close."
