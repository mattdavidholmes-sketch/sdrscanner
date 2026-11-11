#!/usr/bin/env python3
"""
SDR Scanner for Baofeng UV-32
-----------------------------
Sweeps an RTL-SDR across everything the UV-32 can receive, watches for hours,
finds every active frequency, ranks by signal strength and how often it is busy,
and writes:
  results.csv      - every hit, ranked
  chirp_uv32.csv   - ready to import into CHIRP (UV-21ProGPS driver) or walkieTalkieTool
  report.html      - phone / iPad / Mac viewable report

Usage:
  python3 sdr_scan.py                     # full UV-32 range, 5 hour watch
  python3 sdr_scan.py --hours 0.5         # quick half-hour look
  python3 sdr_scan.py --bands ham         # 2m + 70cm + GMRS/FRS only
  python3 sdr_scan.py --location Muskoka  # label the report
  python3 sdr_scan.py --known my_chirp.csv  # mark hits already in your CHIRP file
  python3 sdr_scan.py listen 146.520      # listen to one frequency through the Mac speakers
  python3 sdr_scan.py report scan_2026-10-29_0100   # rebuild report from a finished scan folder
"""
import argparse, csv, datetime as dt, math, os, re, shutil, signal, statistics, subprocess, sys, time
from collections import defaultdict

# ---------------------------------------------------------------- band plan
# (name, start MHz, stop MHz, mode, channel raster kHz)
BANDS = {
    "air":  ("Airband",        108.000, 137.000, "AM",  25.0),
    "vhf":  ("VHF 137-174",    137.000, 174.000, "NFM",  2.5),
    "uhf":  ("UHF 400-520",    400.000, 520.000, "NFM",  2.5),
}
BAND_SETS = {
    "all":  ["uhf", "vhf", "air", "ps700"],       # (v198) 851-870 MHz left out: three weeks of sweeps found 99 signals there and every one is digital
                                                 # trunking (Peel Region's P25 system among them) - nothing a sweep can turn into something to listen to.
                                                 # The band itself (ps800) is still defined and can be swept on its own.
    "ham":  ["ham2m", "ham70", "gmrs"],
    "vhf":  ["vhf"], "uhf": ["uhf"], "air": ["air"],
}
BAND_SETS["toronto"] = ["yyz", "rpt2m_lo", "rpt2m_hi", "rpt70"]
BAND_SETS["airfrs"]  = ["yyz", "gmrs"]
BAND_SETS["nets"]    = ["rpt2m_lo", "rpt2m_hi", "rpt70", "gmrs"]
BANDS["yyz"]      = ("YYZ arrivals/tower", 118.000, 135.000, "AM",  25.0)
BANDS["rpt2m_lo"] = ("2m repeaters A",     145.100, 145.500, "NFM",  2.5)
BANDS["rpt2m_hi"] = ("2m repeaters B",     146.600, 147.400, "NFM",  2.5)
BANDS["rpt70"]    = ("70cm repeaters",     442.000, 445.000, "NFM",  2.5)
# Whole RTL-SDR range, in slices (what the dongle can hear, not just the UV-32)
BAND_SETS["full"] = ["lowvhf", "air", "vhf", "band3", "milair", "uhf", "uhf7", "uhf8", "uhf9", "l1", "l2"]
BANDS["lowvhf"] = ("Low VHF 24-88",     24.000,  88.000, "NFM",  5.0)
BANDS["fmbc"]   = ("FM broadcast",      88.000, 108.000, "WFM", 100.0)
BANDS["band3"]  = ("174-225 / 1.25m",  174.000, 225.000, "NFM",  5.0)
BANDS["milair"] = ("Mil air 225-400",  225.000, 400.000, "AM",  25.0)
BANDS["uhf7"]   = ("520-700",          520.000, 700.000, "NFM",  6.25)
BANDS["uhf8"]   = ("700-806 public",   700.000, 806.000, "NFM",  6.25)
BANDS["uhf9"]   = ("806-960",          806.000, 960.000, "NFM",  6.25)
BANDS["l1"]     = ("960-1300 ADS-B/23cm", 960.000, 1300.000, "NFM", 25.0)
BANDS["l2"]     = ("1300-1766",       1300.000, 1766.000, "NFM", 25.0)
# What the UV-32 itself can tune (receive). Anything outside is SDR-only.
UV32_RX = [(108.0, 136.0), (136.0, 174.0), (200.0, 260.0), (350.0, 390.0), (400.0, 520.0)]
def uv32_can_hear(mhz): return any(lo <= mhz <= hi for lo, hi in UV32_RX)
BANDS["ham2m"] = ("2m Ham",       144.000, 148.000, "NFM", 2.5)
BANDS["ham70"] = ("70cm Ham",     430.000, 450.000, "NFM", 2.5)
BANDS["gmrs"]  = ("FRS/GMRS",     462.000, 468.000, "NFM", 2.5)
BANDS["marine"] = ("Marine VHF",   156.000, 162.600, "NFM", 2.5)
BANDS["murs"]   = ("MURS",         151.800, 154.700, "NFM", 2.5)
BANDS["rail"]   = ("Railway",      159.800, 161.600, "NFM", 2.5)
BANDS["bizv"]   = ("VHF business", 150.800, 174.000, "NFM", 2.5)
BANDS["bizu"]   = ("UHF business", 450.000, 470.000, "NFM", 2.5)
BANDS["retv"]   = ("Store radios VHF", 151.500, 154.700, "NFM", 2.5)
BANDS["retu"]   = ("Store radios UHF", 461.000, 469.600, "NFM", 2.5)
BANDS["ps700"]  = ("700 MHz public safety", 764.000, 776.000, "NFM", 6.25)      # Toronto P25 Phase II (fire / paramedics) lives here
BANDS["ps800"]  = ("800 MHz public safety", 851.000, 870.000, "NFM", 6.25)
# dashboard category -> band set (click a chip = focused scan of just that range)
CATEGORY_BANDS = {
    "Ham repeater": ["rpt2m_lo", "rpt2m_hi", "rpt70"],
    "Ham":          ["ham2m", "ham70"],
    "Airport":      ["yyz"],
    "FRS/GMRS":     ["gmrs"],
    "Marine":       ["marine"],
    "MURS":         ["murs"],
    "Railway":      ["rail"],
    "Business":     ["bizv", "bizu"],
    "Retail":       ["retv", "retu", "gmrs"],
    "Digital":      ["ps700", "uhf"],
    "City services": ["vhf", "uhf"],
    "Other":        ["vhf", "uhf"],
    "All":          ["uhf", "vhf", "air", "ps700"],
}
for _k, _v in CATEGORY_BANDS.items(): BAND_SETS.setdefault(_k.lower().replace("/", "").replace(" ", "_"), _v)

