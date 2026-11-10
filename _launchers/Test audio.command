#!/bin/bash
# Audio + dongle test. Beep, then 25 s each on VA3RCI CN Tower 444.8, VE3SKY 146.985, Pearson Departure 128.8, squelch OFF, volume boosted.
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:$PATH"
pkill -f rtl_fm 2>/dev/null; pkill -f rtl_power 2>/dev/null; sleep 1
echo "=== 1. Beep ==="; play -q -n synth 1 sine 660
for FM in "444.8 fm" "146.985 fm" "128.8 am"; do
  set -- $FM; F=$1; M=$2
  echo; echo "=== $F MHz $M, squelch OFF, 25 s - static at minimum, voices if anyone is talking ==="
  rtl_fm -f ${F}M -M $M -s 24k -g 49.6 -l 0 -E dc - | play -q -r 24k -t raw -e s -b 16 -c 1 - vol 4 &
  sleep 25
  pkill -f rtl_fm; sleep 1
done
echo; echo "Done."; read -n 1 -s -r -p "Press any key to close."
