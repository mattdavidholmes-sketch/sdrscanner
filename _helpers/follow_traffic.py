#!/usr/bin/env python3
"""Follow the traffic: the Monitor's channel list derived from live ADS-B (which aircraft are on which YYZ/YTZ frequency right now).
Imported by dashboard.py (monitor_list "Follow traffic"); the channelizer re-reads it at the start of every lap, so the scanner
retunes itself as the planes move - nothing to press. Same rules as the Air tab (monitor_panel.html): short final / just airborne ->
tower (E or W by side of field), on approach -> Arrival 1-4, climbing out -> Departure 1-2, ground -> Ground N/S + Apron, Billy Bishop -> 118.2."""
import math, sys, time
YYZ = (43.6777, -79.6248); YTZ = (43.6275, -79.3962)
CH = {"twrE": (118.700, "YYZ TWR E"), "twrW": (118.350, "YYZ TWR W"), "arr": [(124.475, "YYZ ARR 1"), (125.400, "YYZ ARR 2"), (132.800, "YYZ ARR 3"), (134.175, "YYZ ARR 4")],
      "dep": [(127.575, "YYZ DEP 1"), (128.800, "YYZ DEP 2")], "gndN": (121.650, "YYZ GND N"), "gndS": (121.900, "YYZ GND S"), "apron": (122.825, "YYZ APRON"),
      "ytzTwr": (118.200, "YTZ TWR"), "ytzGnd": (121.700, "YTZ GND"), "vfr": (133.400, "TOR VFR")}
_last = {"t": 0.0, "chans": None, "n_ac": 0}

def _geo(a, b):
    d2r = math.pi / 180; dl = (b[0] - a[0]) * d2r; dn = (b[1] - a[1]) * d2r
    s = math.sin(dl / 2) ** 2 + math.cos(a[0] * d2r) * math.cos(b[0] * d2r) * math.sin(dn / 2) ** 2
    km = 2 * 6371 * math.asin(math.sqrt(min(1.0, s)))
    y = math.sin(dn) * math.cos(b[0] * d2r); x = math.cos(a[0] * d2r) * math.sin(b[0] * d2r) - math.sin(a[0] * d2r) * math.cos(b[0] * d2r) * math.cos(dn)
    return km, (math.degrees(math.atan2(y, x)) + 360) % 360

def _small(t):
    import re
    return bool(re.match(r"^(DH8|DHC|AT[47]|SF34|D328|B190|C208|PC12|PC24|TBM|BE[1-9]|B350|PA[0-9]|C1[0-9]|C2[0-9]|C3[0-9]|SR2|DA4|DA6|M20|R22|R44|R66|EC[0-9]|H1[0-9]|AS[0-9]|A109|A139|AW[0-9]|B06|B407|B412|B429|S76|S92)", (t or "").upper()))

def phase(x):
    if x.get("on_ground"): return "on the ground"
    alt = x.get("alt_ft") or 0; vr = x.get("vrate") or 0; tr = x.get("track") or 0; rt = x.get("route") or ""
    to_yyz = rt.rstrip().endswith("CYYZ"); to_ytz = rt.rstrip().endswith("CYTZ"); from_yyz = rt.lstrip().startswith("CYYZ"); from_ytz = rt.lstrip().startswith("CYTZ")
    dz, bz = _geo((x["lat"], x["lon"]), YYZ); dt, bt = _geo((x["lat"], x["lon"]), YTZ)
    az = abs((tr - bz + 540) % 360 - 180); at = abs((tr - bt + 540) % 360 - 180)
    if not (to_ytz or from_ytz):
        if to_yyz and alt < 12000 and vr <= 100 and dz < 60: return "arriving YYZ"
        if from_yyz and alt < 12000 and vr > 100 and dz < 45: return "departing YYZ"
        if not (to_yyz or from_yyz):
            if dz < 45 and alt < 9000 and vr <= 0 and az < 50: return "arriving YYZ"
            if dz < 35 and alt < 9000 and vr > 200 and az > 110: return "departing YYZ"
    if _small(x.get("type")) or to_ytz or from_ytz:
        if (to_ytz or (not from_ytz and vr <= 0 and at < 55)) and dt < 28 and alt < 6000: return "arriving YTZ"
        if (from_ytz or (vr > 200 and at > 110)) and dt < 20 and alt < 6000: return "departing YTZ"
    return "climbing" if vr > 300 else ("descending" if vr < -300 else "level")

def freqs(x):
    """channels this aircraft is on right now, most likely first"""
    p = phase(x); alt = x.get("alt_ft") or 0
    dz, _ = _geo((x["lat"], x["lon"]), YYZ); dt, _ = _geo((x["lat"], x["lon"]), YTZ)
    east = x["lon"] > YYZ[1]
    if x.get("on_ground"):
        if dz < 4: return [CH["gndN"] if x["lat"] > YYZ[0] else CH["gndS"], CH["apron"]]
        if dt < 2: return [CH["ytzGnd"]]
        return []
    if "YTZ" in p: return [CH["ytzTwr"]]
    if p == "arriving YYZ":
        if dz < 14 and alt < 3500: return [CH["twrE"] if east else CH["twrW"], CH["twrW"] if east else CH["twrE"]]
        return list(CH["arr"])
    if p == "departing YYZ":
        if dz < 9 and alt < 3000: return [CH["twrE"] if east else CH["twrW"]]
        return list(CH["dep"])
    if alt < 4000 and dz > 10 and dt > 6: return [CH["vfr"]]
    return []

def follow_list(limit=10):
    """(mhz, mode, name) for the Monitor, weighted by how many aircraft are on each channel now. Cached 5 s. Falls back to the
    tower + arrival channels when the feed is empty, so the monitor never starts with nothing."""
    if time.time() - _last["t"] < 5 and _last["chans"] is not None: return _last["chans"]
    score = {}
    try:
        d2 = next((m for m in (sys.modules.get("dashboard2"), sys.modules.get("__main__")) if m is not None and hasattr(m, "aircraft_now")), None)
        if d2 is None:                                                       # not inside the dashboard: ask it over HTTP
            import json, urllib.request
            d = json.load(urllib.request.urlopen("http://127.0.0.1:8765/api/aircraft", timeout=4))
        else: d = d2.aircraft_now()
        ac = (d or {}).get("aircraft") or []
        _last["n_ac"] = len(ac)
        for x in ac:
            if x.get("lat") is None: continue
            parked = x.get("on_ground") and (x.get("speed_kt") or 0) < 5
            for i, (f, n) in enumerate(freqs(x)):
                w = (3.0 if i == 0 else 1.0) * (2.0 if x.get("km", 99) < 15 else 1.0) * (0.15 if parked else 1.0)   # a parked aircraft is not talking
                s = score.setdefault((f, n), 0.0); score[(f, n)] = s + w
    except Exception: pass
    ranked = [k for k, _ in sorted(score.items(), key=lambda kv: -kv[1])][:limit]
    if not ranked: ranked = [CH["twrE"], CH["twrW"], CH["arr"][2], CH["dep"][1], CH["arr"][0]]
    for t in (CH["twrE"], CH["twrW"]):                                     # both towers always ride along: every arrival and departure passes through them
        if t not in ranked: ranked.append(t)
    chans = [(f, "am", n) for f, n in ranked]
    _last.update(t=time.time(), chans=chans); return chans

if __name__ == "__main__":
    import json, urllib.request
    j = json.load(urllib.request.urlopen("http://localhost:8765/api/aircraft"))
    class D: aircraft_now = staticmethod(lambda: j)
    sys.modules["dashboard2"] = D
    for c in follow_list(): print(c)
