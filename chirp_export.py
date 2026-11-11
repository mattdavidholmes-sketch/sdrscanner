#!/usr/bin/env python3
"""(v199) The CHIRP files for the Baofeng UV-32 - one layout for every CHIRP file this app writes.

Two files, same memory numbers in both:
  chirp_bank.csv   everything the radio can tune and a person can listen to
  chirp_car.csv    the part of it the car antenna is built for (Nagoya UT-72 mag mount: 2 m and 70 cm)

What the radio accepts is taken from the CHIRP driver the UV-32 is programmed with (UV-21ProGPS, chirp/drivers/baofeng_uv17Pro.py):
memories 1..1000 in ten fixed banks of 100, names up to 12 characters, modes NFM / FM / AM (AM only between 108 and 136 MHz),
duplex "", "-", "+", "off" (off = the memory cannot transmit), tone modes "", Tone, TSQL, DTCS.

The layout is by what a channel is FOR, one bank per function, and it is the radio's own bank structure:
   bank 0    1-100   AIRBAND        Pearson first (ATIS, clearance, ground, tower, arrival, departure), then the other airports
   bank 1  101-200   HAM 2M         repeaters nearest first, then simplex and net channels
   bank 2  201-300   HAM 70CM       the same on 70 cm
   bank 3  301-400   FRS GMRS       channels 1-22 in order
   bank 4  401-500   STORES SITES   MURS, the business "dot" and "star" channels, store radios
   bank 5  501-600   RAIL TRANSIT   CN / CP road and yard channels, Union Station, TTC
   bank 6  601-700   MARINE
   bank 7  701-800   CITY ROADS     highway and road crews, waste, city security, utilities
   bank 8  801-900   HEARD HERE     identified by licence or confirmed as voice by this receiver, not in the directory yet
   bank 9  901-1000  (left free)

A frequency keeps its memory number for good: the numbers handed out are remembered in chirp_layout.json, a new channel takes the
next free number in its bank, and nothing already in the radio moves. "UV-32 ch 105" means the same channel after every export
and in both files (the car file simply has gaps).

Rules, all of them from how the radio is used:
  - analog voice only. Digital systems, data channels and broadcast FM never go in.
  - transmit is possible only where it belongs: the ham banks (repeater shifts and tones filled in) and FRS / GMRS. Every other
    memory is written with duplex "off" - the radio cannot key up on an airport, railway, marine or city frequency by accident.
  - repeaters get tone mode "Tone" (the radio sends the tone, its own squelch stays open) - never TSQL, which mutes a repeater
    that does not send the tone back.
  - listen-only channels get NO tone squelch even when the tone is known (it is in the comment): a wrong or changed tone would
    make the memory silent, and for listening an open squelch loses nothing.
  - ATIS / AWOS never stop talking: they are marked "skip" so a scan does not park on them.
"""
import csv, datetime as dt, io, json, os, re, time

COLS = ["Location", "Name", "Frequency", "Duplex", "Offset", "Tone", "rToneFreq", "cToneFreq", "DtcsCode", "DtcsPolarity", "RxDtcsCode", "CrossMode",
        "Mode", "TStep", "Skip", "Power", "Comment", "URCALL", "RPT1CALL", "RPT2CALL", "DVCODE"]
RADIO = "Baofeng UV-32 (CHIRP driver UV-21ProGPS)"
RADIO_RX = ((108.0, 136.0), (136.0, 174.0), (220.0, 260.0), (350.0, 390.0), (400.0, 520.0))     # the driver's VALID_BANDS
AM_BELOW = 136.0                                                                                  # the driver makes 108-136 MHz AM, nothing else
NAME_LEN = 10                                                                                     # the radio shows 12; 10 is the house rule
PER_BANK = 100
BANKS = ("AIRBAND", "HAM 2M", "HAM 70CM", "FRS GMRS", "STORES SITES", "RAIL TRANSIT", "MARINE", "CITY ROADS", "HEARD HERE", "")
BANK_TITLE = ("Airband", "Ham 2 m", "Ham 70 cm", "FRS / GMRS", "Stores and job sites", "Rail and transit", "Marine", "City, roads and utilities",
              "Heard here, not in the directory yet", "free")
