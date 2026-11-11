#!/usr/bin/env python3
"""Audio session for the dashboard: rtl_fm -> zero-crossing noise squelch (Python, cheap) -> sox (filter, AGC, resample, C) -> consumer.

Squelch metric: zero-crossing rate of the raw demodulated audio. Measured on this dongle:
  idle hiss  ~0.50 crossings/sample, real signal ~0.10. Level-independent, works mid-transmission.
"""
import signal, array, math, os, subprocess, threading, time, datetime as dt

RATE = 24000
SETTLE = 4           # blocks of 0.1 s muted after tuning (own 100 Hz high-pass settles in ~10 ms)
HP_A = 0.9742        # one-pole high-pass coefficient, 100 Hz at 24 kHz: kills DC / carrier-drop thumps without rtl_fm -E dc
CTCSS = [67.0,69.3,71.9,74.4,77.0,79.7,82.5,85.4,88.5,91.5,94.8,97.4,100.0,103.5,107.2,110.9,114.8,118.8,123.0,127.3,131.8,136.5,141.3,146.2,
         151.4,156.7,159.8,162.2,165.5,167.9,171.3,173.8,177.3,179.9,183.5,186.2,189.9,192.8,196.6,199.5,203.5,206.5,210.7,218.1,225.7,229.1,233.6,241.8,250.3,254.1]

SOX_VOICE = ["highpass", "300", "highpass", "300", "lowpass", "2800", "lowpass", "2800", "compand", "0.05,0.3", "-70,-70,-45,-22,-25,-13,0,-9", "0", "-70", "0.1"]
SOX_WFM   = ["lowpass", "11000", "lowpass", "11000", "lowpass", "11000", "rate", "-q", "24000", "compand", "0.1,0.5", "-70,-70,-40,-22,-15,-11,0,-7", "0", "-70", "0.2"]

