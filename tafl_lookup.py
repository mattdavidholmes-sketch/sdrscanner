#!/usr/bin/env python3
"""Build licences_gta.csv - who is licensed on each frequency around Mississauga/Toronto - from ISED's public
Spectrum Management System authorization extract (Land Mobile + Aeronautical + Maritime). Run on the Mac:
    python3 tafl_lookup.py            (downloads ~3 files, filters to 50 km of home, writes licences_gta.csv)
The scanner and dashboard then name "business" hits with the licensee (e.g. 463.3125 -> a courier, a hospital...).
Data: https://open.canada.ca/data/en/dataset/508040d7-6fa9-46e4-afbc-aa61f3ca317e (ISED, open licence)."""
import csv, io, math, os, sys, urllib.request, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
def _home():
    try:
        import json; j = json.load(open(os.path.join(HERE, "home_location.json"))); return float(j["lat"]), float(j["lon"])
    except Exception: return 43.6532, -79.3832           # downtown Toronto unless home_location.json says otherwise
HOME_LAT, HOME_LON = _home()
RADIUS_KM = 50.0
FILES = {
    "landmobile": "https://www.ic.gc.ca/engineering/SMS_TAFL_Files/TAFL_LandMobile-LTAF_MobileTerrestre.zip",
    "aero":       "https://www.ic.gc.ca/engineering/SMS_TAFL_Files/TAFL_LTAF_Aero.zip",
    "maritime":   "https://www.ic.gc.ca/engineering/SMS_TAFL_Files/TAFL_LTAF_Maritime.zip",
}
# column indexes (0-based) from tafl_description_ltaf.pdf
# (the real files have one extra leading column, TX/RX, in front of the documented list - measured Nov 2026)
F_TXRX, F_MHZ, F_ANALOG, F_BW, F_EMISSION, F_POWER, F_LOCATION, F_CALL, F_STATYPE, F_PROV, F_LAT, F_LON, F_SERVICE, F_SUB, F_STATUS, F_LICENSEE = 0, 1, 9, 10, 11, 15, 31, 33, 34, 39, 40, 41, 48, 49, 51, 54

def km(lat1, lon1, lat2, lon2):
    p = math.pi / 180; a = 0.5 - math.cos((lat2 - lat1) * p) / 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p)) / 2
    return 12742 * math.asin(math.sqrt(a))

def coord(v):
    """WGS84 field: decimal degrees, or DDMMSS as an integer."""
    v = (v or "").strip()
    if not v: return None
    try: x = float(v)
    except ValueError: return None
    if abs(x) > 180:                                   # DDMMSS
        s = abs(int(round(x))); d = s // 10000; m = (s // 100) % 100; sec = s % 100
        x = math.copysign(d + m / 60 + sec / 3600, x)
    return x

DEFAULT_IDX = {"hdr": False, "mhz": F_MHZ, "analog": F_ANALOG, "bw": F_BW, "emis": F_EMISSION, "pwr": F_POWER, "loc": F_LOCATION, "call": F_CALL,
               "stype": F_STATYPE, "prov": F_PROV, "lat": F_LAT, "lon": F_LON, "svc": F_SERVICE, "sub": F_SUB, "status": F_STATUS, "lic": F_LICENSEE}
HEADER_KEYS = {"mhz": ("frequency",), "analog": ("analog",), "bw": ("bandwidth",), "emis": ("emission",), "pwr": ("transmitter power",), "loc": ("station location",),
               "call": ("call sign",), "stype": ("type of station",), "prov": ("province",), "lat": ("latitude",), "lon": ("longitude",), "svc": ("service",), "sub": ("subservice",),
               "status": ("authorization status",), "lic": ("licensee name",)}
def columns(first_row):
    """If the first row is a header, map columns by name; otherwise use the documented fixed positions."""
    low = [c.strip().lower() for c in first_row]
    if not any("frequen" in c for c in low): return dict(DEFAULT_IDX)      # data row, no header
    idx = dict(DEFAULT_IDX); idx["hdr"] = True
    for key, needles in HEADER_KEYS.items():
        for i, c in enumerate(low):
            if any(n in c for n in needles) and not (key == "svc" and ("sub" in c or "regulatory" in c)):
                idx[key] = i; break
    return idx

def fetch(url, dest):
    if os.path.exists(dest) and os.path.getsize(dest) > 1000:
        print(f"  using cached {os.path.basename(dest)}"); return
    print(f"  downloading {url}")
    # macOS python ships without the CA bundle that ic.gc.ca's chain needs; curl uses the system trust store (verified)
    import subprocess
    rc = subprocess.call(["curl", "-L", "--fail", "--progress-bar", "-A", "Mozilla/5.0 (SDR Scanner licence lookup)", "-o", dest, url])
    if rc != 0 or not os.path.exists(dest) or os.path.getsize(dest) < 1000:
        raise RuntimeError(f"curl exit {rc}")
    print(f"  {os.path.getsize(dest) / 1e6:.1f} MB")

def main():
    cache = os.path.join(HERE, "models", "tafl"); os.makedirs(cache, exist_ok=True)
    rows = []; seen = set()
    for key, url in FILES.items():
        dest = os.path.join(cache, key + ".zip")
        try: fetch(url, dest)
        except Exception as e: print(f"  {key}: download failed - {e}"); continue
        with zipfile.ZipFile(dest) as z:
            for name in z.namelist():
                if not name.lower().endswith((".csv", ".txt")): continue
                print(f"  reading {name}")
                with z.open(name) as fh:
                    text = io.TextIOWrapper(fh, encoding="utf-8-sig", errors="replace", newline="")
                    idx = None
                    for r in csv.reader(text):
                        if idx is None:
                            idx = columns(r)                                  # header row (if any) decides the column map
                            if idx["hdr"]: continue
                        if len(r) <= max(idx.values()): continue
                        if r[F_TXRX].strip().upper() == "RX": continue      # keep what is transmitted on air
                        try: mhz = float(r[idx["mhz"]])
                        except ValueError: continue                          # junk row
                        if not (25.0 <= mhz <= 1300.0): continue
                        lat = coord(r[idx["lat"]]); lon = coord(r[idx["lon"]])
                        if lat is None or lon is None: continue
                        if lon > 0: lon = -lon                               # some rows store west longitude positive
                        d = km(HOME_LAT, HOME_LON, lat, lon)
                        if d > RADIUS_KM: continue
                        lic = r[idx["lic"]].strip(); loc = r[idx["loc"]].strip().title()
                        k = (round(mhz, 4), lic, loc)
                        if k in seen: continue
                        seen.add(k)
                        g = lambda k: r[idx[k]].strip() if idx.get(k) is not None and idx[k] < len(r) else ""
                        rows.append({"mhz": f"{mhz:.4f}", "licensee": lic, "location": loc, "km": f"{d:.0f}", "callsign": g("call"),
                                     "digital": g("analog"), "bw_khz": g("bw"), "emission": g("emis"), "power_w": g("pwr"),
                                     "station_type": g("stype"), "service": g("svc"), "subservice": g("sub"), "status": g("status"), "source": key})
    rows.sort(key=lambda x: (float(x["mhz"]), float(x["km"])))
    out = os.path.join(HERE, "licences_gta.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["mhz"]); w.writeheader(); w.writerows(rows)
    print(f"\nlicences_gta.csv: {len(rows)} licensed frequencies within {RADIUS_KM:.0f} km")
    if "--show" in sys.argv:
        for x in rows[:40]: print(x["mhz"], x["licensee"], "-", x["location"], x["km"], "km")

if __name__ == "__main__":
    main()