AIR, HAM2, HAM70, FRS, SITES, RAIL, MARINE, CITY, HEARD = range(9)
TX_BANKS = (HAM2, HAM70, FRS)                                 # the only banks whose memories may transmit

# The car antenna: Nagoya UT-72, a magnetic-mount dual-band whip - a quarter wave on 2 m, a collinear on 70 cm. It is matched on
# 144-148 and 430-450 MHz; up to 470 (FRS / GMRS, UHF business) the match is worse but it still works, and those channels are
# wanted in the car. Anywhere else the whip is the wrong length: it will pick up a strong nearby signal, but it is not what the
# antenna is for, so the car file leaves those bands out.
CAR_ANTENNA = "Nagoya UT-72 (magnetic mount, 2 m / 70 cm)"
CAR_RX = ((144.0, 148.0), (430.0, 470.0))
CAR_TX = ((144.0, 148.0), (430.0, 470.0))

def tunable(m): return any(lo <= m < hi for lo, hi in RADIO_RX) or abs(m - 520.0) < 1e-6
def in_car(m): return any(lo <= m <= hi for lo, hi in CAR_RX)
def _key(m): return f"{m:.4f}"

# ---------------------------------------------------------------- names: at most ten characters, capitals, unique
_AIR_WORDS = (("ATIS/AWOS", "ATIS"), ("Clearance", "CLNC"), ("Ground", "GND"), ("Tower", "TWR"), ("Arrival", "ARR"), ("Departure", "DEP"),
              ("Apron", "APRN"), ("Pad control", "PAD"), ("VFR advisory", "VFR"), ("Toronto Terminal Arr/Dep", "TERMNL"), ("Toronto Arr/Dep", "ARR DEP"),
              ("Porter FBO", "PORTER"), ("Stolport FBO", "STOLPT"), ("Cargojet ops", "CARGOJ"), ("Hamilton Jetport", "JETPRT"), ("/ MF", ""),
              ("ATF / UNICOM", "ATF"), ("Guard / emergency", "GUARD"), ("London FIC flight plans", "LONDON FIC"), ("Toronto VFR", "TOR VFR"), ("(primary)", ""))
_RULES = tuple((re.compile(p, re.I), r) for p, r in (
    (r"^Home Depot ch(\d)", r"HD CH\1"), (r"^Walmart ch(\d)", r"WALMART \1"), (r"^Walmart UHF", "WALMART U"), (r"^Costco", "COSTCO"),
    (r"^Dot: (\w+) star", r"\1 STAR"), (r"^Dot: (\w+)", r"\1 DOT"), (r"^(\w+) dot", r"\1 DOT"),
    (r"^(\w+) star", r"\1 STAR"), (r"^Itinerant (\w)$", r"ITIN \1"), (r"^Itinerant \d+\.(\d+)", r"ITIN \1"), (r"^Business itinerant", "BIZ ITIN"),
    (r"^MURS (\d)", r"MURS \1"), (r"^FRS (\d+)", r"FRS \1"), (r"^GMRS (\d+)", r"GMRS \1"),
    (r"^City of Toronto Corporate Secu\w* mobile", "TOR SEC M"), (r"^City of Toronto Corporate Secu", "TOR SEC"),
    (r"Solid Waste", "TOR WASTE"), (r"^GFL", "GFL WASTE"), (r"^Miller Waste", "MILLER WST"), (r"^Emterra", "EMTERRA"), (r"^Halton Region Waste", "HALTON W"),
    (r"^St\. Lawrence Market", "STLAWMKT"), (r"^TTC \(VHF", "TTC VHF"), (r"^TTC Etobicoke", "TTC ETOB"), (r"^TTC Zone 1", "TTC KIPL"),
    (r"^Enbridge", "ENBRIDGE"), (r"^MTO highway plows", "MTO PLOWS"), (r"^MTO maint", "MTO MAINT"), (r"^MTO road crews", "MTO ROAD"),
    (r"^Marine (\d+)", r"MARINE \1"), (r"^Prescott CG", "PRESCOT CG"),
    (r"^CP Toronto Yard hump (\d)", r"CP HUMP \1"), (r"^CN(\d) RTC", r"CN\1 RTC"), (r"^CP78", "CP78 CANPA"), (r"^CP4", "CP4 MACTR"), (r"^CP5", "CP5 GALT"),
    (r"^CN1", "CN1 ROAD"), (r"^CP1", "CP1 GALT"), (r"^CP7", "CP7 RTC"), (r"^CP RTC call-in 2", "CP RTC 2"),
    (r"^Union Station platform ops 2|^TTR Union", "UNION PLT2"), (r"^Union Station platform", "UNION PLAT"),
    (r"^Via Rail Union Station 2", "VIA UNION2"), (r"^Via Rail Union|^VIA Union", "VIA UNION"), (r"^VIA TMC", "VIA TMC"),
    (r"^CP Rail Etobicoke Yard", "CP ETOB YD"), (r"^CP Rail Etobicoke", "CP ETOB"), (r"^CP Rail Car Shop", "CP CARSHOP"),
    (r"^Board Of Governors Of Ex", "EXHIBIT"), (r"^Toronto East General", "TOR E HOSP"), (r"^City Of Toronto", "CITY TOR"),
))