# ---------------------------------------------------------------- known services (Ontario / North America)
# (lo, hi, label, duplex, offset MHz, mode override)
SERVICES = [
    (26.965, 27.405, "CB",        "", 0, "AM"),
    (28.000, 29.700, "10M HAM",   "", 0, None),
    (30.000, 50.000, "LOW BAND",  "", 0, None),
    (50.000, 54.000, "6M HAM",    "", 0, None),
    (54.000, 88.000, "VHF TV",    "", 0, None),
    (88.000, 108.000, "FM RADIO", "", 0, "WFM"),
    (174.000, 216.000, "TV/DAB",  "", 0, None),
    (222.000, 225.000, "1.25M",   "", 0, None),
    (225.000, 400.000, "MIL AIR", "", 0, "AM"),
    (406.000, 406.100, "EPIRB",   "", 0, None),
    (520.000, 698.000, "UHF TV",  "", 0, None),
    (698.000, 758.000, "LTE 700", "", 0, None),
    (758.000, 775.000, "PS 700",  "", 0, None),
    (788.000, 806.000, "PS 700",  "", 0, None),
    (806.000, 824.000, "PS 800",  "", 0, None),
    (824.000, 849.000, "CELL",    "", 0, None),
    (851.000, 869.000, "PS 800",  "", 0, None),
    (869.000, 894.000, "CELL",    "", 0, None),
    (902.000, 928.000, "33CM/ISM","", 0, None),
    (929.000, 932.000, "PAGING",  "", 0, None),
    (960.000, 1215.000, "DME/ADSB","", 0, None),
    (1240.000, 1300.000, "23CM HAM","", 0, None),
    (1525.000, 1560.000, "SATCOM", "", 0, None),
    (1575.000, 1576.000, "GPS L1", "", 0, None),
    (118.000, 137.000, "AIR",     "", 0, "AM"),
    (121.500, 121.500, "AIR GUARD","", 0, "AM"),
    (108.000, 118.000, "NAV",     "", 0, "AM"),
    (144.000, 144.100, "2M CW",   "", 0, None),
    (144.200, 144.275, "2M SSB",  "", 0, None),
    (145.100, 145.500, "2M RPT",  "-", 0.600, None),
    (145.800, 146.000, "SAT/ISS", "", 0, None),
    (146.400, 146.580, "2M SMPLX","", 0, None),
    (146.610, 146.985, "2M RPT",  "-", 0.600, None),
    (147.000, 147.390, "2M RPT",  "+", 0.600, None),
    (147.420, 147.570, "2M SMPLX","", 0, None),
    (151.820, 151.940, "MURS",    "", 0, None),
    (154.570, 154.600, "MURS",    "", 0, None),
    (152.000, 153.000, "PAGING",  "", 0, None),
    (156.000, 157.450, "MARINE",  "", 0, None),
    (160.200, 161.600, "RAILWAY", "", 0, None),
    (161.600, 162.025, "MARINE",  "", 0, None),
    (162.400, 162.550, "WX(OFF)", "", 0, None),
    (148.000, 174.000, "VHF BIZ", "", 0, None),
    (420.000, 430.000, "70CM",    "", 0, None),
    (430.000, 440.000, "70CM",    "", 0, None),
    (442.000, 445.000, "70CM RPT","+", 5.000, None),
    (446.000, 446.200, "70CM SMPX","", 0, None),
    (447.000, 450.000, "70CM RPT","-", 5.000, None),
    (462.550, 462.725, "GMRS",    "", 0, None),
    (462.5625, 462.7125, "FRS/GMRS","", 0, None),
    (467.550, 467.725, "GMRS IN", "", 0, None),
    (467.5625, 467.7125, "FRS",   "", 0, None),
    (450.000, 470.000, "UHF BIZ", "", 0, None),
    (470.000, 520.000, "UHF T",   "", 0, None),
]
FRS = {462.5625:"FRS1",462.5875:"FRS2",462.6125:"FRS3",462.6375:"FRS4",462.6625:"FRS5",
       462.6875:"FRS6",462.7125:"FRS7",467.5625:"FRS8",467.5875:"FRS9",467.6125:"FRS10",
       467.6375:"FRS11",467.6625:"FRS12",467.6875:"FRS13",467.7125:"FRS14",462.5500:"GMRS15",
       462.5750:"GMRS16",462.6000:"GMRS17",462.6250:"GMRS18",462.6500:"GMRS19",462.6750:"GMRS20",
       462.7000:"GMRS21",462.7250:"GMRS22",
       146.520:"2M CALL", 446.000:"70CM CALL", 121.500:"AIR GUARD", 156.800:"MARINE16",
       145.800:"ISS VOICE", 151.820:"MURS1",151.880:"MURS2",151.940:"MURS3",154.570:"MURS4",154.600:"MURS5"}

# Known Toronto-area stations: MHz -> (name, CTCSS tone or "", duplex, offset)
# Repeaters per RepeaterBook (Toronto + Mississauga, analog FM). Pearson per SkyVector / NAV CANADA.
REFERENCE = {
    120.825: ("YYZ ATIS", "", "", 0),   121.300: ("YYZ CLNC", "", "", 0),
    119.100: ("YYZ GND", "", "", 0),    121.650: ("YYZ GND N", "", "", 0),  121.900: ("YYZ GND S", "", "", 0),
    118.350: ("YYZ TWR S", "", "", 0),  118.700: ("YYZ TWR N", "", "", 0),      # Tower South / Tower North (published names)
    124.475: ("YYZ ARR 1", "", "", 0),  125.400: ("YYZ ARR 2", "", "", 0),
    132.800: ("YYZ ARR 3", "", "", 0),  134.175: ("YYZ ARR 4", "", "", 0),
    127.575: ("YYZ DEP 1", "", "", 0),  128.800: ("YYZ DEP 2", "", "", 0),
    122.825: ("YYZ APRON", "", "", 0),  122.275: ("YYZ APRN N", "", "", 0), 122.075: ("YYZ APRN S", "", "", 0),
    131.175: ("YYZ PAD", "", "", 0),    119.300: ("YYZ VFR", "", "", 0),    133.400: ("TOR VFR", "", "", 0),
    145.110: ("VE3WOO", "82.5", "-", 0.6),   145.130: ("VA3GTU 2M", "103.5", "-", 0.6),
    145.230: ("VE3OBN", "103.5", "-", 0.6),  145.430: ("VE3MIS", "103.5", "-", 0.6),
    145.450: ("VE3YPL", "85.4", "-", 0.6),   146.805: ("VE3SKV", "110.9", "-", 0.6),
    146.940: ("VE3TOR", "103.5", "-", 0.6),  146.985: ("VE3SKY", "103.5", "-", 0.6),
    147.270: ("VE3TNC", "103.5", "+", 0.6),
    442.000: ("VE3BRA", "103.5", "+", 5.0),  442.375: ("VA3GTU 70", "103.5", "+", 5.0),
    442.650: ("VA3GTU B", "100.0", "+", 5.0), 442.750: ("VE3OBN 70", "103.5", "+", 5.0),
    442.975: ("VA3GTU C", "103.5", "+", 5.0), 443.025: ("VA3SCR", "103.5", "+", 5.0),
    443.050: ("VE3YYZ", "156.7", "+", 5.0),  443.100: ("VE3SKI", "", "+", 5.0),
    443.500: ("VE3NIB", "103.5", "+", 5.0),  443.650: ("VE3NOR", "103.5", "+", 5.0),
    443.8375: ("VA3PCS", "103.5", "+", 5.0), 443.900: ("VE3WOO 70", "127.3", "+", 5.0),
    444.1625: ("VA3YYZ", "103.5", "+", 5.0), 444.250: ("VA3PMO", "103.5", "+", 5.0),
    444.450: ("VA3BMI", "103.5", "+", 5.0),  444.475: ("VE3URU", "103.5", "+", 5.0),
    444.700: ("VE3RAK", "103.5", "+", 5.0),  444.775: ("VA3ATL", "110.9", "+", 5.0),
    444.800: ("VA3RCI", "100.0", "+", 5.0),  444.850: ("VA3GTU D", "103.5", "+", 5.0),
}
# Rail - analog NFM road/yard channels around Toronto (RadioReference CP Rail (ON) wiki, CN subcat 59688, VIA subcat 49883; Nov 2026).
# GO / Metrolinx rail traffic control on these tracks is NXDN digital (GO13 160.350 / GO14 161.4375 Lakeshore West) - needs a digital decoder.
RAIL = {
    161.415: ("CN1 road / standby", "", "", 0), 160.935: ("CN3 RTC Oakville Sub (Union)", "", "", 0), 161.025: ("CN8 RTC Oakville Sub", "", "", 0),
    160.215: ("CN MOW 1", "", "", 0), 160.455: ("CN MOW 2", "", "", 0), 161.205: ("CN2 RTC Kingston Sub", "", "", 0), 160.665: ("CN4 RTC Weston/York/Halton", "", "", 0),
    161.475: ("CP1 Galt Sub road", "", "", 0), 161.325: ("CP5 Galt Sub", "", "", 0), 161.115: ("CP4 North Toronto / Mactier", "", "", 0),
    161.535: ("CP7 / RTC call-in", "", "", 0), 161.580: ("CP RTC call-in 2", "", "", 0), 160.725: ("CP78 Canpa Sub (Oakville RTC)", "", "", 0),
    158.850: ("CP Toronto Yard hump 1", "", "", 0), 159.930: ("CP Toronto Yard hump 2", "", "", 0), 160.050: ("CP Toronto Yard hump 3", "", "", 0),
    463.9625: ("VIA TMC (PL 103.5)", "", "", 0), 461.2125: ("TTR Union stationmaster (PL 107.2)", "", "", 0), 451.725: ("VIA Union porters", "", "", 0),
}
for _f, _v in RAIL.items(): REFERENCE.setdefault(_f, _v)
# GO / Metrolinx rail traffic control (NXDN 4800 digital, RAN 0): GO13 160.350 crew / GO14 161.4375 RTC (Lakeshore West, Willowbrook),
# GO11 160.950 / GO12 160.650 (USRC West). Digital: not voice for the sweep, and SDRTrunk 0.6.1 has no NXDN decoder (the nightly build does).
RAIL_NXDN = {160.350: "GO13 Lakeshore W crew", 161.4375: "GO14 Lakeshore W RTC (Willowbrook)", 160.950: "GO11 USRC West crew", 160.650: "GO12 USRC West RTC"}