def am_bw():
    """(v186) Pre-detection bandwidth for single-channel AM in Hz. rtl_fm's AM output at -s 24000 carries noise from a +-12 kHz slice
    of spectrum although airband speech needs about +-3 kHz; a narrower slice gives the envelope detector a better signal-to-noise
    ratio on weak carriers. Off by default: write 12000 or 16000 into _state/am_bw.txt to use it (the audio stays at RATE)."""
    try: v = int(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_state", "am_bw.txt")).read().strip())
    except (OSError, ValueError): return RATE
    return v if v in (8000, 12000, 16000) else RATE

def rtl_cmd(mhz, mode, gain):
    if mode == "wbfm": return ["rtl_fm", "-f", f"{mhz:.4f}M", "-M", "wbfm", "-s", "200k", "-r", "48000", "-g", str(gain), "-"]
    if mode == "am":                                       # (v186) -s is the pre-detection bandwidth; _state/am_bw.txt can narrow it (see am_bw)
        bw = am_bw()
        return ["rtl_fm", "-f", f"{mhz:.4f}M", "-M", "am", "-s", str(bw)] + (["-r", str(RATE)] if bw != RATE else []) + ["-g", str(gain), "-l", "0", "-E", "dc", "-F", "9", "-"]   # -E dc: no zero-IF spike in the AM envelope; -F 9: low-leakage channel filter (less adjacent-channel splatter)
    return ["rtl_fm", "-f", f"{mhz:.4f}M", "-M", "fm", "-s", str(RATE), "-g", str(gain), "-l", "0", "-"]

def sox_cmd(mode, voice=True):
    rin = 48000 if mode == "wbfm" else RATE
    fx = SOX_WFM if mode == "wbfm" else (SOX_VOICE if voice else ["compand", "0.05,0.3", "-70,-70,-45,-22,-25,-13,0,-9", "0", "-70", "0.1"])
    return ["sox", "-q", "--buffer", "7200", "-t", "raw", "-r", str(rin), "-e", "signed", "-b", "16", "-c", "1", "-",
            "-t", "raw", "-r", str(RATE), "-e", "signed", "-b", "16", "-c", "1", "-"] + fx

class _Denoiser:
    """Real-time speex RNN-style noise suppressor, ADDITIVE to the sox filter chain (not a replacement).
    Runs only on audio the squelch has already decided is real signal (gate.open), on the raw demodulated
    PCM before it reaches sox. Lazily creates one NoiseSuppression instance per sample rate actually used
    (24000 Hz for fm/am, 48000 Hz for wbfm), in 10 ms frames. Fails open: any missing package or runtime
    error permanently disables denoising for this process and audio passes through unchanged - this must
    never be able to break the live audio pipe."""
    def __init__(self):
        self._ns = {}
        self._disabled = False
    def _get(self, rate):
        ns = self._ns.get(rate)
        if ns is None:
            from speexdsp_ns import NoiseSuppression       # imported lazily so a missing package only disables this stage
            fs = 480 if rate == 48000 else 240              # 10 ms frame
            ns = NoiseSuppression.create(fs, rate)
            self._ns[rate] = ns
        return ns
    def process(self, b, rate):
        if self._disabled: return b
        try:
            ns = self._get(rate)
            fs = 480 if rate == 48000 else 240
            frame_bytes = fs * 2                             # 16-bit mono samples
            n = len(b) // frame_bytes
            if n == 0: return b
            out = bytearray()
            for i in range(n):
                out += ns.process(b[i * frame_bytes:(i + 1) * frame_bytes])
            rem = len(b) - n * frame_bytes
            if rem: out += b[-rem:]                          # odd remainder (short final read): pass through untouched
            return bytes(out)
        except Exception:
            self._disabled = True                            # one failure disables it for good; never risk the live pipe
            return b

DENOISE = _Denoiser()

class ZcrSquelch:
    """open when smoothed zero-crossing rate < thresh (signal), close when > thresh+0.08; 0.8 s hang. manual: 0 = always open,
    1..60 maps to threshold 0.55..0.22 (1 = most sensitive, 60 = tightest). None = auto: adaptive, idle-noise ZCR minus a margin. Measured: idle hiss ZCR 0.50-0.67,
    strong voice 0.15-0.25, weak voice 0.30-0.45, DC-filter settle ramps 0.03->0.5 over 1.6 s (hence SETTLE)."""
    def __init__(self, mode, manual=None):
        self.mode = mode; self.manual = manual
        self.open = mode == "wbfm"; self.hang = 0; self.n = 0; self.zcr = 0.0; self.level = 0.0
        self.thresh = 0.35 if manual is None else (0.55 - 0.33 * min(60, max(1, manual)) / 60.0)
        self.ema = None
        # adaptive: learn this channel's own idle-noise ZCR (FM ~0.60-0.67, AM ~0.48) and open on anything clearly below it.
        # Much more sensitive than a fixed 0.35 on FM, still no static: the gap to the noise is fixed, not the threshold.
        self.noise = None; self.margin = 0.07 if mode != "am" else 0.05      # (v178: AM 0.06 -> 0.05, weak aircraft were closing between words)
        self.noise_n = 0; self.lvl_max = 0.0                               # (v193) blocks the idle-noise estimate was learned from; the loudest recent block
        self._m0 = self.margin                                              # auto = as open as possible without static (measured hiss ZCR 0.50-0.67, voice 0.10-0.35)
        self.wide = False; self.hang_blocks = 30 if mode == "am" else 15    # (v178) airband: 3 s hang - a pilot/controller exchange stays in one piece; set_wide(): a known / expected voice channel opens on weaker audio and hangs longer
        self.always = (manual == 0) or mode == "wbfm"
        self.hist = []; self.data = False; self.data_blocks = 0; self.mute_data = True; self.data_latched = False
        self.detect_data = True      # False on known voice channels (repeaters, airport, FRS): a repeater tail / courtesy tone is NOT data
        self.open_levels = []; self.gap = 1; self.voice_cv = 0.0; self.open_blocks = 0      # syllable-rhythm detector across bursts
        self.hx = 0.0; self.hy = 0.0; self.hp = mode != "wbfm"           # high-pass state (not for broadcast FM: sox handles it)
        # (v178) look-ahead for AM: the output runs 2 blocks (0.2 s) behind the decision, so when the squelch opens the 0.2 s it
        # needed to make up its mind is played too instead of being lost (the first syllable of every airband call was clipped).
        self.look_n = 2 if mode == "am" else 0; self.look = []; self._aud = b""; self._closed = False
    def set_wide(self, on=True):
        """Known or expected transmissions (directory channels, confirmed voice, a channel the user marked 'open more'):
        threshold 0.03 closer to the hiss (opens on weaker, noisier speech) and a 2.5 s hang so the reply is not clipped."""
        self.wide = bool(on); self.margin = (0.04 if self.mode != "am" else 0.03) if on else self._m0
        self.hang_blocks = (40 if on else 30) if self.mode == "am" else (25 if on else 15)
    def process(self, b):
        out = self._proc(b)
        if not self.look_n or self.always or len(b) < 4: return out
        self.look.append([out, self._aud, self._closed])
        if self.open:                                                   # just opened (or still open): release what was held back while closed
            for e in self.look:
                if e[2]: e[0] = e[1]; e[2] = False
        if len(self.look) <= self.look_n: return bytes(len(b))
        return self.look.pop(0)[0]
    def _proc(self, b):
        self._closed = False
        a = array.array("h", b); n = len(a)
        if n < 2: return b
        self.n += 1
        # one-pole high-pass (removes DC / AM carrier steps), then zero crossings and rms, in one pass
        prev = True; z = 0; acc = 0
        if self.hp:
            hx = self.hx; hy = self.hy; A = HP_A
            for i in range(n):
                x = a[i]; hy = A * hy + (x - hx); hx = x
                y = int(hy)
                if y > 32767: y = 32767
                elif y < -32768: y = -32768
                a[i] = y
                s = y >= 0
                if i and s != prev: z += 1
                prev = s; acc += y * y
            self.hx = hx; self.hy = hy
            b = a.tobytes()
        else:
            prev = a[0] >= 0
            for x in a:
                s = x >= 0
                if s != prev: z += 1; prev = s
                acc += x * x
        self.zcr = z / n; self.level = math.sqrt(acc / n)
        if self.always: self.open = True; return b
        if self.n < SETTLE: self.open = False; return bytes(len(b))      # rtl_fm DC filter settling (measured ~1.6 s)
        # smooth the ZCR over ~0.3 s so a weak voice signal (ZCR 0.3-0.45, measured on 444.8) does not chatter
        self.ema = self.zcr if self.ema is None else 0.5 * self.zcr + 0.5 * self.ema
        if self.manual is None:
            # noise estimate: slow EMA of the ZCR while closed (fast at first so it settles within ~1 s)
            # (v193) Learned from blocks that look like noise, and from nothing else. The estimate used to start from the first block
            # whatever was on it: tuning in while somebody was talking, or onto a digital signal, took the SIGNAL for the idle noise.
            # From then on "open" needed something even quieter than that signal, so the squelch chattered through the transmission
            # (open one block, 1.5 s of hang, shut, open again - the hang is silent until the level is known) and stayed deaf to weak
            # voice for the next 10-20 s. A scan visit starts a new squelch every time, so this hit every call already in progress.
            # Until noise has been seen the fixed 0.35 threshold decides, which is what the squelch was before it adapted.
            # FM only: the pause between two words is a quiet carrier - hiss-like but faint (a few hundred, where an empty channel
            # is the loudest thing a discriminator puts out: 9,500 measured on 146.52, 4,800 on 443.9 with a faint carrier under
            # it). Those pauses are not idle noise either; learned as such they left the squelch open on the full hiss after the call.
            self.lvl_max = max(self.level, 0.99 * self.lvl_max)
            if not self.open and self.zcr > self.thresh and (self.mode != "fm" or self.level >= 0.4 * self.lvl_max):
                if self.noise is None: self.noise = self.zcr
                else:
                    a = 0.2 if self.noise_n < 12 else 0.02
                    self.noise = (1 - a) * self.noise + a * self.zcr
                self.noise_n += 1
            if self.noise is not None: self.thresh = min(0.55, max(0.30, self.noise - self.margin))
        if self.open: sig = self.ema < (min(self.thresh + 0.08, self.noise - 0.04) if self.noise is not None else self.thresh + 0.05)   # hysteresis, but always below the hiss
        else:         sig = self.ema < self.thresh
        # data / digital detector: a real voice signal pulses at syllable rate; data and digital carriers do not.
        if sig:
            self.hist.append((self.level, self.zcr)); self.hist = self.hist[-8:]
            if len(self.hist) >= 6:                                   # 0.6 s is enough: voice never holds a flat envelope that long
                lv = [h[0] for h in self.hist]; zc = [h[1] for h in self.hist]
                mean = sum(lv) / len(lv); cv = (max(lv) - min(lv)) / mean if mean else 0
                zspread = max(zc) - min(zc)
                self.data = self.detect_data and ((cv < 0.15 and zspread < 0.04) or (mean > 3000 and cv < 0.25 and zspread < 0.06))
                if self.data: self.data_latched = True
                if self.data: self.data_blocks += 1
        else:
            self.hist = []; self.data = False
        # digital voice (DMR/P25/NXDN) comes as flat-topped bursts with short gaps; real speech rises and falls every
        # syllable. Track the level of open blocks ACROSS gaps (up to 0.5 s) and look at how much it varies.
        if sig:
            if self.gap == 0:                                   # skip the first block after a gap (burst ramp-up / squelch transition)
                self.open_levels.append((self.level, self.zcr)); self.open_levels = self.open_levels[-20:]
            self.gap = 0; self.open_blocks += 1
        else:
            self.gap += 1
            if self.gap > 5: self.open_levels = []
        if len(self.open_levels) >= 6:
            lv = [x[0] for x in self.open_levels]; zc = [x[1] for x in self.open_levels]; mean = sum(lv) / len(lv)
            self.voice_cv = (sum((x - mean) ** 2 for x in lv) / len(lv)) ** 0.5 / mean if mean else 0
            zspread = max(zc) - min(zc)
            # measured: voice level CV 0.24-0.5 with ZCR spread 0.07-0.10; digital bursts CV < 0.05 within a burst and ZCR spread ~0.02
            if self.detect_data and len(self.open_levels) >= 12 and mean > 1200 and (self.voice_cv < 0.12 or (self.voice_cv < 0.25 and zspread < 0.04)):
                self.data = True; self.data_latched = True; self.data_blocks += 1
        if sig: self.hang = self.hang_blocks                               # 1.5 s hang (2.5 s on a known channel): pauses between words stay open
        elif self.hang > 0: self.hang -= 1
        self.open = (sig or self.hang > 0) and not ((self.data or (self.data_latched and self.data_blocks >= 10)) and self.mute_data)
        if self.open and not sig:
            # hang time: keep the audio only if it is still quiet carrier (a pause in speech). When the carrier drops the
            # discriminator output jumps to full-scale noise (~7400 rms) - that burst is the loud "ksssh" tail: mute it.
            ref = (sum(x[0] for x in self.open_levels) / len(self.open_levels)) if self.open_levels else 0
            if ref <= 0 or self.level > 2.5 * ref: return bytes(len(b))
        self._aud = b; self._closed = not self.open
        return b if self.open else bytes(len(b))

DCS_CODES = [23,25,26,31,32,36,43,47,51,53,54,65,71,72,73,74,114,115,116,122,125,131,132,134,143,145,152,155,156,162,165,172,174,205,212,223,
             225,226,243,244,245,246,251,252,255,261,263,265,266,271,274,306,311,315,325,331,332,343,346,351,356,364,365,371,411,412,413,423,431,
             432,445,446,452,454,455,462,464,465,466,503,506,516,523,526,532,546,565,606,612,624,627,631,632,654,662,664,703,712,723,731,732,734,743,754]
def _golay_encode(data12):
    """(23,12) Golay: returns 23-bit codeword (int) for a 12-bit value; generator polynomial 0xAE3 (x^11+x^10+x^6+x^5+x^4+x^2+1)."""
    g = 0xAE3; r = data12 << 11
    for i in range(22, 10, -1):
        if r & (1 << i): r ^= g << (i - 11)
    return (data12 << 11) | (r & 0x7FF)
_DCS_WORDS = {}
for _c in DCS_CODES:
    _d = int(str(_c), 8) | (0b100 << 9)                       # 9 code bits (octal) + fixed 100
    _w = _golay_encode(_d)
    _DCS_WORDS[_w] = _c
def _rot23(w, k): return ((w << k) | (w >> (23 - k))) & 0x7FFFFF

class ToneDecoder:
    """CTCSS (Goertzel, 2 s Hann windows at 1 kHz) and DCS (134.4 bps Golay words) on the raw discriminator audio while the
    squelch is open. Reports only after two agreeing windows, so a mis-read tone does not reach the CHIRP file."""
    def __init__(self, rate=RATE):
        self.rate = rate; self.dec = rate // 1000; self.buf = []; self.acc = 0; self.k = 0
        self.ctcss = {}; self.dcs = {}; self.last = ""; self._cand = None; self._cand_n = 0
    def feed(self, b):
        a = array.array("h", b); d = self.dec
        for x in a:                                   # decimate 24 -> 1 kHz by averaging (cheap low-pass)
            self.acc += x; self.k += 1
            if self.k == d: self.buf.append(self.acc / d); self.acc = 0; self.k = 0
        if len(self.buf) >= 2000:
            self._analyse(self.buf[:2000]); self.buf = self.buf[1000:]          # 2 s window, 1 s hop
    def _analyse(self, y):
        n = len(y); mean = sum(y) / n; y = [v - mean for v in y]
        # Hann window + Goertzel at every CTCSS tone
        win = [0.5 - 0.5 * math.cos(2 * math.pi * i / (n - 1)) for i in range(n)]
        yw = [a * b for a, b in zip(y, win)]
        def g(f):
            w = 2 * math.pi * f / 1000.0; c = 2 * math.cos(w); s0 = s1 = s2 = 0.0
            for v in yw: s0 = v + c * s1 - s2; s2 = s1; s1 = s0
            return s1 * s1 + s2 * s2 - c * s1 * s2
        p = {f: g(f) for f in CTCSS}
        best = max(p, key=p.get); vals = sorted(p.values()); second = vals[-2] if len(vals) > 1 else 1.0
        total = sum(v * v for v in yw) + 1e-9
        found = None
        if p[best] > 8 * max(second, 1e-9) and p[best] / n > 0.08 * total / n * 4:
            found = ("CTCSS", best)
        else:
            code = self._dcs(y)
            if code: found = ("DCS", code)
        if found and found == self._cand: self._cand_n += 1
        else: self._cand, self._cand_n = found, 1 if found else 0
        if found and self._cand_n >= 2:
            k, v = found
            if k == "CTCSS": self.ctcss[v] = self.ctcss.get(v, 0) + 1; self.last = f"CTCSS {v}"
            else: self.dcs[v] = self.dcs.get(v, 0) + 1; self.last = f"DCS {v:03d}"
    def _dcs(self, y):
        """134.4 bps NRZ sub-audio: slice at the bit rate, try every 23-bit window, accept a known Golay word (either polarity)."""
        n = len(y); spb = 1000.0 / 134.4
        if n < 2 * 23 * spb: return None
        # remove slow drift, then slice
        lp = []; acc = 0.0
        for v in y: acc = 0.98 * acc + 0.02 * v; lp.append(v - acc)
        # find the bit phase that maximises |sample| at bit centres
        best_phase, best_e = 0.0, -1
        for ph in [i * spb / 8 for i in range(8)]:
            idx = [int(ph + i * spb + spb / 2) for i in range(int((n - ph) / spb) - 1)]
            e = sum(abs(lp[i]) for i in idx if i < n)
            if e > best_e: best_e, best_phase = e, ph
        bits = [1 if lp[int(best_phase + i * spb + spb / 2)] > 0 else 0 for i in range(int((n - best_phase) / spb) - 1)]
        if len(bits) < 23: return None
        hits = {}
        for start in range(0, len(bits) - 23):
            w = 0
            for i in range(23): w |= bits[start + i] << i            # LSB first
            for cand in (w, (~w) & 0x7FFFFF):
                if cand in _DCS_WORDS: hits[_DCS_WORDS[cand]] = hits.get(_DCS_WORDS[cand], 0) + 1
        if not hits: return None
        code, cnt = max(hits.items(), key=lambda kv: kv[1])
        return code if cnt >= 3 else None
    def summary(self):
        if self.ctcss:
            t = max(self.ctcss, key=self.ctcss.get); return f"CTCSS {t} ({self.ctcss[t]}s)"
        if self.dcs:
            c = max(self.dcs, key=self.dcs.get); return f"DCS {c:03d} ({self.dcs[c]}s)"
        return ""

class Recorder:
    def __init__(self, folder, mhz, name):
        self.dir = os.path.join(folder, "recordings"); os.makedirs(self.dir, exist_ok=True)
        self.mhz = mhz; self.name = "".join(c if c.isalnum() or c in "-_." else "_" for c in (name or "chan"))[:16]
        self.f = None; self.path = None; self.closed = 0; self.bytes = 0; self.count = 0; self.data = False
    def feed(self, b, open_, data=False):
        if data: self.data = True
        if open_:
            if not self.f:
                self.path = os.path.join(self.dir, f"{self.mhz:.4f}_{self.name}_{dt.datetime.now():%Y%m%d_%H%M%S}.wav")
                self.f = open(self.path, "wb"); self.f.write(wav_header(0)); self.bytes = 0
            self.f.write(b); self.bytes += len(b); self.closed = 0
        elif self.f:
            self.closed += 1
            if self.closed > 20: self.close()
    def close(self):
        if not self.f: return
        self.f.seek(0); self.f.write(wav_header(self.bytes)); self.f.close(); self.f = None
        bad = self.bytes < RATE * 2 * 0.7 or self.data
        self.data = False
        if bad:
            try: os.remove(self.path)
            except OSError: pass
        else: self.count += 1

def wav_header(nbytes):
    import struct
    return (b"RIFF" + struct.pack("<I", (nbytes + 36) & 0xFFFFFFFF) + b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, RATE, RATE * 2, 2, 16)
            + b"data" + struct.pack("<I", nbytes & 0xFFFFFFFF))