def _fit(s):
    """capitals, plain characters, cut at a word where that still leaves something"""
    s = re.sub(r"\([^)]*\)?", " ", s); s = re.sub(r"[^A-Za-z0-9/ .+-]", " ", s).upper(); s = re.sub(r"\s+", " ", s).strip()
    if len(s) <= NAME_LEN: return s
    cut = s[:NAME_LEN + 1].rsplit(" ", 1)[0]
    return (cut if len(cut) >= 5 else s[:NAME_LEN]).strip()

def short_name(full, bank, mhz, ref="", group=""):
    if ref and len(ref) <= NAME_LEN and ref == ref.upper(): return ref              # the app's own short names (YYZ TWR N, VA3GTU 70, CN MOW 1 ...)
    if bank == AIR:
        if not full.strip() or re.match(r"^[\d.]+|^Nav Canada", full): return f"ATC{mhz:.3f}"          # heard and confirmed, sector not known
        code = (group.split() or [""])[0]; code = "" if code == "Common" else code; s = full
        for a, b in _AIR_WORDS: s = s.replace(a, b)
        return _fit(f"{code} {s}")
    for rx, rep in _RULES:
        m = rx.search(full)
        if m: return _fit(m.expand(rep))
    if re.match(r"^[\d.]+", full) or not full.strip(): return f"{mhz:.4f}"[:NAME_LEN]
    return _fit(full.split(" - ")[0])

def _number(chans):
    """Names are unique. A family that shares a name (thirteen City security channels) is numbered in the order it sits in the radio."""
    fam = {}
    for c in chans: fam.setdefault(c["name"], []).append(c)
    taken = {n for n, v in fam.items() if len(v) == 1}
    for stem, v in fam.items():
        if len(v) == 1: continue
        for i, c in enumerate(v, 1):
            n = str(i)
            for cand in (f"{stem} {n}", f"{stem}{n}", f"{stem[:NAME_LEN - len(n) - 1].strip()} {n}", f"{stem[:NAME_LEN - len(n)]}{n}"):
                if len(cand) <= NAME_LEN and cand not in taken: break
            c["name"] = cand; taken.add(cand)

# ---------------------------------------------------------------- what goes in
_DIGITAL_WORDS = re.compile(r"digital only|\(digital\)", re.I)             # a mixed-mode repeater (FM + DMR) stays in: its FM side works
_PL = re.compile(r"\b(D?PL)\s?(\d{2,3}(?:\.\d)?)")

