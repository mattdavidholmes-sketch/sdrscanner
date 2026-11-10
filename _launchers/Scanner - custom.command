#!/bin/bash
# Live scanner on your own list of frequencies (MHz, space separated).
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:$PATH"
read -p "Frequencies in MHz, space separated (e.g. 118.7 124.475 125.4): " F
python3 sdr_scan.py live custom $F
