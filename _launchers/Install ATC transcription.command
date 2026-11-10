#!/bin/bash
# Installs the air-traffic-control speech-to-text engine (faster-whisper + Whisper medium.en fine-tuned on ATC audio, ~1.5 GB download).
cd "$(dirname "$0")/.."; export PATH="/opt/homebrew/bin:$PATH"
echo "== installing faster-whisper"; python3 -m pip install -q --upgrade faster-whisper huggingface_hub 2>&1 | tail -2
echo "== downloading the ATC model (first run only, ~1.5 GB)"
python3 - <<'PY'
from faster_whisper import WhisperModel
m = WhisperModel("jacktol/whisper-medium.en-fine-tuned-for-ATC-faster-whisper", device="cpu", compute_type="int8")
print("model ready")
PY
echo "== done. The dashboard uses it for every airband clip from now on (asr_atc.py)."
