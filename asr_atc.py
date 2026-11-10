#!/usr/bin/env python3
"""Air-traffic-control speech-to-text: Whisper medium.en FINE-TUNED ON ATC AUDIO (jacktol/whisper-medium.en-fine-tuned-for-ATC,
ATCO2 + UWB-ATCC; reported 94.6% -> 15.1% word error rate against the stock model) through faster-whisper (CTranslate2, int8 on the CPU).
Usage: python3 asr_atc.py clip.wav [...]   -> one JSON line per file: {"file":..., "text":..., "secs":...}
Install: _launchers/Install ATC transcription.command  (pip install faster-whisper; the model, ~1.5 GB, downloads on first use)."""
import json, os, subprocess, sys, tempfile, time, wave

def _fix_ssl():
    """(v130) The python.org build of Python 3.13 on the Mac ships without root certificates, so every https call from the
    dashboard (OpenSky, adsb.fi, Flightradar24 ...) failed with CERTIFICATE_VERIFY_FAILED. Use certifi's bundle when it is
    installed, else macOS's own /etc/ssl/cert.pem - verification stays on either way."""
    import ssl
    for cafile in ([__import__("certifi").where()] if _has("certifi") else []) + ["/etc/ssl/cert.pem", "/private/etc/ssl/cert.pem"]:
        if os.path.exists(cafile):
            ssl._create_default_https_context = lambda cafile=cafile: ssl.create_default_context(cafile=cafile); return cafile
def _has(mod):
    import importlib.util; return importlib.util.find_spec(mod) is not None
try: SSL_CA = _fix_ssl()
except Exception: SSL_CA = None

MODEL = os.environ.get("ATC_MODEL", "jacktol/whisper-medium.en-fine-tuned-for-ATC-faster-whisper")
# Toronto airspace vocabulary: the thesis behind these models measured ~5 WER points from a domain prompt alone.
PROMPT = ("Air traffic control, Toronto Pearson tower, ground, departure, arrival, Toronto Centre, Billy Bishop, "
          "Air Canada, Jazz, WestJet, Porter, Flair, Sunwing, Air Transat, Swoop, Delta, United, American, Speedbird, "
          "runway two four left, two four right, two three, zero five, zero six left, zero six right, three three left, three three right, "
          "ILS, visual, VFR, IFR, squawk, altimeter, wind, cleared for takeoff, cleared to land, line up and wait, hold short, taxi via, "
          "contact departure, climb and maintain, descend and maintain, flight level, heading, knots, feet, "
          "alpha bravo charlie delta echo foxtrot golf hotel india juliett kilo lima mike november oscar papa quebec romeo sierra tango "
          "uniform victor whiskey x-ray yankee zulu, niner, tree, fife.")

ADSB_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag", "adsb.json")
def adsb_callsigns(max_age=300):
    """Aircraft currently over the GTA (OpenSky, anonymous API, cached 5 min): their callsigns prime the recogniser so
    'Jazz four seven two' is heard as a callsign and not random words. Any failure -> empty list, never an error."""
    try:
        import json as _j
        if os.path.exists(ADSB_CACHE) and time.time() - os.path.getmtime(ADSB_CACHE) < max_age:
            return _j.load(open(ADSB_CACHE)).get("callsigns", [])
        import urllib.request
        url = "https://opensky-network.org/api/states/all?lamin=43.3&lomin=-80.3&lamax=44.3&lomax=-78.7"
        with urllib.request.urlopen(url, timeout=8) as r: data = _j.load(r)
        cs = sorted({(st[1] or "").strip() for st in (data.get("states") or []) if st and (st[1] or "").strip()})
        os.makedirs(os.path.dirname(ADSB_CACHE), exist_ok=True)
        _j.dump({"t": time.time(), "callsigns": cs, "n": len(cs)}, open(ADSB_CACHE, "w"))
        return cs
    except Exception:
        return []

AIRLINES = {"ACA": "Air Canada", "JZA": "Jazz", "WJA": "WestJet", "POE": "Porter", "FLE": "Flair", "SWG": "Sunwing", "TSC": "Air Transat",
            "WSW": "Swoop", "DAL": "Delta", "UAL": "United", "AAL": "American", "BAW": "Speedbird", "ROU": "Rouge", "CJT": "Cargojet",
            "FDX": "FedEx", "UPS": "UPS", "SKV": "Sky Regional", "KLM": "KLM", "DLH": "Lufthansa", "AFR": "Air France", "EIN": "Shamrock",
            "JBU": "JetBlue", "SWA": "Southwest", "ASA": "Alaska", "GGN": "Air Georgian", "ETD": "Etihad", "UAE": "Emirates", "QTR": "Qatari"}
def spoken(cs):
    """ACA123 -> 'Air Canada one two three' (how the controller says it)."""
    import re as _re
    m = _re.match(r"([A-Z]{3})(\d+)([A-Z]*)$", cs)
    if not m: return cs
    digits = " ".join({"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "niner"}[d] for d in m.group(2))
    return f"{AIRLINES.get(m.group(1), m.group(1))} {digits}{(' ' + m.group(3)) if m.group(3) else ''}"

def to16k(path):
    sox = "/opt/homebrew/bin/sox" if os.path.exists("/opt/homebrew/bin/sox") else "sox"
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
    r = subprocess.run([sox, path, "-r", "16000", "-c", "1", "-b", "16", tmp, "highpass", "300", "lowpass", "3400", "gain", "-n", "-3"],
                       capture_output=True)
    return (tmp, tmp) if r.returncode == 0 else (path, None)

def main(files):
    from faster_whisper import WhisperModel
    model = WhisperModel(MODEL, device="cpu", compute_type="int8", cpu_threads=max(2, (os.cpu_count() or 4) // 2))
    live = adsb_callsigns()
    prompt = PROMPT + (" Aircraft on frequency now: " + ", ".join(spoken(c) for c in live[:40]) + "." if live else "")
    for f in files:
        t0 = time.time(); p, tmp = to16k(f)
        try:
            segs, info = model.transcribe(p, language="en", beam_size=5, temperature=0.0, initial_prompt=prompt,
                                          condition_on_previous_text=False, vad_filter=True,
                                          vad_parameters=dict(min_silence_duration_ms=400), no_speech_threshold=0.6)
            text = " ".join(s.text.strip() for s in segs).strip()
            print(json.dumps({"file": f, "text": text, "secs": round(time.time() - t0, 1)}), flush=True)
        except Exception as e:
            print(json.dumps({"file": f, "error": repr(e)}), flush=True)
        finally:
            if tmp:
                try: os.remove(tmp)
                except OSError: pass

if __name__ == "__main__":
    if len(sys.argv) < 2: print("usage: asr_atc.py clip.wav [...]"); sys.exit(1)
    main(sys.argv[1:])