# Outer-GTA net repeaters (RepeaterBook / club sites) - merged in from build_nets.py's table
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from build_nets import REPEATERS as _NETR, SIMPLEX as _NETS
    for _k, (_f, _d, _o, _t, _w) in _NETR.items():
        REFERENCE.setdefault(round(_f, 4), (_k[:10], _t, _d, _o))
    for _k, (_f, _w) in _NETS.items():
        REFERENCE.setdefault(round(_f, 4), (_k[:10], "", "", 0))
    from build_hub import RETAIL as _RET, CITY as _CITY
    for _n, _f in _RET:
        REFERENCE.setdefault(round(_f, 4), (_n[:24], "", "", 0))
    for _n, _f in _CITY:
        REFERENCE.setdefault(round(_f, 4), (_n[:30], "", "", 0))
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
_LIC = None
def licences():
    """licences_gta.csv (built by tafl_lookup.py from ISED's public licence database): {mhz: [rows sorted by distance]}"""
    global _LIC
    if _LIC is None:
        _LIC = {}
        path = os.path.join(HERE, "licences_gta.csv")
        if os.path.exists(path):
            try:
                with open(path, newline="", encoding="utf-8") as f:
                    for r in csv.DictReader(f):
                        try: _LIC.setdefault(round(float(r["mhz"]), 4), []).append(r)
                        except (ValueError, KeyError): pass
                for v in _LIC.values(): v.sort(key=lambda r: float(r.get("km") or 99))
            except OSError: pass
    return _LIC

def licence_for(mhz, tol=0.0026):
    """All licence rows within tol of mhz, nearest first."""
    out = []
    for f, rows in list(licences().items()):
        if abs(f - mhz) <= tol: out.extend(rows)
    out.sort(key=lambda r: float(r.get("km") or 99))
    return out

def licence_name(mhz):
    rows = licence_for(mhz)
    if not rows: return ""
    names = []
    for r in rows:
        n = r["licensee"].strip()
        if n and n.upper() not in [x.upper() for x in names]: names.append(n)
    first = names[0]
    for junk in (" LIMITED", " LTD.", " LTD", " INC.", " INC", " CORPORATION", " CORP.", " CORP", " COMPANY", " CO."):
        if first.upper().endswith(junk): first = first[: -len(junk)]
    return first.title() + (f" +{len(names)-1}" if len(names) > 1 else "")

def reference_for(mhz):
    for f, v in REFERENCE.items():
        if abs(f - mhz) <= 0.0026: return v
    return None

def service_for(mhz):
    for lo, hi, label, dup, off, mode in SERVICES:
        if lo - 0.0001 <= mhz <= hi + 0.0001:
            return label, dup, off, mode
    return "", "", 0, None

# RTL2832U / R820T spurs sit at multiples of 28.8 MHz
def is_spur(mhz):
    k = round(mhz / 28.8)
    return abs(mhz - k * 28.8) < 0.012

# ---------------------------------------------------------------- rtl_power sweep
HERE_DIR = os.path.dirname(os.path.abspath(__file__))
FASTSCAN_STATE = {"fails": 0, "engine": ""}        # two early deaths of fastscan in one scan -> back to rtl_power for the rest of it
def fastscan_available():
    """fastscan needs numpy + scipy + rtl_tcp (the channelizer's requirements); FASTSCAN=0 in the environment or a fastscan_off file forces rtl_power"""
    if os.environ.get("FASTSCAN", "1") == "0" or os.path.exists(os.path.join(HERE_DIR, "fastscan_off")): return False
    try:
        import numpy, scipy  # noqa
        return bool(shutil.which("rtl_tcp")) and os.path.exists(os.path.join(HERE_DIR, "fastscan.py"))
    except Exception: return False
def run_rtl_power(band_key, seconds, gain, outdir, bin_khz=None):
    """One slice of sweeping on one band. Output is appended so slices across the night stack up."""
    name, lo, hi, mode, raster = BANDS[band_key]
    bin_hz = int((bin_khz or max(5.0, raster / 2)) * 1000)
    out = os.path.join(outdir, f"power_{band_key}.csv")
    # -i integration interval, -e slice length, -c crop edges (kills the DC spike per hop); no filename = stdout
    cmd = ["rtl_power", "-f", f"{int(lo*1e6)}:{int(hi*1e6)}:{bin_hz}",
           "-i", "10", "-e", f"{int(seconds)}s", "-g", str(gain), "-c", "25%"]
    engine = "rtl_power"
    if fastscan_available() and FASTSCAN_STATE["fails"] < 2:                                                  # (5 Nov) fastscan: same CSV, full-rate FFT per 2 MHz window, a row a second
        cmd = [sys.executable, os.path.join(HERE_DIR, "fastscan.py"), "-f", f"{int(lo*1e6)}:{int(hi*1e6)}:{bin_hz}", "-e", f"{int(seconds)}s", "-g", str(gain)]
        engine = "fastscan"
    FASTSCAN_STATE["engine"] = engine
    print(f"  {dt.datetime.now():%H:%M}  {name:<22} {seconds/60:.0f} min  [{engine}]", flush=True)
    return subprocess.Popen(cmd, stdout=open(out, "a"), stderr=open(os.path.join(outdir, f"rtl_{band_key}.log"), "a")), out

