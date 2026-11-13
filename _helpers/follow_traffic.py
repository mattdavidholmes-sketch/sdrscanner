#!/usr/bin/env python3
"""Follow the traffic: the Monitor's channel list derived from live ADS-B (which aircraft are on which YYZ/YTZ frequency right now).
Imported by dashboard.py (monitor_list "Follow traffic"); the channelizer re-reads it at the start of every lap, so the scanner
retunes itself as the planes move - nothing to press.

Which channel an aircraft is on is WORKED OUT from where it is and what it is doing; it is not read off the radio. The rules (v181), the
same ones the Aircraft tab draws (monitor_panel.html freqs()):
  on final, lined up with a runway (GPS position against the centreline) or low and close -> the tower for that runway
  on approach, further out          -> Toronto Arrival: 132.800 (the main channel) first, then 124.475, 125.400
  just airborne                     -> the tower for the runway it left
  climbing out                      -> Toronto Departure: 127.575 north of the field, 128.800 south
  on the ground                     -> Ground North / South + Apron;  Billy Bishop -> 118.200;  low and local -> 133.400
Channel roles as published (canairradio.ca/yyz.html, Wikipedia "Toronto Area Control Centre"): tower 118.700 is North (runway 05/23) and
118.350 South (06L/24R, 06R/24L); 134.175 is the ILS monitor, not a working arrival channel, so it is no longer a candidate. The names are
the bank's own (sdr_scan.py REFERENCE). Runway ends are from OurAirports (public domain)."""
import math, sys, time
YYZ = (43.6777, -79.6248); YTZ = (43.6275, -79.3962)
CH = {"twrN": (118.700, "YYZ TWR N"), "twrS": (118.350, "YYZ TWR S"), "arr": [(132.800, "YYZ ARR 3"), (124.475, "YYZ ARR 1"), (125.400, "YYZ ARR 2")],
      "depN": (127.575, "YYZ DEP 1"), "depS": (128.800, "YYZ DEP 2"), "gndN": (121.650, "YYZ GND N"), "gndS": (121.900, "YYZ GND S"), "apron": (122.825, "YYZ APRON"),
      "ytzTwr": (118.200, "YTZ TWR"), "ytzGnd": (121.700, "YTZ GND"), "vfr": (133.400, "TOR VFR"),
      # (v212) Toronto Centre around Toronto: west / east of Pearson, low / high sectors (the same table as dashboard.TOR_CENTRE; sources there)
      "ctr": {"wl": [(132.475, "CTR HAMILT"), (133.300, "CTR GRIMSB")], "wh": [(125.775, "CTR CENTRA"), (134.925, "CTR OAKVIL")],
              "el": [(124.925, "CTR E RDR"), (127.000, "CTR SIMCOE"), (134.250, "CTR E RDR2")], "eh": [(134.575, "CTR BARRIE")]}}
RW = [("05", 43.67390, -79.66390, "23", 43.69470, -79.63330), ("06L", 43.66105, -79.62343, "24R", 43.67898, -79.59737), ("06R", 43.65830, -79.62193, "24L", 43.67529, -79.59724),
      ("15L", 43.69190, -79.64220, "33R", 43.67000, -79.61390), ("15R", 43.68580, -79.65170, "33L", 43.66750, -79.62830)]
_KX = 111.32 * math.cos(math.radians(YYZ[0])); _KY = 110.57                # flat-earth km around the field: fine within 60 km
def _xy(lat, lon): return ((lon - YYZ[1]) * _KX, (lat - YYZ[0]) * _KY)
ENDS = []                                                                  # every landing direction: (runway, threshold, unit vector along it, true heading, length km)
for _a, _la, _lo, _b, _lb, _lob in RW:
    for _n, _t, _f in ((_a, _xy(_la, _lo), _xy(_lb, _lob)), (_b, _xy(_lb, _lob), _xy(_la, _lo))):
        _dx, _dy = _f[0] - _t[0], _f[1] - _t[1]; _ln = math.hypot(_dx, _dy)
        ENDS.append((_n, _t, (_dx / _ln, _dy / _ln), (math.degrees(math.atan2(_dx, _dy)) + 360) % 360, _ln))
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
        light = _fam(x.get("type")) in ("ga", "heli") or x.get("category") == "A7"
        if not (to_yyz or from_yyz) and not light:                         # (v183) a Cessna or a helicopter with no Pearson route is not Pearson traffic
            if dz < 45 and alt < 9000 and vr <= 0 and az < 50: return "arriving YYZ"
            if dz < 35 and alt < 9000 and vr > 200 and az > 110: return "departing YYZ"
    if _small(x.get("type")) or to_ytz or from_ytz:
        if (to_ytz or (not from_ytz and vr <= 0 and at < 55)) and dt < 28 and alt < 6000: return "arriving YTZ"
        if (from_ytz or (vr > 200 and at > 110)) and dt < 20 and alt < 6000: return "departing YTZ"
    return "climbing" if vr > 300 else ("descending" if vr < -300 else "level")

