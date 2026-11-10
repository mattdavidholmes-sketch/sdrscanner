#!/usr/bin/env python3
"""fastscan - the sweep engine, rebuilt on the channelizer's rtl_tcp + numpy path (5 Nov).

Drop-in for rtl_power: same command shape and the SAME CSV format (date, time, Hz low, Hz high, Hz step, samples, dB...), appended to
the same power_<band>.csv, so sdr_scan.py, the dashboard spectrum strip, reports and CHIRP export all work unchanged.

Why it is faster and catches more:
  rtl_power retunes the dongle for every FFT across the band and integrates 10 s per row: on a 17 MHz band each 2.4 MHz hop
  gets ~1.2 s of every 10, so a 3 s radio call is missed 9 times out of 10 and a hit needs several rows to show up.
  fastscan parks on one 2 MHz window for DWELL seconds at the full 2.4 MS/s, computes 10 FFTs a second, and writes a row
  EVERY SECOND for that window (power averaged over that second: a 1 s call shows at full strength, rtl_power's 10 s rows dilute it 10x). Nothing is missed inside a window
  and the retune cost is paid once per window, not once per FFT. A full lap of the airband (9 windows) takes ~25 s instead of
  a 60 s slice that only sampled it.

Usage (what sdr_scan.py calls):  fastscan.py -f LO:HI:BIN -e SECONDS -g GAIN [-d DWELL]   -> rows on stdout
SIGINT/SIGTERM = stop cleanly (the dashboard's PAUSE flag path)."""
import argparse, datetime as dt, os, signal, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import channelizer as CH

FS = CH.FS; BLOCK = CH.BLOCK
WINDOW = FS * 3 // 4           # usable span per hop: the central 75% of the sample rate, like rtl_power -c 25% (a strong carrier at the tuner's edge throws spurs)
STOP = False
def _stop(*_):
    global STOP; STOP = True
signal.signal(signal.SIGINT, _stop); signal.signal(signal.SIGTERM, _stop)

def windows(lo_hz, hi_hz):
    """centres covering lo..hi in WINDOW-wide steps (the last one is pulled back so nothing past hi is scanned twice)"""
    out = []; c = lo_hz + WINDOW / 2
    while c - WINDOW / 2 < hi_hz:
        out.append(min(c, max(lo_hz + WINDOW / 2, hi_hz - WINDOW / 2))); c += WINDOW
    return sorted(set(out))

DB_OFFSET = 30.0               # puts fastscan's normalised dB on rtl_power's scale (measured: same band, same gain, same noise floor -> ~-13 dB there, ~-43 dB here)
def emit(out, f_lo, f_hi, step, samples, pw, dc):
    db = 10 * np.log10(pw) + DB_OFFSET
    if len(dc): db[dc] = np.median(db[max(0, dc[0] - 12):dc[0]].tolist() + db[dc[-1] + 1:dc[-1] + 13].tolist() or [np.median(db)])   # DC spike -> neighbours
    now = dt.datetime.now()
    out.write(f"{now:%Y-%m-%d}, {now:%H:%M:%S}, {int(round(f_lo))}, {int(round(f_hi))}, {step:.2f}, {samples}, " + ", ".join(f"{v:.2f}" for v in db) + "\n"); out.flush()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-f", required=True, help="LO:HI:BIN in Hz, e.g. 118000000:135000000:12500")
    ap.add_argument("-e", default="60s", help="total seconds, e.g. 60s")
    ap.add_argument("-g", default="49.6", help="tuner gain dB")
    ap.add_argument("-d", "--dwell", type=float, default=float(os.environ.get("FASTSCAN_DWELL", "3.0")), help="seconds per 2 MHz window")
    ap.add_argument("-i", default="1", help="ignored (rtl_power compatibility)"); ap.add_argument("-c", default="", help="ignored")
    a = ap.parse_args()
    lo, hi, binhz = (float(x) for x in a.f.split(":")); total = float(a.e.rstrip("s"))
    gain_tenths = int(round(float(a.g) * 10))
    nfft = 1 << int(np.ceil(np.log2(FS / binhz)))                      # FFT size that gives at least the asked resolution
    nfft = FS // 5000 if nfft <= 512 else min(nfft, 65536); step = FS / nfft         # 480 -> exactly 5,000 Hz bins (integer grid shared by every window); about rtl_power's resolution
    if FS % nfft: nfft = FS // 5000; step = FS / nfft                                 # keep the bin width an integer number of Hz
    win = np.hanning(nfft).astype(np.float32); wpow = float(np.sum(win ** 2))
    cents = windows(lo, hi)
    cents = sorted({round(c / step) * step for c in cents})               # every window on ONE bin grid: overlapping windows share bins exactly (no 1 Hz-apart neighbours for the hit finder)
    dwell = min(a.dwell, max(0.4, total / (2 * len(cents))))           # every slice covers its band at least twice (UHF 400-520: 67 windows -> 0.45 s each; airband: 3 s)
    t_end = time.time() + total; tcp = None; out = sys.stdout
    try:
        tcp = CH.RtlTcp(cents[0], gain_tenths)
        wi = -1
        while not STOP and time.time() < t_end:
            wi = (wi + 1) % len(cents); center = cents[wi]
            tcp.retune(center)
            for _ in range(2): tcp.block()                              # flush the retune transient (0.2 s)
            # bins inside the usable WINDOW (outer 200 kHz are the tuner's roll-off), DC bin excluded
            freqs = center + (np.fft.fftshift(np.fft.fftfreq(nfft, 1 / FS)))
            keep = (np.abs(freqs - center) <= WINDOW / 2) & (freqs >= lo) & (freqs <= hi)
            idx = np.nonzero(keep)[0]
            if len(idx) == 0: continue
            dc = np.nonzero(np.abs(freqs[idx] - center) <= 3 * step)[0]           # the dongle's DC spike: filled from its neighbours, never a hit
            f_lo = freqs[idx[0]]; f_hi = freqs[idx[-1]]
            t_win = time.time(); acc = None; nblk = 0; t_row = time.time()
            while not STOP and time.time() - t_win < dwell and time.time() < t_end:
                iq = tcp.block()
                # 0.1 s block -> several FFTs averaged (power), then averaged across the second
                nseg = len(iq) // nfft
                if nseg < 1: continue
                segs = iq[:nseg * nfft].reshape(nseg, nfft) * win
                spec = np.fft.fftshift(np.fft.fft(segs, axis=1), axes=1)
                p = np.mean(np.abs(spec) ** 2, axis=0) / (wpow * nfft) + 1e-20
                pw = p[idx]
                acc = pw.copy() if acc is None else acc + pw; nblk += 1              # power AVERAGED over the second (peak-hold made tuner spurs look like stations)
                if os.getppid() == 1: break                                       # the scan that started us is gone: free the dongle
                if time.time() - t_row >= 1.0:
                    emit(out, f_lo, f_hi, step, nblk * nseg, acc / nblk, dc); acc = None; nblk = 0; t_row = time.time()
            if acc is not None and nblk: emit(out, f_lo, f_hi, step, nblk * nseg, acc / nblk, dc)
    except Exception as e:
        sys.stderr.write(f"fastscan: {e!r}\n"); sys.stderr.flush(); return 2
    finally:
        if tcp: tcp.close()
    return 0

if __name__ == "__main__": sys.exit(main())
