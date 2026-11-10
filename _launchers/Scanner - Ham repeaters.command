#!/bin/bash
# Live radio scanner - Ham repeaters. Hops the channels, stops on whoever is talking, plays it, moves on.
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:$PATH"
python3 sdr_scan.py live ham2m