# aircraft families by ICAO type code - the same table the Aircraft tab draws its silhouettes from (monitor_panel.html T)
_FAM = [(r"^(R22|R44|R66|EC\d|H1\d|H60|UH1|AS\d|A109|A119|A139|AW\d|B06|B407|B412|B429|B47|S76|S92|MD5|MD6|EH10|H135|H145|H155|BK17|NH90|CH47)", "heli"),
        (r"^(B74|A38|A34|A124|AN12|C5M|IL96|IL86|B52|E3[CT]F|E6$|C17$|KC13)", "quad"),     # (v183) C17$ / E3.F / E6$: "C172" and "E35L" used to match here
        (r"^(B76|B77|B78|A3[01]|A33|A35|MD11|DC10|IL76|KC10|L101|A400)", "wide"),
        (r"^(DH8|DHC|AT[47]|SF34|D328|B190|C208|PC12|PC24|TBM|BE[1-9]|BE20|B350|PA3[1-9]|PA46|C130|L410|AN2|AN26|CN35|C295|E120|SW[34]|F50|JS[34]|ATP|SB20|P3$|C27J|DH[CT])", "prop"),
        (r"^(CRJ|E13|E14|ERJ|MD8|MD9|DC9|B712|B717|F100|F70|GLF|GL[0-9]|GLEX|CL[36]|C25|C5[0-9]|C68|C75|LJ|FA[5-9]|F2TH|F900|H25|HA4T|E35L|E50P|E55P|E545|E550|PRM1|BE40|ASTR|G150|G280|WW24|SBR|YK4)", "rear"),
        (r"^(C1[05-9]|C2[0-1]|C3[0-9]|P28|PA2|PA3[0-4]|PA44|SR2|DA4|DA6|BE3|BE5|BE9|M20|RV|GLID|AA5|AC|BL8|C7|CH7|DV20|P2|EVOT|ULAC|HXB|LNC)", "ga")]
def _fam(t):
    import re
    t = (t or "").upper()
    return next((f for rx, f in _FAM if re.match(rx, t)), "narrow")

def _light(x):
    """a GA aircraft or a helicopter (by type family, or ADS-B category A1 / A7); an unknown aircraft counts as light"""
    c = x.get("category") or ""
    return _fam(x.get("type")) in ("ga", "heli") or c in ("A1", "A7") or (not x.get("type") and not c)

def lined_up(x, dep=False):
    """Which runway the aircraft is lined up with: its GPS position against the extended centreline, heading within 15 degrees.
    -> (runway, km to the threshold, km off the centreline) or None. dep=False: on approach, up to 40 km out, within 400 m of the
    centreline. dep=True: rolling or just off the far end (the distance is then km from the start of the runway)."""
    p = _xy(x["lat"], x["lon"]); tr = x.get("track") or 0; best = None
    for n, t, u, hdg, ln in ENDS:
        if abs((tr - hdg + 540) % 360 - 180) > 15: continue
        ax, ay = p[0] - t[0], p[1] - t[1]; along = ax * u[0] + ay * u[1]; cross = abs(ax * u[1] - ay * u[0])
        ok = (0 < along < ln + 10 and cross < 0.7) if dep else (-40 < along < 0 and cross < 0.4)
        if ok and (best is None or cross < best[2]): best = (n, along if dep else -along, cross)
    return best

def tower_for(rwy):
    """most likely tower first: North works 05/23, South the 06/24 pair; for 15/33 either one"""
    return [CH["twrS"], CH["twrN"]] if rwy[:2] in ("06", "24") else [CH["twrN"], CH["twrS"]]