def _pl(c, text):
    """a tone written in a channel's name ("PL 103.5", "DPL 263") is kept for the comment"""
    for kind, val in _PL.findall(text):
        v = ("D" if kind == "DPL" else "") + val
        if v not in c["pl"]: c["pl"].append(v)

def channels(hub, reference, tables, bank, is_digital=None):
    """Every memory, in first-assignment order. hub = build_hub.entries(); reference = sdr_scan.REFERENCE; tables = the Monitor's own
    channel lists {"sites": [...], "rail": [...], "city": [...]} as (MHz, mode, name); bank = bank.json; is_digital(MHz) -> True on a
    frequency of a digital system the app decodes."""
    out = []; idx = {}; never = []                                  # never = frequencies a list calls digital-only: no later list brings them back
    def find(m):
        for d in (0.0, 0.0025, -0.0025):
            c = idx.get(_key(m + d))
            if c is not None: return c
        return None
    def refname(m):
        for f, v in reference.items():
            if abs(f - m) <= 0.0026: return v
        return None
    def add(m, full, b, sect, duplex="", offset=0.0, tone="", group="", note="", nets="", claim=False):
        m = round(float(m), 5)
        if _DIGITAL_WORDS.search(full) or _DIGITAL_WORDS.search(note): never.append(m); return None
        if not tunable(m) or (is_digital and is_digital(m)) or any(abs(m - f) < 0.0031 for f in never): return None
        c = find(m)
        if c is not None and claim and c["bank"] == SITES:
            # a shared business channel that a known local user is licensed on (CP's Etobicoke yard sits on an itinerant channel):
            # the memory goes where that user's other channels are
            c["also"].insert(0, c["full"]); c.update(full=full, bank=b, sect=sect, note=note); _pl(c, full)
            return c
        if c is not None:                                           # one memory per frequency: the first bank it belongs to keeps it
            if full and full not in c["also"] and full != c["full"]: c["also"].append(full)
            if tone and tone not in c["pl"]: c["pl"].append(tone)
            _pl(c, full)
            return c
        r = refname(m)
        c = {"mhz": m, "full": full, "bank": b, "sect": sect, "seq": len(out), "duplex": duplex or "", "offset": float(offset or 0), "tone": tone or "",
             "group": group, "note": note, "nets": nets, "also": [], "pl": [tone] if tone else [], "ref": r[0] if r else "",
             "skip": "S" if re.search(r"\bATIS\b|\bAWOS\b", full) else ""}
        _pl(c, full); out.append(c); idx[_key(m)] = c
        return c

    # 1. the directory - the curated list, already in listening order
    for e in hub:
        cat = e.get("cat", ""); grp = e.get("group", ""); nm = e.get("name", ""); m = e["mhz"]
        if cat == "Digital" or e.get("digital"): continue
        if cat == "Airport": add(m, nm, AIR, 0, group=grp, note=grp)
        elif cat == "Ham repeater": add(m, nm, HAM2 if m < 200 else HAM70, 0, e.get("duplex"), e.get("offset"), e.get("tone"), note=e.get("note", ""), nets=e.get("nets", ""))
        elif cat == "Retail" and _DIGITAL_WORDS.search(nm): never.append(float(m))
        elif cat == "Ham simplex": add(m, nm, HAM2 if m < 200 else HAM70, 1, note=e.get("note", ""))
        elif cat == "FRS/GMRS": add(m, nm, FRS, 0, note="FRS / GMRS")
        elif cat == "MURS": add(m, nm, SITES, 0, note="MURS")
        elif cat == "Retail": add(m, nm, SITES, 2, note="store radios")
        elif cat == "Marine": add(m, nm, MARINE, 0, note="marine VHF")
        elif cat == "City services":
            if nm.startswith("TTC"): add(m, nm, RAIL, 3, note="TTC analog licence")
            else: add(m, nm, CITY, {"Winter / snow / roads": 0, "Garbage / recycling": 1}.get(grp, 2), tone=e.get("tone", ""), note=grp)
    # 2. the app's reference names that the directory does not carry (railway road channels, a few repeaters and net channels)
    for m, v in sorted(reference.items()):
        nm = v[0]
        if find(m) is not None: continue
        if re.match(r"^(CN|CP)\d*\b", nm): add(m, nm, RAIL, 0, note="railway")
        elif re.match(r"^(VIA|TTR)\b", nm): add(m, nm, RAIL, 1, note="Union Station")
        elif re.match(r"^V[AE]\d[A-Z]{2,3}", nm): add(m, nm, HAM2 if m < 200 else HAM70, 0, v[2], v[3], v[1], note="repeater")
        elif 144 <= m <= 148 or 430 <= m <= 450: add(m, nm, HAM2 if m < 200 else HAM70, 1, note="simplex / net")
        elif 108 <= m < AM_BELOW: add(m, nm, AIR, 1, group="", note="airband")
        elif m < 174 and re.search(r"^TTC", nm): add(m, nm, RAIL, 3, note="TTC analog licence")
        elif re.search(r"Walmart|Costco|Home Depot|Dot:|Itinerant", nm): add(m, nm, SITES, 2, note="store radios")
        else: add(m, nm, CITY, 2, note="city / utilities")
    # 3. the Monitor's own banks
    for m, _mo, nm in tables.get("sites", ()): add(m, nm, FRS if re.match(r"^(FRS|GMRS) \d", nm) else SITES, 0 if nm.startswith("MURS") else 1, note="licence-free / business handhelds")
    for m, _mo, nm in tables.get("rail", ()): add(m, nm, RAIL, 1 if re.search(r"Union|Via", nm, re.I) else 2, note="rail", claim=True)
    for m, _mo, nm in tables.get("transit", ()): add(m, nm, RAIL, 4, note="TTC analog licence", claim=True)
    for m, _mo, nm in tables.get("city", ()): add(m, nm, CITY, 1, note="waste", claim=True)
    # 4. heard here and not in any list above. The sweep's own finds are mostly data bursts and interference, so the bar is high:
    #    confirmed as voice by this receiver, or a licensed channel (it has a licensee's name) seen at least five times.
    extra = []
    for k, e in bank.items():
        try: m = float(e.get("mhz", k))
        except (TypeError, ValueError): continue
        nm = str(e.get("name") or ""); named = bool(nm) and not re.match(r"^[\d.]+", nm)
        if e.get("ver") in ("DATA", "DIGITAL", "QUIET") or (e.get("cat") or "") == "Digital" or 88 <= m < 108 or not tunable(m): continue
        if not (e.get("ver") == "VOICE" or (named and (e.get("times") or 0) >= 5)): continue
        # the sweep reports a signal to the nearest few kHz: anything this close to a channel already listed IS that channel
        if any(abs(c["mhz"] - m) <= (0.0045 if named else 0.0130) for c in out): continue
        extra.append((m, nm, e))
    for m, nm, e in sorted(extra, key=lambda x: x[0]):
        if any(abs(c["mhz"] - m) <= 0.0045 for c in out): continue
        m = round(m / 0.0025) * 0.0025 if m < 174 else round(m / 0.00625) * 0.00625           # onto the channel raster (2.5 kHz at VHF, 6.25 kHz at UHF)
        add(m, nm if not re.match(r"^[\d.]+", nm) else "", AIR if m < AM_BELOW else HEARD, 9, note="confirmed voice" if e.get("ver") == "VOICE" else "licensed channel, seen here")

    # what this receiver has heard on each (the sweep files a signal up to 2.5 kHz off the channel)
    stat = []
    for k, e in bank.items():
        try: stat.append((float(e.get("mhz", k)), e))
        except (TypeError, ValueError): pass
    stat.sort(key=lambda x: x[0]); fs = [s[0] for s in stat]
    import bisect
    for c in out:
        i = bisect.bisect_left(fs, c["mhz"] - 0.0031); n = 0; last = 0; voice = False; tone = ""
        while i < len(fs) and fs[i] <= c["mhz"] + 0.0031:
            e = stat[i][1]; i += 1
            if e.get("ver") in ("DATA", "DIGITAL"): continue
            n += int(e.get("times") or 0); last = max(last, e.get("last_heard") or 0); voice = voice or e.get("ver") == "VOICE"; tone = tone or (e.get("tone") or "")
        c["heard"] = n; c["last"] = last; c["voice"] = voice; c["heard_tone"] = tone
    # names, modes, transmit
    for c in out:
        c["name"] = short_name(c["full"], c["bank"], c["mhz"], c["ref"], c["group"])
        c["mode"] = "AM" if c["mhz"] < AM_BELOW else ("FM" if c["bank"] in (HAM2, HAM70, MARINE) else "NFM")     # ham and marine are 25 kHz channels; the rest are narrow
        if c["bank"] not in TX_BANKS: c["duplex"] = "off"; c["offset"] = 0.0; c["tone"] = ""
        c["car"] = in_car(c["mhz"])
    out.sort(key=lambda c: (c["bank"], c["sect"], _chan_no(c), c["seq"]))
    _number(out)
    return out

