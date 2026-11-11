#!/usr/bin/env python3
"""Airband voice receiver: one AM channel, from complex baseband to clean audio.

Aircraft radio is double-sideband AM, and the carrier is on for as long as the microphone is keyed. That carrier is the most
useful thing in the channel, and this receiver is built around it:

  find it     One spectrum per 0.1 s block. The carrier is a single line: when carrier and noise are equal in a 6 kHz voice
              channel it stands 26 dB above the noise bins around it. Noise never makes such a line, a burst of interference
              does not either, so the squelch opens on the carrier itself - no audio statistics, no learning period - and the
              signal is reported as carrier-to-noise in a 6 kHz channel (CNR), the figure that decides how the voice will sound.
  lock to it  The channel is turned so that the carrier sits at 0 Hz; a 60 Hz low-pass then leaves the carrier alone, its
              amplitude and phase sample by sample.
  detect      Synchronously: only the part of the signal in phase with the carrier is audio. (An envelope detector - a simple
              AM radio, rtl_fm - rectifies the noise together with the signal.)
  level       The audio is divided by the carrier, so a weak aircraft and a strong one are equally loud and nothing has to pump
              the volume afterwards. (The single-channel path used a compander that lifted quiet sound by up to 23 dB; the
              hiss between the words came up with it.)
  clean       300-2800 Hz voice band, then a Wiener noise filter that does not have to guess the noise: its level follows from
              the measured CNR. Never more than 12 dB of reduction, so speech is not chopped.
  key         The audio is keyed by the carrier's own rise and fall, a hundredth of a second behind it: up once the carrier
              has settled, down before its collapse reaches the output. No click when a call starts, no noise burst when the
              aircraft unkeys, and a fade in the middle of a call does not touch it.

Input: blocks of complex baseband at 24 kS/s, the channel centred to within +-2.5 kHz and at least +-5 kHz wide.
Output: int16 audio at 24 kS/s, as many samples out as in, 31 ms behind the antenna. numpy / scipy only.
"""
import math
import numpy as np
from scipy import signal

FS = 24_000
VOICE_BW = 6_000.0             # Hz: the channel width all CNR figures refer to

def _db(x): return 10.0 * math.log10(max(float(x), 1e-30))


