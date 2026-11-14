#!/usr/bin/env python3
"""What a browser receives from /stream, with a stopwatch on every arrival (v228).
   stream_probe.py <MHz> <fm|am> <seconds> [name] [sq: auto, 0 = held open, 1-60] [rec: 0|1]   -> diag/probe_<MHz>_<mode>.json
   Silent: nothing is played on the Mac. It takes the dongle like any listen in a browser, so the caller checks first
   that nobody is listening. Each arrival is kept as [seconds since the request, bytes, rms of that piece]."""
import sys, time, json, os, array, math, urllib.request, urllib.parse
mhz = sys.argv[1]; mode = sys.argv[2]; secs = float(sys.argv[3]); name = sys.argv[4] if len(sys.argv) > 4 else "probe"; sq = sys.argv[5] if len(sys.argv) > 5 else "auto"; rec = sys.argv[6] if len(sys.argv) > 6 else "0"
url = "http://127.0.0.1:8765/stream?raw=1&rec=%s&mhz=%s&mode=%s&sq=%s&voice=1&name=%s&t=%d" % (rec, mhz, mode, sq, urllib.parse.quote(name), int(time.time() * 1000))
t0 = time.time(); arr = []; tot = 0; err = ""
try:
    r = urllib.request.urlopen(url, timeout=20)
    while time.time() - t0 < secs:
        b = r.read1(65536)
        if not b: err = "stream ended"; break
        t = time.time() - t0; n = len(b) - (len(b) & 1)
        a = array.array("h", b[:n]); rms = math.sqrt(sum(x * x for x in a) / max(1, len(a))) if a else 0
        arr.append([round(t, 4), len(b), int(rms)]); tot += len(b)
    r.close()
except Exception as e: err = repr(e)
out = {"mhz": mhz, "mode": mode, "sq": sq, "when": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)), "pieces": len(arr), "bytes": tot, "err": err, "arrivals": arr}
if arr:
    span = arr[-1][0] - arr[0][0]; gaps = [b[0] - a[0] for a, b in zip(arr, arr[1:])]
    snd = sum(a[1] for a in arr if a[2] > 30)
    out.update(first_piece_s=arr[0][0], span_s=round(span, 2), audio_s=round(tot / 48000, 2), bytes_per_s=int((tot - arr[0][1]) / span) if span > 0 else 0,
               gap_max_s=round(max(gaps), 3) if gaps else 0, gaps_over_03=sum(1 for g in gaps if g > 0.3), sound_fraction=round(snd / max(1, tot), 3))
json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "diag", "probe_%s_%s.json" % (mhz, mode)), "w"))
print(json.dumps({k: v for k, v in out.items() if k != "arrivals"}))