def _chan_no(c):
    """FRS / GMRS go in channel order whatever order the lists had them in"""
    m = re.match(r"^(?:FRS|GMRS) (\d+)$", c["full"]) if c["bank"] == FRS else None
    return int(m.group(1)) if m else 0

# ---------------------------------------------------------------- memory numbers that never move
def assign(chans, layout):
    """Give every channel its memory number. layout = {"slots": {"146.9850": 105, ...}} from the last export; returns the new layout.
    A channel that had a number keeps it. A new one takes the lowest number in its bank that nothing has ever had; only when a bank
    has no untouched number left is the number of a channel that is gone used again. A full bank drops what does not fit (reported)."""
    old = {k: int(v) for k, v in (layout or {}).get("slots", {}).items()}; used = {}
    for c in chans:
        s = old.get(_key(c["mhz"]))
        if s and (s - 1) // PER_BANK == c["bank"] and s not in used: c["loc"] = s; used[s] = c
        else: c["loc"] = None
    stale = {v for k, v in old.items() if v not in used}
    for c in chans:
        if c["loc"] is not None: continue
        rng = range(c["bank"] * PER_BANK + 1, (c["bank"] + 1) * PER_BANK + 1)
        s = next((n for n in rng if n not in used and n not in stale), None)
        if s is None: s = next((n for n in rng if n not in used), None)
        if s is not None: c["loc"] = s; used[s] = c; stale.discard(s)
    slots = dict(old)
    for k in [k for k, v in slots.items() if v in used and _key(used[v]["mhz"]) != k]: del slots[k]      # a number that was handed on
    for s, c in used.items(): slots[_key(c["mhz"])] = s
    return {"version": 1, "radio": RADIO, "slots": dict(sorted(slots.items(), key=lambda kv: kv[1]))}