class Session:
    """One tuned channel. read() yields 24 kHz processed PCM blocks; stop() tears everything down."""
    def __init__(self, mhz, mode, manual=None, voice=True, gain="49.6", record_dir=None, name="", log_path=None, raw_path=None):
        self.mhz = mhz; self.mode = mode; self.gate = ZcrSquelch(mode, manual); self.tones = ToneDecoder()
        self.rawf = open(raw_path, "wb") if raw_path else None
        self.rec = Recorder(record_dir, mhz, name) if record_dir else None
        self.alive = True; self.err = ""; self.last_data = time.time(); self.keep_all = False      # keep_all: a user listen - never mute, never drop a clip as 'data'
        lf = open(log_path, "a") if log_path else subprocess.DEVNULL
        if log_path:
            with open(log_path, "a") as f: f.write(f"\n=== {dt.datetime.now():%H:%M:%S} {' '.join(rtl_cmd(mhz, mode, gain))}\n")
        self.rtl = subprocess.Popen(rtl_cmd(mhz, mode, gain), stdout=subprocess.PIPE, stderr=lf)
        self.sox = subprocess.Popen(sox_cmd(mode, voice), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=lf)
        self.blk = 9600 if mode == "wbfm" else 4800
        self.denoise_rate = 48000 if mode == "wbfm" else RATE
        self.t = threading.Thread(target=self._pump, daemon=True); self.t.start()
    def _pump(self):
        try:
            while self.alive:
                b = self.rtl.stdout.read(self.blk)
                if not b: self.err = "rtl_fm stopped"; break
                self.last_data = time.time()
                out = self.gate.process(b)
                if self.rawf: self.rawf.write(b)
                if self.gate.open and self.mode == "fm": self.tones.feed(b)
                if self.gate.open and self.mode != "am": out = DENOISE.process(out, self.denoise_rate)   # (v178) not on AM: on weak airband the suppressor ate quiet speech along with the hiss; additive: runs on top of the existing sox chain, not instead of it
                self.sox.stdin.write(out); self.sox.stdin.flush()
        except Exception as e:
            if self.alive: self.err = repr(e); self.alive = False     # let the watchdog see it at once
        finally:
            try: self.sox.stdin.close()
            except Exception: pass
            if self.rawf:
                try: self.rawf.close()
                except Exception: pass
    def read(self, n=4800):
        b = self.sox.stdout.read(n)
        if b and self.rec: self.rec.feed(b, self.gate.open, self.gate.data and not self.keep_all)
        return b
    def stop(self):
        self.alive = False
        # rtl_fm: SIGINT first (its own handler cancels the USB transfer cleanly - SIGTERM mid-transfer trips libusb's
        # "usbi_mutex_unlock" assertion and can leave the dongle wedged for the next open), then terminate, then kill.
        try: self.rtl.send_signal(signal.SIGINT)
        except Exception: pass
        try: self.rtl.wait(timeout=2)
        except Exception:
            try: self.rtl.terminate()
            except Exception: pass
        try: self.sox.terminate()
        except Exception: pass
        for p in (self.rtl, self.sox):
            try: p.wait(timeout=3)
            except Exception:
                try: p.kill()
                except Exception: pass
        if self.rec: self.rec.close()
    def meter(self):
        g = self.gate
        return {"open": g.open, "zcr": round(g.zcr, 2), "thresh": round(g.thresh, 2), "level": round(g.level), "tones": self.tones.summary(), "tone_now": self.tones.last, "err": self.err,
                "data": g.data or g.data_latched, "data_secs": round(g.data_blocks / 10, 1), "voice_cv": round(g.voice_cv, 2),
                "denoise_off": DENOISE._disabled}      # (v169) visibility into the speex denoiser's fail-open state, for diagnostics - never gates playback


