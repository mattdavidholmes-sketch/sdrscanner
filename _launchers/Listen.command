#!/bin/bash
# Listen to one frequency through the Mac speakers using the SDR.
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:$PATH"
read -p "Frequency in MHz (e.g. 146.520): " F
python3 sdr_scan.py listen "$F"
