#!/usr/bin/env python3
"""Live dashboard for a running scan. Serves http://localhost:8765, refreshes every 10 s,
and streams any listed frequency to the browser (pauses the scan while listening).
   python3 dashboard.py            -> watches the newest scan_* folder
   python3 dashboard.py scan_x     -> watches that folder"""
import csv, glob, html, json, os, re, shutil, signal, socket, struct, subprocess, sys, threading, time, datetime as dt
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sdr_scan

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8765
RATE = 24000
RESUME_AFTER = 30         # seconds after Stop before the scan gets the dongle back
_lock = threading.Lock()
_player = {"proc": None, "mhz": None, "since": 0, "where": None, "gate": None, "play": None, "rec": None, "session": None}
import audio as AU
USER_WANTS = threading.Event()        # set the moment a play request arrives: auditions stop and stay out of the way
LAST_USER_PLAY = [0.0]; PRIO_LAST = [0.0]; BG_STATE = {"what": "", "since": 0.0}; AUDIO_LAST = [0.0]
def say(msg):
    """One place for every event worth seeing: it prints to the dashboard's own terminal window (the only one)."""
    try: print(f"{dt.datetime.now():%H:%M:%S}  {msg}", flush=True)
    except Exception: pass
    try:                                                     # and to events.log, so a closed window never loses the story
        with open(os.path.join(HERE, "events.log"), "a") as f: f.write(f"{dt.datetime.now():%m-%d %H:%M:%S}  {msg}\n")
    except Exception: pass

def newest_scan():
    if len(sys.argv) > 1: return os.path.join(HERE, sys.argv[1])
    dirs = sorted(glob.glob(os.path.join(HERE, "scan_*")), key=lambda d: (os.path.basename(d)[:20], os.path.getmtime(d)))   # by the timestamp in the name: touching a folder (PAUSE flag) never reorders them
    return dirs[-1] if dirs else None

def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close(); return ip
    except Exception: return "localhost"

def scan_running():
    try: return subprocess.call(["pgrep", "-f", "sdr_scan.py scan"], stdout=subprocess.DEVNULL) == 0
    except Exception: return False

# ---------------------------------------------------------------- dongle hand-off
def pause_flag():
    f = newest_scan(); return os.path.join(f, "PAUSE") if f else os.path.join(HERE, "PAUSE")