def detail(x):
    """-> (why, [channels, most likely first], the runway it is lined up with or "")"""
    p = phase(x); alt = x.get("alt_ft") or 0
    dz, _ = _geo((x["lat"], x["lon"]), YYZ); dt, _ = _geo((x["lat"], x["lon"]), YTZ)
    if x.get("on_ground"):
        if dz < 4: return "on the ground at Pearson", [CH["gndN"] if x["lat"] > YYZ[0] else CH["gndS"], CH["apron"]], ""
        if dt < 2: return "on the ground at Billy Bishop", [CH["ytzGnd"]], ""
        return "on the ground", [], ""
    if "YTZ" in p: return p.replace("YTZ", "Billy Bishop"), [CH["ytzTwr"]], ""
    if p == "arriving YYZ":
        r = lined_up(x)
        if (r and r[1] < 13) or (dz < 14 and alt < 3500):
            return (f"final for runway {r[0]}, {r[1]:.0f} km out" if r else "short final") + " - with the tower", tower_for(r[0]) if r else [CH["twrN"], CH["twrS"]], r[0] if r else ""
        return (f"lined up for runway {r[0]}, {r[1]:.0f} km out" if r else "on approach") + " - with Toronto Arrival", list(CH["arr"]), r[0] if r else ""
    if p == "departing YYZ":
        if dz < 9 and alt < 3000:
            r = lined_up(x, dep=True)
            return "just airborne" + (f" off runway {r[0]}" if r else "") + " - with the tower", tower_for(r[0]) if r else [CH["twrN"], CH["twrS"]], ""
        north = x["lat"] > YYZ[0]
        return f"climbing out, {'north' if north else 'south'} of the field - with Toronto Departure", [CH["depN"], CH["depS"]] if north else [CH["depS"], CH["depN"]], ""
    if alt < 4000 and dz > 10 and dt > 6 and _light(x): return "low and local - VFR advisory", [CH["vfr"]], ""
    if alt < 6000 and dz < 30 and not _light(x) and (x.get("vrate") or 0) <= 300: return "low near Pearson, being vectored - probably with Toronto Arrival", list(CH["arr"]), ""
    # (v212) enroute: Toronto Centre. THIS line wrote "with Toronto Centre (not in the bank)" into the Aircraft tab - v210 added the
    # sectors everywhere else and left this file alone. Its side of Pearson, low sectors first below 18,000 ft (where Canadian
    # high-level airspace begins; the sectors' real limits are not in the sources used).
    K = CH["ctr"]; west = x["lon"] < YYZ[1]; high = alt >= 18000
    order = ((K["wh"] + K["wl"]) if high else (K["wl"] + K["wh"])) if west else ((K["eh"] + K["el"]) if high else (K["el"] + K["eh"]))
    if alt < 5000 and dz >= 30:                                               # (v213) low and far from Pearson: Hamilton, Waterloo, Buffalo, Oshawa ...
        return f"{p or 'enroute'} - low and away from Pearson: with another airport's controller, not known here (Centre's low sectors are a guess)", order, ""
    return f"{p or 'enroute'} - with Toronto Centre, {'west' if west else 'east'} side (which sector is a best guess)", order, ""

def freqs(x):
    """channels this aircraft is on right now, most likely first"""
    return detail(x)[1]

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
            for i, (f, n) in enumerate(c for c in freqs(x) if not c[1].startswith("CTR ")):   # (v212) the Follow bank stays on Pearson's and Billy Bishop's channels: Centre is its own bank
                w = (3.0 if i == 0 else 1.0) * (2.0 if x.get("km", 99) < 15 else 1.0) * (0.15 if parked else 1.0)   # a parked aircraft is not talking
                s = score.setdefault((f, n), 0.0); score[(f, n)] = s + w
    except Exception: pass
    ranked = [k for k, _ in sorted(score.items(), key=lambda kv: -kv[1])][:limit]
    if not ranked: ranked = [CH["twrN"], CH["twrS"], CH["arr"][0], CH["depS"], CH["arr"][1]]
    for t in (CH["twrN"], CH["twrS"]):                                     # both towers always ride along: every arrival and departure passes through them
        if t not in ranked: ranked.append(t)
    chans = [(f, "am", n) for f, n in ranked]
    _last.update(t=time.time(), chans=chans); return chans

if __name__ == "__main__":
    import json, urllib.request
    j = json.load(urllib.request.urlopen("http://localhost:8765/api/aircraft"))
    class D: aircraft_now = staticmethod(lambda: j)
    sys.modules["dashboard2"] = D
    for c in follow_list(): print(c)
