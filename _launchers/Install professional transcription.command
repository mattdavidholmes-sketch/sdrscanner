#!/bin/bash
# Installs a second, stronger speech-to-text engine (NVIDIA Parakeet TDT 0.6B v2, local, Apple-silicon MLX) and the
# large whisper model. The dashboard then runs BOTH on every clip and marks transcripts the two engines agree on.
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/Library/Frameworks/Python.framework/Versions/3.13/bin:$PATH"
echo "=== Parakeet (parakeet-mlx) ==="
python3 -m pip install --upgrade parakeet-mlx
echo "=== first run downloads the model (~1.2 GB) ==="
python3 -c "from parakeet_mlx import from_pretrained; m=from_pretrained('mlx-community/parakeet-tdt-0.6b-v2'); print('parakeet ready')"
echo "=== whisper large-v3-turbo (~1.6 GB) ==="
mkdir -p models
[ -s models/ggml-large-v3-turbo.bin ] || curl -L --progress-bar -o models/ggml-large-v3-turbo.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin
ls -la models/ | tail -3
echo "Done. The dashboard uses both engines from the next clip on."
sleep 8
exit 0
