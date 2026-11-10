#!/bin/bash
# The dongle sometimes wedges (rtl_power stops writing, "PLL not locked"). This force-kills the stuck
# reader, lets the running scan retry its slice, and checks the dongle answers.
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:$PATH"
echo "killing stuck rtl_power / rtl_fm ..."
pkill -9 -x rtl_power; pkill -9 -x rtl_fm; pkill -9 -x rtl_tcp; pkill -9 -x sox
sleep 4
echo "testing the dongle for 3 s ..."
rtl_test -t 2>&1 | head -12 &
sleep 4; pkill -x rtl_test
echo
echo "If it printed 'Found 1 device(s)' the dongle is back and the scan will resume on its own."
echo "If it printed 'No supported devices' unplug the dongle, plug it in again, then run Resume scan.command."
sleep 6
exit 0
