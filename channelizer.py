#!/usr/bin/env python3
"""Channelizer: one RTL-SDR dongle, many channels at once.

rtl_tcp streams 2.4 MS/s of raw IQ; every channel inside that window is mixed to baseband, decimated to 24 kHz
and demodulated (NFM / AM / WFM) in numpy - all of them at the same time, no retuning, nothing missed. Channel
groups wider than the window are visited in turn (a hop costs ~50 ms, not the ~700 ms of restarting rtl_fm).
Each channel gets its own squelch (audio.ZcrSquelch), recorder and tone decoder; the strongest open channel is
what you hear, the others are still logged and recorded.

Interface matches audio.Session / audio.Monitor: read() -> 24 kHz int16 PCM blocks, stop(), meter(), gate, mhz, rtl.
Needs numpy + scipy (Install channelizer.command). Falls back to audio.Monitor when they are missing.
"""
import math, os, socket, struct, subprocess, threading, time, datetime as dt
import numpy as np
from scipy import signal

import audio as AU

def _fs():
    """(v176) Dongle sample rate. 2.4 MS/s unless _state/sample_rate.txt (or SDR_FS) says 2880000: the wider rate puts ~20% more
    spectrum in every window (fewer hops for Monitor and the sweep) but pushes more data through USB - only switch it on after
    `rtl_test -s 2880000` shows no lost samples on this dongle and port."""
    try: v = int(os.environ.get("SDR_FS") or open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_state", "sample_rate.txt")).read().strip())
    except Exception: v = 2_400_000
    return v if v in (2_400_000, 2_880_000) else 2_400_000
FS = _fs()                     # dongle sample rate
RATE = 24_000                  # audio / baseband rate
DECIM1, DECIM2 = FS // 240_000, 10        # FS -> 240 kS/s -> 24 kS/s
BLOCK = FS // 10               # 0.1 s of IQ per processing block
WINDOW = FS - 400_000          # usable span (the outer 200 kHz on each side are rolled off by the tuner filter)
TCP_PORT = 1234
OPEN_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitor_opens.log")   # one line per squelch open (name, secs, max modulation, mean SNR, voice verdict)

def _sos(cut, fs, order=6):
    return signal.butter(order, cut / (fs / 2), output="sos")

class Channel:
    def __init__(self, mhz, mode, name, center_hz, manual=None, record_dir=None):
        self.mhz = mhz; self.mode = mode; self.name = name
        self.offset = mhz * 1e6 - center_hz
        self.rot = complex(1, 0)                   # phase carried between blocks (one complex multiply, not a new exp() per block)
        self._nco = None
        self.gate = AU.ZcrSquelch("fm" if mode == "wbfm" else mode, manual)
        self.tones = AU.ToneDecoder()
        self.rec = AU.Recorder(record_dir, mhz, name) if record_dir else None
        self.last_raw = b""
        # stateful filters: wide first stage, narrow second stage; narrower for NFM/AM than for WFM
        self.sos1 = _sos(80_000 if mode != "wbfm" else 110_000, FS, 4).astype(np.float32); self.zi1 = signal.sosfilt_zi(self.sos1).astype(np.complex64)
        self.sosw = _sos(11_000, FS // DECIM1, 6).astype(np.float32); self.ziw = signal.sosfilt_zi(self.sosw).astype(np.float32)   # WFM audio low-pass before decimation
        bw2 = 9_000 if mode == "fm" else (5_500 if mode == "am" else 11_000)
        self.sos2 = _sos(bw2, FS // DECIM1, 6).astype(np.float32); self.zi2 = signal.sosfilt_zi(self.sos2).astype(np.complex64)
        self.last = complex(1, 0)                  # previous baseband sample for the FM discriminator
        self.sosv = _sos(3_400 if mode == "am" else 2_800, RATE, 4); self.ziv = signal.sosfilt_zi(self.sosv)      # voice low-pass
        self.sosh = signal.butter(2, 300 / (RATE / 2), btype="high", output="sos"); self.zih = signal.sosfilt_zi(self.sosh)
        self.agc = 1.0; self.car = None              # car: carrier level smoothed ACROSS blocks (a per-block value put a step at every 0.1 s edge)
        self.level = 0.0; self.open_since = None; self.last_open = 0.0; self.secs_open = 0.0
        # AM (airband): a real carrier squelch. Channel power vs this channel's own noise floor - the way a scanner does it.
        # Far more sensitive than judging the demodulated audio, because an AM carrier is there even between words.
        self.floor_db = None; self.pow_db = -99.0; self.am_open = False; self.am_hang = 0; self.carrier = False
        # AM (airband): a weak tower/aircraft carrier lifts the envelope power by only ~3 dB here (measured 5 Nov on 118.35); idle jitter is +-0.3 dB, so 1.5 dB is safe
        self.carrier_margin = 1.5 if mode == "am" else 4.0
        self.weak_blocks = 0                       # consecutive blocks open on a weak carrier without voice evidence
        self.open_blocks = 0                       # consecutive blocks the AM carrier squelch has been open
        self.nblk = 0                              # blocks processed since this channel object was made (retune)
        self.pinned = False                        # set when the carrier never dropped for 20 s: a beacon / ATIS / data, not a conversation
        self.env_hist = []; self.voice_mod = 0.0; self.voice_ok = False; self.am_blocks = 0   # syllabic modulation detector (AM): speech ~0.5-1.0, steady noise/carrier < 0.15
        self.pending = []; self.pending_raw = []; self.live = b""; self.max_mod = 0.0; self.open_n = 0; self.snr_sum = 0.0; self.confirmed = True   # confirmed: the last AM open carried speech (FM: always)
        self.nb = None                                             # noise blanker state
    def process(self, iq, t_block):
        """iq: complex64 block at FS. Returns (audio_int16_bytes or None, is_open)."""
        n = len(iq)
        if self._nco is None or len(self._nco) != n:
            k = np.arange(n, dtype=np.float64)
            self._nco = np.exp(-2j * np.pi * (self.offset / FS) * k).astype(np.complex64)
            self._step = np.exp(-2j * np.pi * (self.offset / FS) * n)
        x = iq * (self._nco * np.complex64(self.rot))
        self.rot *= self._step; self.rot /= abs(self.rot)
        x, self.zi1 = signal.sosfilt(self.sos1, x, zi=self.zi1); x = x[::DECIM1]          # 240 kS/s
        if self.mode == "wbfm":
            # broadcast FM: demodulate at 240 kS/s (needs the full +-75 kHz deviation), then low-pass and decimate the audio
            prev = np.concatenate(([self.last], x[:-1])); self.last = x[-1]
            d = np.angle(x * np.conj(prev)).astype(np.float32)
            d, self.ziw = signal.sosfilt(self.sosw, d, zi=self.ziw); audio = d[::DECIM2] * (16384.0 / np.pi) * 0.6
        else:
            x, self.zi2 = signal.sosfilt(self.sos2, x, zi=self.zi2); x = x[::DECIM2]       # 24 kS/s complex baseband
            if self.mode == "am":
                env = np.abs(x)
                # noise blanker: clip impulse spikes (ignition / lightning) to 3x the median envelope
                med = float(np.median(env)) + 1e-9; env = np.minimum(env, 3.0 * med)
                self.pow_db = 10 * np.log10(float(np.mean(env * env)) + 1e-12)
                self.nblk += 1
                if self.nblk <= 5: self.floor_db = self.pow_db                       # (5 Nov) tuner gain ramps for ~0.5 s after a retune: the floor is learned after that, never from the quiet start
                elif not self.am_open: self.floor_db = min(self.floor_db + 0.05, 0.9 * self.floor_db + 0.1 * self.pow_db)   # track the floor slowly upward, quickly downward
                else:
                    # (5 Nov) while open the floor still creeps up 0.02 dB/block (a 5 s call loses 1 dB), and after 20 s of continuous "open"
                    # the level IS the floor (a tuner settle step or a continuous carrier had frozen the old one and pinned the window)
                    self.floor_db = min(self.floor_db + 0.02, self.pow_db); self.open_blocks += 1
                    if self.open_blocks >= 200: self.floor_db = self.pow_db; self.open_blocks = 0; self.pinned = True   # continuous carrier: not voice
                if not self.am_open: self.open_blocks = 0
                snr = self.pow_db - self.floor_db
                # (v178) once speech is confirmed the carrier may sag to 0.3 dB over the floor before the channel is called closed (was 0.8 dB:
                # a weak aircraft fluttering around the threshold was chopped into pieces)
                self.am_open = self.nblk > 5 and (snr > self.carrier_margin or (self.am_open and snr > self.carrier_margin - 0.7)); self.carrier = self.am_open
                # audio = envelope minus the carrier, normalised by the carrier (so a weak carrier and a strong one sound the same).
                # The carrier estimate is smoothed across blocks: using each block's own median put a DC step (a click) at every edge.
                self.car = med if self.car is None else 0.85 * self.car + 0.15 * med
                audio = (env - self.car) * (10000.0 / self.car)
            else:
                # carrier detector (fast, 1 block): channel power against this channel's own noise floor. Decides the HOP, not the audio.
                self.pow_db = 10 * np.log10(float(np.mean(np.abs(x) ** 2)) + 1e-12)
                self.nblk += 1
                if self.nblk <= 5: self.floor_db = self.pow_db                       # (5 Nov) see the AM branch: no floor from the retune ramp
                elif not self.carrier: self.floor_db = min(self.floor_db + 0.05, 0.9 * self.floor_db + 0.1 * self.pow_db)
                else:                                                            # (5 Nov) same as AM: creep while open, re-learn after 20 s continuous
                    self.floor_db = min(self.floor_db + 0.02, self.pow_db); self.open_blocks += 1
                    if self.open_blocks >= 200: self.floor_db = self.pow_db; self.open_blocks = 0; self.pinned = True
                if not self.carrier: self.open_blocks = 0
                self.carrier = (self.pow_db - self.floor_db) > (self.carrier_margin if not self.carrier else self.carrier_margin - 2.0)
                prev = np.concatenate(([self.last], x[:-1])); self.last = x[-1]
                d = np.angle(x * np.conj(prev))                                          # discriminator, +-pi
                audio = d * (16384.0 / np.pi)
        if self.mode == "am":
            fr = 480                                                                     # 20 ms frames at 24 kHz; 1 s history
            seg = audio[:len(audio) - len(audio) % fr]
            if len(seg) >= fr:
                rms = np.sqrt(np.mean(seg.reshape(-1, fr) ** 2, axis=1)); self.env_hist.extend(rms.tolist()); self.env_hist = self.env_hist[-50:]
                h = np.array(self.env_hist); self.voice_mod = float(h.std() / (h.mean() + 1e-9)) if len(h) >= 20 else 0.0
        raw = np.clip(audio, -32767, 32767).astype(np.int16).tobytes()
        gated = self.gate.process(raw)                                                   # squelch on the raw discriminator audio
        g = self.gate
        if self.mode == "am" and g.manual is None:
            # carrier squelch decides for AM. A strong carrier (>= 3 dB over the floor) opens on its own; a weak one (1.5-3 dB,
            # the 5 Nov threshold) also needs the audio noise-squelch to hear voice-like audio, so a noisy channel cannot pin the hop
            zopen = g.open                                             # the ZCR gate's own verdict on this block's audio
            strong = (self.pow_db - (self.floor_db if self.floor_db is not None else self.pow_db)) >= 3.0
            # (5 Nov 02:00) AM opener = carrier present AND speech-like modulation. Measured on tonight's clips: real speech has envelope
            # modulation depth 0.5-1.0 over a second, the noise clips whisper called "Thank you" had 0.06-0.11. The carrier gets 0.6 s to
            # prove itself; once speech is confirmed the channel stays open until the carrier drops (pauses do not close it).
            if self.am_open: self.am_blocks += 1
            else:
                self.am_blocks = 0
                if self.am_hang <= 0: self.voice_ok = False                      # (v178) a 0.1-0.4 s carrier dropout does not un-confirm the call
            if self.am_open and self.am_blocks == 1: self.confirmed = False           # a new open: nothing proven yet
            snr_now = self.pow_db - (self.floor_db if self.floor_db is not None else self.pow_db)
            # (v155 6 Nov) a carrier 8+ dB over the floor is a real transmission (noise blips sit at ~2 dB): it opens at once and the scanner STAYS until it
            # drops - the speech detector alone refused weak-modulation calls (carrier 13 dB up, mod 0.19) after 0.6 s and the scan hopped away mid-call
            # (v178) monitor_opens.log, 8-10 Nov: 94% of 3,312 airband opens were cut at 0.6 s and half of them re-opened within 3 s - weak calls
            # (carrier ~2 dB up, modulation ~0.14) were played in 0.6 s pieces. A carrier 3+ dB up with some modulation, or one that simply
            # lasts 0.8 s at 2.5+ dB, is a transmission too (noise blips do neither); weak carriers get 1.0 s to prove themselves instead of 0.6.
            # (v180) The v178 rule above was wrong and is gone. Live test 10 Nov 21:52-21:55: the opens it let through (133.4, carrier ~3 dB up,
            # modulation 0.05-0.23, up to 11.7 s) all came back from the transcriber empty - weak carriers with no speech, played as hiss. The
            # short 0.6 s opens it was meant to rescue were unconfirmed noise blips that were never played in the first place.
            if self.am_open and (self.voice_mod > 0.3 or snr_now >= 8.0): self.voice_ok = True; self.confirmed = True
            carrier_ok = self.am_open and (self.voice_ok or self.am_blocks <= (12 if snr_now >= 4.0 else 6))
            if carrier_ok and self.voice_ok: self.am_hang = 4                          # confirmed speech: ride through 0.4 s of carrier flutter
            elif not self.am_open and self.am_hang > 0: self.am_hang -= 1
            g.open = g.n > AU.SETTLE and (carrier_ok or (self.am_hang > 0 and not self.am_open))
            self.weak_blocks = 0
            # airband is analog by definition: no digital veto
            g.data = False; g.data_latched = False
            gated = raw if (g.open and self.am_open) else bytes(len(raw))            # during the flutter hang the channel stays open but silent (no hiss burst)
        if g.open and not (g.data or (g.data_latched and g.data_blocks >= 10)):
            if self.open_since is None: self.open_since = t_block
            self.last_open = t_block
            if self.mode == "fm": self.tones.feed(raw)
            a = np.frombuffer(gated, dtype=np.int16).astype(np.float64)
            if self.mode != "wbfm":
                a, self.zih = signal.sosfilt(self.sosh, a, zi=self.zih)
                a, self.ziv = signal.sosfilt(self.sosv, a, zi=self.ziv)
            rms = float(np.sqrt(np.mean(a * a))) + 1e-6
            # AGC: never more than 3x (x10 turned a weak, noisy carrier into loud static - measured as hundreds of 'clicks' per second),
            # slow (0.9/0.1), gliding across the block, and a soft knee instead of a hard clip.
            target = 5000.0; want = min(3.0, target / rms); new_agc = 0.9 * self.agc + 0.1 * want
            ramp = np.linspace(self.agc, new_agc, len(a))
            self.agc = new_agc
            y = a * ramp; y = 28000.0 * np.tanh(y / 28000.0)
            out = y.astype(np.int16).tobytes()
            self.last_raw = gated                                        # the un-filtered squelched audio: the live path sends THIS through sox (same chain as a direct listen)
            self.live = gated
            if self.mode == "am" and not self.voice_ok:                  # (5 Nov) grace period: hold the audio back until the speech detector confirms
                self.pending.append(out); self.pending = self.pending[-8:]; self.pending_raw.append(gated); self.pending_raw = self.pending_raw[-8:]
                self.live = b""                                          # nothing to the speakers yet either: no 0.6 s noise burst on a false carrier
            else:
                if self.rec:
                    for b in self.pending: self.rec.feed(b, True)
                    self.rec.feed(out, True)
                self.live = b"".join(self.pending_raw) + gated           # confirmed: the held 0.6 s plays first, then live
                self.pending = []; self.pending_raw = []
            self.max_mod = max(self.max_mod, self.voice_mod); self.open_n += 1; self.snr_sum += (self.pow_db - (self.floor_db or self.pow_db))
            return out, True
        if self.open_since is not None:
            self.secs_open += (self.last_open - self.open_since) + 0.1; self.open_since = None
            if self.open_n and OPEN_LOG:                                 # one line per open: tune the speech detector from real data
                try:
                    with open(OPEN_LOG, "a") as f: f.write(f"{dt.datetime.now():%m-%d %H:%M:%S} {self.name} {self.mhz:.4f} {self.mode} {self.open_n/10:.1f}s max_mod {self.max_mod:.2f} mean_snr {self.snr_sum/self.open_n:.1f} voice {'yes' if self.voice_ok else 'no'}\n")
                except Exception: pass
            self.max_mod = 0.0; self.open_n = 0; self.snr_sum = 0.0
        self.pending = []; self.pending_raw = []; self.live = b""
        if self.rec: self.rec.feed(b"", False)
        return None, False

class RtlTcp:
    """rtl_tcp server + client socket. Commands: 1 freq, 2 rate, 3 gain mode, 4 tuner gain (tenths dB), 8 AGC."""
    def __init__(self, center_hz, gain_tenths=496, log=None):
        self.proc = subprocess.Popen(["rtl_tcp", "-a", "127.0.0.1", "-p", str(TCP_PORT), "-s", str(FS), "-f", str(int(center_hz)), "-g", f"{gain_tenths/10:.1f}", "-b", "16", "-n", "32"],
                                     stdout=log or subprocess.DEVNULL, stderr=subprocess.STDOUT)
        self.sock = None
        for _ in range(60):
            try:
                s = socket.create_connection(("127.0.0.1", TCP_PORT), timeout=1); s.settimeout(3); self.sock = s; break
            except OSError: time.sleep(0.25)
        if not self.sock: raise RuntimeError("rtl_tcp did not start (dongle busy or missing?)")
        hdr = self._recv(12)                                         # "RTL0" + tuner type + gain count
        self.cmd(3, 1); self.cmd(4, gain_tenths); self.cmd(8, 0); self.cmd(2, FS); self.cmd(1, int(center_hz))
    def cmd(self, c, v):
        self.sock.sendall(struct.pack(">BI", c, int(v) & 0xFFFFFFFF))
    def retune(self, center_hz): self.cmd(1, int(center_hz))
    def _recv(self, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk: raise RuntimeError("rtl_tcp closed the stream")
            buf += chunk
        return bytes(buf)
    def block(self, n=BLOCK):
        raw = np.frombuffer(self._recv(2 * n), dtype=np.uint8).astype(np.float32)
        raw = (raw - 127.5) / 127.5
        return (raw[0::2] + 1j * raw[1::2]).astype(np.complex64)
    def close(self):
        try: self.sock.close()
        except Exception: pass
        try: self.proc.terminate(); self.proc.wait(timeout=3)
        except Exception:
            try: self.proc.kill()
            except Exception: pass

def group_channels(chans):
    """Split [(mhz, mode, name)] into windows <= WINDOW wide; returns [(center_hz, [chan tuples])]."""
    chans = sorted(chans, key=lambda c: c[0]); groups = []
    cur = []
    for c in chans:
        if cur and (c[0] - cur[0][0]) * 1e6 > WINDOW: groups.append(cur); cur = []
        cur.append(c)
    if cur: groups.append(cur)
    out = []
    for g in groups:
        lo, hi = g[0][0] * 1e6, g[-1][0] * 1e6; center = (lo + hi) / 2
        if any(abs(c[0] * 1e6 - center) < 25_000 for c in g): center += 150_000          # keep channels off the DC spike
        out.append((center, g))
    return out

class Channelizer:
    DWELL = 1.1            # (v151: was 2.5; the retune flush now happens BEFORE this clock starts, so 1.4 s is a full look at a quiet window) # seconds on a group with nothing open before hopping on (5 Nov: 0.9 was too fast - retune flush + gate settle ate most of it)
    HOLD = 6.0             # seconds to stay after the last channel in the group closes: the gap between "go ahead" and the reply is often 3-5 s
    def __init__(self, channels, manual=None, voice=True, gain="49.6", record_dir=None, log_path=None, on_heard=None, trusted=(), refresh=None):
        self.refresh = refresh                     # Follow traffic: callable returning a new channel list; read at the start of every lap (no audio cut)
        self.sox = None
        self.groups = group_channels([c for c in channels if c[0] > 0] or [(146.52, "fm", "146.52")])
        self.manual = manual; self.record_dir = record_dir; self.on_heard = on_heard
        self.gain_tenths = int(round(float(gain) * 10))
        self.log = open(log_path, "a") if log_path else None
        self.alive = True; self.err = ""; self.gi = -1; self.chans = []; self.center = 0.0; self.trusted = set(trusted)
        self.mhz = 0.0; self.mode = "fm"; self.name = ""; self.active = None
        self.hold = False; self.skip_req = False; self.base = 0; self.hotwin = {}; self.visit = {}; self.nvisit = 0; self.lap_t0 = time.time(); self.lap_s = None; self.laps = 0; self.lockout = set()          # (v137) scanner keys: HOLD stays on this window, SKIP hops now, lockout = never stop here
        self.heard = []; self.lock = threading.Lock(); self.q = []; self.qcond = threading.Condition(self.lock)
        self.tcp = None; self.blocks = 0; self.load = 0.0
        # (5 Nov) the live audio goes through the SAME sox chain as a direct listen (voice band-pass + compand, 100 ms look-ahead):
        # Monitor sounds exactly like pressing play on the channel. Without sox the old scipy chain is used. (started last: the reader needs qcond/alive)
        try:
            self.sox = subprocess.Popen(AU.sox_cmd("fm", voice), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            threading.Thread(target=self._sox_reader, daemon=True).start()
        except Exception: self.sox = None
        self.t = threading.Thread(target=self._run, daemon=True); self.t.start()
    # --- Session-compatible surface
    @property
    def gate(self): return self.active.gate if self.active else AU.ZcrSquelch("fm", self.manual)
    @property
    def tones(self): return self.active.tones if self.active else AU.ToneDecoder()
    @property
    def rtl(self):
        class _P:
            def __init__(s, p): s.p = p
            def poll(s): return s.p.poll() if s.p else None
        return _P(self.tcp.proc if self.tcp else None)
    def _run(self):
        try:
            self.tcp = RtlTcp(self.groups[0][0], self.gain_tenths, self.log)
        except Exception as e:
            self.err = f"channelizer: {e}"; self.alive = False; return
        try:
            while self.alive:
                lap_done = False
                if self.base >= len(self.groups):                                      # (v151) a lap = every window once; hot windows get extra visits in between
                    now = time.time(); self.lap_s = round(now - self.lap_t0, 1); self.lap_t0 = now; self.laps += 1; self.base = 0; lap_done = True
                if self.refresh and lap_done:                   # end of a lap: re-read the list (planes moved)
                    try:
                        new = group_channels([c for c in self.refresh() if c[0] > 0])
                        if new and [g[1] for g in new] != [g[1] for g in self.groups]:
                            self.groups = new; self.gi = -1; self.base = 0; self.hotwin = {}; self.visit = {}
                            if self.log:
                                try: self.log.write(f"follow: {len(new)} windows - " + ", ".join(n for _, cs in new for _, _, n in cs) + "\n"); self.log.flush()
                                except Exception: pass
                    except Exception: pass
                self.nvisit += 1; now = time.time(); ng = len(self.groups)
                hot = [i for i, t in self.hotwin.items() if i < ng and now - t < 300 and i != self.gi]
                if ng > 3 and hot and self.nvisit % 5 == 0:                            # (v151) every 5th stop goes back to the window that was last talking (busy windows are heard sooner)
                    self.gi = min(hot, key=lambda i: self.visit.get(i, 0))
                else:
                    self.gi = self.base % ng; self.base += 1
                self.visit[self.gi] = now
                center, chans = self.groups[self.gi]
                if self.center != center:
                    self.tcp.retune(center); self.center = center
                    for _ in range(4): self.tcp.block()                                 # flush the retune transient (0.5 s: the tuner's gain ramps after a retune)
                self.chans = [Channel(m, mo, n, center, self.manual, self.record_dir) for m, mo, n in chans if round(m, 4) not in self.lockout] or \
                             [Channel(m, mo, n, center, self.manual, self.record_dir) for m, mo, n in chans[:1]]
                for ch in self.chans:
                    ch.gate.n = max(0, AU.SETTLE - 2)          # the settle guard is for rtl_fm's DC filter; our own filters settle in 2 blocks
                    if round(ch.mhz, 4) in self.trusted: ch.gate.set_wide(True)
                for ch in self.chans:                                  # known voice channels are never vetoed as 'data' (repeater tails)
                    if round(ch.mhz, 4) in self.trusted: ch.gate.detect_data = False
                t0 = time.time(); last_any = None; opened = {}; graced = {}; grace_until = 0.0
                while self.alive:
                    iq = self.tcp.block(); tb = time.time(); t_cpu = time.perf_counter()
                    outs = []
                    results = [(ch,) + tuple(ch.process(iq, tb)) for ch in self.chans]
                    if len(self.chans) >= 3:
                        # group floor = the median channel power (most channels are idle): a channel well above it carries a signal,
                        # even on the very first block after a retune (its own floor would otherwise learn the carrier as "quiet")
                        med = float(np.median([c.pow_db for c in self.chans]))
                        for c in self.chans:
                            if c.mode != "am": c.carrier = (c.pow_db - med) > c.carrier_margin
                        ams = [c for c in self.chans if c.mode == "am"]
                        if len(ams) >= 3:                                              # AM floor = the quiet channels in the window: a transmission already
                            med_am = float(np.median([c.pow_db for c in ams]))        # in progress when we arrive is not learned as "the floor"
                            for c in ams:
                                if c.floor_db is not None and c.floor_db > med_am + 0.5: c.floor_db = med_am
                    for ch, out, is_open in results:
                        # (v180) Only proven activity earns the 6 s HOLD: an FM channel whose audio gate is open, an AM channel whose speech is
                        # confirmed. A carrier that has not proven itself gets 1.2 s from the moment it appears (enough to confirm), once per
                        # visit for weak ones. Before, every block of any carrier reset the hold: 118.7's noise floor flickering 1.5 dB (62
                        # times in 95 s on 10 Nov) pinned the scanner to that window and the other windows were not visited.
                        proven = is_open and (ch.mode != "am" or ch.confirmed)
                        if ch.carrier and ch.gate.n >= 2 and not proven:
                            g0 = graced.get(ch.mhz)
                            strong = ch.mode != "am" or (ch.floor_db is not None and ch.pow_db - ch.floor_db >= 4.0)
                            if g0 is None or (strong and tb - g0[1] > 1.0): graced[ch.mhz] = [tb, tb]; grace_until = max(grace_until, tb + 1.2)
                            else: g0[1] = tb
                        if is_open:
                            outs.append((ch.gate.level, ch, out))
                            if ch.mhz not in opened: opened[ch.mhz] = tb
                        if proven: last_any = tb
                        elif ch.mhz in opened:
                            secs = round(tb - opened.pop(ch.mhz), 1)
                            if secs >= 0.5 and (ch.mode != "am" or ch.confirmed):            # (5 Nov) flickers and refused AM carriers stay out of the heard log
                                self.heard.append({"mhz": ch.mhz, "name": ch.name, "when": dt.datetime.now().strftime("%H:%M:%S"), "secs": secs, "tones": ch.tones.summary()})
                                self.heard = self.heard[-50:]; self.hotwin[self.gi] = time.time()
                            if self.on_heard and secs >= 0.5 and (ch.mode != "am" or ch.confirmed):     # (v180) refused AM carriers no longer count as "heard" in the bank
                                try: self.on_heard(ch.mhz, ch.name, secs, ch.tones.summary())
                                except Exception: pass
                    self.load = 0.8 * self.load + 0.2 * ((time.perf_counter() - t_cpu) / 0.1)
                    self.blocks += 1
                    if outs:
                        # the loudest open channel is what you hear (voice is quieter than noise on FM, so prefer the one that has been open longest)
                        outs.sort(key=lambda o: opened.get(o[1].mhz, tb))
                        lvl, ch, out = outs[0]
                        self.active = ch; self.mhz, self.mode, self.name = ch.mhz, ch.mode, ch.name
                        if self.sox is not None:
                            try:
                                if ch.live: self.sox.stdin.write(ch.live); self.sox.stdin.flush()
                            except Exception: self.sox = None                     # sox died: fall back to the in-process chain
                        if self.sox is None:
                            with self.qcond:
                                self.q.append(out); self.q = self.q[-40:]; self.qcond.notify_all()
                    else:
                        if self.active is None or self.active.gate.open is False:
                            self.mhz, self.mode, self.name = chans[0][0], chans[0][1], f"group {self.gi+1}/{len(self.groups)} ({len(chans)} ch)"
                        self.active = None
                    pinned = [ch for ch in self.chans if ch.pinned]                      # (5 Nov) 20 s of continuous carrier = not voice: lock it out, move on
                    for ch in pinned:
                        self.lockout.add(round(ch.mhz, 4)); ch.pinned = False
                        if self.log:
                            try: self.log.write(f"monitor: {ch.name} {ch.mhz:.4f} open 20 s without a break - continuous carrier, locked out for this session\n"); self.log.flush()
                            except Exception: pass
                        if self.active is ch: self.active = None; last_any = None
                        if ch.rec: ch.rec.close()
                    if pinned and len(self.chans) > len(pinned): self.chans = [c for c in self.chans if c not in pinned]
                    if self.skip_req: self.skip_req = False; break                      # SKIP: hop now, whoever is talking
                    if self.active is not None and round(self.active.mhz, 4) in self.lockout: self.active = None; break   # locked out mid-call: leave
                    if len(self.groups) > 1 and not self.hold:                           # HOLD: stay in this window until released
                        if last_any is None and tb - t0 > self.DWELL and tb >= grace_until: break
                        if last_any is not None and tb - last_any > self.HOLD and tb >= grace_until: break
                for ch in self.chans:
                    if ch.rec: ch.rec.close()
        except Exception as e:
            if self.alive: self.err = f"channelizer: {e!r}"
        finally:
            if self.tcp: self.tcp.close()
            self.alive = False
            with self.qcond: self.qcond.notify_all()
    def _sox_reader(self):
        try:
            while self.alive and self.sox is not None:
                b = self.sox.stdout.read(4800)
                if not b: break
                with self.qcond:
                    self.q.append(b); self.q = self.q[-40:]; self.qcond.notify_all()
        except Exception: pass
    def read(self, n=4800):
        with self.qcond:
            if not self.q: self.qcond.wait(0.1)
            if not self.q and self.active is not None: self.qcond.wait(0.3)     # mid-sentence and the DSP is a little behind: wait, do not punch a 100 ms hole
            if self.q: return self.q.pop(0)
        return bytes(n) if self.alive else b""
    def stop(self):
        self.alive = False
        with self.qcond: self.qcond.notify_all()
        if self.tcp: self.tcp.close()
        if self.sox is not None:
            try: self.sox.stdin.close(); self.sox.terminate()
            except Exception: pass
        for ch in self.chans:
            try:
                if ch.rec: ch.rec.close()
            except Exception: pass
    def meter(self):
        a = self.active; g = a.gate if a else None
        m = {"open": bool(g and g.open), "zcr": round(g.zcr, 2) if g else 0, "thresh": round(g.thresh, 2) if g else 0, "level": round(g.level) if g else 0,
             "snr_db": round(a.pow_db - a.floor_db, 1) if (a and a.mode == "am" and a.floor_db is not None) else None,
             "tones": a.tones.summary() if a else "", "tone_now": a.tones.last if a else "", "err": self.err, "data": bool(g and (g.data or g.data_latched)),
             "data_secs": 0, "voice_cv": round(g.voice_cv, 2) if g else 0}
        n_all = sum(len(c) for _, c in self.groups)
        chans = []
        for ch in list(self.chans):                                              # the window as it is right now (reactive UI)
            try:
                g = ch.gate
                chans.append({"mhz": ch.mhz, "name": ch.name, "mode": ch.mode, "level": round(g.level), "open": bool(g.open), "carrier": bool(ch.carrier),
                              "snr": round(ch.pow_db - ch.floor_db, 1) if (ch.floor_db is not None) else None, "data": bool(g.data or g.data_latched),
                              "pow": round(ch.pow_db, 1), "floor": round(ch.floor_db, 1) if ch.floor_db is not None else None, "n": g.n, "mod": round(ch.voice_mod, 2), "voice": bool(ch.voice_ok)})
            except Exception: pass
        m["monitor"] = {"mhz": self.mhz, "name": self.name, "n": n_all, "idx": (sum(len(c) for _, c in self.groups[:self.gi]) if self.gi >= 0 else 0), "heard": self.heard[-8:],
                        "group": self.gi + 1, "groups": len(self.groups), "load": round(self.load, 2), "engine": "channelizer", "chans": chans,
                        "active": a.mhz if a else None, "center": round((self.center or 0) / 1e6, 4), "blocks": self.blocks, "audio": "sox" if self.sox is not None else "dsp",
                        "lap_s": self.lap_s, "laps": self.laps, "hot": len([1 for t in list(self.hotwin.values()) if time.time() - t < 300]),
                        "span": ([round(self.groups[self.gi][1][0][0], 3), round(self.groups[self.gi][1][-1][0], 3)] if 0 <= self.gi < len(self.groups) else None)}
        return m

def available():
    try:
        import numpy, scipy  # noqa
        return bool(__import__("shutil").which("rtl_tcp"))
    except Exception:
        return False
