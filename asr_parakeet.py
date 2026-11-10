#!/usr/bin/env python3
"""Second, independent speech-to-text engine: NVIDIA Parakeet TDT 0.6B v2 running locally on Apple silicon (parakeet-mlx).
Tops the open ASR leaderboards for English and is far better than whisper-small on noisy radio audio.
Usage: python3 asr_parakeet.py clip.wav [clip2.wav ...]   -> prints one line of text per file (JSON lines)
Install: Install professional transcription.command"""
import json, sys, wave, os, tempfile, subprocess

def to16k(path):
    """Parakeet wants 16 kHz mono; our clips are 24 kHz. Use sox if present, else leave as is (the library resamples)."""
    try:
        with wave.open(path) as w:
            if w.getframerate() == 16000: return path, None
    except Exception: return path, None
    sox = "/opt/homebrew/bin/sox" if os.path.exists("/opt/homebrew/bin/sox") else "sox"
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
    r = subprocess.run([sox, path, "-r", "16000", "-c", "1", "-b", "16", tmp, "highpass", "200", "compand", "0.05,0.3", "-60,-60,-30,-15,0,-8", "0", "-60", "0.1"], capture_output=True)
    return (tmp, tmp) if r.returncode == 0 else (path, None)

def transcribe_no_ffmpeg(model, path):
    """parakeet-mlx loads audio through ffmpeg; without it, read the 16 kHz wav ourselves and feed the mel spectrogram."""
    import numpy as np, mlx.core as mx
    from parakeet_mlx.audio import get_logmel
    with wave.open(path) as w:
        assert w.getframerate() == 16000, "need 16 kHz"
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    mel = get_logmel(mx.array(a), model.preprocessor_config)
    res = model.generate(mel)
    r = res[0] if isinstance(res, (list, tuple)) else res
    return (getattr(r, "text", "") or "").strip()

def main():
    from parakeet_mlx import from_pretrained
    model = from_pretrained("mlx-community/parakeet-tdt-0.6b-v2")
    for p in sys.argv[1:]:
        f, tmp = to16k(p)
        try:
            try:
                res = model.transcribe(f); text = (res.text or "").strip()
            except Exception as e:
                if "FFmpeg" not in repr(e) and "ffmpeg" not in repr(e): raise
                text = transcribe_no_ffmpeg(model, f)
            print(json.dumps({"file": p, "text": text}), flush=True)
        except Exception as e:
            print(json.dumps({"file": p, "error": repr(e)}), flush=True)
        finally:
            if tmp:
                try: os.remove(tmp)
                except OSError: pass

if __name__ == "__main__":
    main()
