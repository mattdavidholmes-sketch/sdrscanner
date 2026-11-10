#!/bin/bash
# Installs Icecast (local streaming server). SDRTrunk sends its decoded audio to it; the dashboard relays it to every browser as /sdrt_audio.
cd "$(dirname "$0")/.."; export PATH="/opt/homebrew/bin:$PATH"
echo "== installing icecast"; brew list icecast >/dev/null 2>&1 || brew install icecast
ls -l /opt/homebrew/bin/icecast && echo "== done: the dashboard starts Icecast automatically the next time SDRTrunk is used."