class Wiener:
    """Noise filter for audio whose noise level is known (white in front of the voice band-pass, variance `nvar` per sample).
    512-sample Hann frames, 75 % overlap, decision-directed a-priori SNR (Ephraim & Malah), gain never below `floor`.
    Streaming: any block length in, the same number of samples out, 512 samples (21 ms) late."""
    N, HOP = 512, 128
    def __init__(self, shape, floor=0.25, alpha=0.96):
        self.win = signal.get_window("hann", self.N, fftbins=True)
        self.wsum = float(np.sum(self.win ** 2)); self.norm = self.wsum / self.HOP      # overlap-add gain of analysis * synthesis window
        self.shape = np.maximum(np.asarray(shape, dtype=np.float64), 1e-6)              # |H(f)|^2 of the band-pass in front, per FFT bin
        self.floor, self.alpha = floor, alpha
        self.inq = np.zeros(0); self.outq = np.zeros(self.N); self.ola = np.zeros(self.N); self.prev = np.zeros(self.N // 2 + 1)
    def process(self, a, nvar):
        self.inq = np.concatenate((self.inq, a)); lam = max(nvar, 1e-12) * self.wsum * self.shape    # expected noise power per bin
        done = []
        while len(self.inq) >= self.N:
            X = np.fft.rfft(self.inq[:self.N] * self.win); P = X.real ** 2 + X.imag ** 2
            prio = self.alpha * self.prev / lam + (1.0 - self.alpha) * np.maximum(P / lam - 1.0, 0.0)
            G = np.maximum(prio / (1.0 + prio), self.floor)
            self.prev = (G ** 2) * P
            self.ola += np.fft.irfft(G * X, self.N) * self.win
            done.append(self.ola[:self.HOP] / self.norm)
            self.ola = np.concatenate((self.ola[self.HOP:], np.zeros(self.HOP))); self.inq = self.inq[self.HOP:]
        if done: self.outq = np.concatenate([self.outq] + done)
        out, self.outq = self.outq[:len(a)], self.outq[len(a):]
        return out if len(out) == len(a) else np.concatenate((out, np.zeros(len(a) - len(out))))


class AmRx:
    """See the module text. One object per channel; give it every block, in order.
    manual: None = automatic squelch; 0 = held open; 1..100 = squelch by hand, from -3 dB (1, opens on almost any carrier) to
    +20 dB CNR (100, strong aircraft only)."""
    OPEN_DB = 4.0              # automatic squelch: the CNR that opens it - speech is just followable there
    CLOSE_DB = 2.0             # ... and the CNR below which it closes again
    LINE_DB = 15.0             # a spectral line this far above the noise bins is a carrier (noise alone gets there once in years)
    DELAY = 240                # samples (10 ms) the audio runs behind the keying decision
    def __init__(self, fs=FS, block=2400, manual=None, nr=True):
        self.fs, self.block, self.manual = fs, block, manual
        self.nfft = 4096
        self.win = signal.get_window("hann", block, fftbins=True).astype(np.float64)
        self.wgain = float(np.sum(self.win)) ** 2; self.wpow = float(np.sum(self.win ** 2))
        self.f = np.fft.fftfreq(self.nfft, 1.0 / fs)
        self.search = np.abs(self.f) <= 2500.0; self.inband = np.abs(self.f) <= 4700.0
        # carrier
        self.f_nco = 0.0; self.ph = 0.0
        self.sos_c = signal.butter(2, 60.0 / (fs / 2), output="sos"); self.zc = np.zeros((self.sos_c.shape[0], 2), dtype=np.complex128)
        self.ybuf = np.zeros(88, dtype=np.complex128)                       # the signal waits 3.7 ms: the delay of that low-pass, so both meet in step
        self.cp_tail = np.zeros(60)                                         # the carrier power 2.5 ms back, to see when it has stopped rising
        # audio
        self.sos_a = signal.butter(4, [300.0 / (fs / 2), 2800.0 / (fs / 2)], btype="band", output="sos"); self.za = np.zeros((self.sos_a.shape[0], 2))
        w, h = signal.sosfreqz(self.sos_a, worN=Wiener.N // 2 + 1, fs=fs)
        self.wiener = Wiener(np.abs(h) ** 2) if nr else None
        self.abuf = np.zeros(self.DELAY)                                    # the detected audio, waiting for its key
        self.nvar = 1e-9                                                    # noise variance per sample of the detected audio, from the CNR
        self.agc = 1.6; self.peak = 0.35
        # squelch and key
        self.open = False; self.below = 0; self.cnr_db = -30.0; self.line_db = 0.0; self.n0 = 0.0; self.c_pow = 0.0; self.mod = 0.0; self.f_off = 0.0
        self.a_ref = 0.0                                                    # the carrier power the key and the divisor refer to
        self.kstate = False; self.khist = np.zeros(self.DELAY, dtype=bool)  # is the carrier up (now / over the last 10 ms)
        kw = signal.get_window("hann", 97); self.kwin = kw / kw.sum(); self.ktail = np.zeros(len(self.kwin) - 1)     # 4 ms edges on the key
        self.key = 0.0; self.ring = 0; self.trace = None                    # trace: a list here collects the keying signals of every block (tests)
        self.open_blocks = 0; self.sb_avg = 0.0; self.c_avg = 0.0           # for the dead-carrier rule: sideband and carrier power, averaged over ~10 s
        self.shut = None; self.gone = 0                                     # frequency of a carrier shut out for carrying nothing; blocks since it was last seen
        self.threshold = self.OPEN_DB if manual in (None, 0) else (-3.0 + 23.0 * (min(100, max(1, manual)) - 1) / 99.0)
        self.always = manual == 0

    # ---- one spectrum per block: where is the carrier, how strong is it, how much noise is there, is anything on it
    def _measure(self, x):
        X = np.fft.fft(x * self.win, self.nfft); P = X.real ** 2 + X.imag ** 2
        k = int(np.argmax(np.where(self.search, P, 0.0)))
        a, b, c = P[k - 1], P[k], P[(k + 1) % self.nfft]
        d = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0   # parabolic peak: good to about a hertz (the FFT is zero-padded)
        f0 = self.f[k] + d * self.fs / self.nfft
        # noise: what lies inside the flat part of the channel filter (+-4.7 kHz) but more than 3.3 kHz from the carrier, where the voice sidebands
        # have ended; the median resists a stray line
        dist = np.abs(self.f - f0)
        ring = P[(dist >= 3300.0) & self.inband]
        nbin = float(np.median(ring)) / math.log(2.0) if len(ring) > 50 else float(np.mean(P))
        line = b / max(nbin, 1e-30)                                         # the carrier line over the mean noise bin
        # carrier power over (noise density * 6 kHz):  A^2 / sigma^2 = line * wpow / wgain, and sigma^2 = N0 * fs
        cnr = line * (self.wpow / self.wgain) * (self.fs / VOICE_BW)
        n0 = nbin / self.wpow / self.fs                                     # noise density, power per Hz
        c_pow = b / self.wgain
        sb = P[(dist >= 300.0) & (dist <= 3000.0)]                          # the voice sidebands: their power above the noise, per sample
        sb_ex = (float(np.sum(sb)) - len(sb) * nbin) / (self.nfft * self.wpow)
        return f0, line, cnr, n0, c_pow, sb_ex

    def process(self, x):
        """x: one block of complex baseband. Returns (int16 PCM bytes, squelch open?)."""
        x = np.asarray(x, dtype=np.complex128); n = len(x)
        f0, line, cnr, n0, c_pow, sb_ex = self._measure(x)
        self.line_db = _db(line); self.cnr_db = _db(cnr); self.n0 = n0; self.c_pow = c_pow
        carrier = self.line_db >= self.LINE_DB
        # "usable": a carrier the squelch would open on or stay open for. Only such a carrier sets the levels the receiver works
        # to - the last few milliseconds of a transmission at the start of a block are still a line, but a hundred times weaker.
        usable = carrier and self.cnr_db >= self.threshold - (self.OPEN_DB - self.CLOSE_DB)
        mod = math.sqrt(max(2.0 * sb_ex / max(c_pow, 1e-30), 0.0)) if carrier else 0.0
        mod_floor = math.sqrt(16.0 / max(line, 1e-9))                       # what noise alone makes this estimate read, at this carrier strength
        self.mod = min(mod, 2.0)
        # ---- squelch, per block
        was = self.open
        if self.always: self.open = True
        elif not self.open:
            # a carrier that was shut out for carrying nothing stays out until it carries something or has gone for 3 s
            if self.shut is not None:
                self.gone = self.gone + 1 if (not carrier or abs(f0 - self.shut) > 40.0) else 0
                if self.gone >= 30: self.shut = None
            ok = self.shut is None or abs(f0 - self.shut) > 40.0 or mod >= max(0.2, 3.0 * mod_floor)
            if carrier and self.cnr_db >= self.threshold and ok: self.open = True
        else:
            self.below = self.below + 1 if not usable else 0
            if self.below >= 2: self.open = False
        if self.open and not was:
            self.below = 0; self.open_blocks = 0; self.sb_avg = 0.0; self.c_avg = 0.0; self.peak = 0.35; self.agc = 1.6; self.shut = None
        if self.open and usable and not self.always:
            # a carrier with nothing on it for 20 s (a spur, a stuck transmitter) stops holding the squelch open. Sideband power is
            # averaged over ~10 s, so this works on weak carriers too, where one block cannot tell speech from noise.
            self.open_blocks += 1; self.sb_avg += 0.01 * (sb_ex - self.sb_avg); self.c_avg += 0.01 * (c_pow - self.c_avg)
            if self.open_blocks >= 200 and math.sqrt(max(2.0 * self.sb_avg / max(self.c_avg, 1e-30), 0.0)) < 0.06:
                self.open = False; self.shut = f0; self.gone = 0
        # ---- follow the carrier: jump to a new transmitter, glide with the one we have
        if usable or (carrier and not was): self.f_nco = f0 if (abs(f0 - self.f_nco) > 25.0 or not was) else self.f_nco + 0.3 * (f0 - self.f_nco)
        self.f_off = self.f_nco
        ph = self.ph + 2 * np.pi * self.f_nco / self.fs * np.arange(1, n + 1); self.ph = float(ph[-1] % (2 * np.pi))
        y = x * np.exp(-1j * ph)
        c, self.zc = signal.sosfilt(self.sos_c, y, zi=self.zc)              # the carrier alone
        yy = np.concatenate((self.ybuf, y)); yd, self.ybuf = yy[:n], yy[n:]
        cp = c.real ** 2 + c.imag ** 2
        # the level the carrier has settled at: the spectrum's figure, or the end of the block when the call began inside it
        if usable:
            settled = max(c_pow, float(np.mean(cp[-240:])))
            self.a_ref = settled if (not was or settled > self.a_ref or settled < 0.5 * self.a_ref) else self.a_ref + 0.3 * (settled - self.a_ref)
        # ---- detect: in phase with the carrier, carrier removed, divided by the carrier. The divisor never falls below a quarter
        # of the settled level (nor into the noise), so nothing blows up while the carrier estimate runs up or down.
        den = np.maximum(cp, max(0.25 * self.a_ref if (usable or was) else 0.0, 6.0 * n0 * 133.0))
        a = np.clip((yd.real * c.real + yd.imag * c.imag - cp) / den, -2.0, 2.0)
        # ---- key. "Up" = the carrier estimate is above 85 % of its level and has stopped rising; "down" = below a quarter.
        # The audio runs 10 ms behind: the key goes up 10 ms after "up" (the run-up is over by then) and down at once on "down"
        # (the collapse has not reached the output yet).
        cpp = np.concatenate((self.cp_tail, cp)); self.cp_tail = cpp[-60:]
        ref = max(self.a_ref, 1e-30)
        up = (cp > 0.85 * ref) & (np.abs(cp - cpp[:n]) < 0.1 * cp)
        ev = np.where(cp < 0.25 * ref, -1, np.where(up, 1, 0))
        idx = np.maximum.accumulate(np.where(ev != 0, np.arange(n), -1))
        state = np.where(idx >= 0, ev[np.maximum(idx, 0)] > 0, self.kstate)
        if not self.open: state = np.zeros(n, dtype=bool)
        elif self.always and not usable: state = np.ones(n, dtype=bool)
        self.kstate = bool(state[-1])
        hist = np.concatenate((self.khist, state)); self.khist = hist[n:]
        st = np.concatenate((self.ktail, (state & hist[:n]).astype(np.float64))); self.ktail = st[-(len(self.kwin) - 1):]
        key = np.convolve(st, self.kwin, mode="valid")[:n]
        aa = np.concatenate((self.abuf, a)); a, self.abuf = aa[:n], aa[n:]
        if self.always and not usable: a = a * 0.06                         # held open with nobody there: the hiss is audible, quietly
        if self.trace is not None: self.trace.append((cp / ref, key.copy(), a.copy(), state.copy()))
        a = a * key
        self.key = float(key[-1]) if n else 0.0
        self.ring = self.ring - 1 if not np.any(key > 1e-4) else 4          # blocks the filters may still be ringing after the key went down
        if not self.open and self.ring <= 0:
            return bytes(2 * n), False                                      # shut and nothing ringing: silence, at no cost
        # ---- voice band, noise filter (its noise level follows from the CNR: variance per sample = fs * N0 / (2 A^2))
        a, self.za = signal.sosfilt(self.sos_a, a, zi=self.za)
        if usable: self.nvar = self.fs * n0 / (2.0 * ref)                  # stays as it was through the last 21 ms of a call (still inside the filter when the carrier has gone)
        if self.wiener is not None: a = self.wiener.process(a, self.nvar)
        # ---- level: one slow gain per transmission (pilots modulate anywhere between 30 and 90 %), then a soft ceiling
        if self.open and usable:
            self.peak = max(float(np.percentile(np.abs(a), 99.5)), self.peak * 0.97)
            want = min(2.6, max(0.8, 0.75 / max(self.peak, 1e-3)))
        else: want = self.agc
        g = np.linspace(self.agc, want, n); self.agc = want
        out = 30000.0 * np.tanh(15000.0 * a * g / 30000.0)
        return out.astype(np.int16).tobytes(), bool(self.open or self.key > 0.01)
