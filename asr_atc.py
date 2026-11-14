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
    """Aircraft in the air around Toronto right now: their callsigns prime the recogniser so 'Jazz four seven two' is heard as a
    callsign and not random words. (v210) Asked of the app itself first - it already tracks every aircraft within 60 nm, nearest
    first, and that list does not depend on OpenSky letting an anonymous caller in. Then OpenSky (cached 5 min). Any failure ->
    empty list, never an error."""
    try:
        import json as _j, urllib.request as _u
        with _u.urlopen("http://127.0.0.1:8765/api/aircraft", timeout=4) as r: a = _j.load(r).get("aircraft") or []
        cs = [(x.get("callsign") or "").strip() for x in sorted(a, key=lambda x: x.get("km") or 1e9) if not x.get("on_ground")]
        cs = [c for i, c in enumerate(cs) if c and c not in cs[:i]]
        if cs: return cs
    except Exception: pass
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
            "JBU": "JetBlue", "SWA": "Southwest", "ASA": "Alaska", "GGN": "Air Georgian", "ETD": "Etihad", "UAE": "Emirates", "QTR": "Qatari",
            # (v210) more of the telephony names heard at Pearson
            "WEN": "Encore", "MAL": "Morningstar", "THY": "Turkish", "ELY": "El Al", "TAP": "Air Portugal", "ICE": "Iceair", "AMX": "Aeromexico",
            "CPA": "Cathay", "KAL": "Korean Air", "ANA": "All Nippon", "EVA": "Eva", "AIC": "Air India", "PIA": "Pakistan", "LOT": "LOT", "SWR": "Swiss",
            "AUA": "Austrian", "SAS": "Scandinavian", "ETH": "Ethiopian", "MSR": "Egyptair", "RJA": "Jordanian", "SVA": "Saudia", "AVA": "Avianca",
            "CMP": "Copa", "CES": "China Eastern", "CHH": "Hainan", "CCA": "Air China", "EDV": "Endeavor", "RPA": "Brickyard", "SKW": "SkyWest",
            "ENY": "Envoy", "JIA": "Blue Streak", "PDT": "Piedmont", "GJS": "Lindbergh"}
