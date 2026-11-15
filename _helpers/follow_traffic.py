#!/usr/bin/env python3
"""Follow the traffic: the Monitor's channel list derived from live ADS-B (which aircraft are on which YYZ/YTZ frequency right now).
Imported by dashboard.py (monitor_list "Follow traffic"); the channelizer re-reads it at the start of every lap, so the scanner
retunes itself as the planes move - nothing to press.

Which channel an aircraft is on is WORKED OUT from where it is and what it is doing; it is not read off the radio. The rules (v181), the
same ones the Aircraft tab draws (monitor_panel.html freqs()):
  on final, lined up with a runway (GPS position against the centreline) or low and close -> the tower for that runway
  on approach, further out          -> Toronto Arrival: 132.800 (the main channel) first, then 124.475, 125.400
  just airborne                     -> the tower for the runway it left, below 1,500 ft; above that already Toronto Departure (v230)
  climbing out                      -> Toronto Departure: 127.575 north of the field, 128.800 south - unless only one of the two has
                                       had a voice on it here in the last day (v242): then that one, wherever the aircraft is;
                                       from 6,000 ft Centre's sector for that side joins the list, from 9,500 ft it leads
  leaving Billy Bishop              -> its tower below 1,500 ft, then Toronto Terminal's East Satellite 133.400 (v230)
  on the ground                     -> Ground North / South + Apron;  Billy Bishop -> 118.200
  low, not arriving or departing    -> (v214) inside a control zone: that tower. Otherwise Toronto Terminal's "satellite" position for
                                       where it is: Hamilton 119.700, Kitchener 128.275, West 119.300, East 133.400
  everything else                   -> Toronto Centre, the sector on its side of Pearson
Channel roles as published (canairradio.ca/yyz.html, Wikipedia "Toronto Area Control Centre"): tower 118.700 is North (runway 05/23) and
118.350 South (06L/24R, 06R/24L); 134.175 is the ILS monitor, not a working arrival channel, so it is no longer a candidate. The names are
the bank's own (sdr_scan.py REFERENCE). Runway ends are from OurAirports (public domain)."""
import math, sys, time
YYZ = (43.6777, -79.6248); YTZ = (43.6275, -79.3962)
CH = {"twrN": (118.700, "YYZ TWR N"), "twrS": (118.350, "YYZ TWR S"), "arr": [(132.800, "YYZ ARR 3"), (124.475, "YYZ ARR 1"), (125.400, "YYZ ARR 2")],
      "depN": (127.575, "YYZ DEP 1"), "depS": (128.800, "YYZ DEP 2"), "gndN": (121.650, "YYZ GND N"), "gndS": (121.900, "YYZ GND S"), "apron": (122.825, "YYZ APRON"),
      "ytzTwr": (118.200, "YTZ TWR"), "ytzGnd": (121.700, "YTZ GND"), "vfr": (133.400, "TOR VFR"),
      # (v214) Toronto Terminal's satellite positions (Wikipedia "Toronto Area Control Centre": East Satellite 133.400 - the "vfr" entry
      # above -, West Satellite 119.300, West Satellite (Hamilton) 119.700, West Satellite (Kitchener) 128.275)
      "satW": (119.300, "YYZ VFR"), "satHam": (119.700, "TML HAMILT"), "satKW": (128.275, "TML KITCH"),
      # (v212) Toronto Centre around Toronto: west / east of Pearson, low / high sectors (the same table as dashboard.TOR_CENTRE; sources there)
      # (v230) + 135.625, Centre's Kitchener sector, West Low (radioreference.com/db/subcat/69789, canairradio.ca/yyz.html): ACA501 was
      # heard on it on 14 Nov, climbing through 16,200 ft 60 km west, while this table offered four other sectors.
      "ctr": {"wl": [(132.475, "CTR HAMILT"), (133.300, "CTR GRIMSB"), (135.625, "CTR KITCHN")], "wh": [(125.775, "CTR CENTRA"), (134.925, "CTR OAKVIL")],
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
HAM = (43.1736, -79.9350); YKF = (43.4608, -80.3786)                       # Hamilton and Kitchener-Waterloo airports (OurAirports)

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
    # (v221) The table's direction against what the aircraft is doing. 13 Nov 11:44: SKW6488, on file Chicago -> Toronto, was "on
    # short final" at 675 ft and four minutes later at 7,625 ft, climbing: it had been taking off, the table has the inbound leg
    # of that flight number. A firm climb close to Pearson voids "to Pearson", a firm descent voids "from Pearson"; the rules for
    # an aircraft without a route then decide. And JZA541, on file "-> CYYZ", landed at Billy Bishop: a turboprop below 3,500 ft,
    # nearer the island than Pearson and pointing at it, is the island's.
    if (to_ytz or from_ytz) and x.get("type") and not _small(x.get("type")): to_ytz = from_ytz = False     # (v242) Billy Bishop takes no jets: the route table has another leg of that flight number
    if to_yyz and vr > 300 and alt < 9000 and dz < 35: to_yyz = False
    if from_yyz and vr < -300 and alt < 10000 and dz < 60: from_yyz = False
    if to_yyz and _small(x.get("type")) and dt < dz and dt < 15 and alt < 3500 and at < 45: to_yyz = False; to_ytz = True
    if not (to_ytz or from_ytz):
        if to_yyz and alt < 12000 and vr <= 100 and dz < 60: return "arriving YYZ"
        if from_yyz and alt < 12000 and vr > 100 and dz < 45: return "departing YYZ"
        light = _fam(x.get("type")) in ("ga", "heli") or x.get("category") == "A7"
        if not (to_yyz or from_yyz) and not light:                         # (v183) a Cessna or a helicopter with no Pearson route is not Pearson traffic
            if dz < 45 and alt < 9000 and vr <= 0 and az < 50: return "arriving YYZ"
            if dz < 35 and alt < 9000 and vr > 200 and az > 110: return "departing YYZ"
            # (v214) On the downwind leg an arrival points AWAY from the field, and the rule above wants it pointing at it. 13 Nov
            # 02:08: CPA075, a 747 at 7,575 ft 24 km out, descending 1,800 ft/min on heading 294, was shown as "with Toronto Centre"
            # while Matt listened to Arrival talking to it. A jet (not a turboprop: those may be Billy Bishop's) that is coming down
            # below 10,000 ft, or sits level below 8,000 ft inside 40 km, nearer Pearson than Hamilton, is a Pearson arrival.
            jet = bool(x.get("type")) and not _small(x.get("type"))
            if jet and dz < 60 and dz < _geo((x["lat"], x["lon"]), HAM)[0] and alt < 10000 and (vr < -300 or (alt < 8000 and vr <= 100 and dz < 40)): return "arriving YYZ"
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

# (v242) WHAT THE RECEIVER HAS HEARD, AS EVIDENCE. heard_check.csv, 84 calls matched to an aircraft from 14 Nov 11:04 to 15 Nov 10:18:
# the channel offered first was the one the aircraft was on 27 times, a later one 33 times, none 24 times. 23 of the 33 were one
# rule: "north of the field -> Departure 127.575". Every one of those 23 departures was on 128.800. monitor_opens.log agrees:
# 127.575 has opened 28 times in ten days and never with speech; 128.800 has speech every day (284 openings on 11 Nov alone, a
# day on which both were being listened to). Which of its two channels Departure is working cannot be told from where an
# aircraft is; it can be told from what has been heard. _air_activity() counts, per channel, the openings with speech of the
# last 24 hours. (Not proof that 127.575 is silent: the Monitor listens to the channels heard most recently, and since
# 11 Nov that has probably not included it. So 127.575 stays in every list, second.)
_ACT = {"t": 0.0, "d": {}}
def _air_activity():
    """{MHz rounded to 3 places: openings with speech in the last 24 h} from monitor_opens.log; read again every five minutes"""
    if time.time() - _ACT["t"] < 300: return _ACT["d"]
    d = {}
    try:
        import os, re
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "monitor_opens.log")
        with open(path, "rb") as fh:
            fh.seek(0, 2); n = fh.tell(); fh.seek(max(0, n - 900000)); txt = fh.read().decode("utf-8", "ignore")
        now = time.time(); yr = time.localtime().tm_year
        for m in re.finditer(r"^(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d) .* (\d+\.\d{4}) am [\d.]+s .* voice yes", txt, re.M):
            try: ts = time.mktime((yr, int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)), 0, 0, -1))
            except (ValueError, OverflowError): continue
            if ts - now > 86400: ts = time.mktime((yr - 1,) + time.localtime(ts)[1:6] + (0, 0, -1))     # a line of 31 Dec read on 1 Jan
            if 0 <= now - ts <= 86400: k = round(float(m.group(6)), 3); d[k] = d.get(k, 0) + 1
    except Exception: pass
    _ACT.update(t=time.time(), d=d); return d