SCAN_HOURS = 5
_LAST_SCAN_START = {"key": None, "t": 0.0, "folder": None}
def start_scan(bands_key, label, why=""):
    """Stop the running scan (if any) and start a new one on the given band set, in its own folder. Returns folder name.
    (v141) The same band set asked for again within 15 s (a chip tapped three times) keeps the sweep that just started."""
    if _LAST_SCAN_START["key"] == bands_key and time.time() - _LAST_SCAN_START["t"] < 15 and scan_running():
        say(f"sweep: {label} already starting - ignored the repeat"); return _LAST_SCAN_START["folder"]
    _LAST_SCAN_START.update(key=bands_key, t=time.time())
    say(f"sweep: starting {label} ({bands_key}){' - ' + why if why else ''}")
    subprocess.call(["pkill", "-f", "sdr_scan.py scan"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        if not scan_running(): break
        time.sleep(0.25)
    subprocess.call(["pkill", "-x", "rtl_power"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.call(["pkill", "-f", "fastscan.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    tag = "".join(c for c in label.lower().replace("/", "").replace(" ", "-") if c.isalnum() or c == "-")
    folder = f"scan_{dt.datetime.now():%Y-%m-%d_%H%M}" + (f"_{tag}" if tag and tag != "all" else "")
    os.makedirs(os.path.join(HERE, folder), exist_ok=True)
    log = open(os.path.join(HERE, folder, "scan.log"), "a")
    env = dict(os.environ); env["PATH"] = "/opt/homebrew/bin:" + env.get("PATH", "")
    subprocess.Popen([sys.executable, os.path.join(HERE, "sdr_scan.py"), "scan", "--hours", str(SCAN_HOURS), "--bands", bands_key, "--location", "GTA",
                      "--slice", "1" if bands_key == "all" else "3",
                      "--out", folder, "--known", os.path.join(HERE, "gta_nets_chirp.csv")], cwd=HERE, stdout=log, stderr=subprocess.STDOUT, env=env)
    _LAST_SCAN_START["folder"] = folder
    return folder

def all_pause_flags():
    """A running sweep watches the PAUSE file in ITS OWN folder, which is not always the newest one: write/remove the flag everywhere recent."""
    fl = {pause_flag()}
    try:                                                     # the folder of every sweep that is actually running (its --out argument)
        out = subprocess.run(["ps", "-axo", "command="], capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            if "sdr_scan.py" in line and " scan" in line and "--out" in line:
                a = line.split(); d = a[a.index("--out") + 1]
                if os.path.isdir(os.path.join(HERE, d)): fl.add(os.path.join(HERE, d, "PAUSE"))
    except Exception: pass
    return fl

def dongle_holders():
    return any(subprocess.call(["pgrep", "-x", n], stdout=subprocess.DEVNULL) == 0 for n in ("rtl_power", "rtl_fm", "rtl_tcp"))   # rtl_tcp: channelizer monitor / fastscan sweep

def take_dongle():
    global _resume_timer
    if _resume_timer: _resume_timer.cancel(); _resume_timer = None
    killed = dongle_holders()                             # something is using the dongle right now: it needs time to let go
    for f in all_pause_flags():
        try: open(f, "w").write(str(time.time()))
        except OSError: pass
    for _ in range(8):                                    # the scan polls the PAUSE flag every 0.25 s (v129) and stops rtl_power itself; after ~0.8 s we stop it ourselves
        if subprocess.call(["pgrep", "-x", "rtl_power"], stdout=subprocess.DEVNULL) != 0: break
        time.sleep(0.1)
    for i in range(20):                                  # still there (or a stale rtl_fm from an auto-resume): kill until the dongle is free
        subprocess.call(["pkill", "-x", "rtl_power"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.call(["pkill", "-x", "rtl_fm"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.call(["pkill", "-f", "fastscan.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)     # the sweep's engine (it closes its rtl_tcp)
        subprocess.call(["pkill", "-x", "rtl_tcp"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)          # (v169) a live user request always wins over a background
        if i >= 12: subprocess.call(["pkill", "-9", "-x", "rtl_tcp"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # sweep. (v180) SIGKILL only after 1.2 s: v169 used it after 0.3 s, which can cut rtl_tcp off
                                                                                                                         # before it has released the USB device - the kind of hand-off that leaves the dongle unclaimable
        time.sleep(0.1)
        if not dongle_holders(): break
    time.sleep(0.4 if killed else 0.15)                   # let libusb release the device (v131: 0.4 s - rtl_fm/rtl_power exit cleanly on SIGINT; make_session_retry covers the rare miss)

_resume_timer = None
def give_dongle_back(delay=0):
    """Remove the pause flag now, or after `delay` s (cancelled if a new play starts first)."""
    global _resume_timer
    if os.path.exists(os.path.join(HERE, "SDRTRUNK_HAS_DONGLE")): return      # SDRTrunk has it until the user takes it back
    if _resume_timer: _resume_timer.cancel(); _resume_timer = None
    def _go():
        for f in all_pause_flags():
            try: os.remove(f)
            except OSError: pass
    if delay:
        _resume_timer = threading.Timer(delay, _go); _resume_timer.daemon = True; _resume_timer.start()
    else: _go()

def log_tones():
    ses = _player.get("session"); m = _player.get("mhz")
    if not ses or not m or getattr(ses, "logged", False): return
    ses.logged = True
    summ = ses.tones.summary()
    if getattr(ses.gate, "data_blocks", 0) >= 20: summ = (summ + "; " if summ else "") + "DATA"
    if not summ: return
    f = newest_scan() or HERE
    with open(os.path.join(f, "tones.csv"), "a") as fh:
        fh.write(f"{dt.datetime.now():%Y-%m-%d %H:%M},{m:.4f},{summ}\n")

def stop_player():
    with _lock:
        log_tones()
        ses = _player.get("session")
        if ses: say("stopped listening")
        if ses:
            try:                                             # who stopped the user's stream, and when (stops.log)
                import traceback
                st = "".join(traceback.format_stack(limit=6)[:-1]).replace(HERE, ".")
                with open(os.path.join(HERE, "stops.log"), "a") as f: f.write(f"--- {dt.datetime.now():%H:%M:%S} stop_player {_player.get('mhz')} ({_player.get('where')}) after {time.time()-_player.get('since',time.time()):.0f} s\n{st}\n")
            except Exception: pass
            LAST_USER_PLAY[0] = time.time()                  # a stop counts as activity too: nothing background jumps in for 20 s
            try: ses.stop()
            except Exception: pass
        pl = _player.get("play")
        if pl:
            try: pl.terminate()
            except Exception: pass
        _player.update(proc=None, mhz=None, since=0, where=None, gate=None, play=None, rec=None, session=None)
    for name in ("rtl_fm", "sox", "rtl_tcp"):
        subprocess.call(["pkill", "-x", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        if subprocess.call(["pgrep", "-x", "rtl_fm"], stdout=subprocess.DEVNULL) != 0 and subprocess.call(["pgrep", "-x", "rtl_tcp"], stdout=subprocess.DEVNULL) != 0: break
        time.sleep(0.2)
    give_dongle_back(RESUME_AFTER)

FM_LOG = os.path.join(HERE, "rtl_fm.log")
def start_rtl_fm(mhz, mode, squelch, gain):
    if mode == "wbfm":    # broadcast FM: demod at 48 kHz so the 19 kHz stereo pilot stays above our low-pass; we filter + decimate to 24 kHz
        cmd = ["rtl_fm", "-f", f"{mhz:.4f}M", "-M", "wbfm", "-s", "200k", "-r", str(RATE * 2), "-g", str(gain), "-"]
    elif mode == "am":    # airband AM at 24 kHz (rtl_fm cannot upsample, so demod rate == output rate)
        cmd = ["rtl_fm", "-f", f"{mhz:.4f}M", "-M", "am", "-s", str(RATE), "-g", str(gain), "-l", str(squelch), "-E", "dc", "-"]
    else:                 # narrow FM (ham/FRS/GMRS/marine) at 24 kHz, de-emphasis, DC removed
        cmd = ["rtl_fm", "-f", f"{mhz:.4f}M", "-M", "fm", "-s", str(RATE), "-g", str(gain), "-l", str(squelch), "-E", "deemp", "-E", "dc", "-"]
    with open(FM_LOG, "a") as lf: lf.write(f"\n=== {dt.datetime.now():%H:%M:%S} {' '.join(cmd)}\n")
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=open(FM_LOG, "a"))

def last_fm_error():
    try:
        with open(FM_LOG, "rb") as fh:
            fh.seek(max(0, os.path.getsize(FM_LOG) - 3000)); tail = fh.read().decode(errors="ignore")
    except OSError: return ""
    lines = [l.strip() for l in tail.splitlines() if l.strip()]
    for l in reversed(lines):
        if any(k in l.lower() for k in ("fail", "error", "no supported", "usb", "claim", "not found", "cannot", "unable")): return l
    return ""                                                   # the last line is usually a harmless sox note - not an error

import array, math
class VoiceShaper:
    """300-3000 Hz voice band-pass (two biquads) + slow AGC + soft limiter, applied to open-squelch audio.
    Makes airband and NFM voice clearer and brings every channel to a similar loudness."""
    def __init__(self, rate, enabled=True):
        self.enabled = enabled
        # 2nd-order Butterworth HP @300 Hz and LP @3000 Hz (coefficients computed at init)
        self.hp = self._biquad("hp", 300.0, rate); self.lp = self._biquad("lp", 2800.0, rate)
        self.gain = 8.0; self.target = 9000.0
    def _biquad(self, kind, fc, fs):
        w0 = 2 * math.pi * fc / fs; c = math.cos(w0); sn = math.sin(w0); a = sn / (2 * 0.7071)
        if kind == "lp": b0 = (1 - c) / 2; b1 = 1 - c; b2 = (1 - c) / 2
        else:            b0 = (1 + c) / 2; b1 = -(1 + c); b2 = (1 + c) / 2
        a0 = 1 + a; a1 = -2 * c; a2 = 1 - a
        return {"b": (b0 / a0, b1 / a0, b2 / a0), "a": (a1 / a0, a2 / a0), "x1": 0.0, "x2": 0.0, "y1": 0.0, "y2": 0.0}
    def _run(self, f, x):
        b0, b1, b2 = f["b"]; a1, a2 = f["a"]
        y = b0 * x + b1 * f["x1"] + b2 * f["x2"] - a1 * f["y1"] - a2 * f["y2"]
        f["x2"] = f["x1"]; f["x1"] = x; f["y2"] = f["y1"]; f["y1"] = y
        return y
    def process(self, b):
        if not self.enabled: return b
        a = array.array("h", b); out = array.array("h", bytes(len(b)))
        peak = 1.0
        for i, x in enumerate(a):
            y = self._run(self.lp, self._run(self.hp, float(x)))
            out[i] = int(max(-32767, min(32767, y))); peak = max(peak, abs(y))
        # AGC: move gain toward target/peak, slowly up, quickly down
        want = min(40.0, self.target / peak)                     # rtl_fm output is quiet (noise ~400, signal ~2000 of 32767)
        self.gain += (want - self.gain) * (0.5 if want < self.gain else 0.12)
        g = self.gain
        for i, y in enumerate(out):
            v = y * g
            if v > 20000: v = 20000 + (v - 20000) * 0.15          # soft knee limiter
            elif v < -20000: v = -20000 + (v + 20000) * 0.15
            out[i] = int(max(-32767, min(32767, v)))
        return out.tobytes()

class WFMShaper:
    """Broadcast FM clean-up: 4th-order low-pass at 15 kHz (kills the 19 kHz pilot and RDS at 57 kHz aliases),
    then 2:1 decimation 48 kHz -> 24 kHz, plus a gentle limiter."""
    def __init__(self, rate_in):
        v = VoiceShaper(rate_in, enabled=True)
        self.lp1 = v._biquad("lp", 15000.0, rate_in); self.lp2 = v._biquad("lp", 15000.0, rate_in); self._run = v._run
        self.phase = 0
    def process(self, b):
        a = array.array("h", b); out = array.array("h")
        for x in a:
            y = self._run(self.lp2, self._run(self.lp1, float(x)))
            self.phase ^= 1
            if self.phase:
                if y > 24000: y = 24000 + (y - 24000) * 0.2
                elif y < -24000: y = -24000 + (y + 24000) * 0.2
                out.append(int(max(-32767, min(32767, y))))
        return out.tobytes()

NOISE_FILE = os.path.join(HERE, "noise_floor.json")
try: NOISE_FLOOR = json.load(open(NOISE_FILE))
except Exception: NOISE_FLOOR = {}
def save_noise_floor():
    try: json.dump(NOISE_FLOOR, open(NOISE_FILE, "w"))
    except Exception: pass

class AutoSquelch:
    """Self-tuning squelch on demodulated audio.
    FM: an idle channel is loud white noise; a carrier quiets it. Open when RMS drops well below the noise floor.
    AM: an idle channel is low hiss; a carrier + voice raises RMS. Open when RMS climbs well above the floor.
    The floor is learned only while the squelch is CLOSED (median of recent closed blocks), so a long
    transmission never becomes the new 'normal'."""
    def __init__(self, mode, manual=None):
        if mode == "wbfm": manual = 0                   # broadcast: never squelch
        self.mode = mode; self.manual = manual          # manual: 0-60 slider, None = auto
        self.hist = []; self.open = False; self.hang = 0; self.n = 0
        self.level = 0.0; self.floor = None; self.thresh = None
        self.tones = ToneDecoder(RATE)
        self.shaper = WFMShaper(RATE * 2) if mode == "wbfm" else VoiceShaper(RATE, enabled=True)
    def _rms(self, b):
        a = array.array("h", b)
        return math.sqrt(sum(x * x for x in a) / max(1, len(a)))
    def process(self, b):
        if self.mode == "wbfm":                                       # broadcast: no squelch, straight to the WFM shaper
            self.open = True; self.level = self._rms(b); self.floor = self.level; self.thresh = self.level
            return self.shaper.process(b)
        rms = self._rms(b); self.level = rms; self.n += 1
        self.hist.append(rms); self.hist = self.hist[-600:]           # last 60 s, open or closed
        srt = sorted(self.hist); p90 = srt[int(len(srt) * 0.9)]
        remembered = NOISE_FLOOR.get(self.mode)
        # idle noise is the LOUDEST state on FM/AM; take the louder of what we've seen and what we remember for this mode
        self.floor = max(p90, remembered or 0) if remembered else p90
        if self.n >= 30 and (remembered is None or p90 > remembered * 0.9):   # after 3 s, refresh the remembered idle level
            NOISE_FLOOR[self.mode] = max(p90, remembered or 0) if remembered else p90; save_noise_floor()
        if self.n < 3: return bytes(len(b))                            # first 0.3 s: settle, muted
        # Both FM and rtl_fm's AM: an arriving carrier QUIETS the idle hiss. Open on quieting, with hysteresis.
        # manual slider 0-60: 0 = always open, 30 = default sensitivity, 60 = needs a very strong signal.
        frac = (self.manual if self.manual is not None else 15) / 60.0
        if self.manual is not None and frac == 0:
            self.thresh = self.floor; sig = True
        else:
            drop = 0.10 + 0.40 * frac                      # 0.1 .. 0.5 ; default 15 -> 0.20 (20% quieting)
            self.thresh = self.floor * (1.0 - drop)
            if self.open: sig = rms < self.floor * (1.0 - drop * 0.5)      # stay open until half-way back to noise
            else:         sig = rms < self.thresh
            if self.mode == "am" and rms > self.floor * 2.5: sig = True   # AM: strong modulation also counts
        if sig: self.hang = 6                                         # ~0.6 s hangover
        elif self.hang > 0: self.hang -= 1
        self.open = sig or self.hang > 0
        if self.open and self.mode == "fm": self.tones.feed(b)
        return self.shaper.process(b) if self.open else bytes(len(b))

CTCSS = [67.0,69.3,71.9,74.4,77.0,79.7,82.5,85.4,88.5,91.5,94.8,97.4,100.0,103.5,107.2,110.9,114.8,118.8,123.0,127.3,131.8,136.5,141.3,146.2,
         151.4,156.7,159.8,162.2,165.5,167.9,171.3,173.8,177.3,179.9,183.5,186.2,189.9,192.8,196.6,199.5,203.5,206.5,210.7,218.1,225.7,229.1,233.6,241.8,250.3,254.1]
DCS_CODES = [23,25,26,31,32,36,43,47,51,53,54,65,71,72,73,74,114,115,116,122,125,131,132,134,143,145,152,155,156,162,165,172,174,205,212,223,225,226,243,244,245,246,251,252,255,261,263,265,266,271,274,306,311,315,325,331,332,343,346,351,356,364,365,371,411,412,413,423,431,432,445,446,452,454,455,462,464,465,466,503,506,516,523,526,532,546,565,606,612,624,627,631,632,654,662,664,703,712,723,731,732,734,743,754]

def _golay23(code_octal):
    """23-bit DCS word for an octal code: 9 data bits + 3 fixed bits (100) + 11 parity (Golay)."""
    d = int(str(code_octal), 8)
    bits = [(d >> i) & 1 for i in range(9)] + [1, 0, 0]
    # generator polynomial for DCS Golay: x^11+x^10+x^6+x^5+x^4+x^2+1 -> parity via LFSR over the 12 bits
    gen = 0b110001110101
    reg = 0
    for b in bits:
        fb = ((reg >> 10) & 1) ^ b
        reg = ((reg << 1) & 0x7FF) ^ (gen & 0x7FF if fb else 0)
    par = [(reg >> i) & 1 for i in range(11)]
    return bits + par
DCS_WORDS = {}
for c in DCS_CODES:
    w = _golay23(c)
    DCS_WORDS[tuple(w)] = f"D{c:03d}N"
    DCS_WORDS[tuple(1 - x for x in w)] = f"D{c:03d}I"

class ToneDecoder:
    """CTCSS via Goertzel over the 50 standard tones; DCS by low-passing, slicing at 134.4 bps and
    matching the 23-bit word (normal and inverted) against the standard table. Fed only while squelch is open."""
    def __init__(self, rate):
        self.rate = rate; self.buf = array.array("h"); self.ctcss = {}; self.dcs = {}; self.last = ""
        self.lp = 0.0; self.bits = []; self.samp_per_bit = rate / 134.4; self.acc = 0.0
    def feed(self, b):
        a = array.array("h", b); self.buf.extend(a)
        # DCS: crude 1-pole low-pass (~150 Hz) then slice around a running mean
        for x in a:
            self.lp += (x - self.lp) * 0.04
            self.acc += 1
            if self.acc >= self.samp_per_bit:
                self.acc -= self.samp_per_bit
                self.bits.append(1 if self.lp > 0 else 0)
                if len(self.bits) > 200: self.bits = self.bits[-200:]
        if len(self.buf) >= self.rate * 1:                             # analyse each full second
            self._ctcss(self.buf[: self.rate]); self.buf = self.buf[self.rate :]
            self._dcs()
    def _goertzel(self, x, f):
        n = len(x); k = int(0.5 + n * f / self.rate); w = 2 * math.pi * k / n; c = 2 * math.cos(w)
        s0 = s1 = s2 = 0.0
        for v in x: s0 = v + c * s1 - s2; s2 = s1; s1 = s0
        return s1 * s1 + s2 * s2 - c * s1 * s2
    def _ctcss(self, x):
        xs = list(x)
        p = {f: self._goertzel(xs, f) for f in CTCSS}
        best = max(p, key=p.get); rest = sorted(p.values())[-2] if len(p) > 1 else 1
        if p[best] > 8 * max(rest, 1e-9) and p[best] > 1e9:           # clearly dominant sub-audible tone
            self.ctcss[best] = self.ctcss.get(best, 0) + 1; self.last = f"CTCSS {best}"
    def _dcs(self):
        if len(self.bits) < 46: return
        seq = self.bits[-92:]
        for i in range(len(seq) - 23):
            w = tuple(seq[i:i + 23])
            if w in DCS_WORDS:
                name = DCS_WORDS[w]; self.dcs[name] = self.dcs.get(name, 0) + 1; self.last = name; break
    def summary(self):
        out = []
        if self.ctcss: t = max(self.ctcss, key=self.ctcss.get); out.append(f"CTCSS {t} ({self.ctcss[t]}s)")
        if self.dcs: d = max(self.dcs, key=self.dcs.get); out.append(f"DCS {d} ({self.dcs[d]}s)")
        return ", ".join(out)

class Recorder:
    """Writes one WAV clip per transmission into <scan>/recordings/. A clip closes 2 s after squelch shuts."""
    def __init__(self, mhz, name):
        self.dir = os.path.join(newest_scan() or HERE, "recordings"); os.makedirs(self.dir, exist_ok=True)
        self.mhz = mhz; self.name = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)[:16]
        self.f = None; self.path = None; self.closed_blocks = 0; self.bytes = 0; self.count = 0
    def feed(self, b, open_):
        if open_:
            if not self.f:
                self.path = os.path.join(self.dir, f"{self.mhz:.4f}_{self.name}_{dt.datetime.now():%Y%m%d_%H%M%S}.wav")
                self.f = open(self.path, "wb"); self.f.write(wav_header(0)); self.bytes = 0
            self.f.write(b); self.bytes += len(b); self.closed_blocks = 0
        elif self.f:
            self.closed_blocks += 1
            if self.closed_blocks > 20: self.close()                 # 2 s of silence ends the clip
    def close(self):
        if not self.f: return
        self.f.seek(0); self.f.write(wav_header(self.bytes)); self.f.close(); self.f = None
        if self.bytes < RATE * 2 * 0.7:                                # under 0.7 s: noise blip, drop it
            try: os.remove(self.path)
            except OSError: pass
        else: self.count += 1

def wav_header(nbytes=0xFFFFFFFF - 44):
    # "infinite" WAV: data size set to max so browsers keep reading the chunked stream
    return (b"RIFF" + struct.pack("<I", (nbytes + 36) & 0xFFFFFFFF) + b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, RATE, RATE * 2, 2, 16)
            + b"data" + struct.pack("<I", nbytes & 0xFFFFFFFF))

DIAG_REQ = os.path.join(HERE, "diag_request.txt"); DIAG_DIR = os.path.join(HERE, "diag")
def run_diag(mhz, mode, secs=6):
    """Tune for a few seconds, save raw + processed audio and per-block stats. Triggered by diag_request.txt."""
    os.makedirs(DIAG_DIR, exist_ok=True)
    res = {"mhz": mhz, "mode": mode, "started": dt.datetime.now().isoformat(), "blocks": [], "error": ""}
    stop_player(); take_dongle()
    try:
        rawp = os.path.join(DIAG_DIR, "raw.pcm")
        if mode == "monitor": ses = make_session(0, "monitor", None, True, "diag", None)
        elif mode.startswith("chan"):                                   # "118.7 chan-am 10": one channel through the channelizer
            import channelizer as CH
            md = mode.split("-", 1)[1] if "-" in mode else ("am" if mhz < 137 else ("wbfm" if 88 <= mhz < 108 else "fm"))
            ses = CH.Channelizer([(mhz, md, "diag")], None, True, "49.6", None, FM_LOG)
        else: ses = AU.Session(mhz, mode, None, True, "49.6", None, "diag", FM_LOG, raw_path=rawp)
        prc = bytearray(); t0 = time.time()
        while time.time() - t0 < secs:
            b = ses.read(4800)
            if not b: res["error"] = ses.err or "no data"; break
            prc += b; o = array.array("h", b); r_out = math.sqrt(sum(x * x for x in o) / max(1, len(o)))
            res["blocks"].append({"t": round(time.time() - t0, 2), "out_rms": round(r_out), "open": ses.gate.open, "zcr": round(ses.gate.zcr, 2), "thresh": ses.gate.thresh, "in_rms": round(ses.gate.level)})
        try: res["monitor"] = ses.meter().get("monitor"); res["err"] = ses.err
        except Exception: pass
        ses.stop()
        with open(os.path.join(DIAG_DIR, "processed.wav"), "wb") as f: f.write(AU.wav_header(len(prc)) + bytes(prc))
        try:
            rb = open(rawp, "rb").read(); rate = 48000 if mode == "wbfm" else RATE
            import struct
            hdr = (b"RIFF" + struct.pack("<I", (len(rb) + 36) & 0xFFFFFFFF) + b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) + b"data" + struct.pack("<I", len(rb) & 0xFFFFFFFF))
            with open(os.path.join(DIAG_DIR, "raw.wav"), "wb") as f: f.write(hdr + rb)
            res["raw_bytes"] = len(rb)
        except Exception as e: res["raw_error"] = repr(e)
        res["processed_bytes"] = len(prc); res["rtl_fm_tail"] = last_fm_error()
    except Exception as e:
        res["error"] = repr(e)
    finally:
        give_dongle_back(0)
    json.dump(res, open(os.path.join(DIAG_DIR, "result.json"), "w"), indent=1)

def gain_test(lo=118.0, hi=137.0, gains=(49.6, 40.2, 32.8, 25.4), secs=15):
    """Which tuner gain hears airband best from here? Strong FM broadcast next door can overload the R820T2 at full gain.
    One rtl_power pass per gain; score = mean SNR (peak minus the band's median floor) of the strongest 8 known channels. -> diag/gain.json"""
    os.makedirs(DIAG_DIR, exist_ok=True); res = {"band": [lo, hi], "secs": secs, "runs": []}
    stop_player(); take_dongle()
    try:
        for g in gains:
            out = os.path.join(DIAG_DIR, f"gain_{g}.csv")
            try: os.remove(out)
            except OSError: pass
            try:
                subprocess.run(["rtl_power", "-f", f"{lo}M:{hi}M:25k", "-g", str(g), "-i", "5", "-e", f"{secs}s", "-1", out], capture_output=True, timeout=secs + 25)
            except Exception as e: res["runs"].append({"gain": g, "error": repr(e)}); continue
            bins = {}
            try:
                for row in csv.reader(open(out)):
                    try: f0, step = float(row[2]), float(row[4]); vals = [float(x) for x in row[6:]]
                    except (ValueError, IndexError): continue
                    for i, v in enumerate(vals):
                        if v > -1e9: bins.setdefault(round((f0 + i * step) / 1e6, 4), []).append(v)
            except Exception as e: res["runs"].append({"gain": g, "error": repr(e)}); continue
            if not bins: res["runs"].append({"gain": g, "error": "no data"}); continue
            peak = {f: max(v) for f, v in bins.items()}
            floor = sorted(peak.values())[len(peak) // 2]
            known = [(f, peak[f] - floor) for f in peak if sdr_scan.reference_for(f)]
            known.sort(key=lambda x: -x[1]); top = known[:8]
            res["runs"].append({"gain": g, "floor_db": round(floor, 1), "top": [(f, round(snr, 1)) for f, snr in top],
                                "mean_snr_top8": round(sum(x[1] for x in top) / max(1, len(top)), 1), "distinct": len(set(round(v, 2) for v in peak.values()))})
        best = max((r for r in res["runs"] if "mean_snr_top8" in r), key=lambda r: r["mean_snr_top8"], default=None)
        res["best_gain"] = best["gain"] if best else None
    finally:
        give_dongle_back(0)
        json.dump(res, open(os.path.join(DIAG_DIR, "gain.json"), "w"), indent=1)
    say(f"gain test done: best {res.get('best_gain')} dB -> diag/gain.json")

def rate_test(mhz, mode, secs=4):
    """Measure rtl_fm's real output rate for a mode: read as fast as possible, no processing."""
    os.makedirs(DIAG_DIR, exist_ok=True); res = {"mode": mode, "mhz": mhz}
    stop_player(); take_dongle()
    try:
        proc = start_rtl_fm(mhz, mode, 0, 49.6)
        proc.stdout.read(65536)                                   # discard startup burst
        t0 = time.time(); n = 0
        while time.time() - t0 < secs:
            b = proc.stdout.read(65536)
            if not b: break
            n += len(b)
        el = time.time() - t0; proc.terminate()
        res.update(bytes=n, secs=round(el, 2), samples_per_sec=int(n / 2 / el), rtl_fm_tail=last_fm_error())
    except Exception as e: res["error"] = repr(e)
    finally: give_dongle_back(0)
    json.dump(res, open(os.path.join(DIAG_DIR, "rate.json"), "w"), indent=1)

RESTART_REQ = os.path.join(HERE, "restart_request.txt"); RESET_REQ = os.path.join(HERE, "reset_request.txt"); OPEN_REQ = os.path.join(HERE, "open_request.txt")
_RESTARTING = [False]
def restart_self():
    # never interrupt a listen: wait (up to 15 min) until nobody is listening before re-exec'ing - unless the request says "force"
    try: force = open(RESTART_REQ).read().strip() == "force"
    except Exception: force = False
    while not force:                                                   # (4 Nov) no 15-minute cap any more: a person listening is never interrupted by a restart
        with _lock: listening = _player.get("session") is not None
        listening = listening or os.path.exists(os.path.join(HERE, "SDRTRUNK_HAS_DONGLE"))     # SDRTrunk decoding counts as listening
        listening = listening or os.path.exists(os.path.join(HERE, "DMR_SCAN_ON"))             # (v144) a DMR bank scan between two windows too
        if not listening and not USER_WANTS.is_set() and time.time() - LAST_USER_PLAY[0] > 20: break
        time.sleep(2)
    try: stop_player()
    except Exception: pass
    try: os.remove(RESTART_REQ)
    except OSError: pass
    import sys
    try:
        with open(os.path.join(HERE, "stops.log"), "a") as f: f.write(f"--- {dt.datetime.now():%H:%M:%S} RESTART (idle)\n")
    except Exception: pass
    say("restarting the dashboard now")
    os.environ["SDR_RESTARTED"] = "1"
    os.execv(sys.executable, [sys.executable] + sys.argv)

def diag_watcher():
    while True:
        time.sleep(2)
        try:
            if os.path.exists(RESTART_REQ) and not _RESTARTING[0]:      # waits for the listen to end on its own thread, so the diagnostic hooks keep working
                _RESTARTING[0] = True; threading.Thread(target=restart_self, daemon=True).start()
            if os.path.exists(os.path.join(HERE, "env_request.txt")):
                os.remove(os.path.join(HERE, "env_request.txt"))
                info = {"python": sys.executable, "version": sys.version, "cwd": HERE, "path": os.environ.get("PATH", "")}
                for mod in ("numpy", "scipy"):
                    try:
                        m = __import__(mod); info[mod] = getattr(m, "__version__", "?")
                    except Exception as e: info[mod] = f"missing ({e.__class__.__name__})"
                for b in ("rtl_tcp", "rtl_fm", "rtl_power", "sox", "play", "whisper-cli", "brew", "pip3"):
                    info[b] = shutil.which(b) or ""
                try: info["cpu"] = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, timeout=5).stdout.strip()
                except Exception: pass
                try: info["procs"] = subprocess.run(["pgrep", "-lf", "rtl_|sdr_scan|dashboard|whisper|sox|play "], capture_output=True, text=True, timeout=5).stdout.strip().splitlines()
                except Exception: pass
                with _lock: info["player"] = {"mhz": _player.get("mhz"), "where": _player.get("where"), "session": type(_player.get("session")).__name__ if _player.get("session") else None}
                info["pause_flag"] = os.path.exists(pause_flag()); info["scan_running"] = scan_running(); info["user_wants"] = USER_WANTS.is_set()
                try:
                    g = _player.get("gate")
                    if g: info["gate"] = {"manual": g.manual, "thresh": round(g.thresh, 3), "noise": round(g.noise, 3) if g.noise is not None else None, "open": g.open, "data": g.data, "latched": g.data_latched, "mute_data": g.mute_data, "detect": g.detect_data}
                except Exception as e: info["gate"] = repr(e)
                os.makedirs(DIAG_DIR, exist_ok=True); json.dump(info, open(os.path.join(DIAG_DIR, "env.json"), "w"), indent=1)
            if os.path.exists(os.path.join(HERE, "asr_request.txt")):
                # re-transcribe the given clips with both engines -> diag/asr.json (validation / A-B testing)
                reqs = [l.strip() for l in open(os.path.join(HERE, "asr_request.txt")) if l.strip()]; os.remove(os.path.join(HERE, "asr_request.txt"))
                outp = {"model": whisper_model(), "parakeet": parakeet_ok(), "clips": []}
                wb = whisper_bin()
                for f in reqs:
                    if not os.path.isabs(f): f = os.path.join(HERE, f)
                    row = {"file": os.path.basename(f)}
                    try:
                        t0 = time.time()
                        r = subprocess.run([wb, "-m", outp["model"], "-f", f, "-nt", "-np", "-l", "en", "-t", "6", "-bs", "5", "-bo", "5", "--prompt", WHISPER_PROMPT], capture_output=True, text=True, timeout=300)
                        row["whisper"] = re.sub(r"\[.*?\]|\(.*?\)", "", " ".join(l.strip() for l in r.stdout.splitlines() if l.strip())).strip(); row["whisper_s"] = round(time.time() - t0, 1)
                    except Exception as e: row["whisper"] = f"error {e!r}"
                    if outp["parakeet"]:
                        try:
                            t0 = time.time()
                            pr = subprocess.run(["nice", "-n", "15", sys.executable, os.path.join(HERE, "asr_parakeet.py"), f], capture_output=True, text=True, timeout=300, cwd=HERE)
                            for line in pr.stdout.splitlines():
                                try: j = json.loads(line); row["parakeet"] = j.get("text", j.get("error"))
                                except ValueError: pass
                            row["parakeet_s"] = round(time.time() - t0, 1); row["parakeet_err"] = pr.stderr[-300:] if not row.get("parakeet") else ""
                        except Exception as e: row["parakeet"] = f"error {e!r}"
                    try: row["old"] = open(f[:-4] + ".txt").read().strip()
                    except Exception: pass
                    outp["clips"].append(row)
                os.makedirs(DIAG_DIR, exist_ok=True); json.dump(outp, open(os.path.join(DIAG_DIR, "asr.json"), "w"), indent=1)
            if os.path.exists(os.path.join(HERE, "scan_request.txt")):
                cat = open(os.path.join(HERE, "scan_request.txt")).read().strip() or "All"; os.remove(os.path.join(HERE, "scan_request.txt"))
                key = cat.lower().replace("/", "").replace(" ", "_")
                try: stop_player()
                except Exception: pass
                start_scan(key, cat, "scan_request.txt")
            if os.path.exists(RESET_REQ):
                os.remove(RESET_REQ)
                subprocess.Popen(["open", os.path.join(HERE, "Reset everything.command")])
            if os.path.exists(OPEN_REQ):                                  # "Install SDRTrunk.command" -> runs that installer (only .command files in this folder)
                name = os.path.basename(open(OPEN_REQ).read().strip()); os.remove(OPEN_REQ)
                cand = [os.path.join(HERE, d, name) for d in ("", "_helpers", "_launchers")]
                path = next((c for c in cand if os.path.exists(c)), None)
                if name.endswith(".command") and path:
                    # runs headless (no new Terminal window): output goes to diag/<name>.log and a line in this window
                    os.makedirs(DIAG_DIR, exist_ok=True); say(f"running {name} (output in diag/{name}.log)")
                    subprocess.Popen(["/bin/bash", path], cwd=HERE, stdout=open(os.path.join(DIAG_DIR, name + ".log"), "w"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
            if os.path.exists(DIAG_REQ):
                with _lock: listening = _player.get("session") is not None
                # (v193) ... nor from SDRTrunk: a digital listen holds the dongle without a player session, and a diagnostic fired during
                # one (11 Nov 02:41) found the dongle busy and gave back nothing
                if listening or USER_WANTS.is_set() or time.time() - LAST_USER_PLAY[0] < 20 or os.path.exists(os.path.join(HERE, "SDRTRUNK_HAS_DONGLE")): continue       # diagnostics never take the dongle from the user
                req = open(DIAG_REQ).read().split(); os.remove(DIAG_REQ)
                if req and req[0] == "RATE" and len(req) >= 3: rate_test(float(req[1]), req[2])
                elif req and req[0] == "GAIN": gain_test(*([float(req[1]), float(req[2])] if len(req) >= 3 else []))
                elif len(req) >= 2: run_diag(float(req[0]), req[1], int(req[2]) if len(req) > 2 else 6)
        except Exception as e:
            try: json.dump({"error": repr(e)}, open(os.path.join(DIAG_DIR, "result.json"), "w"))
            except Exception: pass
threading.Thread(target=diag_watcher, daemon=True).start()

# ---------------------------------------------------------------- Mac player + dongle watch (auto-resume after a USB drop)
LAST_PLAY = {}                       # what the user last asked for on the Mac speakers
DONGLE = {"lost": False, "since": 0, "note": "", "flat": "", "flat_since": 0}
def dongle_error(txt):
    t = (txt or "").lower()
    return any(k in t for k in ("failed to open", "no supported devices", "usb_claim", "no device", "error -", "not found"))
def tunable(mhz, mode):
    """(v136) The dongle tunes 24-1766 MHz; mode 'monitor' uses the 1.0 pseudo-frequency. Anything else is a bad request, never a play."""
    return mode == "monitor" or 24.0 <= float(mhz) <= 1766.0
def play_mac(mhz, mode, manual, name, cat="All", resume=False):
    if not tunable(mhz, mode): return False, f"{mhz} MHz is outside the dongle's range (24-1766 MHz)"
    # (v134) a repeat of the channel that is already playing (double tap, several open pages, a retry) must not restart the receiver -
    # every restart was a 1-3 s hole in the audio. Same frequency, same mode, same squelch within the last 10 min -> just say ok.
    with _lock: cur = dict(_player)
    if not resume and cur.get("session") is not None and cur.get("where") == "mac" and mode != "monitor" and abs((cur.get("mhz") or 0) - mhz) < 0.0026 \
            and cur.get("mode", mode) == mode and cur.get("manual", manual) == manual and time.time() - (cur.get("since") or 0) < 600:
        LAST_USER_PLAY[0] = time.time(); return True, "ok (already playing)"
    USER_WANTS.set(); LAST_USER_PLAY[0] = time.time()
    say(f"{'resuming' if resume else 'play'} {mhz:.4f} MHz {mode} {name or ''}")
    if not resume: LAST_PLAY.update(mhz=mhz, mode=mode, manual=manual, name=name, cat=cat, t=time.time())
    try:
        stop_player(); take_dongle()
        ses = make_session_retry(mhz, mode, manual, True, name, newest_scan() or HERE, cat)
    except Exception as e:
        USER_WANTS.clear(); give_dongle_back(0); return False, f"could not start the receiver: {e}"
    USER_WANTS.clear()
    rtl = getattr(ses, "rtl", None)
    if (rtl is not None and rtl.poll() is not None) or not getattr(ses, "alive", True):
        err = last_fm_error()
        try: ses.stop()
        except Exception: pass
        if dongle_error(err) or not err:
            DONGLE.update(lost=True, since=time.time(), note=err or "rtl_fm stopped"); say(f"dongle not responding ({err or 'no data'}) - will resume {mhz:.4f} when it is back")
            return False, f"dongle not responding ({err or 'no data'}) - reconnect it; the player retries by itself and resumes {mhz:.4f}"
        give_dongle_back(0); return False, f"rtl_fm: {err}"
    DONGLE.update(lost=False, note="")
    say(f"listening on {mhz:.4f} MHz")
    def spawn_play():
        return subprocess.Popen(["play", "-q", "--buffer", "16384", "-r", str(RATE), "-t", "raw", "-e", "s", "-b", "16", "-c", "1", "-", "gain", "-3"], stdin=subprocess.PIPE,   # -3 dB: coreaudio/rate were clipping loud airband
                                stderr=open(os.path.join(HERE, "play.log"), "a"))
    pl = spawn_play(); respawns = []
    def pump():
        cur = pl
        try:
            while True:
                b = ses.read(4800)
                if not b: break
                AUDIO_LAST[0] = time.time()
                try: cur.stdin.write(b); cur.stdin.flush()
                except (BrokenPipeError, OSError):              # the sound output (sox play) died, e.g. the Mac switched output device: start it again, keep the stream
                    if not getattr(ses, "alive", True): break
                    now = time.time(); respawns[:] = [t for t in respawns if now - t < 30] + [now]
                    if len(respawns) > 5: say("audio output keeps failing - giving up (see play.log)"); break
                    say("audio output restarted (the Mac sound device changed or sox stopped)")
                    try: cur.kill()
                    except Exception: pass
                    cur = spawn_play(); _player["play"] = cur
        except Exception: pass
        finally:
            try: cur.stdin.close()
            except Exception: pass
    threading.Thread(target=pump, daemon=True).start()
    with _lock: _player.update(session=ses, mhz=mhz, since=time.time(), where="mac", gate=ses.gate, play=pl, mode=mode, manual=manual)
    return True, "ok"

def dongle_watch():
    """Every 3 s while the dongle is lost: probe it (rtl_test), and when it is back put the user's last listen straight back on."""
    while True:
        time.sleep(3)
        try:
            if not DONGLE["lost"]: continue
            with _lock: ses = _player.get("session")
            if ses is not None: DONGLE.update(lost=False); continue
            if USER_WANTS.is_set(): continue
            if shutil.which("rtl_test") and subprocess.call(["rtl_test", "-t"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15) != 0: continue
            time.sleep(1.5)
            lp = LAST_PLAY
            if lp and time.time() - lp.get("t", 0) < 3600 and lp.get("mhz"):
                ok, msg = play_mac(lp["mhz"], lp["mode"], lp["manual"], lp["name"], lp.get("cat", "All"), resume=True)
                if ok: DONGLE.update(lost=False, note=""); continue
            else:
                DONGLE.update(lost=False); give_dongle_back(0)
        except Exception as e:
            DONGLE["note"] = repr(e)
threading.Thread(target=dongle_watch, daemon=True).start()

def watchdog():
    while True:
        time.sleep(2)
        try: _watchdog_once()
        except Exception as e:                       # one bad pass must never kill the watchdog thread
            try: DONGLE["note"] = "watchdog: " + repr(e)
            except Exception: pass
_STALLS = []
def _watchdog_once():
    if True:
            with _lock:
                ses = _player.get("session")
            stalled = bool(ses) and hasattr(ses, "last_data") and getattr(ses, "alive", True) and ses.rtl.poll() is None and time.time() - ses.last_data > 8   # unplugged USB: rtl_fm hangs alive but silent
            if stalled:
                try: ses.rtl.kill()
                except Exception: pass
                _STALLS[:] = [t for t in _STALLS if time.time() - t < 60] + [time.time()]
                if len(_STALLS) >= 2:                      # (v151) two silent receivers in a minute = something else still holds the dongle (a dying SDRTrunk / rtl_tcp): clear it, then retry
                    for pat in (["pkill", "-f", "sdr-trunk"], ["pkill", "-x", "rtl_tcp"], ["pkill", "-x", "rtl_fm"]):
                        try: subprocess.call(pat)
                        except Exception: pass
                    time.sleep(1.5); _STALLS[:] = []
            if ses and (stalled or not getattr(ses, "alive", True) or ses.rtl.poll() is not None):      # receiver died (dongle unplugged, etc.)
                where = _player.get("where"); err = last_fm_error()
                try: stop_player()
                except Exception: pass
                if where == "mac" and LAST_PLAY.get("mhz") and (stalled or dongle_error(err) or not err):
                    DONGLE.update(lost=True, since=time.time(), note=err or "receiver stopped")
threading.Thread(target=watchdog, daemon=True).start()

# ---------------------------------------------------------------- data
def gather(folder):
    out = {"folder": os.path.basename(folder) if folder else "-", "running": scan_running(), "bands": [], "hits": [], "now": dt.datetime.now().strftime("%H:%M:%S"),
           "playing": _player["mhz"], "where": _player["where"], "paused": os.path.exists(pause_flag())}
    if not folder or not os.path.isdir(folder): return out
    hits = []
    for k, (name, lo, hi, mode, raster) in sdr_scan.BANDS.items():
        p = os.path.join(folder, f"power_{k}.csv")
        if not os.path.exists(p): continue
        st = os.stat(p)
        rows = sum(1 for _ in open(p, errors="ignore"))
        age = time.time() - st.st_mtime
        out["bands"].append({"band": name, "key": k, "lo": lo, "hi": hi, "rows": rows, "kb": round(st.st_size / 1024), "last": f"{int(age)}s ago", "active": age < 30})
        try: hits += sdr_scan.find_hits(sdr_scan.parse_power(p), mode, raster, 10.0, 0.5)
        except Exception as e: out.setdefault("errors", []).append(f"{k}: {e}")
    for h in hits:
        label, dup, off, mo = sdr_scan.service_for(h["mhz"]); h["service"] = label
        if mo: h["mode"] = mo
        ref = sdr_scan.reference_for(h["mhz"]); h["name"] = ref[0] if ref else sdr_scan.chirp_name({**h, "service": label})
        if ref: h["carrier"] = False      # a known repeater / tower busy for a whole slice is a long QSO, not a data carrier
        else:
            lic = sdr_scan.licence_name(h["mhz"])
            if lic: h["name"] = lic[:24]
        h["uv32"] = sdr_scan.uv32_can_hear(h["mhz"]); h["score"] = round(sdr_scan.score(h), 1)
    heard = {}
    tp = os.path.join(folder, "tones.csv")
    if os.path.exists(tp):
        for line in open(tp, errors="ignore"):
            parts = line.strip().split(",", 2)
            if len(parts) == 3:
                try: heard[round(float(parts[1]), 4)] = parts[2]
                except ValueError: pass
    ver = load_verified()
    for h in hits:
        h["heard_tone"] = next((v for f, v in heard.items() if abs(f - h["mhz"]) <= 0.0026), "")
        r = ver.get(round(h["mhz"], 4))
        h["verified"] = r["label"] if r else ""
        if r and r["label"] == "DATA": h["carrier"] = True
        if sdr_scan.reference_for(h["mhz"]):
            if not h["verified"]: h["verified"] = "KNOWN"             # Pearson / repeater / FRS table: voice by definition
        else:
            lic = sdr_scan.licence_for(h["mhz"])
            dig = [x for x in lic if (x.get("digital") or "").upper().startswith("D")]
            # digital licences on the frequency and nobody has ever heard voice here: it is a DMR/P25 "hammer" channel
            voice_strong = bool(r) and r["label"] == "VOICE" and (r.get("tries", 1) >= 2 or "open " in (r.get("note") or ""))
            if lic and dig and (len(dig) >= len(lic) / 2 or len(dig) >= 2) and (h["verified"] != "VOICE" or (len(dig) == len(lic) and not voice_strong)):
                h["carrier"] = True; h["verified"] = "DATA"
    hits = [h for h in hits if not h["spur"]]
    voice = [h for h in hits if not h.get("carrier")]
    out["data_keys"] = [f"{h['mhz']:.4f}" for h in hits if h.get("carrier")]   # data / digital: remembered in the bank, never listed
    out["all_hits"] = hits
    voice.sort(key=lambda h: -h["score"])
    out["hits"] = voice[:200]; out["total"] = len(voice)
    usb = 0
    for lg in glob.glob(os.path.join(folder, "rtl_*.log")):
        try:
            with open(lg, "rb") as fh: fh.seek(max(0, os.path.getsize(lg) - 4000)); usb += fh.read().decode(errors="ignore").count("failed with -4")
        except OSError: pass
    out["usb_errors_recent"] = usb; out["background"] = BG_STATE.get("what", ""); out["background_last"] = BG_STATE.get("last", "")
    logs = sorted(glob.glob(os.path.join(folder, "rtl_*.log")), key=os.path.getmtime)
    if logs:
        try: out["log"] = open(logs[-1], errors="ignore").read()[-500:]
        except Exception: pass
    return out

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Scan Dashboard</title>
<style>
:root{--bg:#0e1116;--card:#171c24;--ink:#e8edf3;--mute:#8b96a5;--acc:#3fb950;--acc2:#58a6ff;--line:#262d38;--warn:#d29922;--red:#f85149}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.4 -apple-system,system-ui,Helvetica,Arial,sans-serif;padding:16px;max-width:1100px;margin:auto}
h1{font-size:1.3rem;margin:0 0 4px}.sub{color:var(--mute);margin-bottom:12px;font-size:.9rem}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}.card b{display:block;font-size:1.4rem}.card span{color:var(--mute);font-size:.82rem}
.live{color:var(--acc)}.dead{color:var(--warn)}
.wrap{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:var(--card);margin-bottom:14px}
table{border-collapse:collapse;width:100%;min-width:700px}th,td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th{font-size:.76rem;color:var(--mute);text-transform:uppercase;letter-spacing:.04em}th.s{cursor:pointer;user-select:none}th.s:hover{color:var(--ink)}th.s.asc:after{content:" \\25B2"}th.s.desc:after{content:" \\25BC"}td.f{font-family:ui-monospace,Menlo,monospace;font-weight:600}#rows tr[data-mhz]{cursor:pointer}#rows tr[data-mhz]:hover td{background:rgba(88,166,255,.06)}
.bar{display:inline-block;width:70px;height:7px;background:var(--line);border-radius:4px;vertical-align:middle;margin-right:6px}.bar i{display:block;height:100%;background:var(--acc);border-radius:4px}
.tag{font-size:.7rem;padding:2px 7px;border:1px solid var(--line);border-radius:10px;color:var(--mute)}.tag.sdr{border-color:var(--warn);color:var(--warn)}
pre{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px;font-size:.76rem;color:var(--mute);overflow-x:auto}
h2{font-size:.9rem;color:var(--acc);text-transform:uppercase;letter-spacing:.04em;margin:14px 0 8px}
button{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:8px;padding:6px 12px;cursor:pointer}
button.play{background:var(--acc);color:#04130a;border-color:var(--acc);font-weight:700}button.stop{background:var(--red);color:#fff;border-color:var(--red);font-weight:700}
button.mac{font-size:.8rem;color:var(--mute)}
#modebar button{min-width:84px}#modebar button.on{background:var(--acc2);color:#04130a;border-color:var(--acc2);font-weight:700}
#player{position:sticky;top:0;z-index:5;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;margin-bottom:14px;display:grid;gap:8px}
#player .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}#np{font:600 1.1rem ui-monospace,Menlo,monospace}
label{font-size:.8rem;color:var(--mute)}input[type=range]{width:140px;vertical-align:middle}
.paused{color:var(--warn);font-size:.85rem}
</style></head><body>
<h1>Scan Dashboard <span class="{{cls}}">{{state}}</span> <span class="paused">{{pausedtxt}}</span></h1>
<div class="sub">{{folder}} - updated <span id="clock">{{now}}</span> - refreshes every 10 s - phone: http://{{ip}}:{{port}}</div>
<div class="row" id="modebar" style="margin-bottom:10px">
 <button id="m-scan" onclick="setMode('scan')">Scan</button>
 <button id="m-listen" onclick="setMode('listen')">Listen</button>
 <span id="modetxt" class="sub" style="margin:0 0 0 6px"></span>
</div>
<div id="player">
 <div class="row"><span id="np">Nothing playing</span><button class="stop" onclick="stopAll()">Stop</button>
  <label>Volume <input id="vol" type="range" min="0" max="1" step="0.05" value="0.8" oninput="au.volume=this.value"></label>
  <label><input id="auto" type="checkbox" checked onchange="sqChanged()"> Auto squelch</label>
  <label><input id="voice" type="checkbox" checked onchange="sqChanged()"> Voice filter + AGC</label>
  <label>Manual (0 = always open) <input id="sq" type="range" min="0" max="60" step="1" value="30" oninput="document.getElementById('sqv').textContent=this.value" onchange="sqChanged()"> <span id="sqv">30</span></label>
  <span id="meter" class="sub" style="margin:0"></span></div>
 <div class="row"><div class="bar" style="width:220px;height:10px"><i id="lvl" style="width:0%;background:var(--acc2)"></i></div><span id="gate" class="sub" style="margin:0">squelch closed</span>
  <span class="sub" style="margin:0 0 0 auto">Listening pauses the scan; it resumes 30 s after Stop.</span></div>
 <div class="row"><span id="stat" class="sub" style="margin:0">player idle</span><a href="/recordings" style="color:var(--acc);margin-left:auto">Recordings &raquo;</a></div>
 <audio id="au" style="display:none"></audio>
</div>
<details id="radio" open style="margin-bottom:14px"><summary style="cursor:pointer;color:var(--acc);font-size:.9rem;text-transform:uppercase;letter-spacing:.04em">Toronto FM radio (audio test)</summary>
<div class="row" style="margin-top:8px;gap:6px">
 <button onclick="play(99.1,'wbfm','CBC Radio One')">99.1 CBC R1</button>
 <button onclick="play(94.1,'wbfm','CBC Music')">94.1 CBC Music</button>
 <button onclick="play(98.1,'wbfm','CHFI')">98.1 CHFI</button>
 <button onclick="play(102.1,'wbfm','The Edge')">102.1 Edge</button>
 <button onclick="play(104.5,'wbfm','CHUM')">104.5 CHUM</button>
 <button onclick="play(107.1,'wbfm','Q107')">107.1 Q107</button>
 <button onclick="play(92.5,'wbfm','KiSS')">92.5 KiSS</button>
 <button onclick="play(97.3,'wbfm','Boom')">97.3 Boom</button>
 <button onclick="play(91.1,'wbfm','Jazz FM')">91.1 Jazz</button>
 <button onclick="play(99.9,'wbfm','Virgin')">99.9 Virgin</button>
 <button onclick="play(103.5,'wbfm','Z103.5')">103.5 Z</button>
 <button onclick="play(100.7,'wbfm','CHIN')">100.7 CHIN</button>
 <span class="sub" style="margin:0">If a station plays here, the dongle, antenna and player all work.</span></div></details>
<div class="cards">
<div class="card"><b>{{total}}</b><span>active frequencies so far</span></div>
<div class="card"><b>{{uvcount}}</b><span>the UV-32 can tune</span></div>
<div class="card"><b>{{top}}</b><span>strongest so far</span></div>
<div class="card"><b>{{busiest}}</b><span>busiest so far</span></div>
</div>
<h2>Top frequencies so far</h2><div class="wrap"><table><thead><tr><th></th><th>#</th><th class="s" data-k="mhz" data-t="n">MHz</th><th class="s" data-k="name" data-t="s">Name</th><th class="s" data-k="service" data-t="s">Service</th><th class="s" data-k="mode" data-t="s">Mode</th><th class="s" data-k="snr" data-t="n">Signal</th><th class="s" data-k="busy" data-t="n">Busy</th><th class="s" data-k="tone" data-t="s">Tone heard</th><th class="s" data-k="uv" data-t="s">Radio</th></tr></thead><tbody>{{rows}}</tbody></table></div>
<h2>Bands</h2><div class="wrap"><table><thead><tr><th>Band</th><th>Sweeps logged</th><th>Data</th><th>Last write</th><th>Now</th></tr></thead><tbody>{{bands}}</tbody></table></div>
<details id="dbg" style="margin-bottom:14px"><summary style="cursor:pointer;color:var(--acc);font-size:.9rem;text-transform:uppercase;letter-spacing:.04em">Debug - what is running</summary>
<div class="card" style="margin-top:8px"><button onclick="loadDebug()">Refresh</button> <span id="dbgsum" class="sub" style="margin:0"></span><pre id="dbgout" style="margin-top:8px">press Refresh</pre></div></details>
<h2>rtl_power log</h2><pre>{{log}}</pre>
<script>
const au=document.getElementById('au');let playing=null;
let cur=null;let mode='scan';
function setMode(m){mode=m;if(m==='scan'){stopAll();}fetch('/mode?m='+m);paintMode();}
function paintMode(){for(const k of ['scan','listen']){document.getElementById('m-'+k).classList.toggle('on',mode===k);}
 document.getElementById('modetxt').textContent=mode==='scan'?'survey is sweeping - press Listen to park it and audition channels':'survey parked - press play on any row; Scan to resume';}
paintMode();
function sqArg(){return document.getElementById('auto').checked?'auto':document.getElementById('sq').value;}
function sqChanged(){if(cur)cur.where==='mac'?playMac(cur.mhz,cur.mode,cur.name):play(cur.mhz,cur.mode,cur.name);}
async function meter(){try{const r=await fetch('/meter');const m=await r.json();const g=document.getElementById('gate'),l=document.getElementById('lvl');
 if(m.paused!==undefined){const nm=m.playing||m.paused?'listen':'scan';if(nm!==mode){mode=nm;paintMode();}}
 if(!m.playing){g.textContent='';l.style.width='0%';return;}
 const pct=m.floor?Math.min(100,Math.round(100*m.level/(m.floor*2))):0;l.style.width=pct+'%';l.style.background=m.open?'var(--acc)':'var(--acc2)';
 g.textContent=(m.open?'OPEN - signal':'closed - noise')+'  level '+m.level+' / floor '+m.floor+' / threshold '+m.thresh+(m.auto?' (auto)':' (manual)')+(m.tones?'   |   '+m.tones:'');}catch(e){}}
setInterval(meter,500);
async function loadDebug(){try{const d=await (await fetch('/debug')).json();
 document.getElementById('dbgsum').textContent='scan '+(d.paused?'PAUSED (listening)':'running')+' - '+d.folder+' - recent USB errors: '+d.usb_errors_recent+(d.player.mhz?' - player on '+d.player.mhz:'');
 const NL=String.fromCharCode(10);let t='PROCESSES'+NL+(d.procs||'(none)')+NL+NL+'rtl_fm (listener)'+NL+d.fm_log;for(const k in d.logs)t+=NL+NL+k+NL+d.logs[k];document.getElementById('dbgout').textContent=t;}catch(e){document.getElementById('dbgout').textContent='debug failed: '+e;}}
document.getElementById('dbg').addEventListener('toggle',e=>{if(e.target.open)loadDebug();});
async function info(tr){const mhz=parseFloat(tr.dataset.mhz);const nxt=tr.nextElementSibling;
 if(nxt&&nxt.classList.contains('inforow')){nxt.remove();return;}
 const d=await (await fetch('/info?mhz='+mhz)).json();const r=document.createElement('tr');r.className='inforow';
 r.innerHTML='<td></td><td colspan="9" style="white-space:normal;color:var(--mute)"><b style="color:var(--ink)">'+d.name+'</b><br>'+d.lines.map(l=>'&bull; '+l).join('<br>')+'</td>';tr.after(r);}
document.getElementById('rows').addEventListener('click',e=>{if(e.target.closest('button'))return;const tr=e.target.closest('tr');if(tr&&tr.dataset.mhz)info(tr);});
// Web Audio streaming player: fetch raw PCM, schedule buffers back to back. Works in Safari, Chrome and iOS.
let actx=null,gainNode=null,nextT=0,abort=null,bytesIn=0;
function ensureCtx(){if(!actx){actx=new (window.AudioContext||window.webkitAudioContext)();gainNode=actx.createGain();gainNode.connect(actx.destination);}
 if(actx.state!=='running')actx.resume();gainNode.gain.value=parseFloat(document.getElementById('vol').value);}
document.getElementById('vol').addEventListener('input',e=>{if(gainNode)gainNode.gain.value=parseFloat(e.target.value);});
async function play(mhz,md,name){mode='listen';paintMode();const sq=sqArg();cur={mhz,mode:md,name,where:'browser'};playing=mhz;
 document.getElementById('np').textContent=mhz.toFixed(4)+' '+md.toUpperCase()+'  '+name;
 ensureCtx();if(abort)abort.abort();abort=new AbortController();bytesIn=0;nextT=0;
 const stat=document.getElementById('stat');stat.textContent='connecting...';
 try{const r=await fetch('/stream?raw=1&mhz='+mhz+'&mode='+md+'&sq='+sq+'&name='+encodeURIComponent(name)+'&t='+Date.now(),{signal:abort.signal});
  const rd=r.body.getReader();let carry=new Uint8Array(0);
  while(true){const {value,done}=await rd.read();if(done)break;bytesIn+=value.length;
   let buf=new Uint8Array(carry.length+value.length);buf.set(carry);buf.set(value,carry.length);
   const n=Math.floor(buf.length/2);const pcm=new Int16Array(buf.buffer,0,n);carry=buf.slice(n*2);
   if(n<1200){carry=buf;continue;}
   const ab=actx.createBuffer(1,n,24000);const ch=ab.getChannelData(0);for(let i=0;i<n;i++)ch[i]=pcm[i]/32768;
   const src=actx.createBufferSource();src.buffer=ab;src.connect(gainNode);
   const now=actx.currentTime;if(nextT<now+0.1)nextT=now+0.4;src.start(nextT);nextT+=ab.duration;
   stat.textContent='streaming '+(bytesIn/1024).toFixed(0)+' KB  (buffer '+Math.max(0,nextT-actx.currentTime).toFixed(2)+' s)';}
  const m=await (await fetch('/meter')).json();stat.textContent='stream ended - rtl_fm said: '+(m.fm||'(nothing)');}catch(e){if(e.name!=='AbortError')stat.textContent='player error: '+e.message;}}
function playMac(mhz,md,name){mode='listen';paintMode();if(abort){abort.abort();abort=null;}const sq=sqArg();cur={mhz,mode:md,name,where:'mac'};playing=mhz;document.getElementById('np').textContent=mhz.toFixed(4)+' '+md.toUpperCase()+'  '+name+' (Mac speakers)';
 fetch('/playmac?mhz='+mhz+'&mode='+md+'&sq='+sq+'&name='+encodeURIComponent(name));}
function stopAll(){cur=null;if(abort){abort.abort();abort=null;}playing=null;document.getElementById('np').textContent='Nothing playing';document.getElementById('stat').textContent='player idle';fetch('/stop');}
// refresh the tables every 10 s without touching the player
async function refresh(){try{const r=await fetch('/fragment');const t=await r.text();const d=document.createElement('div');d.innerHTML=t;
 for(const id of ['rows','bands','cards','clock','state','log']){const n=d.querySelector('#'+id),o=document.getElementById(id);if(n&&o)o.innerHTML=n.innerHTML;}}catch(e){}}
let sortK=null,sortDir=-1,sortT='n';
try{const sv=JSON.parse(localStorage.getItem('sort')||'null');if(sv){sortK=sv.k;sortDir=sv.d;sortT=sv.t;}}catch(e){}
function applySort(){if(!sortK)return;const tb=document.getElementById('rows');const rows=[...tb.querySelectorAll('tr')].filter(r=>r.dataset.mhz);
 rows.sort((a,b)=>{let x=a.dataset[sortK],y=b.dataset[sortK];if(sortT==='n'){x=parseFloat(x);y=parseFloat(y);return (x-y)*sortDir;}return x.localeCompare(y)*sortDir;});
 rows.forEach((r,i)=>{tb.appendChild(r);r.children[1].textContent=i+1;});
 document.querySelectorAll('th.s').forEach(th=>{th.classList.remove('asc','desc');if(th.dataset.k===sortK)th.classList.add(sortDir>0?'asc':'desc');});}
document.querySelectorAll('th.s').forEach(th=>th.onclick=()=>{if(sortK===th.dataset.k)sortDir=-sortDir;else{sortK=th.dataset.k;sortT=th.dataset.t;sortDir=sortT==='n'?-1:1;}
 try{localStorage.setItem('sort',JSON.stringify({k:sortK,d:sortDir,t:sortT}));}catch(e){}applySort();});
applySort();
const _refresh=refresh;refresh=async function(){await _refresh();applySort();};
setInterval(refresh,10000);
</script></body></html>"""

SDR_TAG = '<span class="tag sdr">SDR only</span>'

def render(d, fragment=False):
    hits = d["hits"]
    rows = "".join(
        f"<tr data-mhz='{h['mhz']}' data-name='{html.escape(h['name']).lower()}' data-service='{(h['service'] or 'zzz').lower()}' data-mode='{h['mode']}' data-snr='{h['snr_db']}' data-busy='{h['active_pct']}' data-tone='{html.escape(h.get('heard_tone','')) or 'zzz'}' data-uv='{'UV-32' if h['uv32'] else 'SDR only'}'>"
        f"<td><button class='play' onclick='play({h['mhz']},\"{'am' if h['mode']=='AM' else 'fm'}\",\"{html.escape(h['name'])}\")'>&#9654;</button> "
        f"<button class='mac' onclick='playMac({h['mhz']},\"{'am' if h['mode']=='AM' else 'fm'}\",\"{html.escape(h['name'])}\")'>Mac</button></td>"
        f"<td>{i+1}</td><td class='f'>{h['mhz']:.4f}</td><td>{html.escape(h['name'])}</td><td>{h['service'] or '-'}</td><td>{h['mode']}</td>"
        f"<td><div class='bar'><i style='width:{min(100,int(h['snr_db']*2.5))}%'></i></div>{h['snr_db']} dB</td><td>{h['active_pct']}%</td>"
        f"<td>{html.escape(h.get('heard_tone',''))}</td>"
        f"<td>{'<span class=tag>UV-32</span>' if h['uv32'] else SDR_TAG}</td></tr>" for i, h in enumerate(hits))
    bands = "".join(f"<tr><td>{b['band']}</td><td>{b['rows']}</td><td>{b['kb']} KB</td><td>{b['last']}</td><td>{'<span class=live>sweeping</span>' if b['active'] else ''}</td></tr>" for b in d["bands"]) or "<tr><td colspan=5>no data yet</td></tr>"
    top = f"{hits[0]['mhz']:.4f}" if hits else "-"
    busiest = f"{max(hits, key=lambda h: h['active_pct'])['mhz']:.4f}" if hits else "-"
    vals = dict(cls="live" if d["running"] else "dead", state="RUNNING" if d["running"] else "not running",
                pausedtxt="(paused for listening)" if d["paused"] else "", folder=html.escape(d["folder"]), now=d["now"], ip=lan_ip(), port=PORT,
                total=d.get("total", 0), uvcount=sum(1 for h in hits if h["uv32"]), top=top, busiest=busiest, sq=20,
                bands=bands, rows=rows or "<tr><td colspan=10>nothing above the noise floor yet - give it a few minutes</td></tr>", log=html.escape(d.get("log", "")))
    if fragment:
        return (f"<table><tbody id='rows'>{vals['rows']}</tbody></table><table><tbody id='bands'>{vals['bands']}</tbody></table><div id='clock'>{vals['now']}</div>"
                f"<div id='state'>{vals['state']}</div><div id='log'>{vals['log']}</div>"
                f"<div id='cards'><div class='card'><b>{vals['total']}</b><span>active frequencies so far</span></div><div class='card'><b>{vals['uvcount']}</b><span>the UV-32 can tune</span></div>"
                f"<div class='card'><b>{vals['top']}</b><span>strongest so far</span></div><div class='card'><b>{vals['busiest']}</b><span>busiest so far</span></div></div>")
    page = PAGE.replace('<div class="cards">', '<div class="cards" id="cards">').replace("<tbody>{{rows}}</tbody>", "<tbody id='rows'>{{rows}}</tbody>").replace("<tbody>{{bands}}</tbody>", "<tbody id='bands'>{{bands}}</tbody>").replace("<pre>{{log}}</pre>", "<pre id='log'>{{log}}</pre>").replace('<span class="{{cls}}">{{state}}</span>', '<span class="{{cls}}" id="state">{{state}}</span>')
    for k, v in vals.items(): page = page.replace("{{" + k + "}}", str(v))
    return page

SERVICE_BLURB = {
    "AIR": "Civil airband (AM). Aircraft and ATC. 25 kHz channels.", "AIR GUARD": "121.5 international emergency - monitored by all aircraft.",
    "NAV": "VOR/ILS navigation beacons - Morse ident, no voice.", "MIL AIR": "Military airband (AM).",
    "2M RPT": "2 m amateur repeater output. Input is offset by 0.6 MHz; needs the CTCSS tone to key.", "70CM RPT": "70 cm amateur repeater output. Input +/-5 MHz; needs the CTCSS tone.",
    "2M SMPLX": "2 m amateur simplex.", "70CM SMPX": "70 cm amateur simplex.", "2M CW": "2 m CW/weak-signal segment.", "2M SSB": "2 m SSB segment.", "SAT/ISS": "Amateur satellite / ISS downlink.",
    "70CM": "70 cm amateur band.", "FRS/GMRS": "FRS/GMRS shared channel - licence-free family radios.", "FRS": "FRS channel (0.5 W bubble-pack radios).",
    "GMRS": "GMRS channel.", "GMRS IN": "GMRS repeater input.", "MURS": "MURS licence-free VHF business/personal channel.",
    "MARINE": "VHF marine - boats on Lake Ontario / Muskoka. Ch 16 is distress and calling.", "RAILWAY": "Railway (AAR) channel - CN/CP/GO crews and dispatch.",
    "PAGING": "Paging transmitter - data tones, no voice.", "WX(OFF)": "Former Weatheradio Canada channel (service ended March 2026).",
    "VHF BIZ": "VHF business/industrial - taxis, security, construction, schools.", "UHF BIZ": "UHF business/industrial.", "UHF T": "UHF trunked/T-band - often digital.",
    "PEARSON": "Toronto Pearson ATC position.", "REPEATER": "Amateur repeater.",
}
def freq_info(mhz):
    out = {"mhz": mhz, "lines": []}
    try:
        from build_hub import entries as hub_entries
        for e in hub_entries():
            if abs(e["mhz"] - mhz) <= 0.0026:
                out["name"] = e["name"]; out["cat"] = e["cat"]; out["group"] = e["group"]
                out["lines"].append(f"{e['cat']} - {e['group']}")
                if e.get("note"): out["lines"].append(e["note"])
                if e.get("duplex"): out["lines"].append(f"Program: {mhz:.4f} {e['duplex']}{e['offset']}" + (f" / tone {e['tone']}" if e.get("tone") else " / no tone listed"))
                if e.get("nets"): out["lines"].append("Nets: " + e["nets"])
                break
    except Exception as ex: out["lines"].append(f"(hub lookup failed: {ex})")
    label, dup, off, mo = sdr_scan.service_for(mhz)
    if label: out["service"] = label; out["lines"].append(SERVICE_BLURB.get(label, label))
    if not out.get("name"):
        out["name"] = sdr_scan.chirp_name({"mhz": mhz, "service": label})
        if dup: out["lines"].append(f"In a standard repeater range: likely input {mhz + (off if dup == '+' else -off):.4f} ({dup}{off}); tone unknown - listen for the ID and check RepeaterBook.")
    out["uv32"] = sdr_scan.uv32_can_hear(mhz)
    out["lines"].append("The UV-32 can tune this." if out["uv32"] else "Outside the UV-32's range - SDR only.")
    hd = {}
    tp = os.path.join(newest_scan() or HERE, "tones.csv")
    if os.path.exists(tp):
        for line in open(tp, errors="ignore"):
            parts = line.strip().split(",", 2)
            if len(parts) == 3:
                try:
                    if abs(float(parts[1]) - mhz) <= 0.0026: hd[parts[2]] = parts[0]
                except ValueError: pass
    if hd: out["lines"].append("Heard here: " + "; ".join(f"{k} at {v}" for k, v in hd.items()))
    lic = sdr_scan.licence_for(mhz)
    if lic:
        out["lines"].append("Licensed here (ISED database, nearest first):")
        seen = set()
        for r in lic[:6]:
            key = (r["licensee"], r["location"])
            if key in seen: continue
            seen.add(key)
            kind = "digital" if (r.get("digital") or "").upper().startswith("D") else "analog"
            out["lines"].append(f"  {r['licensee'].title()} - {r['location']} ({r['km']} km, {kind}{', ' + r['bw_khz'] + ' kHz' if r.get('bw_khz') else ''})")
    elif not out.get("group") and mhz > 137:
        out["lines"].append("No ISED licence within 50 km on this exact frequency - run Build licence lookup.command if licences_gta.csv is missing.")
    recs = glob.glob(os.path.join(HERE, "scan_*", "recordings", f"{mhz:.4f}_*.wav"))
    if recs: out["lines"].append(f"{len(recs)} recording{'s' if len(recs)!=1 else ''} - see Recordings.")
    tx = transcripts_for(mhz, 5)
    if tx: out["transcripts"] = tx
    return out

# ---------------------------------------------------------------- alerts
ALERTS = os.path.join(HERE, "alerts.json"); ALERT_LOG = os.path.join(HERE, "alerts_log.json")
_alock = threading.Lock()
def alerts_load():
    try: return json.load(open(ALERTS))
    except Exception: return []
def alerts_save(rules):
    with _alock: json.dump(rules, open(ALERTS, "w"), indent=1)
def alert_fire(rule, detail):
    entry = {"when": time.time(), "rule": rule, "detail": detail}
    with _alock:
        try: log = json.load(open(ALERT_LOG))
        except Exception: log = []
        if log and log[-1].get("rule") == rule and log[-1].get("detail") == detail and time.time() - log[-1]["when"] < 120: return   # de-bounce
        log.append(entry); log = log[-200:]; json.dump(log, open(ALERT_LOG, "w"))
    title = {"mhz": f"{rule.get('value')} MHz heard", "cat": f"{rule.get('value')} activity", "kw": f"'{rule.get('value')}' said on air"}.get(rule.get("kind"), "SDR alert")
    try:
        subprocess.Popen(["osascript", "-e", f'display notification {json.dumps(detail[:180])} with title "SDR Scanner" subtitle {json.dumps(title)} sound name "Glass"'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception: pass
def alerts_check_heard(mhz, name, cat):
    for r in alerts_load():
        if r.get("kind") == "mhz" and abs(float(r.get("value", 0)) - mhz) <= 0.0026: alert_fire(r, f"{name} ({mhz:.4f}) is on the air")
        elif r.get("kind") == "cat" and (r.get("value") or "").lower() == (cat or "").lower(): alert_fire(r, f"{name} ({mhz:.4f}) - {cat}")
def alerts_check_text(mhz, name, text):
    low = (text or "").lower()
    for r in alerts_load():
        if r.get("kind") == "kw" and (r.get("value") or "").lower() in low: alert_fire(r, f"{name} ({mhz:.4f}): {text[:140]}")

def transcript_path(wav): return wav[:-4] + ".txt"

KEEP_RECORDINGS = 40
def prune_recordings(keep=KEEP_RECORDINGS):
    """Disk guard: keep only the newest `keep` voice clips (with their transcripts) across every scan folder and delete the rest.
    A clip still being written (touched in the last 60 s) is never removed."""
    try:
        files = sorted(glob.glob(os.path.join(HERE, "scan_*", "recordings", "*.wav")), key=os.path.getmtime, reverse=True)
        gone = 0
        for f in files[keep:]:
            try:
                if time.time() - os.path.getmtime(f) < 60: continue
                os.remove(f); gone += 1
                os.makedirs(os.path.join(HERE, "transcripts_archive"), exist_ok=True)
                for ext in (".txt", ".json"):                                  # (v154) the audio is capped at 40 clips, the TRANSCRIPTS are kept for good
                    try: shutil.move(f[:-4] + ext, os.path.join(HERE, "transcripts_archive", os.path.basename(f)[:-4] + ext))
                    except OSError: pass
            except OSError: pass
        if gone: say(f"recordings: kept the newest {keep}, deleted {gone} older clips")
        return gone
    except Exception: return 0
def housekeeping():
    """(v154) The app never hoards: besides the newest KEEP_RECORDINGS clips it keeps only the 3 newest sweep folders, 24 h of SDRTrunk call logs
    and 24 h of diagnostics. Older sweep folders lose everything except their recordings/ (which prune_recordings caps at 40 clips)."""
    now = time.time(); freed = 0
    def size(p):
        try: return os.path.getsize(p) if os.path.isfile(p) else sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(p) for f in fs)
        except OSError: return 0
    try:
        dirs = sorted(glob.glob(os.path.join(HERE, "scan_*")), key=os.path.getmtime, reverse=True)
        for d in dirs[3:]:
            if now - os.path.getmtime(d) < 3600: continue
            for e in os.listdir(d):
                p = os.path.join(d, e)
                if e == "recordings" and os.path.isdir(p) and glob.glob(os.path.join(p, "*")): continue            # recordings and their transcripts always stay
                freed += size(p)
                shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p)
            try: os.rmdir(os.path.join(d, "recordings"))
            except OSError: pass
            try: os.rmdir(d)
            except OSError: pass
        for pat in (os.path.expanduser("~/SDRTrunk/event_logs/*"), os.path.join(HERE, "diag", "*")):
            for f in glob.glob(pat):
                if os.path.isfile(f) and now - os.path.getmtime(f) > 24 * 3600 and not f.endswith("threads.txt"):
                    freed += size(f)
                    try: os.remove(f)
                    except OSError: pass
    except Exception as e: say(f"housekeeping: {e}")
    if freed > 1_000_000: say(f"housekeeping: cleared {freed / 1e6:.0f} MB of old data")
def _prune_loop():
    time.sleep(30)
    while True:
        prune_recordings(); housekeeping(); time.sleep(600)
threading.Thread(target=_prune_loop, daemon=True).start()

def capture_health(folder):
    """Judge the newest rtl_power capture in `folder`: a healthy capture has hundreds of distinct dB values and a real peak;
    a dongle whose USB link is starving returns the same 3 quantized values in every bin ('Error: dropped samples' in the log).
    Returns "" when healthy, else a one-line reason."""
    try:
        csvs = sorted(glob.glob(os.path.join(folder, "power_*.csv")), key=os.path.getmtime)
        if not csvs: return ""
        f = csvs[-1]
        if time.time() - os.path.getmtime(f) > 240: return ""                 # not being written right now: nothing to judge
        with open(f, "rb") as fh:
            fh.seek(max(0, os.path.getsize(f) - 60000)); tail = fh.read().decode(errors="ignore")
        vals = []
        for line in tail.splitlines()[1:]:
            parts = line.split(",")
            if len(parts) < 10: continue
            try: vals += [float(x) for x in parts[6:]]
            except ValueError: continue
        vals = [v for v in vals if v > -1e9]
        if len(vals) < 400: return ""
        distinct = len(set(vals))
        if distinct <= 6:
            band = os.path.basename(f)[6:-4]
            return f"flat capture on {band}: every bin reads the same {distinct} values (max {max(vals):.1f} dB) - the USB link is dropping all samples"
        lg = f[:-4].replace("power_", "rtl_") + ".log"
        if os.path.exists(lg):
            with open(lg, "rb") as fh:
                fh.seek(max(0, os.path.getsize(lg) - 3000)); lt = fh.read().decode(errors="ignore")
            if lt.count("dropped samples") >= 8 and distinct <= 40: return "receiver is dropping most samples (USB starved)"
        return ""
    except Exception as e:
        return ""

def dongle_health_loop():
    """Every 30 s: judge the live capture. First bad verdict -> red banner (DONGLE['flat']), events.log line, and a Mac alert
    sound; first good verdict afterwards -> banner cleared. The 4 Nov failure (flat captures for 20 minutes, silently banking
    nothing) would have been shown within a minute."""
    time.sleep(45)
    while True:
        time.sleep(30)
        try:
            folder = newest_scan()
            if not folder: continue
            bad = capture_health(folder)
            if bad and not DONGLE["flat"]:
                DONGLE.update(flat=bad, flat_since=time.time())
                say("DONGLE PROBLEM: " + bad + " - unplug it, wait 10 s, plug it into a port directly on the Mac")
                subprocess.Popen(["afplay", "/System/Library/Sounds/Basso.aiff"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif not bad and DONGLE["flat"]:
                DONGLE.update(flat="", flat_since=0); say("dongle delivering real data again")
        except Exception as e:
            print("dongle_health_loop:", e)
threading.Thread(target=dongle_health_loop, daemon=True).start()

def dongle_watchdog():
    """The sweep sat 'paused - dashboard is listening' for 40 hours once (3 Nov): a holder wedged and the PAUSE flag was never
    removed. Now: while the dashboard legitimately holds the dongle (audio flowing, SDRTrunk running, a background job under 3 min)
    the PAUSE flags are touched every 20 s as a heartbeat; a flag nobody has touched for 3 min is stale - remove it, kill the strays
    and log it. sdr_scan.py resumes on its own when a flag goes 3 min without a heartbeat (dashboard dead)."""
    while True:
        time.sleep(20)
        try:
            flags = [f for f in all_pause_flags() if os.path.exists(f)]
            if not flags: continue
            now = time.time()
            with _lock: ses = _player.get("session") is not None
            live_audio = ses and now - AUDIO_LAST[0] < 60
            sdrt = os.path.exists(os.path.join(HERE, "SDRTRUNK_HAS_DONGLE")) and subprocess.call(["pgrep", "-f", "sdr-trunk"], stdout=subprocess.DEVNULL) == 0
            bg = bool(BG_STATE.get("what")) and now - BG_STATE.get("since", 0) < 180
            recent = now - LAST_USER_PLAY[0] < 90 or USER_WANTS.is_set()
            if live_audio or sdrt or bg or recent:
                for f in flags:
                    try: os.utime(f, None)
                    except OSError: pass
                continue
            stale = [f for f in flags if now - os.path.getmtime(f) > 180]
            if not stale: continue
            say("dongle watchdog: PAUSE flag stale for 3 min with nothing legitimately listening - resuming the sweep")
            try:
                with open(os.path.join(HERE, "stops.log"), "a") as fh: fh.write(f"--- {dt.datetime.now():%H:%M:%S} WATCHDOG cleared stale PAUSE (ses={ses} audio_age={now-AUDIO_LAST[0]:.0f}s bg={BG_STATE.get('what')!r})\n")
            except Exception: pass
            BG_STATE["what"] = ""
            if ses: stop_player()
            for prog in ("rtl_fm", "rtl_tcp"): subprocess.call(["pkill", "-x", prog], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for f in stale:
                try: os.remove(f)
                except OSError: pass
        except Exception as e:
            print("dongle_watchdog:", e)
threading.Thread(target=dongle_watchdog, daemon=True).start()
def transcripts_for(mhz, n=5):
    """Most recent transcripts for a frequency: [{time, secs, text}]"""
    out = []
    for f in sorted(glob.glob(os.path.join(HERE, "scan_*", "recordings", f"{mhz:.4f}_*.wav")), key=os.path.getmtime, reverse=True):
        tp = transcript_path(f)
        if os.path.exists(tp):
            txt = open(tp, errors="ignore").read().strip()
            if txt:
                st = os.stat(f); agree = None
                try: agree = json.load(open(tp[:-4] + ".asr.json")).get("agree")
                except Exception: pass
                out.append({"time": dt.datetime.fromtimestamp(st.st_mtime).strftime("%H:%M:%S"), "secs": round(max(0, (st.st_size - 44) / (RATE * 2)), 1), "text": txt, "agree": agree})
        if len(out) >= n: break
    return out

# ---------------------------------------------------------------- automatic voice / data verification
# rtl_power cannot tell a DMR/P25 "hammer" channel from an analog voice channel: both are bursty. So the dashboard
# auditions each new unknown hit for a few seconds with the real demodulator and the data detector, and remembers
# the verdict in <scan>/verified.csv: VOICE (keep), DATA (drop, never shown again), QUIET (nothing on air just then; retried later).
def verified_path(): return os.path.join(HERE, "verified.csv")          # global: a verdict survives scan restarts
def find_recording(name):
    """A clip by file name, whichever scan folder it lives in (sweeps change folders; the recordings do not move)."""
    for d in sorted(glob.glob(os.path.join(HERE, "scan_*")), reverse=True):
        p = os.path.join(d, "recordings", name)
        if os.path.exists(p): return p
    return None

def load_verified():
    out = {}
    p = verified_path()
    if os.path.exists(p):
        for line in open(p, errors="ignore"):
            parts = line.strip().split(",")
            if len(parts) >= 3:
                try: out[round(float(parts[1]), 4)] = {"label": parts[2], "when": parts[0], "tries": int(parts[3]) if len(parts) > 3 else 1, "note": parts[4] if len(parts) > 4 else ""}
                except ValueError: pass
    return out
def save_verified(v):
    with open(verified_path(), "w") as f:
        for m, r in sorted(v.items()): f.write(f"{r['when']},{m:.4f},{r['label']},{r.get('tries', 1)},{r.get('note', '')}\n")

# (v137) "Walkies": what household bubble-pack radios, construction crews, stores and film sets in Canada actually use - the 22
# FRS/GMRS channels, the 5 MURS channels and the licence-exempt / itinerant business "dot" and "star" channels. All NFM. The Monitor
# hops these whether or not the sweep has banked them yet, so the first time a crew keys up next door you hear it.
WALKIES = [(m, "fm", n) for m, n in (
    (462.5625, "FRS 1"), (462.5875, "FRS 2"), (462.6125, "FRS 3"), (462.6375, "FRS 4"), (462.6625, "FRS 5"), (462.6875, "FRS 6"), (462.7125, "FRS 7"),
    (467.5625, "FRS 8"), (467.5875, "FRS 9"), (467.6125, "FRS 10"), (467.6375, "FRS 11"), (467.6625, "FRS 12"), (467.6875, "FRS 13"), (467.7125, "FRS 14"),
    (462.5500, "GMRS 15"), (462.5750, "GMRS 16"), (462.6000, "GMRS 17"), (462.6250, "GMRS 18"), (462.6500, "GMRS 19"), (462.6750, "GMRS 20"), (462.7000, "GMRS 21"), (462.7250, "GMRS 22"),
    (151.820, "MURS 1 (US radios; licensed LMR in Canada)"), (151.880, "MURS 2 (US radios; licensed LMR in Canada)"), (151.940, "MURS 3 (US radios; licensed LMR in Canada)"),
    (154.570, "MURS 4 / Blue dot (Walmart US ch)"), (154.600, "MURS 5 / Green dot (Walmart US ch)"),
    (151.625, "Red dot (business)"), (151.955, "Purple dot (business)"), (151.700, "Business itinerant"), (151.760, "Business itinerant"),
    (464.500, "Blue dot UHF (business)"), (464.550, "Green dot UHF (business)"), (467.7625, "J dot (business)"), (467.8125, "K dot (business)"),
    (467.850, "Silver star (business)"), (467.875, "Gold star (business)"), (467.900, "Red star (business)"), (467.925, "Purple star (business)"))]

LOCKOUT_FILE = os.path.join(HERE, "lockouts.json")
def lockouts():
    """(v137) Channels the scanner must never stop on (the SKIP / lockout key of a real scanner), persisted."""
    try: return {round(float(m), 4): n for m, n in json.load(open(LOCKOUT_FILE)).items()}
    except Exception: return {}
def set_lockout(mhz, name="", on=True):
    lo = lockouts(); k = round(float(mhz), 4)
    if on: lo[k] = name or lo.get(k) or f"{k:.4f}"
    else: lo.pop(k, None)
    try: json.dump({f"{m:.4f}": n for m, n in lo.items()}, open(LOCKOUT_FILE, "w"))
    except Exception: pass
    with _lock: ses = _player.get("session")
    if ses is not None and hasattr(ses, "lockout"): ses.lockout = set(lo)
    return lo

def scanner_state():
    """(v137) The head-unit view: what the scanner is doing right now."""
    with _lock: ses = _player.get("session"); where = _player.get("where"); since = _player.get("since")
    st = {"running": ses is not None and getattr(ses, "mode", "") == "monitor" or (ses is not None and hasattr(ses, "groups")),
          "hold": bool(getattr(ses, "hold", False)), "delay": getattr(ses, "HOLD", None), "lockouts": [{"mhz": m, "name": n} for m, n in sorted(lockouts().items())],
          "where": where, "since": since}
    try: st["autoscan"] = (json.load(open(os.path.join(HERE, "scanner.json"))).get("autoscan") or "")
    except Exception: st["autoscan"] = ""
    if ses is not None and hasattr(ses, "groups"):
        st.update(mhz=ses.mhz, name=ses.name, active=bool(ses.active), group=ses.gi + 1, groups=len(ses.groups), n=sum(len(c) for _, c in ses.groups), heard=ses.heard[-20:])
    return st

def scanner_cmd(cmd, q):
    with _lock: ses = _player.get("session")
    if cmd == "hold":
        if ses is not None and hasattr(ses, "hold"): ses.hold = True; say("scanner: HOLD"); return {"ok": True, "hold": True}
        return {"ok": False, "error": "scanner is not running"}
    if cmd == "resume":
        if ses is not None and hasattr(ses, "hold"): ses.hold = False; say("scanner: resume"); return {"ok": True, "hold": False}
        return {"ok": False, "error": "scanner is not running"}
    if cmd == "skip":
        if ses is not None and hasattr(ses, "skip_req"):
            ses.hold = False; ses.skip_req = True; return {"ok": True}
        return {"ok": False, "error": "scanner is not running"}
    if cmd == "lockout":
        mhz = float(q.get("mhz", ["0"])[0]) if q.get("mhz") else (getattr(ses, "mhz", 0) if ses is not None else 0)
        if not mhz or mhz < 24: return {"ok": False, "error": "nothing to lock out"}
        name = q.get("name", [""])[0] or (getattr(ses, "name", "") if ses is not None and abs(getattr(ses, "mhz", 0) - mhz) < 0.0026 else "")
        lo = set_lockout(mhz, name, True); say(f"scanner: locked out {mhz:.4f} {name}")
        if ses is not None and hasattr(ses, "skip_req") and abs(getattr(ses, "mhz", 0) - mhz) < 0.0026: ses.skip_req = True
        return {"ok": True, "lockouts": sorted(lo)}
    if cmd == "unlock":
        lo = set_lockout(float(q.get("mhz", ["0"])[0]), "", False); return {"ok": True, "lockouts": sorted(lo)}
    if cmd == "delay":
        d = max(0.5, min(30.0, float(q.get("s", ["6"])[0])))
        if ses is not None and hasattr(ses, "HOLD"): ses.HOLD = d
        try: json.dump({"delay": d}, open(os.path.join(HERE, "scanner.json"), "w"))
        except Exception: pass
        return {"ok": True, "delay": d}
    return {"ok": False, "error": "unknown command"}

def top_today(n=20):
    """The n voice channels used most in the last 24 h (bank 'times' + hours histogram), busiest first - "the most prevalent streams"."""
    try: bank = json.load(open(os.path.join(HERE, "bank.json")))
    except Exception: bank = {}
    now = time.time(); rows = []
    for k, e in bank.items():
        if e.get("ver") in ("DATA", "DIGITAL"): continue
        m = float(e.get("mhz", k))
        if 88 <= m < 108 or not e.get("last_heard") or now - e["last_heard"] > 86400: continue
        score = (e.get("times") or 0) + sum((e.get("hours") or [])[-24:]) / 10.0
        rows.append((-score, m, "am" if m < 137 else "fm", e.get("name") or f"{m:.4f}"))
    rows.sort()
    return [(m, mo, nm) for _, m, mo, nm in rows[:n]]

# (v157) "Union Station": the actual Union Station porter/platform-ops radio is NOT on the Metrolinx P25 trunked system - it's a
# separate analog (narrow FM) licence held by Toronto Terminals Railway Co. (the company that physically runs the station) plus two
# Via Rail Union Station licences (ISED licence list). This is the closest thing to "the porters" a receiver can actually hear.
UNION_STATION = [(m, "fm", n) for m, n in (
    (466.2125, "Union Station platform ops (Toronto Terminals Railway)"), (461.2125, "Union Station platform ops 2 (Toronto Terminals Railway)"),
    (451.7250, "Via Rail Union Station"), (456.7250, "Via Rail Union Station 2"),
)]

# (v158) "CP Rail Etobicoke Yard": Canadian Pacific's own freight yard/trailer-operations voice channels right in Etobicoke (4-6 km from
# home - closer than GO's Willowbrook Yard in Mississauga). This is freight railroading, not GO/Metrolinx and not the porters at Union Station.
CP_ETOBICOKE = [(m, "fm", n) for m, n in (
    (464.2375, "CP Rail Etobicoke"), (464.5125, "CP Rail Etobicoke Yard"), (464.9625, "CP Rail Car Shop"),
)]

# (v153) "TTC Etobicoke": TTC analog (narrow FM) licences near Etobicoke - 3300 Bloor St W and the Zone 1 / Kipling set (ISED licence list). TTC's main voice
# system is TETRA (not decodable); these analog channels may be quiet, but they are the part of TTC that a plain receiver can hear.
# (v190) Pearson's terminal channels as two Monitor banks. Departures 127.575 + 128.800 are 1.2 MHz apart: one dongle window, both
# decoded at the same time, no hopping - the same trick as the four Fire / EMS sites. Arrivals 124.475 + 125.400 share a window too;
# 132.800 is 8 MHz away (one dongle sees about 2 MHz), so the scan alternates between those two windows, about half a second each
# while both are quiet.
YYZ_DEP = [(127.575, "am", "YYZ DEP 1"), (128.800, "am", "YYZ DEP 2")]
YYZ_ARR = [(132.800, "am", "YYZ ARR 3"), (124.475, "am", "YYZ ARR 1"), (125.400, "am", "YYZ ARR 2")]
def yyz_arrivals():
    """Overnight Pearson combines its terminal sectors: on 11 Nov 2026 at 01:22 an inbound flight was cleared down to 3,000 ft on 128.800,
    the 'departure' frequency, and after midnight it was the only terminal channel with voice. So from 23:00 to 07:00 the departure
    pair is watched with the arrivals. (One night's evidence - the app should learn this from the air rather than from the clock.)"""
    h = dt.datetime.now().hour
    return YYZ_ARR + (YYZ_DEP if (h >= 23 or h < 7) else [])

TTC_ETOB = [(m, "fm", n) for m, n in (
    (417.0375, "TTC Etobicoke 417.0375 (3300 Bloor W)"), (417.0625, "TTC Etobicoke 417.0625 (3300 Bloor W)"), (417.1125, "TTC Etobicoke 417.1125 (3300 Bloor W)"),
    (417.5875, "TTC Etobicoke 417.5875 (3300 Bloor W)"), (417.6125, "TTC Etobicoke 417.6125 (3300 Bloor W)"),
    (422.8250, "TTC Zone 1 Kipling 422.825"), (422.8500, "TTC Zone 1 Kipling 422.850"), (422.9500, "TTC Zone 1 Kipling 422.950"),
    (423.1750, "TTC Zone 1 Kipling 423.175"), (423.2000, "TTC Zone 1 Kipling 423.200"), (423.7750, "TTC Zone 1 Kipling 423.775"),
    (427.8250, "TTC Zone 1 Kipling 427.825"), (427.8500, "TTC Zone 1 Kipling 427.850"), (427.9500, "TTC Zone 1 Kipling 427.950"),
    (428.1750, "TTC Zone 1 Kipling 428.175"), (428.2000, "TTC Zone 1 Kipling 428.200"), (428.7750, "TTC Zone 1 Kipling 428.775"),
)]

# (v169) "GFL Environmental": GFL's own fleet/dispatch licence (ISED licence list) - a private waste hauler, 21 km away in Oakville.
# The only GFL lead in the data, worth keeping even at range since there is no closer alternative.
GFL_ENV = [(m, "fm", n) for m, n in (
    (151.0550, "GFL Environmental (Oakville dispatch)"),
)]

# (v169) "Solid Waste Mgmt": City of Toronto Solid Waste Management Services' own analog dispatch (ISED licence list), separate
# from the ROADS Toronto DMR Capacity Max trunk - this is the conventional repeater, five licensed sites, same frequency at each.
# Etobicoke (120 Disco Rd, 10 km) is the nearest/most relevant; the others are North York x3 and Scarborough.
SOLID_WASTE = [(m, "fm", n) for m, n in (
    (464.1625, "Solid Waste Mgmt (Etobicoke + 4 more sites)"),
)]

_P25F = {"m": None, "f": ()}
def digital_freq(mhz):
    """(v193) True on a frequency of one of the P25 systems the app decodes with SDRTrunk (control and traffic channels). Those are
    never analog voice, whatever the bank says about them."""
    try:
        import sdrtrunk_playlist as SP
        if _P25F["m"] is not SP.P25_SITES:
            _P25F["f"] = tuple(sorted({round(f, 5) for _, _, fr in SP.P25_SITES for f in fr})); _P25F["m"] = SP.P25_SITES
        return any(abs(f - mhz) <= 0.0016 for f in _P25F["f"])
    except Exception: return False

def monitor_list(cat="All", minutes=30, limit=30):
    """Channels worth monitoring: banked voice channels heard in the last `minutes`, plus verified / known voice channels
    in the category, newest first. Read straight from bank.json (written by the dashboard front end).
    cat "X|quick" = Quick scan: every channel heard in the last 24 h (voice or not yet judged), up to 80, busiest-first."""
    quick = cat.endswith("|quick"); cat = cat.split("|")[0]
    lo = lockouts()
    if cat == "YYZ Arrivals": return [c for c in yyz_arrivals() if round(c[0], 4) not in lo]       # (v190)
    if cat == "YYZ Departures": return [c for c in YYZ_DEP if round(c[0], 4) not in lo]           # (v190)
    if cat == "TTC Etobicoke": return [c for c in TTC_ETOB if round(c[0], 4) not in lo]            # (v153)
    if cat == "Union Station": return [c for c in UNION_STATION if round(c[0], 4) not in lo]       # (v157)
    if cat == "CP Etobicoke Yard": return [c for c in CP_ETOBICOKE if round(c[0], 4) not in lo]   # (v158)
    if cat == "GFL Environmental": return [c for c in GFL_ENV if round(c[0], 4) not in lo]        # (v169)
    if cat == "Solid Waste Mgmt": return [c for c in SOLID_WASTE if round(c[0], 4) not in lo]     # (v169)
    if cat == "Walkies": return [c for c in WALKIES if round(c[0], 4) not in lo]       # (v137) every licence-free handheld channel, banked or not
    if cat == "Top": return [c for c in top_today(24) if round(c[0], 4) not in lo][:20]   # (v137) the 20 most-used voice channels of the last 24 h
    if cat.lower().startswith("follow"):                                             # Follow traffic: channels from live ADS-B (_helpers/follow_traffic.py)
        sys.path.insert(0, os.path.join(HERE, "_helpers")); import follow_traffic; return follow_traffic.follow_list()
    if quick: minutes, limit = 1440, 80
    try: bank = json.load(open(os.path.join(HERE, "bank.json")))
    except Exception: bank = {}
    now = time.time(); rows = []
    for k, e in bank.items():
        # (v193) the analog monitor listens to analog channels only. A digital channel demodulated as FM is noise that the squelch
        # opens on for a second and a half before the data detector shuts it - which used to count as "heard".
        if e.get("ver") in ("DATA", "DIGITAL") or (e.get("cat") or "") == "Digital" or digital_freq(float(e.get("mhz", k))): continue
        if 88 <= float(e.get("mhz", k)) < 108: continue                       # broadcast FM is not part of the scanner (the FM Radio tab is only the audio test)
        m = float(e.get("mhz", k)); age = (now - e.get("last_heard", 0)) / 60 if e.get("last_heard") else 1e9
        recent = age <= minutes; sure = e.get("ver") in ("VOICE", "KNOWN") or sdr_scan.reference_for(m) is not None
        if not (recent or sure): continue
        if cat != "All" and (e.get("cat") or "") != cat: continue
        mode = "am" if m < 137 else ("wbfm" if 88 <= m < 108 else "fm")
        rows.append(((0 if sure else 1) if not quick else -(e.get("times") or 0), age, m, mode, e.get("name") or f"{m:.4f}"))     # confirmed voice first, then the freshest (quick: busiest first)
    rows.sort()
    rows = [(a, m, mo, n) for _, a, m, mo, n in rows]
    return [(m, mode, name) for _, m, mode, name in rows if round(m, 4) not in lo][:limit]

def monitor_heard(mhz, name, secs, tones):
    """A monitor hop heard voice: stamp the bank so the dashboard and the next monitor list know."""
    try:
        p = os.path.join(HERE, "bank.json"); bank = json.load(open(p)); k = f"{mhz:.4f}"
        e = bank.setdefault(k, {"mhz": mhz, "name": name, "service": "", "mode": "AM" if mhz < 137 else "NFM", "cat": "", "uv32": sdr_scan.uv32_can_hear(mhz), "first_heard": time.time(), "best_snr": 0, "times": 0, "tone": "", "ver": ""})
        e["last_heard"] = time.time(); e["times"] = e.get("times", 0) + 1
        if secs >= 1.5 and e.get("ver") not in ("KNOWN", "DIGITAL") and not digital_freq(mhz): e["ver"] = "VOICE"     # (v193) digital stays digital
        try: alerts_check_heard(mhz, name, e.get("cat", ""))
        except Exception: pass
        if tones: e["tone"] = tones
        json.dump(bank, open(p + ".tmp", "w")); os.replace(p + ".tmp", p)
    except Exception: pass

def digital_heard(mhz, name, tg, calls=1, enc=False):
    """(v143) SDRTrunk decoded a call on a DMR/NXDN channel: stamp the bank without ever calling it analog VOICE."""
    try:
        p = os.path.join(HERE, "bank.json"); bank = json.load(open(p)); k = f"{mhz:.4f}"
        e = bank.setdefault(k, {"mhz": mhz, "name": name, "service": "", "mode": "NFM", "cat": "Digital", "uv32": False, "first_seen": time.time()})
        e["last_heard"] = time.time(); e["times"] = e.get("times", 0) + calls; e["ver"] = "DIGITAL"
        if name and (not e.get("name") or e["name"].startswith(k[:3])): e["name"] = name
        lc = e.get("last_call") or {}
        e["last_call"] = {"tg": tg, "calls": lc.get("calls", 0) + calls, "when": dt.datetime.now().strftime("%b %d %H:%M"), "enc": "encrypted" if enc else ""}
        json.dump(bank, open(p + ".tmp", "w")); os.replace(p + ".tmp", p)
        try: alerts_check_heard(mhz, name, "Digital")
        except Exception: pass
    except Exception: pass

def expected_voice(mhz):
    """Directory channel, confirmed voice in the bank, or a channel the user marked 'open more' (bank wide=True)."""
    try:
        if sdr_scan.reference_for(mhz): return True
        e = json.load(open(os.path.join(HERE, "bank.json"))).get(f"{mhz:.4f}") or {}
        return bool(e.get("wide")) or e.get("ver") in ("VOICE", "KNOWN")
    except Exception: return False

def make_session_retry(*args, tries=5, **kw):
    """The dongle can still be held for a few seconds by whatever had it last (rtl_power, SDRTrunk's Java process, an
    audition): if rtl_fm cannot claim it, wait and try again instead of reporting a dead stream to the user."""
    last = None
    for i in range(tries):
        ses = make_session(*args, **kw)
        rtl = getattr(ses, "rtl", None)
        t0 = time.time(); died = False
        while time.time() - t0 < 0.35:                             # rtl_fm fails within ~0.2 s when the dongle is still held (v148: 0.35 s probe)
            if rtl is not None and rtl.poll() is not None: died = True; break
            if not getattr(ses, "alive", True): died = True; break
            time.sleep(0.1)
        if not died: return ses
        last = ses
        try: ses.stop()
        except Exception: pass
        subprocess.call(["pkill", "-x", "rtl_power"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep((0.3, 0.6, 1.0, 1.5, 2.5)[min(i, 4)])            # (v134) rtl_fm fails in ~0.2 s while libusb still holds the device; the 2nd try normally works 0.3 s later (was 1.5 s + i)
    return last

def trusted_set(chans):
    """Known voice channels (reference tables / audited VOICE): the data detector must never veto or mute them."""
    try:
        ver = load_verified()
        return {round(m, 4) for m, _, _ in chans if sdr_scan.reference_for(round(m, 4)) or ver.get(round(m, 4), {}).get("label") in ("VOICE", "KNOWN")}
    except Exception: return set()

def make_session(mhz, mode, manual, voice, name, record, cat="All"):
    if mode == "monitor":
        chans = monitor_list(cat)
        try:
            import channelizer as CH
            if CH.available():
                follow = (lambda: monitor_list(cat)) if cat.lower().startswith("follow") else None
                ch = CH.Channelizer(chans, manual, voice, "49.6", record, FM_LOG, on_heard=monitor_heard, trusted=trusted_set(chans), refresh=follow)
                try: ch.lockout = set(lockouts()); ch.HOLD = float(json.load(open(os.path.join(HERE, "scanner.json"))).get("delay", ch.HOLD))
                except Exception: pass
                return ch
        except Exception as e:
            with open(os.path.join(HERE, "verify.log"), "a") as f: f.write(f"{dt.datetime.now():%H:%M:%S} channelizer unavailable: {e!r}\n")
        return AU.Monitor(chans, manual, voice, "49.6", record, FM_LOG, on_heard=monitor_heard)
    # (4 Nov) A single channel ALWAYS goes through rtl_fm. The channelizer's AM path had better squelch, but its audio comes out
    # in 0.1 s blocks with a discontinuity at every block edge and 100 ms silence gaps whenever it falls behind: measured on
    # 132.800 - 1,819 clicks and 23 gaps in 14 s, against 19 and 0 through rtl_fm. The channelizer stays for Monitor (hopping).
    ses = AU.Session(mhz, mode, manual, voice, "49.6", record, name, FM_LOG)
    # A user listen is never muted by the data detector (a repeater's tail or courtesy tone looked like "data" and muted
    # VE3URU mid-conversation) and never loses a clip to it; on known voice channels the detector is off altogether.
    ses.keep_all = True; ses.gate.mute_data = False
    if manual is None and expected_voice(mhz): ses.gate.set_wide(True)        # known / expected transmissions: open a little more, hang longer
    try:
        known = bool(sdr_scan.reference_for(round(mhz, 4))) or (load_verified().get(round(mhz, 4), {}).get("label") in ("VOICE", "KNOWN"))
    except Exception: known = False
    if known: ses.gate.detect_data = False
    return ses

def audition(mhz, mode, secs=6.0):
    """Tune for a few seconds; return (label, note)."""
    ses = AU.Session(mhz, mode, None, True, "49.6", None, "verify", FM_LOG)
    t0 = time.time(); blocks = 0; opened = 0; data_blocks = 0; zs = []; levels = []
    try:
        while time.time() - t0 < secs:
            if USER_WANTS.is_set(): return "QUIET", "user pressed play"
            b = ses.read(4800)
            if not b: break
            blocks += 1
            g = ses.gate
            if g.n <= AU.SETTLE: continue
            if g.data or g.data_latched: data_blocks += 1
            if g.open: opened += 1; zs.append(g.zcr); levels.append(g.level)
    finally:
        ses.stop()
    if blocks < 10: return "QUIET", "no audio from rtl_fm"
    g = ses.gate
    if data_blocks >= 3 or g.data_latched: return "DATA", f"flat bursts (level cv {g.voice_cv:.2f}) {data_blocks} blocks"
    zspread = (max(zs) - min(zs)) if zs else 0
    zmed = sorted(zs)[len(zs) // 2] if zs else 0
    if opened >= 4 and zmed >= 0.42: return "DATA", f"noise-like audio (zcr median {zmed:.2f}) - digital bursts, {opened} blocks"   # 463.3100: DMR bursts were called VOICE on zcr spread alone
    if opened >= 12 and len(g.open_levels) >= 12:
        # the gate has a full window of open audio: judge by syllable rhythm (measured voice cv 0.24-0.8, digital < 0.15)
        if g.voice_cv >= 0.2 and zspread >= 0.05: return "VOICE", f"open {opened}/{blocks} blocks, level cv {g.voice_cv:.2f}, zcr spread {zspread:.2f}"
        if g.voice_cv < 0.15: return "DATA", f"flat envelope cv {g.voice_cv:.2f}"
        return "QUIET", f"unclear (cv {g.voice_cv:.2f}, zcr spread {zspread:.2f}) - retry"
    if opened >= 6 and zspread >= 0.08 and len(g.open_levels) >= 6 and g.voice_cv >= 0.25: return "VOICE", f"short clear burst {opened} blocks, level cv {g.voice_cv:.2f}"
    if opened >= 6 and len(g.open_levels) >= 6 and g.voice_cv < 0.15: return "DATA", f"flat bursts cv {g.voice_cv:.2f}"
    return "QUIET", f"squelch open {opened}/{blocks} blocks - nothing clear"

def verify_worker():
    time.sleep(90)                                            # let the scan get going first
    while True:
        time.sleep(90)
        try:
            with _lock: busy = _player.get("session") is not None
            if busy or os.path.exists(os.path.join(HERE, "SDRTRUNK_HAS_DONGLE")) or USER_WANTS.is_set() or time.time() - LAST_USER_PLAY[0] < 45 or not scan_running(): continue
            folder = newest_scan()
            if not folder: continue
            d = gather(folder)
            ver = load_verified()
            # voice check (was "priority check"): every ~2 minutes, listen on the known / confirmed voice channels with the channelizer, so
            # the bank's "last heard" and the Monitor list stay true even while the sweep is busy elsewhere.
            # (v190) Before, 40 channels meant about 15 tuning positions at 1.5 s each: the 20 s were over before one lap was, and a call had
            # to START in the 0.6 s the radio really looked. Now a quiet position takes half a second (channelizer v190), so 20 s is two to
            # three full laps; a net that should be on the air right now goes to the front of the list; and when somebody is talking the
            # check stays until they finish (75 s at most) instead of cutting the call at 20 s. What it heard is written to the log.
            now = time.time()
            if now - PRIO_LAST[0] > 110:
                PRIO_LAST[0] = now
                try:
                    import channelizer as CH
                    chans = [c for c in monitor_list("All", minutes=30, limit=40)]
                    try:
                        import ham_nets
                        on = [(round(float(o["mhz"]), 4), "fm", o["name"]) for o in ham_nets.view()["now"] if o.get("kind") == "net" and o.get("rx") and 30 < float(o["mhz"]) < 1700]
                        chans = on + [c for c in chans if round(c[0], 4) not in {m for m, _, _ in on}]
                    except Exception: pass
                    if CH.available() and chans:
                        take_dongle(); BG_STATE["what"] = f"voice check: listening on {len(chans)} known channels (20 s, longer while someone talks)"; BG_STATE["since"] = time.time()
                        try:
                            ses = CH.Channelizer(chans, None, True, "49.6", newest_scan() or HERE, FM_LOG, on_heard=monitor_heard, trusted=trusted_set(chans))
                            t0 = time.time()
                            while not USER_WANTS.is_set():
                                el = time.time() - t0
                                if el >= 75 or (el >= 20 and ses.active is None): break
                                b = ses.read(4800)
                                if not b: break
                            got = [f"{h['name']} {h['mhz']:.4f} ({h['secs']:.0f} s)" for h in list(ses.heard)]
                            ses.stop()
                            BG_STATE["last"] = f"{dt.datetime.now():%H:%M} voice check: " + (("heard " + ", ".join(got[:4])) if got else f"nothing on {len(chans)} channels in {ses.laps} laps")
                            if got: say("voice check: heard " + ", ".join(got[:6]))
                        finally:
                            BG_STATE["what"] = ""
                            with _lock: still = _player.get("session") is not None
                            if not still and not USER_WANTS.is_set(): give_dongle_back(0)
                        if USER_WANTS.is_set(): continue
                except Exception as e:
                    with open(os.path.join(HERE, "verify.log"), "a") as f: f.write(f"{dt.datetime.now():%H:%M:%S} priority pass {e!r}\n")
            todo = []
            for h in d["hits"]:
                m = round(h["mhz"], 4)
                if h.get("spur") or h.get("carrier"): continue
                if 764 <= m <= 776 or 851 <= m <= 870: continue    # P25 trunked channels: digital by definition, SDRTrunk's job
                if sdr_scan.reference_for(m): continue             # known repeaters / towers are voice by definition
                r = ver.get(m)
                if r and (r["label"] in ("VOICE", "DATA") or r.get("tries", 1) >= 4): continue
                # only audition a channel that is transmitting RIGHT NOW (seen in the last 3 minutes with real activity):
                # a silent channel cannot be judged, and taking the dongle for it is wasted time
                if not h.get("recent_pct", 0) or h["recent_pct"] < 5: continue
                if h.get("last_active") and h["last_active"] < (dt.datetime.now() - dt.timedelta(minutes=3)).strftime("%H:%M"): continue
                todo.append(h)
            if not todo: continue
            todo.sort(key=lambda h: -h["score"])
            take_dongle()
            try:
                for h in todo[:6]:
                    with _lock:
                        if _player.get("session") is not None: break      # the user pressed play: give way immediately
                    if USER_WANTS.is_set(): break
                    m = round(h["mhz"], 4); mode = "am" if m < 137 else "fm"
                    BG_STATE["what"] = f"auditioning {m:.4f} ({h.get('name') or 'unknown'})"; BG_STATE["since"] = time.time()
                    label, note = audition(m, mode)
                    BG_STATE["what"] = ""
                    if note in ("no audio from rtl_fm", "user pressed play"):   # not a verdict - try again another round
                        if USER_WANTS.is_set(): break
                        time.sleep(2); continue
                    prev = ver.get(m, {"tries": 0})
                    tries = prev.get("tries", 0) + 1
                    if label == "QUIET" and prev.get("label") == "VOICE": label = "VOICE"
                    ver[m] = {"label": label, "when": dt.datetime.now().strftime("%H:%M"), "tries": tries, "note": note.replace(",", ";")}
                    save_verified(ver)
                    time.sleep(1.5)                                        # let libusb release the dongle between auditions
            finally:
                with _lock: still = _player.get("session") is not None
                if not still: give_dongle_back(0)
        except Exception as e:
            try:
                with open(os.path.join(HERE, "verify.log"), "a") as f: f.write(f"{dt.datetime.now():%H:%M:%S} {e!r}\n")
            except Exception: pass
            time.sleep(30)
threading.Thread(target=verify_worker, daemon=True).start()

WHISPER_MODELS = [os.path.join(HERE, "models", n) for n in ("ggml-large-v3-turbo.bin", "ggml-medium.en.bin", "ggml-small.en.bin")]
def whisper_model():
    for m in WHISPER_MODELS:
        if os.path.exists(m) and os.path.getsize(m) > 1e8: return m
    return None
WHISPER_MODEL = whisper_model() or WHISPER_MODELS[-1]
# radio vocabulary: steers the recogniser toward the words that actually occur on these channels (filtered if echoed back)
WHISPER_PROMPT = ("Toronto Pearson tower, arrival, departure, runway two four left, taxi via alpha, descend and maintain, flight level, altimeter, "
                  "Air Canada, WestJet, Porter, roger, wilco, say again, VA3RCI, VE3WOO, repeater, seventy-three, QSO, net control, "
                  "dispatch, copy, ten-four, unit, en route, Beck Taxi, address, pickup, Walmart, Home Depot, customer service, aisle.")
def whisper_bin():
    for c in ("/opt/homebrew/bin/whisper-cli", "/usr/local/bin/whisper-cli", shutil.which("whisper-cli") or ""):
        if c and os.path.exists(c): return c
    return None
_pk = {"ok": None, "t": 0}
_atc = {"ok": None, "t": 0}
def atc_ok():
    """ATC-tuned Whisper (asr_atc.py) available? Checked every 10 min."""
    if _atc["ok"] is None or time.time() - _atc["t"] > 600:
        try: _atc["ok"] = subprocess.run([sys.executable, "-c", "import faster_whisper"], capture_output=True, timeout=60).returncode == 0
        except Exception: _atc["ok"] = False
        _atc["t"] = time.time()
    return bool(_atc["ok"])

def parakeet_ok():
    if _pk["ok"] is None or time.time() - _pk["t"] > 300:
        try: _pk["ok"] = subprocess.run([sys.executable, "-c", "import parakeet_mlx"], capture_output=True, timeout=60).returncode == 0
        except Exception: _pk["ok"] = False
        _pk["t"] = time.time()
    return _pk["ok"]
def _agreement(a, b):
    """word-level overlap of two transcripts, 0..1"""
    wa = set(re.findall(r"[a-z0-9']+", (a or "").lower())); wb = set(re.findall(r"[a-z0-9']+", (b or "").lower()))
    if not wa or not wb: return 0.0
    return len(wa & wb) / max(1, min(len(wa), len(wb)))
_TX_FAIL = {}
def clean_for_asr(src):
    """(v168) Band-limit to the voice band with a steep sinc filter (kills out-of-band hiss far better than a soft
    highpass/lowpass), compand to lift quiet radio speech out of the noise floor without clipping the loud parts,
    then normalize to -1 dBFS - rtl_fm's own output is quiet (comment above: noise ~400, signal ~2000 of 32767),
    and ASR is measurably more accurate on a clip that is both cleaner and consistently loud. Falls back to the
    raw clip untouched if sox is missing or the clean pass errors - never blocks a transcript on this step."""
    try:
        if not shutil.which("sox"): return src
        out = src[:-4] + "_clean.wav"
        r = subprocess.run(["sox", src, out, "sinc", "300-3400", "gain", "-6",
                             "compand", "0.02,0.20", "6:-70,-60,-20", "-5", "-90", "0.1",
                             "norm", "-1"], capture_output=True, text=True, timeout=20)
        return out if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 44 else src
    except Exception:
        return src
def transcribe_worker():
    """Turns every recording longer than 1.5 s into a .txt blurb with whisper.cpp (if installed - Install transcription.command)."""
    while True:
        time.sleep(4)
        try:
            wb = whisper_bin()
            model = whisper_model()
            if not wb or not model: time.sleep(26); continue
            with _lock: busy = _player.get("session") is not None
            # every scan folder, newest clips first: a chip or a restart starts a new folder and its clips must still get transcripts
            for f in sorted(glob.glob(os.path.join(HERE, "scan_*", "recordings", "*.wav")), key=os.path.getmtime, reverse=True)[:40]:
                tp = transcript_path(f)
                if os.path.exists(tp): continue
                st = os.stat(f)
                if time.time() - st.st_mtime < 3: continue                     # still being written
                if st.st_size < 44 + RATE * 2 * 2.0: open(tp, "w").write(""); continue
                cf = clean_for_asr(f)
                # low priority, 4 threads: transcription must never make the dashboard or the player sluggish
                r = subprocess.run(["nice", "-n", "15", wb, "-m", model, "-f", cf, "-nt", "-np", "-l", "en", "-t", "4", "-bs", "5", "-bo", "5", "--prompt", WHISPER_PROMPT],
                                   capture_output=True, text=True, timeout=300)
                if r.returncode != 0 and not r.stdout.strip():             # unreadable right now (still being finalised): retry later, give up after 3 tries
                    _TX_FAIL[f] = _TX_FAIL.get(f, 0) + 1
                    if _TX_FAIL[f] < 3: continue
                txt = " ".join(line.strip() for line in r.stdout.splitlines() if line.strip())
                txt = re.sub(r"\[.*?\]|\(.*?\)|\*.*?\*", "", txt).strip()             # drop [BLANK_AUDIO], (music), *noise* markers
                low = txt.lower()
                if len(txt) < 3 or low in ("you", "thank you.", "thanks for watching.", "bye.") or low.startswith("toronto pearson tower, arrival"): txt = ""   # hallucinations / echoed prompt
                if sum(1 for w in ("tower", "arrival", "departure", "runway", "dispatch", "repeater") if w in low) >= 4 and len(txt) > 150: txt = ""   # prompt echoed back
                # airband: the ATC-fine-tuned Whisper (asr_atc.py) is the primary engine when installed - measured 15% WER vs 95% for stock
                atc = ""
                try: clip_mhz = float(os.path.basename(f).split("_")[0])
                except ValueError: clip_mhz = 0.0
                if 108 <= clip_mhz <= 137 and atc_ok():
                    try:
                        pr = subprocess.run(["nice", "-n", "15", sys.executable, os.path.join(HERE, "asr_atc.py"), cf], capture_output=True, text=True, timeout=600, cwd=HERE)
                        for line in pr.stdout.splitlines():
                            try:
                                j = json.loads(line)
                                if j.get("file") == f: atc = (j.get("text") or "").strip()
                            except ValueError: pass
                    except Exception as e:
                        with open(os.path.join(HERE, "transcribe.log"), "a") as lf: lf.write(f"{dt.datetime.now():%H:%M:%S} atc {e!r}\n")
                # second engine (Parakeet, if installed): the better of the two becomes the transcript, the other the cross-check
                alt = ""
                if parakeet_ok():
                    try:
                        pr = subprocess.run(["nice", "-n", "15", sys.executable, os.path.join(HERE, "asr_parakeet.py"), cf], capture_output=True, text=True, timeout=300, cwd=HERE)
                        for line in pr.stdout.splitlines():
                            try:
                                j = json.loads(line)
                                if j.get("file") == f: alt = (j.get("text") or "").strip()
                            except ValueError: pass
                    except Exception as e:
                        with open(os.path.join(HERE, "transcribe.log"), "a") as lf: lf.write(f"{dt.datetime.now():%H:%M:%S} parakeet {e!r}\n")
                try:
                    base = os.path.basename(f); alerts_check_text(float(base.split("_")[0]), base.split("_")[1] if "_" in base else "", alt or txt)
                except Exception: pass
                if atc:
                    agree = _agreement(atc, txt or alt) if (txt or alt) else 0.0
                    open(tp, "w").write(atc)
                    json.dump({"atc": atc, "whisper": txt, "parakeet": alt, "agree": round(agree, 2)}, open(tp[:-4] + ".asr.json", "w"))
                elif alt:
                    agree = _agreement(txt, alt)
                    # measured on real Pearson clips: whisper large-v3-turbo with the radio vocabulary reads hissy airband far better
                    # than Parakeet; Parakeet is the independent cross-check (the "2 engines agree" tag)
                    primary = txt if txt else alt
                    open(tp, "w").write(primary)
                    json.dump({"whisper": txt, "parakeet": alt, "agree": round(agree, 2)}, open(tp[:-4] + ".asr.json", "w"))
                else:
                    open(tp, "w").write(txt)
                if r.returncode != 0:
                    with open(os.path.join(HERE, "transcribe.log"), "a") as lf: lf.write(f"{dt.datetime.now():%H:%M:%S} {os.path.basename(f)} rc={r.returncode} {r.stderr[-300:]}\n")
                if cf != f:
                    try: os.remove(cf)
                    except Exception: pass
                if busy: break                                                   # keep the CPU for live audio while listening
        except Exception as e:
            try:
                with open(os.path.join(HERE, "transcribe.log"), "a") as lf: lf.write(f"{dt.datetime.now():%H:%M:%S} worker error {e!r}\n")
            except Exception: pass
            time.sleep(10)
threading.Thread(target=transcribe_worker, daemon=True).start()

def recordings_page():
    d = os.path.join(newest_scan() or HERE, "recordings")
    files = sorted(glob.glob(os.path.join(d, "*.wav")), key=os.path.getmtime, reverse=True)
    groups = {}
    for f in files:
        base = os.path.basename(f); mhz = base.split("_")[0]
        groups.setdefault(mhz, []).append(f)
    def clip(f):
        base = os.path.basename(f); st = os.stat(f); secs = max(0, (st.st_size - 44) / (RATE * 2))
        t = dt.datetime.fromtimestamp(st.st_mtime).strftime("%b %d %H:%M:%S")
        return (f"<div class='clip'><div><b>{t}</b> <span class='sub' style='margin:0'>{secs:.1f} s</span></div>"
                f"<audio controls preload='none' src='/rec/{html.escape(base)}'></audio>"
                f"<button onclick=\"fetch('/delrec?f={html.escape(base)}').then(()=>location.reload())\">Delete</button></div>")
    sections = ""
    for mhz, fs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        name = fs[0] and os.path.basename(fs[0]).split("_")[1] or ""
        sections += f"<h2>{mhz} MHz <span class='sub' style='margin:0'>{html.escape(name)} - {len(fs)} clip{'s' if len(fs)!=1 else ''}</span></h2>" + "".join(clip(f) for f in fs)
    total = len(files)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Recordings</title>
<style>:root{{--bg:#0e1116;--card:#171c24;--ink:#e8edf3;--mute:#8b96a5;--acc:#3fb950;--line:#262d38}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.4 -apple-system,system-ui,Helvetica,Arial,sans-serif;padding:16px;max-width:900px;margin:auto}}
h1{{font-size:1.3rem;margin:0 0 4px}}h2{{font-size:1rem;color:var(--acc);margin:18px 0 8px;font-family:ui-monospace,Menlo,monospace}}.sub{{color:var(--mute);font-size:.85rem}}
.clip{{display:grid;grid-template-columns:150px 1fr auto;gap:10px;align-items:center;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 12px;margin-bottom:6px}}
audio{{width:100%;height:36px}}button{{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--mute);border-radius:8px;padding:6px 10px;cursor:pointer}}
a{{color:var(--acc)}}@media(max-width:600px){{.clip{{grid-template-columns:1fr}}}}
</style></head><body><h1>Recordings</h1><div class="sub">{total} clips in {html.escape(os.path.basename(newest_scan() or HERE))}/recordings - every transmission heard while listening is saved here. <a href="/">Back to dashboard</a></div>
{sections or "<p class='sub'>Nothing recorded yet. Press play on a channel in the dashboard; each transmission that opens the squelch becomes a clip.</p>"}
</body></html>"""

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, body, ctype="text/html; charset=utf-8", status=200):
        if isinstance(body, str): body = body.encode()
        # (v141) gzip anything text-like over 4 KB when the browser accepts it: /api/state went 119 KB -> ~14 KB per 2.5 s poll,
        # the page itself 137 KB -> ~32 KB. Audio and binaries are never touched.
        if len(body) > 4096 and "gzip" in (self.headers.get("Accept-Encoding") or "") and (ctype.startswith("text/") or "json" in ctype or "javascript" in ctype or "xml" in ctype):
            import gzip as _gz
            body = _gz.compress(body, 5); enc = "gzip"
        else: enc = None
        self.send_response(status); self.send_header("Content-Type", ctype)
        if enc: self.send_header("Content-Encoding", enc); self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(body))); self.end_headers()
        try: self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError): pass
    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        if u.path == "/stream":
            mhz = float(q.get("mhz", ["146.52"])[0]); mode = q.get("mode", ["fm"])[0]
            sqv = q.get("sq", ["auto"])[0]; manual = None if sqv == "auto" else int(float(sqv))
            voice = q.get("voice", ["1"])[0] != "0"; raw = q.get("raw", ["0"])[0] == "1"
            if not shutil.which("rtl_fm"): return self.send("rtl_fm not installed - run _launchers/setup.command", "text/plain", 500)
            if not tunable(mhz, mode): return self.send(f"{mhz} MHz is outside the dongle's range (24-1766 MHz)", "text/plain", 400)
            USER_WANTS.set(); LAST_USER_PLAY[0] = time.time()
            try:
                stop_player(); take_dongle()
                ses = make_session_retry(mhz, mode, manual, voice, q.get("name", [""])[0], (newest_scan() or HERE) if q.get("rec", ["1"])[0] != "0" else None, q.get("cat", ["All"])[0])
            except Exception as e:
                USER_WANTS.clear(); give_dongle_back(0); return self.send(f"could not start the receiver: {e}", "text/plain", 500)
            USER_WANTS.clear()
            with _lock: _player.update(session=ses, mhz=mhz, since=time.time(), where="browser", gate=ses.gate, mode=mode, manual=manual)
            _dd = getattr(self, "_dongle_done", None)                 # (v178) the receiver is running: the one-command-at-a-time gate can open again
            if _dd: _dd()
            self.send_response(200); self.send_header("Content-Type", "application/octet-stream" if raw else "audio/wav"); self.send_header("Cache-Control", "no-store"); self.send_header("Transfer-Encoding", "chunked"); self.end_headers()
            def chunk(b): self.wfile.write(b"%x\r\n" % len(b) + b + b"\r\n")
            try:
                if not raw: chunk(wav_header())
                while True:
                    b = ses.read(4800)
                    if not b: break
                    AUDIO_LAST[0] = time.time()
                    chunk(b)
                self.wfile.write(b"0\r\n\r\n")
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with _lock: mine = _player.get("session") is ses
                if mine: stop_player()
                else: ses.stop()
            return
        if u.path == "/playmac":
            mhz = float(q.get("mhz", ["146.52"])[0]); mode = q.get("mode", ["fm"])[0]
            sqv = q.get("sq", ["auto"])[0]; manual = None if sqv == "auto" else int(float(sqv))
            if not shutil.which("rtl_fm"): return self.send("rtl_fm not installed - run _launchers/setup.command", "text/plain", 500)
            if not shutil.which("play"): return self.send("sox 'play' not installed - run _launchers/setup.command", "text/plain", 500)
            ok, msg = play_mac(mhz, mode, manual, q.get("name", [""])[0], q.get("cat", ["All"])[0])
            return self.send(msg, "text/plain", 200 if ok else 500)
        if u.path == "/meter":
            with _lock: ses = _player.get("session"); m = _player["mhz"]
            if not ses: return self.send(json.dumps({"playing": None, "paused": os.path.exists(pause_flag()), "fm": last_fm_error(),
                                                       "dongle_lost": DONGLE["lost"], "dongle_note": DONGLE["note"], "resume": LAST_PLAY.get("mhz") if DONGLE["lost"] else None}), "application/json")
            mt = ses.meter()
            return self.send(json.dumps({"playing": m, "open": mt["open"], "level": mt["level"], "zcr": mt["zcr"], "thresh": mt["thresh"], "floor": 0, "mode": ses.mode,
                                         "auto": ses.gate.manual is None, "tones": mt["tones"], "tone_now": mt["tone_now"], "paused": True, "fm": mt["err"] or last_fm_error(),
                                         "data": mt.get("data", False), "monitor": mt.get("monitor"),
                                         "denoise_off": mt.get("denoise_off", False)}), "application/json")   # (v170) diagnostic only - never affects playback
        if u.path == "/debug":
            def sh(cmd):
                try: return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip()
                except Exception as e: return str(e)
            procs = sh(["pgrep", "-lf", "rtl_|sdr_scan|dashboard"])
            folder = newest_scan() or HERE
            logs = {}
            for lg in sorted(glob.glob(os.path.join(folder, "rtl_*.log")), key=os.path.getmtime)[-3:]:
                try:
                    with open(lg, "rb") as fh: fh.seek(max(0, os.path.getsize(lg) - 1200)); logs[os.path.basename(lg)] = fh.read().decode(errors="ignore")
                except OSError: pass
            try:
                with open(FM_LOG, "rb") as fh: fh.seek(max(0, os.path.getsize(FM_LOG) - 1500)); fm = fh.read().decode(errors="ignore")
            except OSError: fm = "(no rtl_fm log yet)"
            usb_err = sum(l.count("failed with -4") for l in logs.values())
            try:
                with open(os.path.join(HERE, "events.log"), "rb") as fh: fh.seek(max(0, os.path.getsize(fh.name) - 6000)); events = fh.read().decode(errors="ignore").splitlines()[-40:]
            except OSError: events = []
            return self.send(json.dumps({"procs": procs, "paused": os.path.exists(pause_flag()), "folder": os.path.basename(folder), "fm_log": fm, "logs": logs, "events": events,
                                         "usb_errors_recent": usb_err, "player": {"mhz": _player["mhz"], "where": _player["where"]}}), "application/json")
        if u.path == "/info":
            mhz = float(q.get("mhz", ["0"])[0])
            return self.send(json.dumps(freq_info(mhz)), "application/json")
        if u.path == "/recordings":
            return self.send(recordings_page())
        if u.path.startswith("/rec/"):
            name = os.path.basename(u.path[5:])
            path = find_recording(name)
            if not path: self.send_response(404); self.end_headers(); return
            data = open(path, "rb").read()
            return self.send(data, "audio/wav")
        if u.path == "/delrec":
            name = os.path.basename(q.get("f", [""])[0])
            path = find_recording(name) or ""
            try: os.remove(path)
            except OSError: pass
            return self.send("ok", "text/plain")
        if u.path == "/stop":
            stop_player(); LAST_PLAY.clear(); DONGLE.update(lost=False); return self.send("ok", "text/plain")
        if u.path == "/mode":
            m = q.get("m", ["scan"])[0]
            if m == "scan":
                with _lock: listening = _player.get("session") is not None
                if not listening: give_dongle_back(0)                  # survey gets the dongle now; a running listen is never cut
            elif m == "listen":
                stop_player(); take_dongle()                           # park the survey; play is instant
            return self.send("ok", "text/plain")
        d = gather(newest_scan())
        if u.path == "/json": return self.send(json.dumps(d), "application/json")
        if u.path == "/fragment": return self.send(render(d, fragment=True))
        return self.send(render(d))

if __name__ == "__main__":
    # make sure no listener from a previous dashboard still holds the dongle before the scan gets it back
    subprocess.call(["pkill", "-x", "rtl_fm"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        if subprocess.call(["pgrep", "-x", "rtl_fm"], stdout=subprocess.DEVNULL) != 0: break
        time.sleep(0.2)
    time.sleep(1.5)
    give_dongle_back()
    print(f"Dashboard: http://localhost:{PORT}   (phone: http://{lan_ip()}:{PORT})   Ctrl-C to stop")
    if sys.platform == "darwin" and not os.environ.get("SDR_RESTARTED"): subprocess.Popen(["open", f"http://localhost:{PORT}"])
    try: ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
    finally: stop_player()
