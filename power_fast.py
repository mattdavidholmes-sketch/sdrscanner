#!/usr/bin/env python3
"""power_fast - incremental, vectorised reader for rtl_power / fastscan CSV files (5 Nov).
sdr_scan.parse_power() returns a PowerData from here: the file is parsed once and only NEW rows are read on later calls
(the dashboard asks every 3 s; the UHF file is 20 MB), and per-bin max / median come from numpy over the rows of each window
instead of 45,000 Python medians. Same interface find_hits() needs: truthiness, sorted(bins), bins[f] -> [(minute_of_day, dB)]."""
import csv, math, os, threading
import numpy as np

_CACHE = {}; _LOCK = threading.Lock()
MAX_ROWS = 900          # rows kept per window: 15 min of fastscan (1 row/s) or 2.5 h of rtl_power (1 row/10 s) - what the hit finder needs

class PowerData:
    def __init__(self):
        self.wins = {}          # (lo, hi, step) -> {"hours": [...], "pend": [new float32 rows], "stack": ndarray (bounded to MAX_ROWS), "freqs": ndarray int}
        self.centers = set(); self._index = None; self._stats = None
    def __bool__(self): return bool(self.wins)
    def _build_index(self):
        if self._index is not None: return
        idx = {}
        for key, w in self.wins.items():
            for j, f in enumerate(w["freqs"].tolist()): idx.setdefault(f, []).append((key, j))
        self._index = idx
    def __iter__(self): self._build_index(); return iter(self._index)
    def __len__(self): self._build_index(); return len(self._index)
    def keys(self): self._build_index(); return self._index.keys()
    def _stack(self, w):
        """one float32 array per window; new rows wait in w["pend"] and are appended here; history bounded to MAX_ROWS (16:40: no row list kept)"""
        if w["pend"]:
            new = np.vstack(w["pend"]); w["pend"] = []
            w["stack"] = new if w["stack"] is None else np.vstack([w["stack"], new])
        if w["stack"] is not None and w["stack"].shape[0] > MAX_ROWS:
            w["stack"] = np.ascontiguousarray(w["stack"][-MAX_ROWS:]); w["hours"] = w["hours"][-MAX_ROWS:]
        return w["stack"]
    def nrows(self): return sum(len(w["hours"]) for w in self.wins.values())
    def __getitem__(self, f):
        self._build_index(); out = []
        for key, j in self._index[f]:
            w = self.wins[key]; col = self._stack(w)[:, j]
            out.extend(zip(w["hours"], col.tolist()))
        return out
    def stats(self):
        """(freqs sorted list, mx ndarray, med ndarray) aligned - cached until new rows arrive"""
        self._build_index()
        total = self.nrows()
        if self._stats and self._stats[0] == total: return self._stats[1]
        freqs = sorted(self._index); mx = np.empty(len(freqs)); med = np.empty(len(freqs))
        wmax = {}; wmed = {}
        for key, w in self.wins.items():
            st = self._stack(w); wmax[key] = st.max(axis=0); wmed[key] = np.median(st, axis=0)
        for i, f in enumerate(freqs):
            refs = self._index[f]
            if len(refs) == 1:
                key, j = refs[0]; mx[i] = wmax[key][j]; med[i] = wmed[key][j]
            else:
                vals = np.concatenate([self._stack(self.wins[k])[:, j] for k, j in refs]); mx[i] = vals.max(); med[i] = np.median(vals)
        self._stats = (total, (freqs, mx, med)); return self._stats[1]

def _parse_rows(pd, fh):
    for row in csv.reader(fh):
        if len(row) < 7: continue
        try:
            lo, hi, step = float(row[2]), float(row[3]), float(row[4])
            tt = row[1].strip(); hour = int(tt[:2]) * 60 + int(tt[3:5])
        except ValueError: continue
        vals = row[6:]
        try: arr = np.array([float(v) for v in vals], dtype=np.float32)
        except ValueError:
            arr = np.array([_f(v) for v in vals], dtype=np.float32)
        bad = ~np.isfinite(arr)
        if bad.any():
            if bad.all(): continue
            arr[bad] = np.nanmedian(arr[~bad])
        key = (int(lo), int(hi), round(step, 2)); n = len(arr)
        w = pd.wins.get(key)
        if w is None or len(w["freqs"]) != n:
            if w is not None: key = (key[0], key[1], key[2] + n * 1e-6)     # same window header, different bin count: keep apart
            w = pd.wins.setdefault(key, {"hours": [], "pend": [], "stack": None, "freqs": (lo + step * np.arange(n)).astype(np.int64)})
            pd._index = None
        w["hours"].append(hour); w["pend"].append(arr); pd._stats = None      # (v150) history bounded to MAX_ROWS per window in _stack(): a 5-hour sweep
        pd.centers.add(int(lo + step * (n // 2)))                             # kept whole in RAM took the dashboard to 8 GB and macOS killed it
        if len(w["pend"]) > MAX_ROWS: w["pend"] = w["pend"][-MAX_ROWS:]; w["hours"] = w["hours"][-len(w["pend"]):]; w["stack"] = None
def _f(v):
    try: return float(v)
    except ValueError: return float("nan")

def parse_power(path):
    """incremental: only rows appended since the last call are read; a shrunken/replaced file is re-read from the start"""
    with _LOCK:
        try: st = os.stat(path)
        except OSError: return PowerData()
        c = _CACHE.get(path)
        if c is None or st.st_size < c["size"] or st.st_mtime < c["mtime"] - 1:
            c = {"size": 0, "mtime": 0, "offset": 0, "data": PowerData()}; _CACHE[path] = c
        c["used"] = os.times().elapsed if hasattr(os, "times") else 0
        if len(_CACHE) > 12:                                                            # old scan folders: let them go
            for k in sorted(_CACHE, key=lambda k: _CACHE[k].get("used", 0))[:len(_CACHE) - 12]: _CACHE.pop(k, None)
        if st.st_size == c["size"] and st.st_mtime == c["mtime"]: return c["data"]
        with open(path, newline="", errors="ignore") as fh:
            fh.seek(c["offset"])
            # the last line may still be being written: only consume complete lines
            chunk = fh.read(); cut = chunk.rfind("\n")
            if cut < 0: return c["data"]
            _parse_rows(c["data"], chunk[:cut + 1].splitlines())
            c["offset"] += len(chunk[:cut + 1].encode("utf-8", errors="ignore")); c["size"] = st.st_size; c["mtime"] = st.st_mtime
        return c["data"]

def rolling_median(med, half):
    """median of `med` over a window of 2*half+1 bins, like the original loop (truncated at the edges)"""
    try:
        from scipy.ndimage import median_filter
        return median_filter(np.asarray(med, dtype=np.float64), size=2 * half + 1, mode="nearest")
    except Exception:
        # no scipy: the floor is smooth, so take the window median every `stride` bins and interpolate between them
        m = np.asarray(med, dtype=np.float64); n = len(m)
        if n == 0: return m
        stride = max(1, half // 8); pts = np.arange(0, n, stride)
        if pts[-1] != n - 1: pts = np.append(pts, n - 1)
        vals = np.array([np.median(m[max(0, i - half): i + half + 1]) for i in pts])
        return np.interp(np.arange(n), pts, vals)
