#!/bin/bash
# Installs whisper.cpp (speech to text, runs on the Mac, no cloud) and the small English model, so the dashboard
# can write a text blurb of what was said on every recording.
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:$PATH"
echo "Installing whisper-cpp with Homebrew (a few minutes the first time) ..."
brew list whisper-cpp >/dev/null 2>&1 || brew install whisper-cpp
mkdir -p models
if [ ! -s models/ggml-small.en.bin ]; then
  echo "Downloading the small English model (~470 MB) ..."
  curl -L --progress-bar -o models/ggml-small.en.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin
fi
ls -la models/
echo
if command -v whisper-cli >/dev/null 2>&1; then echo "whisper-cli installed: $(command -v whisper-cli)"; else echo "whisper-cli NOT found - check the brew output above"; fi
echo "Done. Restart the dashboard (Restart dashboard.command) and transcripts appear under Recordings."
sleep 8
exit 0