def trim_bad_rows(path):
    """Drop rtl_power rows that look like a dead dongle (all values identical / nan / absurd)."""
    if not os.path.exists(path): return
    keep = []; dropped = 0
    with open(path, newline="") as f:
        for row in csv.reader(f):
            vals = row[6:]
            try:
                fv = [float(v) for v in vals[:50]]
                if not fv or any(math.isnan(x) or math.isinf(x) for x in fv) or max(fv) - min(fv) < 0.01 or max(fv) > 50:
                    dropped += 1; continue
            except ValueError:
                dropped += 1; continue
            keep.append(row)
    if dropped:
        with open(path, "w", newline="") as f: csv.writer(f).writerows(keep)
        print(f"  trimmed {dropped} bad rows from {os.path.basename(path)}", flush=True)

HOP_CENTERS = {}   # path -> set of hop-centre bin frequencies (rtl_power leaves a DC spike at each)
def parse_power(path):
    """Returns dict-like bin_hz -> list of (hour, dB) readings over time. (5 Nov) power_fast.py: incremental + numpy when available."""
    try:
        import power_fast
        pd = power_fast.parse_power(path); HOP_CENTERS[path] = pd.centers; return pd
    except ImportError: pass
    return parse_power_slow(path)
def parse_power_slow(path):
    bins = defaultdict(list); centers = set()
    with open(path, newline="") as f:
        for row in csv.reader(f):
            if len(row) < 7: continue
            try:
                lo, hi, step = float(row[2]), float(row[3]), float(row[4])
                tt = row[1].strip(); hour = int(tt[:2]) * 60 + int(tt[3:5])      # minute of day
            except ValueError: continue
            vals = row[6:]
            centers.add(int(lo + step * (len(vals) // 2)))
            for i, v in enumerate(vals):
                try: d = float(v)
                except ValueError: continue
                if math.isnan(d) or math.isinf(d): continue
                bins[int(lo + i * step)].append((hour, d))
    HOP_CENTERS[path] = centers
    return bins

def near_hop_center(hz, step_hz, centers):
    return any(abs(hz - c) <= 2 * step_hz for c in centers)

def snap(mhz, raster_khz):
    """Airband: 25 kHz. VHF/UHF: prefer the 5 kHz grid, then 6.25 kHz, then 2.5 kHz."""
    if raster_khz >= 100:   # FM broadcast: odd 100 kHz steps (88.1, 88.3 ...)
        return round((mhz - 0.1) / 0.2) * 0.2 + 0.1
    if raster_khz >= 25:
        return round(mhz / 0.025) * 0.025
    for grid, tol in ((0.005, 0.002), (0.00625, 0.002), (0.0025, 1)):
        s = round(mhz / grid) * grid
        if abs(s - mhz) <= tol: return s
    return mhz

def find_hits(bins, mode, raster_khz, thresh_db, min_pct, centers=None):
    if not bins: return []
    if centers is None: centers = set().union(*HOP_CENTERS.values()) if HOP_CENTERS else set()
    if hasattr(bins, "stats"):
        freqs, mx, med = bins.stats(); mx = mx.tolist(); med = med.tolist()            # numpy per window (power_fast)
    else:
        freqs = sorted(bins)
        mx  = [max(d for _, d in bins[f]) for f in freqs]
        med = [statistics.median(d for _, d in bins[f]) for f in freqs]
    # rolling noise floor: median of the median, over a 2 MHz window
    # (v150, 5 Nov 13:40) bin spacing = the TYPICAL gap, not the first one: fastscan's windows are not grid-aligned, so two
    # neighbouring bins can be 1 Hz apart -> half = 1,000,000 -> scipy's median filter ran for minutes and the dashboard hung.
    if len(freqs) > 1:
        try:
            import numpy as _np; step = float(_np.median(_np.diff(_np.asarray(freqs, dtype=float))))
        except Exception:
            step = sorted(b - a for a, b in zip(freqs, freqs[1:]))[len(freqs) // 2]
    else: step = 0
    half = max(3, min(1500, int(1_000_000 / step))) if step > 0 else 3
    try:
        import power_fast; floor = power_fast.rolling_median(med, half).tolist()
    except ImportError:
        floor = []
        for i in range(len(freqs)):
            seg = med[max(0, i-half): i+half+1]
            floor.append(statistics.median(seg))
    hits = []
    i = 0
    while i < len(freqs):
        snr = mx[i] - floor[i]
        if snr >= thresh_db and centers and near_hop_center(freqs[i], freqs[1] - freqs[0] if len(freqs) > 1 else 1, centers):
            i += 1; continue                                           # rtl_power DC spike at a hop centre - not a signal
        if snr >= thresh_db:
            # expand the contiguous block above threshold, keep the peak - but stop at a valley 6 dB below the
            # peak so far when the next bin climbs again (two stations / two comb teeth side by side)
            j = i; best = i
            while j < len(freqs) and mx[j] - floor[j] >= thresh_db:
                if mx[j] > mx[best]: best = j
                if j > i and j + 1 < len(freqs) and mx[j] < mx[best] - 6 and mx[j + 1] > mx[j] + 3 and mx[j + 1] - floor[j + 1] >= thresh_db:
                    j += 1; break
                j += 1
            samples = bins[freqs[best]]
            active = sum(1 for _, d in samples if d - floor[best] >= thresh_db)
            pct = 100.0 * active / len(samples)
            by_hour = defaultdict(lambda: [0, 0]); last_min = None; first_min = None
            for mm, d in samples:
                hr = mm // 60
                by_hour[hr][1] += 1
                if d - floor[best] >= thresh_db:
                    by_hour[hr][0] += 1
                    last_min = mm                                     # samples are chronological: latest wins
                    if first_min is None: first_min = mm
            hours = {hr: round(100.0 * a / n) for hr, (a, n) in by_hour.items() if n}
            # activity over the last 30 minutes of sweep data (relative to the newest sample, so a paused scan does not decay)
            newest = samples[-1][0] if samples else 0
            recent = [(mm, d) for mm, d in samples if (newest - mm) % 1440 <= 30]
            recent_pct = round(100.0 * sum(1 for mm, d in recent if d - floor[best] >= thresh_db) / len(recent)) if recent else 0
            if pct >= min_pct:
                # power-weighted centroid across the block (and one bin either side) beats the raw bin grid
                lo_i, hi_i = max(0, i-1), min(len(freqs), j+1)
                wsum = fsum = 0.0
                for k in range(lo_i, hi_i):
                    wgt = 10 ** ((mx[k] - floor[k]) / 10)
                    wsum += wgt; fsum += wgt * freqs[k]
                mhz = (fsum / wsum if wsum else freqs[best]) / 1e6
                snapped = snap(mhz, raster_khz)
                hits.append({
                    "mhz": round(snapped, 4), "raw_mhz": round(mhz, 5),
                    "peak_db": round(mx[best], 1), "floor_db": round(floor[best], 1),
                    "snr_db": round(mx[best] - floor[best], 1),
                    "active_pct": round(pct, 1), "samples": len(samples), "recent_pct": recent_pct,
                    "mode": mode, "spur": is_spur(mhz), "hours": hours,
                    "last_active": f"{last_min // 60:02d}:{last_min % 60:02d}" if last_min is not None else "",
                    "first_active": f"{first_min // 60:02d}:{first_min % 60:02d}" if first_min is not None else "",
                    "width": j - i, "med_snr": round(med[best] - floor[best], 1),
                    # narrow and above the floor in the MEDIAN sweep = on all the time = beacon / data / interference, not voice
                    "carrier": (med[best] - floor[best]) >= 5.0 or (pct >= 98 and len(samples) >= 20),
                })
            i = j
        else:
            i += 1
    # spur combs: 4+ narrow carriers at an even spacing (40-400 kHz) are harmonics of a switching supply / USB hub,
    # not stations (measured Oct 30: a 97 kHz comb across 133.1-135.0 MHz). Flag every member as a spur.
    step_hz = (freqs[1] - freqs[0]) if len(freqs) > 1 else 10000
    narrow = sorted([h for h in hits if h["width"] <= 10], key=lambda h: h["raw_mhz"])
    tol = max(0.75 * step_hz, 3000) / 1e6
    if mode.lower() != "am": narrow = []          # only the airband list suffers from this; UHF always-on carriers are hidden anyway
    for a_i in range(len(narrow)):
        for b_i in range(a_i + 1, len(narrow)):
            sp = narrow[b_i]["raw_mhz"] - narrow[a_i]["raw_mhz"]
            if sp < 0.040: continue
            if sp > 0.400: break
            # walk the harmonics k = 1, 2, 3 ... and stop at the first missing tooth: a real comb has no gaps
            members = [narrow[a_i]]; f0 = narrow[a_i]["raw_mhz"]; k = 1
            while True:
                tooth = [h for h in narrow if abs(h["raw_mhz"] - (f0 + k * sp)) <= tol]
                if not tooth: break
                members.append(tooth[0]); k += 1
                sp = (tooth[0]["raw_mhz"] - f0) / (k - 1)          # refine the spacing as the comb is walked
            if len(members) >= 6:
                for h in members: h["spur"] = True; h["spur_kind"] = f"comb {sp*1000:.0f} kHz"
    if mode.lower() == "am":               # an AM airband voice channel is 1-2 bins wide; 50 kHz+ blocks are interference
        for h in hits:
            if h["width"] >= 5 and not h["spur"]: h["spur"] = True; h["spur_kind"] = f"wideband interference ({h['width']} bins)"
    # merge hits that snapped to the same channel
    merged = {}
    for h in hits:
        k = h["mhz"]
        if k not in merged or h["snr_db"] > merged[k]["snr_db"]:
            merged[k] = h
    return sorted(merged.values(), key=lambda h: -h["snr_db"])

# ---------------------------------------------------------------- outputs
def load_known(path):
    known = {}
    if not path or not os.path.exists(path): return known
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try: known[round(float(row.get("Frequency", "0")), 4)] = row.get("Name", "")
            except ValueError: pass
    return known

def chirp_name(h):
    if h["mhz"] in FRS: return FRS[h["mhz"]][:10]
    f = f"{h['mhz']:.4f}".rstrip("0").rstrip(".")
    tag = {"AIR": "A", "NAV": "N", "2M RPT": "R", "70CM RPT": "R", "MARINE": "M", "RAILWAY": "RR",
           "PAGING": "PG", "MURS": "MU", "GMRS": "G", "FRS/GMRS": "G", "GMRS IN": "G", "FRS": "F",
           "VHF BIZ": "B", "UHF BIZ": "B", "UHF T": "T", "2M SMPLX": "S", "70CM SMPX": "S"}.get(h["service"], "")
    n = f"{f} {tag}".strip() if tag and len(f) + 1 + len(tag) <= 10 else f
    return n[:10].strip()

def score(h):
    # strength matters most, but something that is busy 30% of the time beats a one-off blip
    return h["snr_db"] + 0.6 * h["active_pct"] - (40 if h["spur"] else 0) - (25 if h.get("carrier") else 0)

def load_heard(outdir):
    heard = {}
    tp = os.path.join(outdir, "tones.csv")
    if os.path.exists(tp):
        for line in open(tp, errors="ignore"):
            parts = line.strip().split(",", 2)
            if len(parts) == 3:
                try: heard[round(float(parts[1]), 4)] = parts[2]
                except ValueError: pass
    return heard

def write_outputs(hits, outdir, location, known, started, hours, maxch):
    heard = load_heard(outdir)
    for h in hits:
        label, dup, off, mode_over = service_for(h["mhz"])
        h["service"] = label
        h["duplex"] = dup
        h["offset"] = off
        if mode_over: h["mode"] = mode_over
        h["known_as"] = next((n for f, n in known.items() if abs(f - h["mhz"]) <= 0.0026), "")
        h["uv32"] = uv32_can_hear(h["mhz"])
        h["tone"] = ""
        ref = reference_for(h["mhz"])
        if ref:
            h["name"], h["tone"], h["duplex"], h["offset"] = ref
            if h["mhz"] < 137: h["service"] = "PEARSON"
            elif h["tone"] or h["duplex"]: h["service"] = "REPEATER"
            h["carrier"] = False          # a known repeater / tower busy for a whole slice is a long QSO, not a data carrier
        else:
            h["name"] = chirp_name(h)
            lic = licence_name(h["mhz"])
            if lic: h["name"] = lic[:24]; h["licensee"] = lic
        h["score"] = round(score(h), 1)
        # tones decoded by the dashboard while listening (tones.csv in the scan folder) win over the reference table
        hv = next((v for f, v in heard.items() if abs(f - h["mhz"]) <= 0.0026), "")
        if hv:
            h["heard_tone"] = hv
            m = re.search(r"CTCSS ([\d.]+)", hv)
            if m: h["tone"] = m.group(1)
    hits.sort(key=lambda h: (h["spur"], -h["score"]))
    for i, h in enumerate(hits, 1): h["rank"] = i

    with open(os.path.join(outdir, "results.csv"), "w", newline="") as f:
        cols = ["rank","mhz","name","service","mode","snr_db","peak_db","floor_db","active_pct","samples","duplex","offset","tone","uv32","spur","spur_kind","carrier","med_snr","known_as","raw_mhz","score"]
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(hits)

    # CHIRP generic CSV
    cols = ["Location","Name","Frequency","Duplex","Offset","Tone","rToneFreq","cToneFreq","DtcsCode","DtcsPolarity","RxDtcsCode","CrossMode","Mode","TStep","Skip","Power","Comment","URCALL","RPT1CALL","RPT2CALL","DVCODE"]
    with open(os.path.join(outdir, "chirp_uv32.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(cols)
        loc = 0
        for h in hits:
            if h["spur"] or h.get("carrier") or not h["uv32"]: continue
            if loc >= maxch: break
            comment = f"{location} {started:%Y-%m-%d} SNR {h['snr_db']}dB busy {h['active_pct']}%" + (f" | heard {h['heard_tone']}" if h.get("heard_tone") else "")
            if h["known_as"]: comment += f" | already in file as {h['known_as']}"
            tone_mode = "Tone" if h["tone"] else ""
            w.writerow([loc, h["name"], f"{h['mhz']:.6f}", h["duplex"], f"{h['offset']:.6f}", tone_mode, h["tone"] or "88.5", h["tone"] or "88.5", "023", "NN", "023", "Tone->Tone",
                        h["mode"], "5.00", "", "10.0W", comment, "", "", "", ""])
            loc += 1

    # walkieTalkieTool entry sheet - the fields the iPhone app asks for, top channels first
    with open(os.path.join(outdir, "walkieTalkieTool_sheet.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["Zone", "Channel", "Channel Name", "Rx MHz", "Tx MHz", "CTCSS", "Power", "WN Band"])
        ch = 1
        for h in hits:
            if h["spur"] or h.get("carrier") or not h["uv32"] or ch > maxch: continue
            tx = h["mhz"] + (h["offset"] if h["duplex"] == "+" else -h["offset"] if h["duplex"] == "-" else 0)
            w.writerow([f"SDR {location}"[:10], ch, h["name"], f"{h['mhz']:.4f}", f"{tx:.4f}",
                        h["tone"] or "OFF", "High", "Wide" if h["mode"] == "AM" else "Narrow"])
            ch += 1

    # SDR++ frequency manager bookmarks (Module list > Frequency Manager > Import)
    import json
    sdrpp_mode = {"NFM": 0, "WFM": 1, "AM": 2}
    bw = {"NFM": 12500, "WFM": 200000, "AM": 10000}
    marks = {}
    for h in hits:
        if h["spur"]: continue
        nm = f"{h['name']} {h['mhz']:.4f}"
        marks[nm] = {"bandwidth": bw.get(h["mode"], 12500), "frequency": int(round(h["mhz"] * 1e6)), "mode": sdrpp_mode.get(h["mode"], 0)}
    with open(os.path.join(outdir, "sdrpp_bookmarks.json"), "w") as f:
        json.dump({"bookmarks": marks, "showOnWaterfall": True}, f, indent=2)

    write_report(hits, outdir, location, started, hours)

def hour_strip(hours):
    if not hours: return ""
    hs = sorted(hours)
    if hs[0] < 6 and hs[-1] > 18:   # crossed midnight - evening first, then the small hours
        hs = [h for h in hs if h >= 12] + [h for h in hs if h < 12]
    cells = "".join(f"<i title='{hr:02d}:00 - {hours[hr]}% busy' style='opacity:{max(0.12, hours[hr]/100):.2f}'></i>" for hr in hs)
    return f"<div class='hrs'>{cells}</div><small>{hs[0]:02d}h-{hs[-1]:02d}h</small>"

def write_report(hits, outdir, location, started, hours):
    real = [h for h in hits if not h["spur"]]
    spurs = [h for h in hits if h["spur"]]
    top = real[0] if real else None
    top_txt = f"{top['mhz']:.4f}" if top else "-"
    top_snr = f" - {top['snr_db']} dB" if top else ""
    hi_txt = f"{max(real, key=lambda h: h['mhz'])['mhz']:.4f}" if real else "-"
    def row(h):
        cls = "new" if not h["known_as"] else "known"
        if not h["uv32"]: cls += " sdr"
        bar = min(100, int(h["snr_db"] * 2.5))
        return (f"<tr class='{cls}'><td>{h['rank']}</td><td class='f'>{h['mhz']:.4f}</td><td>{h['name']}</td>"
                f"<td>{h['service'] or '-'}</td><td>{h['mode']}</td>"
                f"<td><div class='bar'><i style='width:{bar}%'></i></div>{h['snr_db']} dB</td>"
                f"<td>{h['active_pct']}%</td><td>{hour_strip(h.get('hours', {}))}</td>"
                f"<td>{(h['duplex'] + ' ' + str(h['offset'])) if h['duplex'] else 'simplex'}{(' / ' + h['tone']) if h['tone'] else ''}{(' <small>heard: ' + h['heard_tone'] + '</small>') if h.get('heard_tone') else ''}</td>"
                f"<td>{'<span class=tag>SDR only</span>' if not h['uv32'] else (h['known_as'] or '<b>NEW</b>')}</td></tr>")
    rows = "\n".join(row(h) for h in real)
    spur_rows = "\n".join(f"<li>{h['mhz']:.4f} MHz ({h['snr_db']} dB)</li>" for h in spurs) or "<li>none</li>"
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SDR Scan - {location}</title>
<style>
:root{{--bg:#0e1116;--card:#171c24;--ink:#e8edf3;--mute:#8b96a5;--acc:#3fb950;--line:#262d38}}
@media (prefers-color-scheme:light){{:root{{--bg:#f5f7fa;--card:#fff;--ink:#101418;--mute:#5c6673;--line:#e1e6ec}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 -apple-system,system-ui,Helvetica,Arial,sans-serif;padding:16px}}
h1{{font-size:1.4rem;margin:0 0 4px}}.sub{{color:var(--mute);margin-bottom:16px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:18px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}}
.card b{{display:block;font-size:1.5rem}}.card span{{color:var(--mute);font-size:.85rem}}
.wrap{{overflow-x:auto;-webkit-overflow-scrolling:touch;border:1px solid var(--line);border-radius:10px;background:var(--card)}}
table{{border-collapse:collapse;width:100%;min-width:760px}}th,td{{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}}
th{{position:sticky;top:0;background:var(--card);font-size:.8rem;color:var(--mute);text-transform:uppercase;letter-spacing:.04em}}
td.f{{font-family:ui-monospace,Menlo,monospace;font-weight:600}}
.bar{{display:inline-block;width:70px;height:7px;background:var(--line);border-radius:4px;vertical-align:middle;margin-right:6px}}.bar i{{display:block;height:100%;background:var(--acc);border-radius:4px}}
tr.known td{{color:var(--mute)}}details{{margin-top:18px;color:var(--mute)}}
.hrs{{display:inline-flex;gap:2px;vertical-align:middle;margin-right:6px}}.hrs i{{width:9px;height:14px;background:var(--acc);border-radius:2px;display:block}}
td small{{color:var(--mute);font-size:.75rem}}
.tag{{font-size:.72rem;padding:2px 7px;border:1px solid var(--line);border-radius:10px;color:var(--mute)}}tr.sdr td.f{{color:var(--mute)}}
.hint{{margin-top:18px;padding:12px;border-left:3px solid var(--acc);background:var(--card);border-radius:0 10px 10px 0}}
</style></head><body>
<h1>SDR Scan - {location}</h1>
<div class="sub">{started:%A %B %d, %Y %H:%M} - watched {hours:g} h - RTL-SDR on the car antenna</div>
<div class="cards">
<div class="card"><b>{len(real)}</b><span>active frequencies</span></div>
<div class="card"><b>{sum(1 for h in real if h['uv32'] and not h['known_as'])}</b><span>new for the UV-32</span></div>
<div class="card"><b>{sum(1 for h in real if not h['uv32'])}</b><span>SDR only (outside UV-32 range)</span></div>
<div class="card"><b>{top_txt}</b><span>strongest signal{top_snr}</span></div>
<div class="card"><b>{hi_txt}</b><span>highest active frequency</span></div>
</div>
<div class="wrap"><table><thead><tr><th>#</th><th>MHz</th><th>Name</th><th>Service</th><th>Mode</th><th>Signal</th><th>Busy</th><th>By hour</th><th>Duplex / tone</th><th>In file</th></tr></thead>
<tbody>{rows}</tbody></table></div>
<div class="hint"><b>Load it:</b> import <code>chirp_uv32.csv</code> in CHIRP (File &gt; Import, UV-21ProGPS driver), or open <code>walkieTalkieTool_sheet.csv</code> on your phone and type the rows into the Freq R/W &amp; Set screen. Known repeaters carry their tone and offset; everything else is left open so you hear it all.</div>
<details><summary>Dongle spurs filtered out ({len(spurs)})</summary><ul>{spur_rows}</ul></details>
</body></html>"""
    with open(os.path.join(outdir, "report.html"), "w") as f: f.write(html)

# ---------------------------------------------------------------- commands
_KILLED = [False]                          # set when the dashboard (or a reset) stops this scan with SIGTERM
def _term(*_): _KILLED[0] = True; raise KeyboardInterrupt
def cmd_scan(a):
    signal.signal(signal.SIGTERM, _term)                                     # pkill from the dashboard: stop the child sweep, write results
    if not shutil.which("rtl_power"):
        sys.exit("rtl_power not found. Run setup.command first (installs rtl-sdr via Homebrew).")
    started = dt.datetime.now()
    outdir = a.out or f"scan_{started:%Y-%m-%d_%H%M}"
    os.makedirs(outdir, exist_ok=True)
    keys = BAND_SETS.get(a.bands, [a.bands])
    # one dongle = one band at a time, so rotate: every band gets a slice of every hour of the night
    slice_s = max(60, a.slice * 60)
    total_s = a.hours * 3600
    rounds = max(1, int(total_s / (slice_s * len(keys))))
    print(f"Scanning {', '.join(BANDS[k][0] for k in keys)}")
    print(f"{a.hours:g} h total - {rounds} rounds of {a.slice:g} min per band. Ctrl-C ends early and still writes results.\n")
    powers = {}
    pause_flag = os.path.join(outdir, "PAUSE")
    try:
        for r in range(rounds):
            print(f"Round {r+1}/{rounds}")
            for k in keys:
                remaining = slice_s; attempts = 0
                while remaining >= 20 and attempts < 4:
                    waited = False
                    while os.path.exists(pause_flag):      # dashboard is using the dongle to play a channel
                        if not waited: print("  paused - dashboard is listening", flush=True); waited = True
                        time.sleep(0.5)
                        try: age = time.time() - os.path.getmtime(pause_flag)
                        except OSError: break
                        if age > 180:                       # the dashboard touches the flag every 20 s while it really holds the dongle:
                            print("  PAUSE flag has had no heartbeat for 3 min (dashboard gone or wedged) - resuming", flush=True)
                            try: os.remove(pause_flag)
                            except OSError: pass
                            break
                    if waited: print(f"  resuming {BANDS[k][0]} ({remaining/60:.0f} min left in this slice)", flush=True)
                    # run the (rest of the) slice with a USB watchdog: if rtl_power loses the dongle it spins forever
                    # logging "failed with -4" - kill it, wait for the USB device to come back, retry (up to 4 times)
                    log = os.path.join(outdir, f"rtl_{k}.log")
                    start_off = os.path.getsize(log) if os.path.exists(log) else 0   # only judge output from THIS attempt
                    t_start = time.time()
                    p, out = run_rtl_power(k, remaining, a.gain, outdir)
                    powers[k] = out
                    bad = False; interrupted = False
                    last_size = os.path.getsize(out) if os.path.exists(out) else 0; last_grow = time.time()
                    try:
                        while p.poll() is None:
                            time.sleep(0.25)                                  # (v129) the dashboard's PAUSE flag is seen within 0.25 s, not 1 s
                            # stall watchdog: rtl_power writes a row every 10 s; no growth for 90 s = wedged dongle
                            size_now = os.path.getsize(out) if os.path.exists(out) else 0
                            if size_now > last_size: last_size = size_now; last_grow = time.time()
                            elif time.time() - last_grow > 90 and time.time() - t_start > 120:
                                print("  dongle stalled (no data for 90 s) - restarting the sweep", flush=True)
                                bad = True; p.kill(); p.wait(); break
                            if os.path.exists(pause_flag):          # dashboard wants the dongle mid-slice
                                p.send_signal(signal.SIGINT)
                                try: p.wait(timeout=10)
                                except subprocess.TimeoutExpired: p.kill(); p.wait()
                                interrupted = True; break
                            try:
                                with open(log, "rb") as fh:
                                    size = os.path.getsize(log); fh.seek(max(start_off, size - 4000)); tail = fh.read().decode(errors="ignore")
                            except OSError: tail = ""
                            if "failed with -4" in tail or "No supported devices" in tail or "usb_claim_interface" in tail:
                                bad = True; p.kill(); p.wait(); break
                    except KeyboardInterrupt:
                        p.send_signal(signal.SIGINT); p.wait(); raise
                    elapsed = time.time() - t_start
                    if p.returncode and not os.path.exists(pause_flag) and elapsed < remaining - 5 and (p.returncode or 0) > 0:
                        bad = True                                                                        # engine died early (dongle busy / USB): retry path
                        if FASTSCAN_STATE["engine"] == "fastscan":
                            try:
                                with open(log, "rb") as fh: fh.seek(max(0, os.path.getsize(log) - 600)); tl = fh.read().decode(errors="ignore")
                            except OSError: tl = ""
                            contention = any(x in tl for x in ("did not start", "dongle busy", "ConnectionReset", "timed out", "Broken pipe", "closed the stream"))
                            if not contention:                                               # losing the dongle to the dashboard is not a fastscan fault
                                FASTSCAN_STATE["fails"] += 1
                                if FASTSCAN_STATE["fails"] >= 2: print("  fastscan failed twice - using rtl_power for the rest of this scan", flush=True)
                    if os.path.exists(pause_flag) or (p.returncode or 0) < 0:
                        # the dashboard took the dongle (it may have killed rtl_power / fastscan itself): that is a pause, not a USB fault
                        bad = False; interrupted = True
                    if bad:
                        attempts += 1; trim_bad_rows(out)
                        quick = elapsed < 3                                      # died at once = the dongle was still held by the player that just stopped: short wait
                        print(f"  dongle {'still busy' if quick else 'dropped off USB'} during {k} - waiting {2 if quick else 8} s and retrying ({attempts}/4)", flush=True)
                        subprocess.call(["pkill", "-x", "rtl_power"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        time.sleep(2 if quick else 8)
                        if attempts >= 4:
                            # never give up: wait for the dongle to come back (replug) and carry on with the same slice
                            print("  dongle not found - unplug it and plug it back in; the scan resumes by itself", flush=True)
                            while subprocess.call(["rtl_test", "-t"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20) != 0 if shutil.which("rtl_test") else False:
                                time.sleep(15)
                            attempts = 0; print("  dongle is back - resuming", flush=True)
                        continue
                    if interrupted:
                        remaining -= elapsed; continue                  # come back to this band after the listen
                    break                                                # slice completed
    except KeyboardInterrupt:
        print("\nStopped early - processing what we have.")
    hits = []
    for k, out in powers.items():
        if not os.path.exists(out): continue
        trim_bad_rows(out)
        name, lo, hi, mode, raster = BANDS[k]
        hs = find_hits(parse_power(out), mode, raster, a.threshold, a.min_busy)
        print(f"[{k}] {len(hs)} active frequencies")
        hits += hs
    write_outputs(hits, outdir, a.location, load_known(a.known), started, a.hours, a.max_channels)
    print(f"\nDone. {len(hits)} hits.\n  {outdir}/report.html\n  {outdir}/chirp_uv32.csv\n  {outdir}/results.csv")
    # (v185) Only a scan you ran yourself in a Terminal window opens its report when it ends. A sweep the dashboard started, or one it
    # stops to start another (every press of a category chip), used to open report.html in a new browser tab each time.
    if sys.platform == "darwin" and sys.stdout.isatty() and not _KILLED[0]: subprocess.call(["open", os.path.join(outdir, "report.html")])

def cmd_report(a):
    started = dt.datetime.now()
    hits = []
    for k in BANDS:
        p = os.path.join(a.folder, f"power_{k}.csv")
        if os.path.exists(p):
            trim_bad_rows(p)
            name, lo, hi, mode, raster = BANDS[k]
            hits += find_hits(parse_power(p), mode, raster, a.threshold, a.min_busy)
    write_outputs(hits, a.folder, a.location, load_known(a.known), started, 0, a.max_channels)
    print(f"Rebuilt {a.folder}/report.html with {len(hits)} hits")

def cmd_listen(a):
    mode = "am" if a.mhz < 137 else "fm"
    if not shutil.which("rtl_fm") or not shutil.which("play"):
        sys.exit("Needs rtl_fm and sox - run setup.command.")
    print(f"Listening on {a.mhz:.4f} MHz ({mode.upper()}). Ctrl-C to stop.")
    rtl = subprocess.Popen(["rtl_fm", "-f", f"{a.mhz}M", "-M", mode, "-s", "24k", "-g", str(a.gain), "-l", str(a.squelch), "-E", "dc", "-"], stdout=subprocess.PIPE)
    try: subprocess.call(["play", "-q", "-r", "24k", "-t", "raw", "-e", "s", "-b", "16", "-c", "1", "-", "vol", "4"], stdin=rtl.stdout)
    except KeyboardInterrupt: pass
    finally: rtl.terminate()

def cmd_live(a):
    """A real scanner: hop through a channel list, stop on activity, play audio, move on when quiet."""
    if not shutil.which("rtl_fm") or not shutil.which("play"):
        sys.exit("Needs rtl_fm and sox - run setup.command first.")
    try:
        from build_hub import entries
    except ImportError:
        sys.exit("build_hub.py must be in the same folder.")
    es = entries()
    groups = {
        "airports": [e for e in es if e["cat"] == "Airport"],
        "pearson":  [e for e in es if e["cat"] == "Airport" and e["group"].startswith("YYZ")],
        "ham":      [e for e in es if e["cat"] in ("Ham repeater", "Ham simplex")],
        "ham2m":    [e for e in es if e["cat"] in ("Ham repeater", "Ham simplex") and e["mhz"] < 200],
        "ham70":    [e for e in es if e["cat"] in ("Ham repeater", "Ham simplex") and e["mhz"] > 400],
        "frs":      [e for e in es if e["cat"] == "FRS/GMRS"],
        "frsmurs":  [e for e in es if e["cat"] in ("FRS/GMRS", "MURS")],
        "marine":   [e for e in es if e["cat"] == "Marine"],
    }
    if a.group == "custom":
        chans = [dict(name=f"{f}", mhz=float(f), mode=("AM" if float(f) < 137 else "NFM")) for f in a.freqs]
    elif a.group in groups:
        chans = groups[a.group]
    else:
        sys.exit(f"group must be one of: {', '.join(groups)}, custom")
    if not chans: sys.exit("No channels in that group.")
    modes = {c["mode"] for c in chans}
    if len(modes) > 1: sys.exit("One mode per scanner run (AM airband or FM) - pick a narrower group.")
    mode = "am" if "AM" in modes else "fm"
    # rtl_fm hops a -f list on its own: squelch -l decides when a channel counts as active
    sq = a.squelch if a.squelch is not None else (8 if mode == "am" else 25)
    cmd = ["rtl_fm", "-M", mode, "-s", "24k", "-g", str(a.gain), "-l", str(sq), "-t", str(a.hang), "-p", str(a.ppm), "-E", "dc"]
    for c in chans: cmd += ["-f", f"{c['mhz']:.4f}M"]
    cmd += ["-"]
    print(f"LIVE SCANNER - {a.group} - {len(chans)} channels ({mode.upper()}), squelch {sq}, gain {a.gain}")
    for c in chans: print(f"  {c['mhz']:9.4f}  {c['name']}")
    print("\nStops on a busy channel, plays it, moves on when it goes quiet. Ctrl-C to stop.")
    print("Hearing hiss all the time -> raise squelch (--squelch 60). Never stops on anything -> lower it (--squelch 15).\n")
    rtl = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    try: subprocess.call(["play", "-q", "-r", "24k", "-t", "raw", "-e", "s", "-b", "16", "-c", "1", "-", "vol", str(a.vol)], stdin=rtl.stdout)
    except KeyboardInterrupt: pass
    finally: rtl.terminate()

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    def common(p):
        p.add_argument("--threshold", type=float, default=10.0, help="dB above noise floor to count as a signal (default 10)")
        p.add_argument("--min-busy", type=float, default=0.5, help="min %% of samples active to keep (default 0.5)")
        p.add_argument("--location", default="GTA", help="label for the report (GTA, Muskoka...)")
        p.add_argument("--known", default="", help="your existing CHIRP CSV, to flag frequencies already programmed")
        p.add_argument("--max-channels", type=int, default=200)
    s = sub.add_parser("scan"); common(s)
    s.add_argument("--hours", type=float, default=5.0)
    s.add_argument("--bands", default="toronto", help="toronto (YYZ + GTA repeaters) | airfrs (YYZ + FRS/GMRS) | nets (repeaters + GMRS) | all (UV-32 range) | full (whole SDR, 24-1766 MHz) | ham | air | vhf | uhf")
    s.add_argument("--gain", default="40")
    s.add_argument("--slice", type=float, default=3.0, help="minutes per band before rotating to the next (default 3)")
    s.add_argument("--out", default="")
    r = sub.add_parser("report"); common(r); r.add_argument("folder")
    l = sub.add_parser("listen"); l.add_argument("mhz", type=float); l.add_argument("--gain", default="40"); l.add_argument("--squelch", default="0")
    v = sub.add_parser("live", help="live scanner: hop a channel list, stop on activity, play audio")
    v.add_argument("group", nargs="?", default="airports", help="airports | pearson | ham | ham2m | ham70 | frs | frsmurs | marine | custom")
    v.add_argument("freqs", nargs="*", help="with 'custom': frequencies in MHz")
    v.add_argument("--squelch", type=int, default=None, help="rtl_fm squelch level (AM default 25, FM default 40)")
    v.add_argument("--gain", default="49.6"); v.add_argument("--hang", type=int, default=3, help="seconds to hang on a channel after it goes quiet")
    v.add_argument("--vol", type=float, default=4.0, help="audio gain (rtl_fm AM output is quiet; default 4)")
    v.add_argument("--ppm", type=int, default=0, help="dongle frequency correction")
    argv = sys.argv[1:]
    if not argv or argv[0].startswith("-"): argv = ["scan"] + argv
    a = ap.parse_args(argv)
    {"scan": cmd_scan, "report": cmd_report, "listen": cmd_listen, "live": cmd_live}[a.cmd](a)

if __name__ == "__main__":
    main()