def _dep(north):
    """Toronto Departure's two channels, most likely first -> (channels, True when the order comes from what was heard)"""
    a = _air_activity(); n, s2 = a.get(127.575, 0), a.get(128.8, 0)
    if s2 >= 3 and s2 >= 10 * n: return [CH["depS"], CH["depN"]], True      # ten to one or better: that one is the working channel
    if n >= 3 and n >= 10 * s2: return [CH["depN"], CH["depS"]], True
    return ([CH["depN"], CH["depS"]] if north else [CH["depS"], CH["depN"]]), False

def _out_sectors(x):
    """The two Centre sectors a Pearson departure is most likely handed to. By where it is around the field, as heard (v242): south
    of the field 132.475 (WJA2136, JZA780, FLE2610, JZA757 - two of them east of the field, where the old rule 'east of Pearson ->
    the east sectors' offered 124.925); north and east 124.925 (DLH471, ACA462, ACA880). North and west: nothing heard yet."""
    K = CH["ctr"]
    if x["lat"] < YYZ[0]: return [K["wl"][0], K["el"][0]]
    if x["lon"] >= YYZ[1]: return [K["el"][0], K["wl"][0]]
    return [K["wl"][0], K["wl"][2]]

def tower_for(rwy):
    """most likely tower first: North works 05/23, South the 06/24 pair; for 15/33 either one"""
    return [CH["twrS"], CH["twrN"]] if rwy[:2] in ("06", "24") else [CH["twrN"], CH["twrS"]]