def spoken(cs):
    """ACA123 -> 'Air Canada one two three' (how the controller says it)."""
    import re as _re
    m = _re.match(r"([A-Z]{3})(\d+)([A-Z]*)$", cs)
    if not m: return cs
    digits = " ".join({"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "niner"}[d] for d in m.group(2))
    return f"{AIRLINES.get(m.group(1), m.group(1))} {digits}{(' ' + m.group(3)) if m.group(3) else ''}"

_NUM = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "tree": "3", "four": "4", "five": "5", "fife": "5", "six": "6", "seven": "7", "eight": "8",
        "nine": "9", "niner": "9", "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
        "eighteen": "18", "nineteen": "19"}
_TENS = {"twenty": "2", "thirty": "3", "forty": "4", "fifty": "5", "sixty": "6", "seventy": "7", "eighty": "8", "ninety": "9"}
# words that belong to a call and are never a mangled airline name
_ATC = set("""heading runway level flight climb climbing descend descending maintain maintaining thousand hundred to for the and direct contact departure arrival
tower ground centre center toronto terminal good day morning evening afternoon night from on with at of turn left right cleared clear land takeoff approach ils visual
speed knots feet roger thanks thank you bye hello is it we are now out passing through leaving reaching up down vectors expect hold short line wait taxi via squawk
altimeter wind traffic radar identified heavy super confirm affirm negative request requesting standby go ahead say again correction approved unable wilco check in a an
that this i course decimal point frequency over continue proceed cross behind follow number sir ma'am please when ready report established localizer glidepath final
alpha bravo charlie delta echo foxtrot golf hotel india juliett juliet kilo lima mike november oscar papa quebec romeo sierra tango uniform victor whiskey xray x-ray yankee zulu""".split())
_GUARD = {"squawk", "squawking", "altimeter", "heading", "runway", "level", "decimal", "point", "frequency", "contact"}
def fix_callsigns(text, live):
    """(v226) Put the airline's radio name right where the flight number says which aircraft it is.
    The model learned its airlines from European recordings: on 13 Nov, 07:28-07:30, Envoy 3524 came out as "Envoy" once and
    then as "on boeing three five twenty four", "voz 3524" and "aero flot three five two four". The number it gets right.
    Rule: a flight number of four digits or more, written as a number of its own, that exactly ONE aircraft in the air carries
    (`live`: their callsigns), not after "squawk / altimeter / heading / runway / level / decimal / frequency / contact" and not a
    count of thousands or hundreds. Then the one or two words in front of it, if they are neither part of a call's vocabulary
    nor any airline's name, are that airline's name mangled, and are replaced. A DIFFERENT airline's name in front is left as
    written (it may be right: an aircraft that just left the list). Three-digit numbers are left alone: on the same morning
    "four five two" was both an Air Canada in the air and a Jazz parked at the gate.
    Also: a known name the model wrote in two words in front of a number ("morning star eight zero eight two").
    -> (text, [{"callsign", "name", "was"}]); the text is returned untouched when nothing applies."""
    import re as _re
    if not text or not live: return text, []
    toks = text.split(); norm = [_re.sub(r"[^a-z0-9'-]", "", t.lower()) for t in toks]
    tel = {}; bynum = {}
    for cs in live:
        m = _re.match(r"([A-Z]{3})(\d+)[A-Z]*$", cs or "")
        if not m or m.group(1) not in AIRLINES: continue
        tel[cs] = AIRLINES[m.group(1)]; n = m.group(2).lstrip("0")
        if len(n) >= 4: bynum.setdefault(n, []).append(cs)
    names = {w for v in AIRLINES.values() for w in v.lower().split()}
    # number runs: (first token, one past the last, digits)
    runs = []; i = 0
    while i < len(norm):
        j = i; d = ""
        while j < len(norm):
            w = norm[j]
            if w.isdigit(): d += w
            elif w in _NUM: d += _NUM[w]
            elif w in _TENS:
                if j + 1 < len(norm) and norm[j + 1] in _NUM and len(_NUM[norm[j + 1]]) == 1 and norm[j + 1] not in ("zero", "oh"): d += _TENS[w] + _NUM[norm[j + 1]]; j += 1
                else: d += _TENS[w] + "0"
            else: break
            j += 1
        if j > i: runs.append((i, j, d)); i = j
        else: i += 1
    fixes = []; edits = []                                              # edits: (first token, one past the last, replacement)
    for a, b, d in runs:
        if b < len(norm) and norm[b] in ("thousand", "hundred"): continue
        if a and norm[a - 1] in _GUARD: continue
        c = bynum.get(d.lstrip("0"))
        if c and len(c) == 1 and a:
            name = tel[c[0]]; k = a
            while k > 0 and a - k < 2 and norm[k - 1] and norm[k - 1] not in _ATC and norm[k - 1] not in names and norm[k - 1] not in _NUM and norm[k - 1] not in _TENS and not norm[k - 1].isdigit(): k -= 1
            if k < a: edits.append((k, a, name)); fixes.append({"callsign": c[0], "name": name, "was": " ".join(toks[k:a])})
    # a known one-word name written in two words, in front of a number, when an aircraft of that airline is in the air
    one = {v.lower(): v for v in set(tel.values()) if " " not in v and len(v) >= 6}
    for a, b, d in runs:
        if a >= 2 and (norm[a - 2] + norm[a - 1]) in one and not any(e[0] <= a - 1 < e[1] for e in edits):
            v = one[norm[a - 2] + norm[a - 1]]; edits.append((a - 2, a, v)); fixes.append({"callsign": "", "name": v, "was": " ".join(toks[a - 2:a])})
    if not edits: return text, []
    for k, a, name in sorted(edits, reverse=True): toks[k:a] = [name]
    return " ".join(toks), fixes

def to16k(path):
    sox = "/opt/homebrew/bin/sox" if os.path.exists("/opt/homebrew/bin/sox") else "sox"
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
    r = subprocess.run([sox, path, "-r", "16000", "-c", "1", "-b", "16", tmp, "highpass", "300", "lowpass", "3400", "gain", "-n", "-3"],
                       capture_output=True)
    return (tmp, tmp) if r.returncode == 0 else (path, None)

def load16k(path):
    """The clip as 16 kHz mono float32, read here. faster-whisper would decode the file itself through PyAV, and its 1.2 release
    asks PyAV for an option this Mac's PyAV does not know ("open() got an unexpected keyword argument 'metadata_errors'" - every
    clip failed on the day the engine was installed, 11 Nov). A WAV needs no decoder."""
    import numpy as np
    with wave.open(path, "rb") as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth(); raw = w.readframes(w.getnframes())
    if sw != 2: raise ValueError(f"16-bit WAV expected, got {8 * sw}-bit")
    a = np.frombuffer(raw[:len(raw) - len(raw) % (2 * ch)], dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1: a = a.reshape(-1, ch).mean(axis=1)
    if sr != 16000:
        from math import gcd
        from scipy.signal import resample_poly
        g = gcd(16000, sr); a = resample_poly(a, 16000 // g, sr // g).astype(np.float32)
    return a

def main(files):
    from faster_whisper import WhisperModel
    model = WhisperModel(MODEL, device="cpu", compute_type="int8", cpu_threads=max(2, (os.cpu_count() or 4) // 2))
    live = adsb_callsigns()
    prompt = PROMPT + (" Aircraft on frequency now: " + ", ".join(spoken(c) for c in live[:40]) + "." if live else "")
    for f in files:
        t0 = time.time(); p, tmp = to16k(f)
        try:
            segs, info = model.transcribe(load16k(p), language="en", beam_size=5, temperature=0.0, initial_prompt=prompt,
                                          condition_on_previous_text=False, vad_filter=True,
                                          vad_parameters=dict(min_silence_duration_ms=400), no_speech_threshold=0.6, word_timestamps=True)
            segs = list(segs)
            # (v210) a stretch the model itself was not sure of is left out: its mean word probability is under 0.4 where it wrote a
            # tail onto a call that had ended ("... cleared to land on cimber three three zero") or a single word over a noise burst
            def _p(s): return sum(w.probability for w in (s.words or [])) / max(1, len(s.words or []))
            segs = [s for s in segs if (s.words and _p(s) >= 0.4)]
            text = " ".join(s.text.strip() for s in segs).strip()
            # the model learned its airlines from European and Asian recordings and writes "air china" for the airline it hears
            # all day at Pearson; unless an Air China flight really is in the air here, that is Air Canada
            if "air china" in text.lower() and not any(c.startswith("CCA") for c in live):
                import re as _re; text = _re.sub(r"(?i)air china", "air canada", text)
            # (v203) how sure the model was: the mean probability it gave the words it wrote. On 40 clips of 128.800 (11 Nov) the two
            # transmissions it read the same way through two different receive chains scored 0.64-0.75; what it wrote on carriers
            # with no speech on them at all ("United four five zero", "heading flight level eight three") scored 0.28-0.61, or was
            # a callsign lifted from the list of aircraft in the prompt. The app marks low figures; it does not hide them.
            heard = text; text, fixed = fix_callsigns(text, live)     # (v226) the airline's name from the aircraft in the air, where the flight number pins it down
            words = [w for s in segs for w in (s.words or [])]
            conf = round(sum(w.probability for w in words) / len(words), 2) if words else None
            out = {"file": f, "text": text, "conf": conf, "words": len(words), "secs": round(time.time() - t0, 1)}
            if fixed: out["heard"] = heard; out["fixed"] = fixed
            print(json.dumps(out), flush=True)
        except Exception as e:
            print(json.dumps({"file": f, "error": repr(e)}), flush=True)
        finally:
            if tmp:
                try: os.remove(tmp)
                except OSError: pass

if __name__ == "__main__":
    if len(sys.argv) < 2: print("usage: asr_atc.py clip.wav [...]"); sys.exit(1)
    main(sys.argv[1:])