class Monitor:
    """One dongle, many channels: hops through a list, dwells ~0.5 s on each, stays while someone is talking (like a
    scanner radio). Same interface as Session (read / stop / meter / gate / mhz / rtl) so the dashboard streams it the
    same way. Each hop restarts rtl_fm (~0.5 s), so 10 channels cycle in roughly 10 s."""
    DWELL = 0.3            # seconds listened per channel when nothing is heard (after the settle)
    HOLD = 6.0             # seconds to stay after the squelch closes before moving on (the reply usually comes within 3-5 s)
    def __init__(self, channels, manual=None, voice=True, gain="49.6", record_dir=None, log_path=None, on_heard=None):
        # channels: [(mhz, mode, name)]
        self.channels = [c for c in channels if c[0] > 0] or [(146.52, "fm", "146.52")]
        self.manual = manual; self.voice = voice; self.gain = gain; self.record_dir = record_dir; self.log_path = log_path
        self.on_heard = on_heard
        self.alive = True; self.err = ""; self.idx = -1; self.ses = None; self.mhz = 0.0; self.mode = "fm"; self.name = ""
        self.heard = []            # (mhz, name, when, secs) history for the UI
        self.lock = threading.Lock(); self.q = []; self.qcond = threading.Condition(self.lock)
        self.t = threading.Thread(target=self._run, daemon=True); self.t.start()
    @property
    def gate(self): return self.ses.gate if self.ses else ZcrSquelch("fm", self.manual)
    @property
    def tones(self): return self.ses.tones if self.ses else ToneDecoder()
    @property
    def rtl(self):
        class _P:                                  # the monitor restarts its own receiver on every hop: never "dead" to the watchdog
            def poll(self_inner): return None
        return _P()
    def _run(self):
        while self.alive:
            self.idx = (self.idx + 1) % len(self.channels)
            mhz, mode, name = self.channels[self.idx]
            self.mhz, self.mode, self.name = mhz, mode, name
            try:
                ses = Session(mhz, mode, self.manual, self.voice, self.gain, self.record_dir, name, self.log_path)
            except Exception as e:
                self.err = repr(e); time.sleep(1); continue
            self.ses = ses
            t0 = time.time(); opened_at = None; last_open = None
            try:
                while self.alive:
                    b = ses.read(4800)
                    if not b: break
                    g = ses.gate
                    if g.open and not (g.data or g.data_latched):
                        if opened_at is None: opened_at = time.time()
                        last_open = time.time()
                        with self.qcond:
                            self.q.append(b); self.q = self.q[-40:]; self.qcond.notify_all()
                    elif opened_at is None and time.time() - t0 > self.DWELL + SETTLE * 0.1: break
                    elif last_open and time.time() - last_open > self.HOLD: break
                    elif g.data_latched and g.data_blocks >= 10: break
            except Exception as e:
                self.err = repr(e)
            finally:
                if opened_at:
                    secs = round((last_open or time.time()) - opened_at, 1)
                    self.heard.append({"mhz": mhz, "name": name, "when": dt.datetime.now().strftime("%H:%M:%S"), "secs": secs, "tones": ses.tones.summary()})
                    self.heard = self.heard[-50:]
                    if self.on_heard:
                        try: self.on_heard(mhz, name, secs, ses.tones.summary())
                        except Exception: pass
                try: ses.stop()
                except Exception: pass
                self.ses = None
                time.sleep(0.6)                       # let libusb release the dongle before the next hop
    def read(self, n=4800):
        """Blocks until audio is available (someone talking) or 0.5 s passed (then returns silence to keep the stream alive)."""
        with self.qcond:
            if not self.q: self.qcond.wait(0.1)        # 0.1 s of silence per 0.1 s of waiting: the stream stays in real time
            if self.q: return self.q.pop(0)
        return bytes(n) if self.alive else b""
    def stop(self):
        self.alive = False
        ses = self.ses
        if ses:
            try: ses.stop()
            except Exception: pass
        with self.qcond: self.qcond.notify_all()
    def meter(self):
        ses = self.ses
        m = ses.meter() if ses else {"open": False, "zcr": 0, "thresh": 0, "level": 0, "tones": "", "tone_now": "", "err": self.err, "data": False, "data_secs": 0, "voice_cv": 0}
        g = ses.gate if ses else None
        chans = [{"mhz": c[0], "name": c[2], "mode": c[1], "level": round(g.level) if (g and i == self.idx) else 0, "open": bool(g and g.open and i == self.idx), "carrier": False, "snr": None, "data": False}
                 for i, c in enumerate(self.channels)]
        m["monitor"] = {"mhz": self.mhz, "name": self.name, "n": len(self.channels), "idx": self.idx, "heard": self.heard[-8:], "engine": "hop", "chans": chans,
                        "active": self.mhz if (g and g.open) else None, "group": self.idx + 1, "groups": len(self.channels), "load": 0}
        return m
