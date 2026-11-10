"""Offline CPU check: can the channelizer keep up at a given sample rate? Usage: SDR_FS=2880000 python3 _helpers/bench_fs.py"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import numpy as np, channelizer as CH
rng = np.random.default_rng(1)
for n in (4, 12, 24):
    center = 120.0e6
    chans = [CH.Channel(118.0 + 0.05 * i + 1.0, "am" if i % 2 else "fm", f"c{i}", center) for i in range(n)]
    iq = (rng.standard_normal(CH.BLOCK) + 1j * rng.standard_normal(CH.BLOCK)).astype(np.complex64) * 0.05
    t0 = time.time(); nb = 15
    for b in range(nb):
        for c in chans: c.process(iq, time.time())
    ms = (time.time() - t0) / nb * 1000
    print(f"FS={CH.FS} DECIM1={CH.DECIM1} WINDOW={CH.WINDOW} channels={n:2d}: {ms:6.1f} ms per 100 ms block ({ms:.0f}% of real time)")