def _night(): h = time.localtime().tm_hour; return h >= 23 or h < 7
def _arr():
    """Toronto Arrival's channels, most likely first. Overnight Pearson combines its terminal sectors on 128.800, the 'departure'
    channel: an inbound was cleared to 3,000 ft there on 11 Nov at 01:22, and on 13 Nov at 02:07 "... speed ... Cathay zero seven
    five" was recorded there while CPA075 flew the downwind. So from 23:00 to 07:00 that channel leads. (Two nights' evidence.)"""
    return ([CH["depS"]] + list(CH["arr"])) if _night() else list(CH["arr"])
def detail(x):
    """-> (why, [channels, most likely first], the runway it is lined up with or "")"""
    p = phase(x); alt = x.get("alt_ft") or 0
    dz, _ = _geo((x["lat"], x["lon"]), YYZ); dt, _ = _geo((x["lat"], x["lon"]), YTZ)
    if x.get("on_ground"):
        if dz < 4: return "on the ground at Pearson", [CH["gndN"] if x["lat"] > YYZ[0] else CH["gndS"], CH["apron"]], ""
        if dt < 2: return "on the ground at Billy Bishop", [CH["ytzGnd"], CH["ytzTwr"]], ""     # (v242) POE2267 was heard on the tower, on the ground (lining up)
        return "on the ground", [], ""
    # (v230) What the app's own on-air check showed on 14 Nov (heard_check.csv, 24 calls matched to an aircraft, 01:51-10:46): the
    # first channel offered was the one the aircraft was heard on 13 times, another of the list twice, none of them 9 times.
    # Seven of the nine fall under three rules, changed here:
    #  - "just airborne - with the tower" up to 3,000 ft: UAL2603, ROU1804 and ACA163 were on Departure 128.800 by 2,400-2,700 ft
    #    (and the call was made 20-30 s before the altitude was read). Tower first only below 1,500 ft now.
    #  - Billy Bishop departures were offered the island's tower only: JZA7956 at 5,675 ft and POE2303 at 6,100 ft were on
    #    133.400, Toronto Terminal's East Satellite. Above 1,500 ft that channel now leads.
    #  - the hand-over from Departure to Centre is not at one altitude: TSC186 was still on 128.800 at 12,775 ft, POE765 already
    #    on Centre 132.475 at 10,075 ft. Each list now ends with the other side of that hand-over.
    if p == "departing YTZ":
        if alt < 1500: return "departing Billy Bishop - with its tower", [CH["ytzTwr"], CH["vfr"]], ""
        return "departing Billy Bishop - with Toronto Terminal (East Satellite)", [CH["vfr"], CH["ytzTwr"]], ""
    if "YTZ" in p:
        # (v242) further out an island arrival is still with the East Satellite: POE2476 (3,850 ft) and POE2122 (1,700 ft) were heard
        # on 133.400 while only the tower was offered; POE2208 and POE2630, closer in, were on the tower.
        if dt > 18: return "arriving Billy Bishop - with Toronto Terminal (East Satellite), then its tower", [CH["vfr"], CH["ytzTwr"]], ""
        return "arriving Billy Bishop - with its tower", [CH["ytzTwr"], CH["vfr"]], ""
    if p == "arriving YYZ":
        r = lined_up(x)
        if (r and r[1] < 13) or (dz < 14 and alt < 3500):
            twr = tower_for(r[0]) if r else [CH["twrN"], CH["twrS"]]
            # (v242) the change from Arrival to the tower is not at one distance: at 11-12 km out CPA826 and POE318 (runway 05) were
            # on the tower, WJA859 and POE150 (06L) still on Arrival 132.800 - which was not in the list. From 8 km out it now is.
            if r and r[1] >= 8: twr = twr[:1] + _arr()[:1] + twr[1:]
            return (f"final for runway {r[0]}, {r[1]:.0f} km out" if r else "short final") + " - with the tower", twr, r[0] if r else ""
        return (f"lined up for runway {r[0]}, {r[1]:.0f} km out" if r else "on approach") + " - with Toronto Arrival" + (" (overnight: on 128.800 with Departure)" if _night() else ""), _arr(), r[0] if r else ""
    if p == "departing YYZ":
        north = x["lat"] > YYZ[0]; dep, by_ear = _dep(north)
        if dz < 9 and alt < 3000:
            r = lined_up(x, dep=True); twr = tower_for(r[0]) if r else [CH["twrN"], CH["twrS"]]; off = f" off runway {r[0]}" if r else ""
            if alt < 1500: return "just airborne" + off + " - with the tower", twr[:1] + dep[:1] + twr[1:], ""
            return "just airborne" + off + " - changing from the tower to Toronto Departure", dep[:1] + twr[:1] + dep[1:], ""
        # (v242) The hand-over to Centre, as heard: DLH471 was on Centre at 7,100 ft; from 9,500 ft up five of five were (ACA462,
        # WJA2136, ACA880, FLE2610, JZA780), below that three of four on Departure. So: Centre's sectors join the list at 6,000 ft
        # and lead it from 9,500 ft; which sectors: _out_sectors().
        ctr = _out_sectors(x) if alt >= 6000 else []
        where = f"climbing out, {'north' if north else 'south'} of the field"
        ear = f" ({dep[0][0]:.3f}: the one of its two channels heard here in the last day)" if by_ear else ""
        if alt >= 9500: return where + " - with Toronto Departure or already with Centre" + ear, ctr[:1] + dep[:1] + ctr[1:] + dep[1:], ""
        return where + " - with Toronto Departure" + ear, dep + ctr, ""
    # (v214) Low and neither arriving nor departing. Until now a helicopter crossing Pearson at 2,900 ft fell through every rule and
    # came out as "level - with Toronto Centre": the rule for light aircraft began 10 km from Pearson, the rule for airliners excluded
    # light aircraft, and Centre was what was left.
    light = _light(x); vr = x.get("vrate") or 0; north = x["lat"] > YYZ[0]
    if light and alt < 3600 and dz <= 13:                                     # a control zone is normally 7 nm wide and 3,000 ft deep (TC AIM RAC 2.7.3); Pearson's field is at 569 ft
        return "crossing Pearson's control zone - with the tower (best guess)", [CH["twrN"], CH["twrS"]] if north else [CH["twrS"], CH["twrN"]], ""
    if light and alt < 3000 and dt <= 9:
        return "in Billy Bishop's control zone - with its tower (best guess)", [CH["ytzTwr"]], ""
    dh, _ = _geo((x["lat"], x["lon"]), HAM); dk, _ = _geo((x["lat"], x["lon"]), YKF); sat = min(dh, dk) < 30
    if alt < 7000 and (light or sat or dz >= 50):
        if dz > 75 and not sat:                                               # London, Buffalo, Peterborough ...: not Toronto's
            K = CH["ctr"]; lo = (K["wl"] + K["wh"]) if x["lon"] < YYZ[1] else (K["el"] + K["eh"])
            return f"{p or 'enroute'} - low and far out: with another airport's controller, not known here (Centre's low sectors are a guess)", lo, ""
        if sat and dh <= dk: where, ch = "Hamilton", [CH["satHam"], CH["satW"]]
        elif sat: where, ch = "Kitchener-Waterloo", [CH["satKW"], CH["satW"]]
        elif x["lon"] >= YYZ[1]: where, ch = "the east side", [CH["vfr"], CH["satW"]]
        else: where, ch = "the west side", [CH["satW"], CH["vfr"]]
        return f"low and {'local' if light else 'away from Pearson'} - probably with Toronto Terminal's satellite controller for {where}", ch, ""
    if alt < 10000 and dz < 50 and not light:                                 # an airliner low inside the terminal area that phase() could not place
        if vr > 300 and _small(x.get("type")) and dt < dz:                    # (v230) a turboprop climbing nearer the island than Pearson: it left Billy Bishop
            return "climbing away from Billy Bishop - probably with Toronto Terminal (East Satellite)", [CH["vfr"]] + _dep(False)[0], ""
        if vr > 300: return "climbing near Pearson - probably with Toronto Departure", _dep(north)[0], ""
        return "low near Pearson, being vectored - probably with Toronto Arrival", _arr(), ""
    # (v212) enroute: Toronto Centre. THIS line wrote "with Toronto Centre (not in the bank)" into the Aircraft tab - v210 added the
    # sectors everywhere else and left this file alone. Its side of Pearson, low sectors first below 18,000 ft (where Canadian
    # high-level airspace begins; the sectors' real limits are not in the sources used).
    K = CH["ctr"]; west = x["lon"] < YYZ[1]; high = alt >= 18000
    own_hi, own_lo, oth_hi = (K["wh"], K["wl"], K["eh"]) if west else (K["eh"], K["el"], K["wh"])
    # (v242) up high the side of Pearson does not settle the sector: TSC938 at 38,000 ft was "east" and on 134.925, ASL507 at
    # 37,000 ft "west" and on 134.575 - each on a high sector of the other side, which was not in the list. Now it is.
    order = (own_hi + oth_hi + own_lo) if high else (own_lo + own_hi)
    lead = []; what = f"with Toronto Centre, {'west' if west else 'east'} side (which sector is a best guess)"
    if p == "climbing" and alt < 16000 and dz < 60 and not light:
        # (v230, v242) may still be with Departure - FLE639 (10,050 ft), JZA757 (13,700 ft) and POE2519 (15,325 ft) were, on
        # 128.800 - or just handed to one of the two sectors that take the departures (JZA757 was on 132.475 at 14,100 ft)
        o = _out_sectors(x); lead = o[:1] + _dep(north)[0][:1] + o[1:]; what = "just handed to Toronto Centre, or still with Departure (a best guess)"
    elif p in ("descending", "level") and alt < 15000 and dz < 75 and not light and x["lat"] < YYZ[0]:
        # (v242) arrivals from the south come in through Centre's Grimsby sector and go to Arrival from there: JZA7188 (11,000 ft)
        # and WJA4112 (13,150 ft) were on 133.300, WJA4112 twenty seconds later on Arrival 132.800 - "east of Pearson", where
        # neither channel was in the list
        lead = [K["wl"][1]] + _arr()[:1] + [K["wl"][0]]; what = "coming in from the south: with Toronto Centre (Grimsby), then Arrival (a best guess)"
    elif p == "descending" and alt < 11000 and not west and _small(x.get("type")):
        # (v242) a turboprop coming down east of Toronto is the island's and already with the East Satellite: POE2632 (8,500 ft)
        # and JZA7959 (10,375 ft), both more than 60 km out, were on 133.400
        lead = [CH["vfr"]]; what = "a turboprop coming down east of Toronto: probably the island's, with Toronto Terminal (East Satellite)"
    if lead: order = lead + [c for c in order if c not in lead]
    return f"{p or 'enroute'} - {what}", order, ""

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