# ---------------------------------------------------------------- the files
def _comment(c, car):
    full = c["full"] or ("not identified" if c["bank"] in (AIR, HEARD) else "")
    if c["bank"] == AIR and c.get("group"): full = c["group"].split(" (")[0] + " - " + full
    bits = [BANK_TITLE[c["bank"]], full + ((" / " + " / ".join(c["also"][:2])) if c["also"] else "")]
    note = re.sub(r"\s+", " ", c.get("note") or "").strip()
    if note and (c["bank"] in (HAM2, HAM70) or c["sect"] == 9): bits.append(note[:70])
    if c.get("nets"): bits.append("nets: " + c["nets"][:70])
    if c["duplex"] == "off":
        bits.append("listen only")
        pl = [p for p in c["pl"] if p]
        if pl: bits.append("tone " + " / ".join(("DCS " + p[1:]) if p.startswith("D") else ("PL " + p) for p in pl) + " (not set: open squelch)")
    if car and c.get("car_note"): bits.append(c["car_note"])
    if c.get("heard"): bits.append(f"heard here {c['heard']}x" + (", last " + dt.datetime.fromtimestamp(c["last"]).strftime("%b %d") if c.get("last") else "") + (", voice confirmed" if c.get("voice") else ""))
    return " | ".join(b for b in bits if b)[:200]

def rows(chans, car=False):
    out = []
    for c in sorted((c for c in chans if c.get("loc") and (c["car"] or not car)), key=lambda c: c["loc"]):
        dup, off, tone = c["duplex"], c["offset"], c["tone"]; c["car_note"] = ""
        if car and dup != "off":
            tx = c["mhz"] + (off if dup == "+" else -off if dup == "-" else 0.0)
            if not any(lo <= tx <= hi for lo, hi in CAR_TX):                 # the repeater listens where this antenna is not matched
                dup, off, tone = "off", 0.0, ""; c["car_note"] = "listen only on the car antenna (the repeater's input is outside its range)"
        t = tone if dup != "off" else ""
        out.append([c["loc"], c["name"], f"{c['mhz']:.6f}", dup, f"{off:.6f}", "Tone" if t else "", t or "88.5", t or "88.5", "023", "NN", "023", "Tone->Tone",
                    c["mode"], "5.00", c["skip"], "10.0W", _comment(c, car), "", "", "", ""])
    return out

def to_csv(chans, car=False):
    f = io.StringIO(); w = csv.writer(f, lineterminator="\r\n"); w.writerow(COLS); w.writerows(rows(chans, car))
    return f.getvalue()

def plan(chans, dropped=()):
    """What both files hold, for the page (and anything else that wants to show the radio's memory map)."""
    banks = []
    for b in range(10):
        cs = [c for c in chans if c["bank"] == b and c.get("loc")]
        banks.append({"bank": b, "name": BANKS[b], "title": BANK_TITLE[b], "first": b * PER_BANK + 1, "last": (b + 1) * PER_BANK, "all": len(cs), "car": sum(1 for c in cs if c["car"])})
    return {"radio": RADIO, "car_antenna": CAR_ANTENNA, "car_mhz": [list(r) for r in CAR_RX], "generated": dt.datetime.now().isoformat(timespec="seconds"),
            "total": sum(b["all"] for b in banks), "car_total": sum(b["car"] for b in banks), "banks": banks, "dropped": [f"{c['mhz']:.4f} {c['full']}" for c in dropped],
            "channels": [{"loc": c["loc"], "name": c["name"], "mhz": round(c["mhz"], 5), "mode": c["mode"], "duplex": c["duplex"], "offset": c["offset"], "tone": c["tone"],
                          "bank": c["bank"], "car": c["car"], "full": c["full"], "listen_only": c["duplex"] == "off", "heard": c.get("heard", 0)}
                         for c in sorted((c for c in chans if c.get("loc")), key=lambda c: c["loc"])]}

FILE_ALL = "chirp_bank.csv"; FILE_CAR = "chirp_car.csv"; FILE_LAYOUT = "chirp_layout.json"

def export(folder, hub, reference, tables, bank, is_digital=None):
    """Build both files in `folder`, keep the memory numbers of the last export, return (csv everything, csv car, plan)."""
    chans = channels(hub, reference, tables, bank, is_digital)
    lp = os.path.join(folder, FILE_LAYOUT)
    try: layout = json.load(open(lp, encoding="utf-8"))
    except (OSError, ValueError): layout = {}
    new = assign(chans, layout)
    dropped = [c for c in chans if not c.get("loc")]
    a = to_csv(chans, False); b = to_csv(chans, True)
    for name, data in ((FILE_ALL, a), (FILE_CAR, b)):
        tmp = os.path.join(folder, name + ".tmp")
        with open(tmp, "w", newline="", encoding="utf-8") as f: f.write(data)
        os.replace(tmp, os.path.join(folder, name))
    if new.get("slots") != (layout or {}).get("slots"):
        tmp = lp + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f: json.dump(new, f, indent=0)
        os.replace(tmp, lp)
    return a, b, plan(chans, dropped)
