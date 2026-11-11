#!/usr/bin/env python3
"""SDR Scanner dashboard v2 - single-page app over the v1 backend.
Everything the browser shows comes from JSON endpoints and is patched in place (no page reloads).
   python3 dashboard2.py            -> watches the newest scan_* folder
   python3 dashboard2.py scan_x     -> watches that folder"""
import glob, html, json, os, re, sys, time, datetime as dt, subprocess
from urllib.parse import urlparse, parse_qs

# (v172) one true copy: on 2026-11-10 an old copy of this app on the Desktop was launched by mistake and ran for two days
# without any of the newer fixes. If this file is ever started from anywhere other than ~/SDRScanner while that copy
# exists, hand over to it immediately instead of running stale code.
_CANON = os.path.expanduser("~/SDRScanner")
if (os.path.realpath(os.path.dirname(os.path.abspath(__file__))) != os.path.realpath(_CANON)
        and os.path.exists(os.path.join(_CANON, "dashboard2.py")) and not os.environ.get("SDR_ALLOW_COPY")):
    print(f"This is not the live copy - switching to {_CANON}", flush=True)
    os.chdir(_CANON); os.execv(sys.executable, [sys.executable, os.path.join(_CANON, "dashboard2.py")] + sys.argv[1:])

def _fix_ssl():
    """(v130) The python.org build of Python 3.13 on the Mac ships without root certificates, so every https call from the
    dashboard (OpenSky, adsb.fi, Flightradar24 ...) failed with CERTIFICATE_VERIFY_FAILED. Use certifi's bundle when it is
    installed, else macOS's own /etc/ssl/cert.pem - verification stays on either way."""
    import ssl
    for cafile in ([__import__("certifi").where()] if _has("certifi") else []) + ["/etc/ssl/cert.pem", "/private/etc/ssl/cert.pem"]:
        if os.path.exists(cafile):
            ssl._create_default_https_context = lambda cafile=cafile: ssl.create_default_context(cafile=cafile); return cafile
def _has(mod):
    import importlib.util; return importlib.util.find_spec(mod) is not None
try: SSL_CA = _fix_ssl()
except Exception: SSL_CA = None
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dashboard as v1                     # backend: squelch, recorder, stream, dongle hand-off, info, debug
def _kill_strays():
    """(v173) A sweep left running from another copy of this app (the old Desktop folder, 2026-11-10) kept taking the dongle from
    SDRTrunk every minute. At startup, stop any sdr_scan.py / fastscan.py / dashboard that was launched from a different folder."""
    here = os.path.realpath(os.path.dirname(os.path.abspath(__file__)))
    try:
        for ln in subprocess.run(["ps", "axo", "pid=,command="], capture_output=True, text=True, timeout=5).stdout.splitlines():
            pid, _, cmd = ln.strip().partition(" ")
            m = re.match(r"\S*[Pp]ython\S*\s+(?:-\S+\s+)*(/(?:(?!\.py\s).)*?/(?:sdr_scan|fastscan|dashboard2?)\.py)(?:\s|$)", cmd)   # (v181) Python RUNNING one of these by absolute path ...
            if m and pid.isdigit() and int(pid) != os.getpid() and os.path.realpath(os.path.dirname(m.group(1))) != here:   # ... from a folder that is not this one
                subprocess.call(["kill", pid]); print(f"stopped a stray process from another copy: {cmd[:120]}", flush=True)
    except Exception: pass
_kill_strays()
# (v150) if the server ever stops answering: `kill -USR1 <pid>` (or the hang watchdog below) writes every thread's stack to
# diag/threads.txt so the deadlock is visible instead of guessed at.
import faulthandler, signal as _sig
try:
    os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag"), exist_ok=True)
    _FH = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag", "threads.txt"), "a")
    faulthandler.register(_sig.SIGUSR1, file=_FH, all_threads=True)
except Exception: pass
from http.server import ThreadingHTTPServer

HERE = v1.HERE; PORT = v1.PORT; RATE = v1.RATE

RADIO = [("99.1", "CBC Radio One"), ("94.1", "CBC Music"), ("98.1", "CHFI"), ("102.1", "The Edge"), ("104.5", "CHUM"), ("107.1", "Q107"),
         ("92.5", "KiSS"), ("97.3", "Boom"), ("91.1", "Jazz FM"), ("99.9", "Virgin"), ("103.5", "Z103.5"), ("100.7", "CHIN"), ("89.5", "CIUT"), ("93.5", "Flow")]

def state():
    d = v1.gather(v1.newest_scan())
    hits = []
    ranked = sorted(d["hits"], key=lambda h: (h["active_pct"] >= 98 and h["samples"] >= 20, -h["score"]))
    for rank, h in enumerate(ranked, 1):
        h["rank"] = rank
    data_keys = []
    for h in d["hits"]:
        if 88.0 <= h["mhz"] < 108.0: continue                                  # broadcast FM is not part of the scanner (the FM Radio tab stays as the audio test)
        ps = 764 <= h["mhz"] <= 776 or 851 <= h["mhz"] <= 870          # P25 trunked bands: listed and named, routed to SDRTrunk
        if ("water" in (h.get("name") or "").lower() and ("meter" in (h.get("name") or "").lower() or "toronto" in (h.get("name") or "").lower())) or any(abs(h["mhz"] - f) < 0.0026 for f in (458.7625, 460.3875)):
            data_keys.append(f"{h['mhz']:.4f}"); continue              # City of Toronto water-meter telemetry: data, nothing to hear
        if not ps and (bool(h.get("carrier")) or (h["active_pct"] >= 98 and h["samples"] >= 20) or ("DATA" in (h.get("heard_tone", "") or ""))):
            data_keys.append(f"{h['mhz']:.4f}"); continue   # data links, beacons, digital control channels: nothing to hear, not listed
        hits.append({"rank": h["rank"],"mhz": h["mhz"], "name": h["name"], "service": h["service"] or "", "mode": h["mode"], "snr": h["snr_db"], "busy": h["active_pct"],
                     "uv32": h["uv32"], "tone": h.get("heard_tone", ""), "score": h["score"], "hours": h.get("hours", {}), "last": h.get("last_active", ""), "first": h.get("first_active", ""), "recent": h.get("recent_pct", 0), "cont": bool(h.get("carrier")) or (h["active_pct"] >= 98 and h["samples"] >= 20) or ("DATA" in (h.get("heard_tone", "") or "")),
                     "cat": category(h), "ver": h.get("verified", "")})
    # THE BANK: every voice channel ever heard stays in the list (bank.json), with when it was last heard. Live hits
    # refresh the numbers; channels not in the current sweep keep their last known values and just age.
    bank_mark_data(data_keys + list(d.get("data_keys", [])) + ["458.7625", "460.3875"] + [k for k, e in bank_load().items() if "water" in (e.get("name") or "").lower() and "toronto" in (e.get("name") or "").lower()])
    hist_write(hits)
    hits = bank_merge(hits)
    recs = glob.glob(os.path.join(HERE, "scan_*", "recordings", "*.wav"))      # all scan folders, same as the Recordings tab
    bands = d["bands"]
    # progress: slices done vs expected (10 min per band per round, 10 rounds)
    return {"folder": d["folder"], "running": d["running"], "paused": d["paused"], "now": d["now"], "ip": v1.lan_ip(), "port": PORT, "usb_errors": d.get("usb_errors_recent", 0), "background": d.get("background", ""),
            "total": d.get("total", 0), "hits": hits, "bands": bands, "recordings": len(recs), "log": d.get("log", "")[-400:],
            "player": {"mhz": v1._player["mhz"], "where": v1._player["where"]}, "dongle_lost": v1.DONGLE["lost"], "dongle_flat": v1.DONGLE.get("flat", "")}

_STC = {"t": 0.0, "val": None, "busy": False, "lock": __import__("threading").Lock()}
def state_cached(max_age=3.0):
    """The page polls this: serve the last result at once and refresh it in the background (the first call computes). Saves seconds on every load."""
    now = time.time()
    if _STC["val"] is not None:
        if now - _STC["t"] >= max_age and not _STC["busy"]:
            _STC["busy"] = True
            def _r():
                try: v = state(); _STC.update(val=v, t=time.time())
                except Exception: pass
                finally: _STC["busy"] = False
            __import__("threading").Thread(target=_r, daemon=True).start()
        return _STC["val"]
    with _STC["lock"]:
        if _STC["val"] is None: _STC.update(val=state(), t=time.time())
    return _STC["val"]

# ---------------------------------------------------------------- history (SQLite): what every channel did, in 10-minute buckets
import sqlite3
HIST = os.path.join(HERE, "history.sqlite")
_hist = {"conn": None, "lock": __import__("threading").Lock()}
def hist_conn():
    if _hist["conn"] is None:
        c = sqlite3.connect(HIST, check_same_thread=False)
        c.execute("CREATE TABLE IF NOT EXISTS heard (mhz REAL, bucket INTEGER, snr REAL, busy REAL, recent REAL, PRIMARY KEY (mhz, bucket))")
        c.execute("CREATE INDEX IF NOT EXISTS ix_b ON heard(bucket)"); c.commit(); _hist["conn"] = c
    return _hist["conn"]
def hist_write(live):
    """one row per live voice hit per 10-minute bucket (keeps the max)"""
    try:
        b = int(time.time() // 600); c = hist_conn()
        with _hist["lock"]:
            for h in live:
                if not h.get("recent") and not h.get("busy"): continue
                c.execute("INSERT INTO heard(mhz,bucket,snr,busy,recent) VALUES(?,?,?,?,?) ON CONFLICT(mhz,bucket) DO UPDATE SET snr=max(snr,excluded.snr), busy=max(busy,excluded.busy), recent=max(recent,excluded.recent)",
                          (round(h["mhz"], 4), b, h["snr"], h["busy"], h.get("recent", 0)))
            c.commit()
    except Exception: pass
def hist_series(mhz, hours=24):
    """[{t, recent}] for the last `hours`, one point per 10 min (0 where nothing)"""
    try:
        now_b = int(time.time() // 600); lo = now_b - hours * 6; c = hist_conn()
        with _hist["lock"]:
            rows = c.execute("SELECT bucket, recent, busy FROM heard WHERE mhz BETWEEN ? AND ? AND bucket > ?", (mhz - 0.0026, mhz + 0.0026, lo)).fetchall()
        d = {b: max(r or 0, bz or 0) for b, r, bz in rows}
        return [{"t": (lo + i + 1) * 600, "v": d.get(lo + i + 1, 0)} for i in range(hours * 6)]
    except Exception: return []
def hist_hourly(mhz, days=7):
    """busy % by hour of day over the last `days` - when is this channel usually active"""
    try:
        lo = int(time.time() // 600) - days * 144; c = hist_conn()
        with _hist["lock"]:
            rows = c.execute("SELECT bucket, recent, busy FROM heard WHERE mhz BETWEEN ? AND ? AND bucket > ?", (mhz - 0.0026, mhz + 0.0026, lo)).fetchall()
        acc = {}
        for b, r, bz in rows:
            hr = dt.datetime.fromtimestamp(b * 600).hour; acc.setdefault(hr, []).append(max(r or 0, bz or 0))
        return {hr: round(sum(v) / len(v)) for hr, v in acc.items()}
    except Exception: return {}

BANK = os.path.join(HERE, "bank.json")
_bank = {"loaded": False, "data": {}, "saved": 0}
NAME_FIX = {"YYZ TWR W": "YYZ TWR S", "YYZ TWR E": "YYZ TWR N"}        # (v183) Pearson's towers are South (118.350) and North (118.700), not West and East
def bank_load():
    """Load the bank. A failed read (iCloud half-synced file, truncated JSON) must NEVER start an empty bank that then overwrites the real one:
    retry, compare with the rolling backups (.bak every 15 min, .bak2 every 6 h) and take the fullest one if the main file lost more than half
    its channels, and if nothing loads, refuse to save (bank_ok False) so the file on disk is left alone."""
    if not _bank["loaded"]:
        cands = {}
        for path in (BANK, BANK + ".bak", BANK + ".bak2"):
            for attempt in range(3 if path == BANK else 1):
                try:
                    if os.path.exists(path):
                        j = json.load(open(path))
                        if isinstance(j, dict): cands[path] = j; break
                except Exception: time.sleep(0.5)
        data = cands.get(BANK)
        if cands:
            best = max(cands.values(), key=len)
            if data is None or len(data) < len(best) * 0.5: data = best
        if data is None:
            data = {}; _bank["ok"] = not os.path.exists(BANK) or os.path.getsize(BANK) == 0      # unreadable real file: do not overwrite it
        for e in data.values():                                               # (v183) channels banked under a name that has since been corrected
            if isinstance(e, dict) and e.get("name") in NAME_FIX: e["name"] = NAME_FIX[e["name"]]
        _bank["data"] = data; _bank["loaded"] = True; _bank["n0"] = len(data)
    return _bank["data"]
def bank_mark_data(keys):
    """Channels the scan classifies as data/digital are remembered as DATA in the bank so Monitor skips them for good."""
    if not keys: return
    data = bank_load(); changed = False
    for k in keys:
        e = data.get(k)
        if e and e.get("ver") != "DATA": e["ver"] = "DATA"; changed = True
    if changed: bank_save()
def bank_save():
    try:
        if _bank.get("ok") is False: return
        data = _bank["data"]
        if _bank.get("n0", 0) >= 100 and len(data) < _bank["n0"] * 0.5: return                # never shrink the bank by half in one go
        tmp = BANK + ".tmp"; json.dump(data, open(tmp, "w"))
        try:
            if os.path.exists(BANK) and os.path.getsize(BANK) > 1000 and (not os.path.exists(BANK + ".bak") or time.time() - os.path.getmtime(BANK + ".bak") > 900):
                shutil_copy(BANK, BANK + ".bak")
            if os.path.exists(BANK) and os.path.getsize(BANK) > 1000 and (not os.path.exists(BANK + ".bak2") or time.time() - os.path.getmtime(BANK + ".bak2") > 21600):
                shutil_copy(BANK, BANK + ".bak2")
        except Exception: pass
        os.replace(tmp, BANK); _bank["saved"] = time.time(); _bank["n0"] = max(_bank.get("n0", 0), len(data))
    except Exception: pass
def shutil_copy(a, b):
    import shutil; shutil.copy(a, b)
def _heard_ts(last_hhmm, now):
    """sweep 'last active' is HH:MM of today (or yesterday if it is ahead of the clock) -> unix time"""
    try:
        hh, mm = map(int, last_hhmm.split(":"))
        t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if t > now + dt.timedelta(minutes=2): t -= dt.timedelta(days=1)
        return t.timestamp()
    except Exception: return 0
def digital_name(mhz, name, service):
    """700/800 MHz public-safety hits: say what they are (nothing in the ISED extract covers them) and mark them digital."""
    if 764 <= mhz <= 776:
        import sdrtrunk_playlist as SP
        site = next((n for n, _, fr in SP.P25_SITES for f in fr if abs(f - mhz) <= 0.0016), None)
        if site: return f"Toronto Fire + Paramedics - {site} CONTROL channel (data only, the calls are on the voice channels)", "P25 Phase II - Toronto Fire / EMS", "DIGITAL"
        try: nm = p25_voice_name(mhz)
        except Exception: nm = "City of Toronto P25 - voice channel"
        return nm, "P25 Phase II - Toronto Fire / EMS", "DIGITAL"
    if 851 <= mhz <= 870:
        if 868.1 <= mhz <= 868.8: return "City of Toronto P25 - PATH / TTC subway site", "P25 Phase II (City of Toronto)", "DIGITAL"
        return (name if name and not name.replace(".", "").isdigit() else "800 MHz trunked system (digital)"), service or "800 MHz trunked", "DIGITAL"
    return name, service, None

def bank_merge(live):
    data = bank_load(); now = dt.datetime.now(); changed = False
    live_by = {}
    for h in live:
        h["name"] = NAME_FIX.get(h.get("name"), h.get("name"))                 # a sweep row written before the rename must not put the old name back
        k = f"{h['mhz']:.4f}"; live_by[k] = h
        e = data.get(k)
        heard = _heard_ts(h.get("last"), now) if h.get("last") else 0
        if not e:
            e = {"mhz": h["mhz"], "name": h["name"], "service": h["service"], "mode": h["mode"], "cat": h["cat"], "uv32": h["uv32"], "first_heard": heard or now.timestamp(),
                 "last_heard": heard, "best_snr": h["snr"], "times": 0, "tone": h.get("tone", ""), "ver": h.get("ver", "")}
            data[k] = e; changed = True
        if heard > e.get("last_heard", 0):
            e["last_heard"] = heard; e["times"] = e.get("times", 0) + 1; changed = True
            if now.timestamp() - heard < 300:
                try: v1.alerts_check_heard(h["mhz"], h["name"], h["cat"])
                except Exception: pass
        if h["snr"] > e.get("best_snr", 0): e["best_snr"] = h["snr"]; changed = True
        for f in ("name", "cat", "service", "mode", "uv32"):
            if h.get(f) and h.get(f) != e.get(f): e[f] = h[f]; changed = True
        if h.get("tone") and h["tone"] != e.get("tone"): e["tone"] = h["tone"]; changed = True
        if h.get("ver") in ("VOICE", "KNOWN", "DATA") and h["ver"] != e.get("ver"): e["ver"] = h["ver"]; changed = True
    # recordings with voice are the strongest evidence: stamp last_heard from the newest clip of each frequency
    try:
        for f in glob.glob(os.path.join(HERE, "scan_*", "recordings", "*.wav")):
            base = os.path.basename(f); k = f"{float(base.split('_')[0]):.4f}"
            e = data.get(k)
            if e:
                ts = os.path.getmtime(f)
                if ts > e.get("last_heard", 0): e["last_heard"] = ts; changed = True
    except Exception: pass
    if changed or time.time() - _bank["saved"] > 120: bank_save()
    out = []
    for k, e in data.items():
        if e.get("ver") == "DATA" and not (764 <= e["mhz"] <= 776 or 851 <= e["mhz"] <= 870): continue   # P25 channels stay listed, named, routed to SDRTrunk
        if 88.0 <= e["mhz"] < 108.0: continue                                  # broadcast FM is not part of the scanner
        h = live_by.get(k)
        age = (now.timestamp() - e.get("last_heard", 0)) / 60 if e.get("last_heard") else None
        row = {"rank": h["rank"] if h else 9000, "mhz": e["mhz"], "name": e.get("name", ""), "service": e.get("service", ""), "mode": e.get("mode", "NFM"),
               "snr": h["snr"] if h else e.get("best_snr", 0), "busy": h["busy"] if h else 0, "uv32": e.get("uv32", True), "tone": (h.get("tone") if h else "") or e.get("tone", ""),
               "score": h["score"] if h else 0, "hours": h.get("hours", {}) if h else {}, "last": h.get("last") if h else "", "first": h.get("first") if h else "",
               "recent": h.get("recent", 0) if h else 0, "cont": False, "cat": e.get("cat") or category({"service": e.get("service", ""), "mhz": e["mhz"]}), "ver": (h.get("ver") if h else "") or e.get("ver", ""), "wide": bool(e.get("wide")),
               "heard_ts": e.get("last_heard", 0), "age_min": None if age is None else round(age), "times": e.get("times", 0), "live": bool(h), "banked": True}
        nm, sv, dig = digital_name(row["mhz"], row["name"], row["service"])
        if dig: row["name"], row["service"], row["ver"], row["mode"] = nm, sv, dig, "P25"
        st = p25_lookup(row["mhz"]) if (dig or 410 <= row["mhz"] <= 416 or 936 <= row["mhz"] <= 940) else None
        if st:
            sysn, svc = trunk_system(row["mhz"])
            if svc: row["service"] = svc
            if not dig and sysn: row["name"] = p25_voice_name(row["mhz"]); row["ver"] = "DIGITAL"; row["mode"] = "P25" if "P25" in svc else "DMR"
            ts, hm = call_stamp(st.get("last", ""))
            if ts > (row["heard_ts"] or 0): row["heard_ts"] = ts; row["age_min"] = round((now.timestamp() - ts) / 60)
            _, hmc = call_stamp(st.get("last_clear", ""))
            row["last_call"] = {"tg": st.get("last_tg", "") or ("Police (encrypted)" if st["enc"] else ""), "t": hmc or hm, "calls": st["calls"], "enc": st["enc"]}
        row["vrank"] = 2 if row["ver"] == "VOICE" else (1 if row["ver"] == "KNOWN" else 0)
        out.append(row)
    out.sort(key=lambda r: -(r["heard_ts"] or 0))
    for i, r in enumerate(out, 1):
        if not r["live"]: r["rank"] = 1000 + i
    # (5 Nov) the bank holds 3,000 channels; shipping all of them every poll (850 KB) and keeping them in the DOM made the page crawl.
    # The page gets what matters: everything live in the current sweep, every known/verified voice or digital channel, anything
    # heard in the last 2 h, anything heard today at least twice - capped at LIST_CAP, newest first. The full bank stays on disk.
    def keep(r):
        if r["live"] or r["ver"] in ("VOICE", "KNOWN", "DIGITAL"): return True
        a = r["age_min"]
        if a is None: return False
        return a <= 120 or (a <= 1440 and (r.get("times") or 0) >= 2)
    out = [r for r in out if keep(r)][:LIST_CAP]
    return out
LIST_CAP = 300

_prev_cache = {"key": None, "hits": []}
_CITY = None
def city_channel(m):
    global _CITY
    if _CITY is None:
        try:
            from build_hub import CITY
            _CITY = {round(f, 4) for _, f in CITY}
        except Exception: _CITY = set()
    return round(m, 4) in _CITY
_RETAIL = None
def retail_channel(m):
    global _RETAIL
    if _RETAIL is None:
        try:
            from build_hub import RETAIL
            _RETAIL = sorted(set(f for _, f in RETAIL))
        except Exception: _RETAIL = []
    return any(abs(f - m) <= 0.0026 for f in _RETAIL)

def category(h):
    s = (h["service"] or "").upper(); m = h["mhz"]
    if s in ("PEARSON", "AIR", "AIR GUARD", "NAV", "MIL AIR") or 108 <= m < 137: return "Airport"
    if s in ("REPEATER", "2M RPT", "70CM RPT"): return "Ham repeater"
    if "2M" in s or "70CM" in s or "SAT" in s: return "Ham"
    if "FRS" in s or "GMRS" in s: return "FRS/GMRS"
    if retail_channel(m): return "Retail"
    if city_channel(m): return "City services"
    if 764 <= m <= 776 or 851 <= m <= 870: return "Digital"
    if s == "MARINE": return "Marine"
    if s == "MURS": return "MURS"
    if s == "RAILWAY": return "Railway"
    if "BIZ" in s or s == "UHF T": return "Business"
    if s == "PAGING": return "Paging"
    return "Other"

def recordings(limit=1000):
    # every scan folder, newest clip first: a chip starts a new folder, the recordings tab must not go blank
    files = []
    for d in glob.glob(os.path.join(HERE, "scan_*")): files += glob.glob(os.path.join(d, "recordings", "*.wav"))
    out = []
    for f in sorted(files, key=os.path.getmtime, reverse=True)[:limit]:
        base = os.path.basename(f); st = os.stat(f); parts = base.split("_")
        tp = f[:-4] + ".txt"; txt = ""
        if os.path.exists(tp):
            try: txt = open(tp, errors="ignore").read().strip()
            except OSError: pass
        agree = None
        try: agree = json.load(open(tp[:-4] + ".asr.json")).get("agree")
        except Exception: pass
        out.append({"file": base, "mhz": parts[0], "name": parts[1] if len(parts) > 2 else "", "secs": round(max(0, (st.st_size - 44) / (RATE * 2)), 1),
                    "time": dt.datetime.fromtimestamp(st.st_mtime).strftime("%b %d %H:%M:%S"), "text": txt, "agree": agree})
    return out

def mac_sound():
    """What the Mac thinks its sound output is: volume, mute, default output device (so 'I hear nothing' can be diagnosed)."""
    out = {"volume": "", "device": "", "devices": []}
    try:
        out["volume"] = subprocess.run(["osascript", "-e", "get volume settings"], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception as e: out["volume"] = f"? {e}"
    try:
        j = json.loads(subprocess.run(["system_profiler", "SPAudioDataType", "-json"], capture_output=True, text=True, timeout=15).stdout)
        for grp in j.get("SPAudioDataType", []):
            for dev in grp.get("_items", []):
                name = dev.get("_name", "?"); 
                if dev.get("coreaudio_output_source") or dev.get("coreaudio_device_output"):
                    out["devices"].append(name)
                    if dev.get("coreaudio_default_audio_output_device") == "spaudio_yes": out["device"] = name
    except Exception as e: out["device"] = out["device"] or f"? {e}"
    return out

DIG_CARRIERS = os.path.join(HERE, "digital_carriers.json")
def digital_carriers():
    """Always-on carriers in the public-safety bands from every digital sweep so far (persisted; newest numbers win)."""
    try: known = json.load(open(DIG_CARRIERS))
    except Exception: known = {}
    seen = known.pop("_seen", {}) if isinstance(known.get("_seen"), dict) else {}
    folders = sorted(d for d in glob.glob(os.path.join(HERE, "scan_*")) if d.endswith("_digital"))
    if v1.newest_scan() and v1.newest_scan() not in folders: folders.append(v1.newest_scan())
    changed = False
    for d in folders:
        # (v129) only re-read a folder whose 700/800 MHz power files changed since the last look: this used to parse every
        # digital sweep ever made (tens of MB) on every Digital-tab load - measured 11.9 s, now ~10 ms when nothing changed
        try: stamp = max([os.path.getmtime(f) for f in glob.glob(os.path.join(d, "power_ps*.csv"))] or [0])
        except Exception: stamp = 0
        key = os.path.basename(d)
        if stamp and seen.get(key) == stamp: continue                # no stamp (no 700/800 files yet) -> cheap gather, never cached
        try: g = v1.gather(d)
        except Exception: continue
        for h in g.get("all_hits", []):
            m = h["mhz"]
            if (764 <= m <= 776 or 851 <= m <= 870) and h.get("carrier"):
                known[f"{m:.5f}"] = {"mhz": m, "snr": h["snr_db"], "busy": h["active_pct"], "seen": key}
        if stamp: seen[key] = stamp; changed = True
    if changed:
        try: json.dump(dict(known, _seen=seen), open(DIG_CARRIERS, "w"))
        except Exception: pass
    known.pop("_seen", None)
    import sdrtrunk_playlist as SP
    table = {round(f, 5): n for n, _, fr in SP.P25_SITES for f in fr}
    out = []
    for e in known.values():
        site = table.get(round(e["mhz"], 5))
        e = dict(e); e["likely"] = f"City of Toronto P25 control channel - {site}" if site else ("P25 control channel (always on)" if e["busy"] >= 95 else "voice / data channel")
        out.append(e)
    out.sort(key=lambda c: -c["snr"]); return out

def build_playlist():
    """Regenerate sdrtrunk/SDR_Scanner.xml (with any control channels the sweep found) and drop a copy where SDRTrunk lists playlists."""
    import sdrtrunk_playlist as SP, shutil
    out, ndmr, ntg = SP.build([c["mhz"] for c in digital_carriers()])
    dest = os.path.expanduser("~/SDRTrunk/playlist"); copied = False
    if os.path.isdir(dest):
        try: shutil.copy(out, os.path.join(dest, "SDR_Scanner.xml")); copied = True
        except Exception: pass
    return {"path": out, "copied": copied, "dmr": ndmr, "talkgroups": ntg}

# ---------------------------------------------------------------- SDRTrunk as a player engine (P25 / DMR voice through the same ▶)
SDRT = {"target": None, "name": None, "started": 0, "proc": None, "note": ""}
SDRT_FLAG = os.path.join(HERE, "SDRTRUNK_HAS_DONGLE")
PRIO_FILE = os.path.join(HERE, "solo.json")         # {"tg": 1605, "muted": [..]} - empty / missing = hear everything (the default)
def solo_load():
    try:
        j = json.load(open(PRIO_FILE)); return {"tg": int(j.get("tg") or 0), "muted": [int(x) for x in j.get("muted", [])]}
    except Exception: return {"tg": 0, "muted": []}
def prio_load():
    """talkgroup -> -1 (do not monitor) for every talkgroup muted by 'hone in'; nothing when hearing everything"""
    return {str(t): -1 for t in solo_load()["muted"]}
def solo_set(tg):
    """tg 0 = hear everything. Otherwise every OTHER talkgroup heard so far (plus all named Fire/EMS ones) is set to do-not-monitor."""
    import sdrtrunk_playlist as SP
    if not tg: json.dump({"tg": 0, "muted": []}, open(PRIO_FILE, "w")); return solo_load()
    known = set(SP.FIRE) | set(SP.EMS)
    try:
        for a in talkgroups():
            if a["id"].isdigit(): known.add(int(a["id"]))
    except Exception: pass
    known.discard(int(tg)); json.dump({"tg": int(tg), "muted": sorted(known)}, open(PRIO_FILE, "w")); return solo_load()
def sdrt_binary():
    cands = []
    nightly = os.path.expanduser("~/Applications/sdr-trunk-nightly/bin/sdr-trunk")          # nightly build (NXDN decoder) wins when installed
    if os.path.exists(nightly) and not os.path.exists(os.path.join(HERE, "sdrtrunk", "use_stable.txt")): cands.append(nightly)
    try: cands.append(open(os.path.join(HERE, "sdrtrunk", "path.txt")).read().strip())
    except Exception: pass
    cands += sorted(glob.glob(os.path.expanduser("~/Applications/sdr-trunk*/bin/sdr-trunk")), reverse=True) + glob.glob("/Applications/sdr-trunk*/bin/sdr-trunk")
    cands += [os.path.expanduser("~/SDRTrunk/bin/sdr-trunk")]
    for c in cands:
        if c and os.path.exists(c): return c
    return None
_SR = {"t": 0.0, "v": False}
def _fresh_running():
    _SR["t"] = 0.0; return sdrt_running()
def sdrt_running(fresh=False):
    """pgrep is a process spawn: the page polls /meter 4x a second, so the answer is cached for 1 s (start/stop loops ask fresh)."""
    now = time.time()
    if not fresh and now - _SR["t"] < 1.0: return _SR["v"]
    _SR["v"] = subprocess.call(["pgrep", "-f", "sdr-trunk"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0; _SR["t"] = time.time()
    return _SR["v"]
def sdrt_playlist_path():
    """The playlist SDRTrunk loads at start (its Java preference), else its default - that is the file we write so nothing has to be clicked."""
    try:
        out = subprocess.run(["defaults", "read", "com.apple.java.util.prefs"], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r'"?playlist\.path"?\s*=\s*"?([^";\n]+)', out)
        if m and m.group(1).strip() and os.path.isdir(os.path.dirname(m.group(1).strip())): return m.group(1).strip()
    except Exception: pass
    d = os.path.expanduser("~/SDRTrunk/playlist"); os.makedirs(d, exist_ok=True)
    return os.path.join(d, "default.xml")
SDRT_LOCK = __import__("threading").RLock()
def sdrt_start(target):
    with SDRT_LOCK: return _sdrt_start(target)
# ---------------------------------------------------------------- SDRTrunk audio -> Icecast (local) -> /sdrt_audio (browser, anywhere)
ICE = {"proc": None, "cfg": os.path.join(HERE, "sdrtrunk", "icecast.xml")}
def icecast_bin():
    for c in ("/opt/homebrew/bin/icecast", "/usr/local/bin/icecast"):
        if os.path.exists(c): return c
    return None
def icecast_ensure():
    """Start a local Icecast (port 8800) if it is installed and not running; SDRTrunk's stream config in the playlist points at it."""
    import sdrtrunk_playlist as SP
    if not icecast_bin(): return False
    if ICE["proc"] and ICE["proc"].poll() is None: return True
    try:
        if subprocess.call(["pgrep", "-x", "icecast"], stdout=subprocess.DEVNULL) == 0: return True
    except Exception: pass
    os.makedirs(os.path.dirname(ICE["cfg"]), exist_ok=True); logd = os.path.join(HERE, "sdrtrunk", "icecast_logs"); os.makedirs(logd, exist_ok=True)
    with open(ICE["cfg"], "w") as f:
        f.write(f"""<icecast><location>Toronto</location><admin>sdr@localhost</admin>
<limits><clients>50</clients><sources>2</sources><queue-size>524288</queue-size><client-timeout>30</client-timeout><header-timeout>15</header-timeout><source-timeout>10</source-timeout><burst-size>65535</burst-size></limits>
<authentication><source-password>{SP.ICECAST_PASS}</source-password><relay-password>{SP.ICECAST_PASS}</relay-password><admin-user>admin</admin-user><admin-password>{SP.ICECAST_PASS}</admin-password></authentication>
<hostname>127.0.0.1</hostname><listen-socket><port>{SP.ICECAST_PORT}</port><bind-address>127.0.0.1</bind-address></listen-socket>
<fileserve>0</fileserve><paths><basedir>{os.path.dirname(ICE["cfg"])}</basedir><logdir>{logd}</logdir><webroot>{logd}</webroot><adminroot>{logd}</adminroot><alias source="/" destination="/status.xsl"/></paths>
<logging><accesslog>access.log</accesslog><errorlog>error.log</errorlog><loglevel>2</loglevel><logsize>2000</logsize></logging><security><chroot>0</chroot></security></icecast>""")
    ICE["proc"] = subprocess.Popen([icecast_bin(), "-c", ICE["cfg"]], stdout=subprocess.DEVNULL, stderr=open(os.path.join(logd, "stderr.log"), "a"))
    v1.say(f"Icecast started on 127.0.0.1:{SP.ICECAST_PORT} (SDRTrunk audio -> browser)"); time.sleep(0.8); return True
def icecast_live():
    """Is SDRTrunk feeding the mount right now?  (v158: asks /admin/listmounts - the XSLT status page is not available in the Homebrew build.)"""
    import sdrtrunk_playlist as SP, urllib.request, base64
    try:
        rq = urllib.request.Request(f"http://127.0.0.1:{SP.ICECAST_PORT}/admin/listmounts")
        rq.add_header("Authorization", "Basic " + base64.b64encode(f"admin:{SP.ICECAST_PASS}".encode()).decode())
        with urllib.request.urlopen(rq, timeout=2) as r: txt = r.read().decode("utf-8", "replace")
        return f'mount="{SP.ICECAST_MOUNT}"' in txt
    except Exception: return False

def _sdrt_start(target):
    """target: 'p25:<site name>' or 'dmr:<mhz>'. Writes a playlist with only that channel auto-starting and (re)launches SDRTrunk."""
    import sdrtrunk_playlist as SP, shutil, importlib
    importlib.reload(SP)                                      # always the playlist builder on disk
    binp = sdrt_binary()
    if not binp: return {"error": "SDRTrunk is not installed - double-click 'Install SDRTrunk.command' in the SDR Scanner/_launchers folder"}
    kind, _, what = target.partition(":")
    if kind == "p25":
        sites = [n for n, _, _ in SP.P25_SITES]
        site = what if what in sites else "West Simulcast"
        sysname = SP.SITE_SYSTEM.get(site, ("Toronto Fire + Paramedics",))[0]
        name = f"{sysname} (P25, {site})"; enable = {"p25": site}
    elif kind == "dmrt":                                                 # "dmrt:939.1375" - a trunked DMR system by its control channel
        try: cc = round(float(what), 4)
        except ValueError: return {"error": "bad control channel"}
        sysrow = next((t for t in SP.DMR_TRUNKED if abs(t[1] - cc) < 0.0001), None)
        if not sysrow: return {"error": "unknown DMR system"}
        name = sysrow[0]; enable = {"dmrt": cc}
    elif kind == "nxdng":                                                # "nxdng:160.3500,161.4375" - NXDN channels decoded together (nightly SDRTrunk)
        if "sdr-trunk-nightly" not in (binp or ""): return {"error": "NXDN needs the SDRTrunk nightly build - run _launchers/Install SDRTrunk nightly.command"}
        try: fr = sorted({round(float(x), 4) for x in what.split(",") if x.strip()})
        except ValueError: return {"error": "bad NXDN group"}
        name = f"GO Transit rail traffic control (NXDN) {fr[0]:.4f}-{fr[-1]:.4f}" if len(fr) > 1 else f"{fr[0]:.4f} NXDN"; enable = {"nxdn": set(fr)}
    elif kind == "dmrg":                                                 # "dmrg:463.3125,463.6375,..." - a whole group decoded at once
        try: fr = sorted({round(float(x), 4) for x in what.split(",") if x.strip()})
        except ValueError: return {"error": "bad DMR group"}
        if not fr: return {"error": "empty DMR group"}
        known = dict(SP.DMR_KNOWN); known.update(SP.dmr_from_data())
        first = known.get(fr[0], "DMR").split(" (")[0]
        name = f"DMR group: {first}" + (f" + {len(fr)-1} more" if len(fr) > 1 else "") + f" ({fr[0]:.4f}-{fr[-1]:.4f})"; enable = {"dmr": set(fr)}
    elif kind == "probe":
        try: pm = round(float(what), 4)
        except ValueError: return {"error": "bad frequency"}
        name = f"{pm:.4f} MHz trunked channel (trying P25 decode)"; enable = {"probe": pm}
    elif kind == "dmr":
        try: mhz = round(float(what), 4)
        except ValueError: return {"error": "bad DMR frequency"}
        known = dict(SP.DMR_KNOWN); known.update(SP.dmr_from_data())
        name = f"{mhz:.4f} {known.get(mhz, 'DMR')} (DMR)"; enable = {"dmr": mhz}
    else: return {"error": "unknown target"}
    try: v1.stop_player()
    except Exception: pass
    SDRT.update({"target": target, "name": name, "started": time.time(), "note": "starting SDRTrunk (10-20 s)"})
    v1.take_dongle(); open(SDRT_FLAG, "w").write(json.dumps({"t": time.time(), "target": target, "name": name}))
    out, _, _ = SP.build([c["mhz"] for c in digital_carriers()], enable=enable, prio=prio_load())
    dest = sdrt_playlist_path()
    try:
        if os.path.exists(dest) and not os.path.exists(dest + ".before-sdr-scanner"): shutil.copy(dest, dest + ".before-sdr-scanner")
        shutil.copy(out, dest); shutil.copy(out, os.path.join(os.path.dirname(dest), "SDR_Scanner.xml"))
    except Exception as e: return {"error": f"could not write the SDRTrunk playlist: {e}"}
    was_sdrt = _fresh_running()
    if was_sdrt:
        subprocess.call(["pkill", "-f", "sdr-trunk"])
        for _ in range(40):
            if not _fresh_running(): break
            time.sleep(0.1)
    # SDRTrunk probes the tuner once at startup and gives up ("Can't set configuration - LIBUSB_ERROR_NO_DEVICE") if
    # rtl_power / rtl_fm has not fully let go yet: wait until a quick rtl_test can open the dongle, then launch.
    for prog in ("rtl_fm", "rtl_power", "rtl_tcp"): subprocess.call(["pkill", "-x", prog], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    free = False
    for _ in range(0 if was_sdrt else 12):                       # SDRTrunk -> SDRTrunk (site switch, hone in): the dongle is already free, skip the probe
        try:
            if subprocess.call(["rtl_test", "-t"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8) == 0: free = True; break
        except Exception: pass
        time.sleep(1)
    time.sleep(0.5 if was_sdrt else (0.9 if free else 1))
    v1.say(f"SDRTrunk starting: {name}")
    try: icecast_ensure()
    except Exception as e: v1.say(f"icecast: {e}")
    SDRT["proc"] = subprocess.Popen([binp], cwd=os.path.dirname(binp), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    SDRT.update({"target": target, "name": name, "started": time.time(), "note": "starting SDRTrunk (a few seconds)"})
    return {"ok": True, "name": name, "playlist": dest}
# ---- (v138) DMR scan: every conventional DMR channel, packed into the fewest 2.2 MHz windows the dongle can decode at once, and
# SDRTrunk walked through those windows like a scanner bank - each window for `step` seconds, extended while a call is in progress.
# One button instead of "single channels, one at a time". The sweep gets the dongle back when the scan is stopped.
DMRSCAN = {"on": False, "windows": [], "i": -1, "step": 90, "slot_end": 0.0, "started": 0.0, "hops": 0, "kind": "dmr"}
DMRSCAN_LOCK = __import__("threading").Lock()
DMRSCAN_FILE = os.path.join(HERE, "dmrscan.json"); DMRSCAN_HOLD = os.path.join(HERE, "DMR_SCAN_ON")      # (v144) the hold file keeps an idle
def dmrscan_persist():                                                                                     # restart away between window hops
    try:
        if DMRSCAN["on"]:
            json.dump({"on": True, "kind": DMRSCAN["kind"], "i": DMRSCAN["i"], "step": DMRSCAN["step"], "started": DMRSCAN["started"], "hops": DMRSCAN["hops"]}, open(DMRSCAN_FILE, "w"))
            open(DMRSCAN_HOLD, "w").write(str(time.time()))
        else:
            for f in (DMRSCAN_FILE, DMRSCAN_HOLD):
                try: os.remove(f)
                except OSError: pass
    except Exception: pass
def dmrscan_resume_after_restart():
    """A restart (idle or forced) must not kill a DMR scan the person started: pick it up at the window it was on."""
    try: st = json.load(open(DMRSCAN_FILE))
    except Exception: return
    if not st.get("on"): return
    try:
        with DMRSCAN_LOCK:
            DMRSCAN.update(on=True, kind=st.get("kind", "dmr"), windows=(p25_windows() if st.get("kind") == "p25" else dmr_windows()), i=int(st.get("i", 0)) - 1, step=int(st.get("step", 90)), started=st.get("started", time.time()), hops=int(st.get("hops", 0)))
            r = dmrscan_next()
            if r.get("error"): DMRSCAN["on"] = False
            dmrscan_persist()
        v1.say("DMR scan resumed after the restart" if DMRSCAN["on"] else "DMR scan could not resume after the restart")
    except Exception as e: v1.say(f"DMR scan resume: {e!r}")
def p25_windows():
    """(v156) Every P25 site as a scan window: one dongle decodes ONE site at a time (a site's control + voice channels fit its 2.4 MHz window; two sites do not).
    Fire / EMS first (double dwell), then the other City sites, PATH, Metrolinx."""
    import sdrtrunk_playlist as SP
    out = []
    for site, fire, freqs in SP.P25_SITES:
        if site == "Fire + EMS - All 4 Sites Combined": continue           # (v157) a direct pick, not part of the rotation - it already covers what West/South/North/East scan separately
        sysname = SP.SITE_SYSTEM.get(site, ("Toronto Fire + Paramedics", ""))[0]
        out.append({"name": f"{sysname} - {site}", "mhz": sorted(freqs), "names": [f"{sysname} ({site})"], "target": f"p25:{site}", "trunked": True, "mult": 2 if fire else 1, "_r": 0 if fire else (2 if "Metrolinx" in site else 1)})
    out.sort(key=lambda w: w["_r"])
    return out
def dmr_windows():
    """All known conventional DMR channels (curated + found by the sweep) greedy-packed into windows no wider than DMR_SPAN."""
    import sdrtrunk_playlist as SP
    allc = dict(SP.DMR_KNOWN); allc.update(SP.dmr_from_data())
    fr = sorted(allc); wins = []; cur = []
    for m in fr:
        if cur and m - cur[0] > SP.DMR_SPAN: wins.append(cur); cur = []
        cur.append(m)
    if cur: wins.append(cur)
    out = []
    for w in wins:
        label = next((nm for lo, hi, nm in SP.GROUP_NAMES if lo <= w[0] <= hi), None)
        if not label:
            short = []
            for m in w:
                n0 = allc[m].split(" (")[0].split(" - ")[0].split(" / ")[0]
                if n0 not in short: short.append(n0)
            label = short[0] + (f" + {len(short)-1} more" if len(short) > 1 else "")
        out.append({"name": label, "mhz": w, "names": [allc[m] for m in w], "target": "dmrg:" + ",".join(f"{m:.4f}" for m in w)})
    for name, cc, chans in SP.DMR_TRUNKED:                                   # the trunked systems are windows too (City services, Billy Bishop ops)
        out.append({"name": name.split(" - ")[0], "mhz": sorted(chans), "names": [name], "target": f"dmrt:{cc:.4f}", "trunked": True})
    for w in out:
        if w["name"].startswith("ROADS"): w["mult"] = 2                       # (v152) SNOWTO: plows and salt trucks get double dwell
    def _rank(w): return 0 if w["name"].startswith("ROADS") else (2 if re.search(r"Billy Bishop|Port Authority|PortsToronto", w["name"]) else 1)
    out.sort(key=_rank)                                                        # (v152) roads first, the airport windows last
    return out
def dmrscan_recent_call(secs=20):
    """True when SDRTrunk logged a voice call in the last `secs` seconds (the slot is extended rather than cut mid-conversation)."""
    try:
        now = dt.datetime.now()
        for c in sdrt_calls(5):
            h, m, sec = (int(x) for x in c["t"].split(":"))
            age = (now - now.replace(hour=h, minute=m, second=sec)).total_seconds()
            if 0 <= age <= secs: return True
    except Exception: pass
    return False
def dmrscan_next():
    """Hop to the next window (called under DMRSCAN_LOCK)."""
    n = len(DMRSCAN["windows"])
    if not n: return {"error": "no DMR channels known yet - run the UHF sweep first"}
    DMRSCAN["i"] = (DMRSCAN["i"] + 1) % n; w = DMRSCAN["windows"][DMRSCAN["i"]]
    if SDRT["target"]: sdrt_stop(handback=False)
    r = sdrt_start(w["target"])
    if r.get("ok"): SDRT["auto"] = False; DMRSCAN["slot_end"] = time.time() + DMRSCAN["step"] * w.get("mult", 1); DMRSCAN["hops"] += 1; dmrscan_persist(); v1.say(f"{DMRSCAN['kind'].upper()} scan: window {DMRSCAN['i']+1}/{n} {w['name']} ({w['mhz'][0]:.4f}-{w['mhz'][-1]:.4f})")
    return r
def dmrscan_start(step=90, kind="dmr"):
    with DMRSCAN_LOCK:
        if not sdrt_binary(): return {"error": "SDRTrunk is not installed"}
        with v1._lock: listening = v1._player.get("session") is not None
        if listening: return {"error": "stop the analog player first - the dongle can only do one thing"}
        DMRSCAN.update(on=True, kind=("p25" if kind == "p25" else "dmr"), windows=(p25_windows() if kind == "p25" else dmr_windows()), i=-1, step=max(30, min(600, int(step))), started=time.time(), hops=0)
        r = dmrscan_next()
        if r.get("error"): DMRSCAN["on"] = False
        dmrscan_persist()
        return dict(r, scan=dmrscan_state())
def dmrscan_stop():
    with DMRSCAN_LOCK:
        was = DMRSCAN["on"]; DMRSCAN["on"] = False
        dmrscan_persist()
        if was and SDRT["target"]: sdrt_stop()
    return {"ok": True}
_DW = {"t": 0.0, "v": []}
def dmrscan_state():
    if not DMRSCAN["on"] and time.time() - _DW["t"] > 60:                     # (v141) show the bank size before the scan is started
        try: _DW.update(t=time.time(), v=dmr_windows())
        except Exception: pass
    if not DMRSCAN["on"] and _DW["v"]: DMRSCAN["windows"] = _DW["v"]
    w = DMRSCAN["windows"][DMRSCAN["i"]] if DMRSCAN["on"] and 0 <= DMRSCAN["i"] < len(DMRSCAN["windows"]) else None
    return {"on": DMRSCAN["on"], "kind": DMRSCAN["kind"], "window": DMRSCAN["i"] + 1 if w else 0, "windows": len(DMRSCAN["windows"]), "name": w["name"] if w else "",
            "mhz": w["mhz"] if w else [], "names": w["names"] if w else [], "left": max(0, int(DMRSCAN["slot_end"] - time.time())) if w else 0,
            "step": DMRSCAN["step"], "hops": DMRSCAN["hops"], "channels": sum(len(x["mhz"]) for x in DMRSCAN["windows"]), "running": sdrt_running() if w else False}
def dmrscan_tick():
    """Called from the background loop every few seconds: advance the window when its slot is over and nobody is mid-call."""
    if not DMRSCAN["on"]: return
    with DMRSCAN_LOCK:
        if not DMRSCAN["on"]: return
        if SDRT["target"] and not sdrt_running() and time.time() - SDRT["started"] > 20: DMRSCAN["on"] = False; dmrscan_persist(); v1.say("DMR scan: SDRTrunk closed - scan stopped"); return
        if time.time() < DMRSCAN["slot_end"]: return
        if dmrscan_recent_call(60): DMRSCAN["slot_end"] = time.time() + 30; return          # (v143) a conversation gets 60 s of silence before we leave
        r = dmrscan_next()
        if r.get("error"): DMRSCAN["on"] = False; dmrscan_persist(); v1.say("DMR scan: " + r["error"])

# (v178) One dongle command at a time. Every request that opens the dongle (tune, stream, SDRTrunk play, siren search, sweep, digital
# scan) takes this gate; a second one that arrives before the first has finished is refused instead of queued. Two opens racing each
# other was what double-clicks did: the second killed the first mid-claim, and the dongle dropped off USB. Stops are never refused.
DG = {"busy": False, "what": "", "t0": 0.0}
DG_LOCK = __import__("threading").Lock()
DG_OPEN = {"/playmac": "tuning", "/stream": "tuning", "/api/sdrt/play": "starting SDRTrunk", "/api/sirens": "the siren search", "/api/scan": "starting a sweep",
           "/api/sdrt/solo": "restarting SDRTrunk", "/api/sdrtrunk": "the hand-over to SDRTrunk"}
def dg_what(u):
    if u.path in DG_OPEN: return DG_OPEN[u.path]
    if u.path == "/api/sdrt/dmrscan":
        q = parse_qs(u.query)
        if q.get("on", [""])[0] == "1" or q.get("next", [""])[0] == "1": return "the digital scan"
    return None
def dg_enter(what):
    now = time.time()
    with DG_LOCK:
        if DG["busy"] and now - DG["t0"] < 10: return False, DG["what"]      # 10 s (v179, was 45): a command that never finished cannot lock the dongle for long
        DG.update(busy=True, what=what, t0=now); return True, ""
def dg_exit():
    with DG_LOCK: DG["busy"] = False
_NUMW = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "tree": "3", "four": "4", "five": "5", "fife": "5", "six": "6", "seven": "7", "eight": "8",
         "nine": "9", "niner": "9", "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14", "fifteen": "15", "sixteen": "16",
         "seventeen": "17", "eighteen": "18", "nineteen": "19"}
_TENS = {"twenty": "2", "thirty": "3", "forty": "4", "fifty": "5", "sixty": "6", "seventy": "7", "eighty": "8", "ninety": "9"}
def _say_norm(txt):
    """(v180) Transcript text with spoken numbers as digits and runs of digits joined, so a callsign can be matched whole:
    'Jazz two six two', 'jazz 2-6-2' and 'Jazz twenty-six two' all become 'jazz 262'."""
    w = re.findall(r"[a-z]+|\d+", txt.lower()); out = []; i = 0
    while i < len(w):
        t = w[i]
        if t in _TENS:
            nx = w[i + 1] if i + 1 < len(w) else ""
            if nx in _NUMW and len(_NUMW[nx]) == 1 and nx not in ("zero", "oh"): out.append(_TENS[t] + _NUMW[nx]); i += 2; continue
            out.append(_TENS[t] + "0"); i += 1; continue
        out.append(_NUMW.get(t, t)); i += 1
    return re.sub(r"(?<=\d) (?=\d)", "", " ".join(out)).lstrip()
_LAST_UREQ = {"target": None, "t": 0.0}
def sdrt_user_start(target):
    # (v173/v177) a double tap (or two devices) used to launch SDRTrunk twice 2 s apart - the second start killed the first. v173 compared
    # against SDRT["target"], which the first request has not set yet when the second arrives; the request itself is remembered now.
    now = time.time()
    if target and target == _LAST_UREQ["target"] and now - _LAST_UREQ["t"] < 6:
        SDRT["auto"] = False; return {"ok": True, "name": SDRT.get("name"), "already": True}
    _LAST_UREQ.update(target=target, t=now)
    SDRT["auto"] = False
    if DMRSCAN["on"] and target != SDRT.get("target"):            # (v152) a listen you chose beats the DMR scan: the scan must not hop away from it 90 s later
        DMRSCAN["on"] = False; dmrscan_persist(); v1.say("DMR scan stopped - you picked a listen")
    SIREN["active"] = False                                        # a listen you chose ends any siren search
    rot = rotation_load()
    if rot.get("auto_stop"): rot["auto_stop"] = 0; rotation_save(rot)       # a listen you started is never auto-stopped
    return sdrt_start(target)
def sdrt_stop(handback=True):
    with SDRT_LOCK: return _sdrt_stop(handback)
def _sdrt_stop(handback=True):
    v1.say("SDRTrunk stopping" + (" (dongle handed back)" if handback else ""))
    try: calls_log_update()
    except Exception: pass
    if _fresh_running():
        subprocess.call(["pkill", "-f", "sdr-trunk"])
        for _ in range(40):                                   # Java takes a few seconds to let go of the dongle
            if not _fresh_running(): break
            time.sleep(0.25)
        else: subprocess.call(["pkill", "-9", "-f", "sdr-trunk"])
        time.sleep(2)
    SDRT.update({"target": None, "name": None, "started": 0, "proc": None, "note": ""})
    try: os.remove(SDRT_FLAG)
    except OSError: pass
    try:                                                      # a slot or siren search that was cut short must not stop the NEXT listen
        rot = rotation_load()
        if rot.get("auto_stop"): rot["auto_stop"] = 0; rotation_save(rot)
        SIREN["active"] = False
    except Exception: pass
    if handback: v1.give_dongle_back(0)
PENDING_SCAN = {"cat": None, "key": None, "t": 0}

# ---------------------------------------------------------------- SDRTrunk call recordings -> the app's Recordings tab
SDRT_REC_DIR = os.path.expanduser("~/SDRTrunk/recordings"); _SDRT_SEEN = set(); _SDRT_T0 = time.time() - 600
def _sdrt_rec_loop():
    """SDRTrunk records every decoded call as a WAV in ~/SDRTrunk/recordings (recorder AUDIO in the playlist). Every 20 s any new,
    finished clip (untouched for 15 s) is copied into the current scan folder's recordings/ as <mhz>_<talkgroup>_<time>.wav so it
    shows in the Recordings tab, plays, and is pruned with the rest. The original stays in ~/SDRTrunk."""
    time.sleep(40)
    while True:
        time.sleep(20)
        try:
            if not os.path.isdir(SDRT_REC_DIR): continue
            dest_dir = os.path.join(v1.newest_scan() or HERE, "recordings"); os.makedirs(dest_dir, exist_ok=True)
            for f in sorted(glob.glob(os.path.join(SDRT_REC_DIR, "*.wav")), key=os.path.getmtime)[-30:]:
                if f in _SDRT_SEEN or time.time() - os.path.getmtime(f) < 15 or os.path.getsize(f) < 8000: continue
                if "baseband" in f.lower() or os.path.basename(f).startswith("RTL") or os.path.getmtime(f) < _SDRT_T0: _SDRT_SEEN.add(f); continue   # raw IQ captures / old files are not calls
                _SDRT_SEEN.add(f)
                base = os.path.basename(f)                               # e.g. 20261104_190212_463312500_463.3125 Bearcom_TO_123_FROM_456.wav
                m = re.search(r"_(\d{9})_", base); mhz = int(m.group(1)) / 1e6 if m else 0.0
                tg = re.search(r"TO_(\d+)", base); who = ("TG" + tg.group(1)) if tg else "call"
                stamp = base[:15] if re.match(r"\d{8}_\d{6}", base) else dt.datetime.now().strftime("%Y%m%d_%H%M%S")
                dest = os.path.join(dest_dir, f"{mhz:.4f}_{who}_{stamp}.wav")
                if not os.path.exists(dest):
                    import shutil; shutil.copy(f, dest); v1.say(f"digital call recorded: {mhz:.4f} {who} -> Recordings tab")
        except Exception as e:
            print("sdrt_rec_loop:", e)
__import__("threading").Thread(target=_sdrt_rec_loop, daemon=True).start()
DIG_LISTEN = {"at": 0.0}        # Digital chip: after one sweep slice of 700/800 MHz, SDRTrunk decodes the strongest site for 5 min
ROTATE_FILE = os.path.join(HERE, "digital_rotation.json"); CALLS_FILE = os.path.join(HERE, "digital_calls.json"); DMR_TG_FILE = os.path.join(HERE, "dmr_talkgroups.json")
def rotation_load():
    try: return json.load(open(ROTATE_FILE))
    except Exception: return {"on": False, "target": "p25:West Simulcast", "minutes": 3, "every": 15, "last": 0, "auto_stop": 0}
def rotation_save(r):
    json.dump(r, open(ROTATE_FILE + ".tmp", "w")); os.replace(ROTATE_FILE + ".tmp", ROTATE_FILE)
def dmr_tgs():
    """{mhz: {"last": "Nov 05 01:20", "last_tg": "2", "tgs": [["2", 5], ["1", 3]]}} from dmr_talkgroups.json (what SDRTrunk decoded per channel)."""
    try: raw = json.load(open(DMR_TG_FILE))
    except Exception: return {}
    out = {}
    try:
        import sdrtrunk_playlist as SP; names = SP.user_dmr_aliases()
    except Exception: names = {}
    for k, e in raw.items():
        tgs = sorted(((t, n) for t, n in e.items() if not t.startswith("_")), key=lambda x: -x[1])
        out[k] = {"last": e.get("_last", ""), "last_tg": e.get("_last_tg", ""), "tgs": tgs[:6], "names": names.get(k, {})}
    return out
def calls_log_update():
    """Persist every decoded call (deduped) so the Digital tab shows what the system carried, even after SDRTrunk stops."""
    try:
        cur = sdrt_calls(60)
        if not cur: return
        try: log = json.load(open(CALLS_FILE))
        except Exception: log = []
        seen = {(c["t"], c["to"], c["from"]) for c in log}
        day = dt.datetime.now().strftime("%b %d ")
        for c in cur:
            if (c["t"], c["to"], c["from"]) in seen: continue
            c = dict(c); c["day"] = day; log.append(c)
        log = log[-4000:]
        json.dump(log, open(CALLS_FILE, "w"))
        # a clear voice call = the site was heard: stamp the bank so Last heard / Activity reflect P25 too
        try:
            import sdrtrunk_playlist as SP
            site = (SDRT["target"] or "").split(":", 1)[1] if (SDRT["target"] or "").startswith("p25:") else None
            cc = next((fr[0] for n, _, fr in SP.P25_SITES if n == site), None)
            if cc and any(not c.get("enc") for c in cur): v1.monitor_heard(cc, f"City of Toronto P25 - {site} CONTROL channel", 5, "")
        except Exception: pass
        # (v143) DMR / NXDN windows: every decoded call stamps ITS channel in the bank (last heard, last call) and the talkgroup is
        # remembered per frequency (dmr_talkgroups.json), so "Scan all DMR" leaves a trail: which channel talked, which TG, when.
        try:
            tgt = SDRT["target"] or ""
            if tgt.startswith(("dmr", "nxdn")):
                new = [c for c in cur if (c["t"], c["to"], c["from"]) not in seen and c.get("mhz") and "call" in c["ev"].lower()]
                if new:
                    try: tgs = json.load(open(DMR_TG_FILE))
                    except Exception: tgs = {}
                    def tgname(to):                                         # "[Other talkgroup (DMR)] (2)" -> "2";  "Roads 3 (153)" stays as is
                        t = re.sub(r"^\[.*?\]\s*", "", to or "").strip()
                        m = re.fullmatch(r"\((\d+)\)", t)
                        return m.group(1) if m else (t or "?")
                    for c in new:
                        k = f"{c['mhz']:.4f}"; tg = tgname(c["to"])
                        e = tgs.setdefault(k, {}); e[tg] = e.get(tg, 0) + 1; tgs[k]["_last"] = dt.datetime.now().strftime("%b %d %H:%M"); tgs[k]["_last_tg"] = tg
                    json.dump(tgs, open(DMR_TG_FILE, "w"))
                    for m in {c["mhz"] for c in new}:
                        nm = (dict(SP.DMR_KNOWN) | SP.dmr_from_data()).get(round(m, 4), "DMR")
                        mine = [c for c in new if c["mhz"] == m]
                        v1.digital_heard(m, nm, "TG " + ", ".join(sorted({tgname(c["to"]) for c in mine})), len(mine), any(c.get("enc") for c in mine))
        except Exception as e:
            v1.say(f"dmr call log: {e!r}")
    except Exception: pass
# ---------------------------------------------------------------- "I hear sirens": go straight to the emergency channels
SIREN = {"active": False, "t0": 0, "idx": 0, "found": "", "msg": ""}
SIREN_SITES = ["West Simulcast", "South Simulcast", "North Simulcast", "East Simulcast"]     # West covers Etobicoke; the others are tried in turn
SIREN_LOG = os.path.join(HERE, "sirens.log")
def siren_press():
    now = time.time()
    try: open(SIREN_LOG, "a").write(time.strftime("%Y-%m-%d %H:%M:%S") + " sirens reported\n")
    except OSError: pass
    SIREN.update(active=True, t0=now, idx=0, found="", last_heard=0, logged=None, msg="listening on the West site (Etobicoke)")
    rot = rotation_load()
    if rot.get("auto_stop"): rot["auto_stop"] = 0; rotation_save(rot)
    tgt = SDRT.get("target") or ""
    if tgt.startswith("p25:") and "West" in tgt and sdrt_running():
        SIREN["msg"] = "already on the West site - watching for calls"
        return {"ok": True, "site": "West Simulcast", "msg": SIREN["msg"]}
    r = sdrt_start("p25:" + SIREN_SITES[0])
    if r.get("error"): SIREN.update(active=False, msg=r["error"]); return r
    return {"ok": True, "site": SIREN_SITES[0], "msg": SIREN["msg"]}
def siren_tick():
    """Every 5 s while SDRTrunk runs. West (Etobicoke) gets 150 s, the other sites 90 s each, then back to West - for 15 min.
    It never leaves a site while a call is in progress or traffic was heard in the last 3 min, and only NAMED Fire / Paramedic
    talkgroups count as a find (a stray unnamed or encrypted call does not end the search)."""
    if not SIREN["active"] or not (SDRT.get("target") or "").startswith("p25:"): return
    now = time.time()
    if now - SIREN["t0"] > 900: SIREN.update(active=False, msg="15 minutes done - still listening on the last site"); return
    calls = [c for c in sdrt_calls(40) if not c.get("enc") and str(c.get("to", "")).startswith("[")]
    if calls:
        c = calls[0]; SIREN["last_heard"] = now
        SIREN.update(found=c.get("to", ""), msg="HEARD " + (c.get("to") or "call") + " at " + c.get("t", "") + " - staying on this site")
        if SIREN.get("logged") != (c.get("t"), c.get("to")):
            SIREN["logged"] = (c.get("t"), c.get("to"))
            try: open(SIREN_LOG, "a").write(time.strftime("%Y-%m-%d %H:%M:%S") + " heard " + (c.get("to") or "") + " on " + (SDRT.get("target") or "") + "\n")
            except OSError: pass
        return
    if now - SIREN.get("last_heard", 0) < 180: return                        # something was just heard here: stay
    live = [c for c in sdrt_calls(5) if not c.get("enc")]
    if live and live[0].get("dur") in ("", None, "0"): return                  # a call is still open
    dwell = 150 if "West" in (SDRT.get("target") or "") else 90
    if now - SDRT["started"] > dwell:
        SIREN["idx"] = (SIREN["idx"] + 1) % len(SIREN_SITES)
        site = SIREN_SITES[SIREN["idx"]]; SIREN["msg"] = "quiet here - trying the " + site
        sdrt_start("p25:" + site)
def siren_status():
    try: tail = open(SIREN_LOG).read().strip().splitlines()[-5:]
    except OSError: tail = []
    return dict(SIREN, site=SIREN_SITES[min(SIREN["idx"], len(SIREN_SITES) - 1)], log=tail, secs=int(time.time() - SIREN["t0"]) if SIREN["t0"] else 0)

# ---------------------------------------------------------------- Map tab
MAP_FILE = os.path.join(HERE, "map_home.json")
MAP_LANDMARKS = [   # approximate, well-known transmitter / airport locations only - never invented coverage
    {"name": "Toronto Pearson (YYZ) - tower, ATIS, arrivals", "lat": 43.6777, "lon": -79.6248, "kind": "airport"},
    {"name": "Billy Bishop (YTZ)", "lat": 43.6275, "lon": -79.3962, "kind": "airport"},
    {"name": "CN Tower - 463.3125 Bearcom DMR, many repeaters", "lat": 43.6426, "lon": -79.3871, "kind": "tower"},
]
def map_data():
    try: home = json.load(open(MAP_FILE))
    except Exception: home = {"lat": HOME_LL[0], "lon": HOME_LL[1], "set": False}
    return {"home": home, "landmarks": MAP_LANDMARKS, "sirens": siren_log_pins()}
def siren_log_pins():
    try: return open(SIREN_LOG).read().strip().splitlines()[-30:]
    except OSError: return []

# ---------------------------------------------------------------- what each 700 MHz P25 voice channel actually carries (from SDRTrunk's call logs)
_P25F = {"t": 0, "sig": None, "data": {}}
def p25_freq_stats():
    """Every call SDRTrunk ever logged carries the voice frequency it used: roll that up per frequency -> which talkgroups
    (Fire / EMS / unnamed), how many calls, how much was encrypted (Police), last used, and which site's control channel heard it."""
    import csv, collections
    files = sorted(glob.glob(os.path.expanduser("~/SDRTrunk/event_logs/*call_events*.log")))
    sig = (len(files), max([os.path.getmtime(f) for f in files] or [0]))
    if _P25F["sig"] is not None and (time.time() - _P25F["t"] < 90 or (_P25F["sig"] == sig and time.time() - _P25F["t"] < 600)): return _P25F["data"]   # (v153) was: re-parse ~36 MB of call logs every time the live log grew = a pinned CPU core, clicking audio
    agg = {}
    for f in files[-400:]:
        m = re.search(r"Hz_(.+?)_call_events", os.path.basename(f)); ms = re.search(r"(West|South|North|East) Simulcast|PATH|Metrolinx|City_of_Toronto_services|Billy_Bishop|DMR", m.group(1) if m else ""); site = (ms.group(0) if ms else "").replace("_", " ")
        try:
            with open(f, errors="ignore") as fh:
                for r in csv.reader(l for l in fh if l.startswith('"')):
                    if len(r) < 10: continue
                    ev = r[3]
                    if "call" not in ev.lower() or "data" in ev.lower(): continue
                    try: fr = round(float(r[7]), 4)
                    except ValueError: continue
                    if not (764 <= fr <= 776 or 851 <= fr <= 870 or 410 <= fr <= 416 or 936 <= fr <= 940 or 453 <= fr <= 462): continue
                    to = r[5].strip(); enc = "ncrypted" in ev or "ENCRYPT" in r[9]
                    nm = re.sub(r"\s*\(\d+\)\s*$", "", to).strip("[] ")
                    a = agg.setdefault(fr, {"calls": 0, "enc": 0, "groups": collections.Counter(), "last": "", "sites": collections.Counter()})
                    a["calls"] += 1; a["enc"] += 1 if enc else 0
                    if not enc and nm: a["groups"][nm] += 1
                    if r[0] >= a["last"]: a["last"] = r[0]
                    if not enc and nm and r[0] >= a.get("last_clear", ""): a["last_clear"] = r[0]; a["last_tg"] = nm      # headline = last call you can HEAR
                    a["sites"][site] += 1
        except OSError: pass
    out = {}
    for fr, a in agg.items():
        out[fr] = {"calls": a["calls"], "enc": a["enc"], "top": a["groups"].most_common(4), "last": a["last"], "last_tg": a.get("last_tg", ""), "last_clear": a.get("last_clear", ""),
                   "site": (a["sites"].most_common(1) or [("", 0)])[0][0]}
    _P25F.update(t=time.time(), sig=sig, data=out); return out
def p25_lookup(mhz):
    """The sweep reports ~25 kHz bins (772.88) while SDRTrunk logs the exact 12.5 kHz channel (772.88125): take the nearest within half a bin."""
    st = p25_freq_stats(); best = None
    for f, v in st.items():
        d = abs(f - mhz)
        if d <= 0.0135 and (best is None or d < best[0]): best = (d, v)
    return best[1] if best else None
def trunk_system(mhz):
    if 764 <= mhz <= 776 or 851 <= mhz <= 870: return "Toronto Fire + Paramedics", "P25 Phase II - Toronto Fire / EMS"
    if 410 <= mhz <= 416: return "Metrolinx / GO Transit", "P25 Phase II - Metrolinx / GO"
    if 936 <= mhz <= 940: return "City of Toronto services", "DMR trunked - City services"
    return "", ""
def call_stamp(raw):
    """'2026:11:04:20:04:31' (SDRTrunk) -> (unix ts, 'HH:MM')"""
    try:
        t = dt.datetime.strptime(raw[:19].replace(":", "-", 2).replace(":", " ", 1), "%Y-%m-%d %H:%M:%S"); return t.timestamp(), t.strftime("%H:%M")
    except Exception: return 0, ""
def p25_voice_name(mhz):
    st = p25_lookup(mhz); sysn, _ = trunk_system(mhz); sysn = sysn or "City of Toronto P25"
    if not st: return f"{sysn} - voice channel (no call decoded on it yet)"
    last = st.get("last_tg") or (st["top"][0][0] if st["top"] else "")
    _, hm = call_stamp(st.get("last_clear") or st.get("last", ""))
    others = [n for n, _ in st["top"][:3] if n != last]
    if not last: return f"{sysn} - only encrypted calls here so far (Police)" + (f", last at {hm}" if hm else "")
    return f"{sysn} - last call {last}" + (f" at {hm}" if hm else "") + (f" (also {', '.join(others)})" if others else "") + (" + Police (encrypted)" if st["enc"] else "")
def p25_voice_info(mhz):
    st = p25_lookup(mhz); lines = ["Toronto P25 Phase II is a trunked system: this is one of its shared voice channels. The system assigns each call to whichever channel is free, so a frequency does not belong to one agency - the list below is what SDRTrunk has actually decoded on it."]
    if not st: return lines + ["Nothing decoded on this frequency yet. Play West or South from the Digital tab for a few minutes and it fills in."]
    clear = st["calls"] - st["enc"]
    lines.append(f"{st['calls']} calls decoded here: {clear} clear (Fire/Paramedics) and {st['enc']} encrypted (Police, silent).")
    if st["top"]: lines.append("Talkgroups heard in the clear: " + ", ".join(f"{n} x{c}" for n, c in st["top"]))
    if st["site"]: lines.append("Decoded through the " + st["site"] + " control channel.")
    if st["last"]: lines.append("Last call on it: " + st["last"].replace(":", "-", 2).replace(":", " ", 1)[:19])
    return lines

# ---- (v139) auto-scan when idle: a scanner radio scans by itself. Off by default (scanner.json "autoscan": bank). When nobody has
# touched the player for AUTO_IDLE s and nothing else holds the dongle, the Monitor starts on the chosen bank through the Mac speakers;
# every SCAN_MIN minutes it yields the dongle to the discovery sweep for SWEEP_MIN minutes, then resumes. A manual Stop parks it 30 min.
AUTO = {"started": 0.0, "ours": False, "paused_until": 0.0, "yield_until": 0.0}
AUTO_IDLE = 90
def autoscan_cfg():
    try: c = json.load(open(os.path.join(HERE, "scanner.json")))
    except Exception: c = {}
    return {"bank": c.get("autoscan", "") or "", "scan_min": max(2, int(c.get("scan_min", 10))), "sweep_min": max(1, int(c.get("sweep_min", 2)))}
def autoscan_set(bank):
    p = os.path.join(HERE, "scanner.json")
    try: c = json.load(open(p))
    except Exception: c = {}
    c["autoscan"] = bank or ""
    try: json.dump(c, open(p, "w"))
    except Exception: pass
    AUTO["paused_until"] = 0.0
    v1.say(f"auto-scan when idle: {bank or 'off'}")
    return autoscan_cfg()
def autoscan_tick(busy):
    """Called every 5 s from sweep_when_idle. Returns True when it did something this tick."""
    cfg = autoscan_cfg(); now = time.time()
    with v1._lock: ses = v1._player.get("session"); since = v1._player.get("since") or 0
    if not cfg["bank"]:
        AUTO["ours"] = False; return False
    if ses is not None:
        if AUTO["ours"] and getattr(ses, "groups", None) is not None and now - AUTO["started"] > cfg["scan_min"] * 60:
            v1.stop_player(); AUTO["ours"] = False; AUTO["yield_until"] = now + cfg["sweep_min"] * 60
            v1.say(f"auto-scan: yielding the dongle to the sweep for {cfg['sweep_min']} min")
            if not v1.scan_running(): v1.start_scan("all", "All", "auto-scan yielding to the sweep")
            return True
        return False
    AUTO["ours"] = False
    if busy or now < AUTO["paused_until"] or now < AUTO["yield_until"]: return False
    if now - v1.LAST_USER_PLAY[0] < AUTO_IDLE or v1.USER_WANTS.is_set(): return False
    if os.path.exists(SDRT_FLAG) or SDRT["target"] or DMRSCAN["on"]: return False
    ok, msg = v1.play_mac(1.0, "monitor", None, "Auto-scan: " + cfg["bank"], cat=cfg["bank"])
    if ok: AUTO.update(started=now, ours=True); v1.say(f"auto-scan: Monitor on {cfg['bank']} (idle {AUTO_IDLE} s)")
    else: AUTO["paused_until"] = now + 300; v1.say(f"auto-scan could not start: {msg}")
    return True

def sweep_when_idle():
    """A chip pressed while listening queues the sweep; it starts once nobody has listened for 20 s.
    Self-healing: a stale SDRTrunk hand-off flag (SDRTrunk not running) is cleared, and if no sweep is running while
    nobody listens for 60 s, the full sweep is started again - the app never sits idle."""
    idle_since = None
    while True:
        time.sleep(5)
        try:
            if os.path.exists(SDRT_FLAG) and not SDRT["target"] and not sdrt_running():
                try: os.remove(SDRT_FLAG)
                except OSError: pass
                v1.give_dongle_back(0)
            if SDRT["target"]:
                sdrt_state()                                                   # notices a closed/dead SDRTrunk even when no browser is polling
            dmrscan_tick()
            if SDRT["target"]:
                calls_log_update(); siren_tick()
                rot = rotation_load()
                if rot.get("auto_stop") and time.time() > rot["auto_stop"] and not DMRSCAN["on"]:        # rotation slot over: back to the sweep
                    rot["auto_stop"] = 0; rotation_save(rot); sdrt_stop(); continue
            with v1._lock: listening = v1._player.get("session") is not None
            busy = listening or SDRT["target"] or os.path.exists(SDRT_FLAG) or v1.USER_WANTS.is_set() or time.time() - v1.LAST_USER_PLAY[0] < 20
            if os.path.exists(v1.RESTART_REQ) and SDRT["target"] and SDRT.get("auto") and not DMRSCAN["on"]:   # (v140) an idle restart is waiting: an
                v1.say("restart pending: ending the automatic SDRTrunk session"); sdrt_stop(); continue              # automatic session is not a person listening
            if autoscan_tick(busy): idle_since = None; continue
            if busy: idle_since = None; continue
            rot = rotation_load()
            if DIG_LISTEN["at"] and time.time() > DIG_LISTEN["at"] and v1.BG_STATE.get("what", "") == "":
                DIG_LISTEN["at"] = 0; rot["auto_stop"] = time.time() + 5 * 60; rotation_save(rot)
                cc = digital_carriers(); site = "West Simulcast"
                for c in cc:                                                       # strongest carrier that belongs to a known site wins
                    lk = c.get("likely") or ""
                    if "control channel - " in lk: site = lk.split("control channel - ", 1)[1]; break
                r = sdrt_start("p25:" + site)
                if r.get("ok"): SDRT["auto"] = True
                if r.get("error"): rot["auto_stop"] = 0; rotation_save(rot); v1.say("digital listen: " + r["error"])
                continue
            if rot.get("on") and time.time() - rot.get("last", 0) > rot.get("every", 15) * 60 and v1.BG_STATE.get("what", "") == "":
                rot["last"] = time.time(); rot["auto_stop"] = time.time() + rot.get("minutes", 3) * 60; rotation_save(rot)
                r = sdrt_start(rot.get("target", "p25:West Simulcast"))
                if r.get("ok"): SDRT["auto"] = True
                if r.get("error"): rot["on"] = False; rot["auto_stop"] = 0; rotation_save(rot)
                continue
            if PENDING_SCAN["cat"]:
                cat, key = PENDING_SCAN["cat"], PENDING_SCAN["key"]; PENDING_SCAN.update(cat=None, key=None)
                v1.start_scan(key, cat, "queued chip press, player now idle")
                if cat == "Digital": DIG_LISTEN["at"] = time.time() + 6.5 * 60; v1.say("digital: SDRTrunk will decode the strongest site for 5 min once the 700/800 MHz slices are done")
                continue
            if v1.scan_running(): idle_since = None; continue
            idle_since = idle_since or time.time()
            if time.time() - idle_since > 60 and v1.BG_STATE.get("what", "") == "":
                idle_since = None; v1.start_scan("all", "All", "no sweep running and idle for 60 s")
        except Exception as e:
            print("sweep_when_idle:", e)
def _hang_watchdog():
    """(v150) The page died twice today with the server alive but every request hanging. Every 20 s fetch /meter from inside the
    process; three misses in a row = dump all thread stacks to diag/threads.txt and re-exec (the launcher loop would never know)."""
    import urllib.request
    misses = 0; time.sleep(30)
    while True:
        time.sleep(20)
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{v1.PORT}/meter", timeout=15).read(); misses = 0
        except Exception:
            misses += 1
            if misses >= 3:
                try:
                    _FH.write(f"\n===== {dt.datetime.now():%Y-%m-%d %H:%M:%S} hang watchdog: /meter unanswered 3x - stacks =====\n"); _FH.flush()
                    faulthandler.dump_traceback(file=_FH, all_threads=True); _FH.flush()
                except Exception: pass
                try: v1.say("hang watchdog: server not answering for 60 s - restarting (stacks in diag/threads.txt)")
                except Exception: pass
                os.execv(sys.executable, [sys.executable] + sys.argv)
__import__("threading").Thread(target=_hang_watchdog, daemon=True).start()
def _dmrscan_boot():
    time.sleep(6)                                                       # let the server come up first
    try: dmrscan_resume_after_restart()
    except Exception: pass
__import__("threading").Thread(target=_dmrscan_boot, daemon=True).start()
__import__("threading").Thread(target=sweep_when_idle, daemon=True).start()

def sdrt_watcher():
    """sdrt_request.txt: 'p25:West Simulcast' / 'dmr:463.3125' / 'stop' -> diag/sdrt.json (remote validation hook, same path as the ▶)."""
    req = os.path.join(HERE, "sdrt_request.txt")
    while True:
        time.sleep(2)
        try:
            if _AUTO_RETRY["pending"]:                            # (v170) tuner_problem() queued a same-target relaunch - dongle confirmed still on USB
                _AUTO_RETRY["pending"] = False
                tgt, was_auto = _AUTO_RETRY["target"], SDRT.get("auto")
                if tgt:
                    v1.say("SDRTrunk lost the tuner claim right after starting (dongle still on USB) - retrying automatically")
                    try:
                        with open(os.path.join(HERE, "README.txt"), "a") as f:
                            f.write(f"\n{dt.datetime.now():%Y-%m-%d %H:%M} - SDRTrunk hit a transient USB claim race right after "
                                    f"starting {SDRT.get('name')} (dongle confirmed present on USB via ioreg) - auto-retried the "
                                    f"launch instead of asking for a physical unplug/replug.\n")
                    except Exception: pass
                    res = sdrt_start(tgt)
                    if res.get("ok") and was_auto: SDRT["auto"] = True
            if not os.path.exists(req): continue
            what = open(req).read().strip(); os.remove(req)
            if what == "stop":
                if SDRT["target"] and not SDRT.get("auto"): res = {"error": "a person started this SDRTrunk session - not stopped", "skipped": True}
                else: sdrt_stop(); res = {"stopped": True}
            elif what == "state":
                res = {"state": sdrt_state(), "running": sdrt_running(), "binary": sdrt_binary(), "playlist": sdrt_playlist_path()}
                try:
                    logs = sorted(glob.glob(os.path.expanduser("~/SDRTrunk/logs/*.log")), key=os.path.getmtime, reverse=True)
                    if logs: res["applog"] = open(logs[0], errors="ignore").readlines()[-120:]
                    res["prefs"] = subprocess.run(["defaults", "read", "com.apple.java.util.prefs"], capture_output=True, text=True, timeout=5).stdout[-3000:]
                    pl = sdrt_playlist_path(); res["playlist_head"] = open(pl, errors="ignore").read()[:400] if os.path.exists(pl) else None
                    res["playlist_dir"] = os.listdir(os.path.dirname(pl))
                    res["event_logs"] = sorted(glob.glob(os.path.expanduser("~/SDRTrunk/event_logs/*")))[-5:]
                except Exception as e: res["diag_err"] = repr(e)
            else:
                with v1._lock: listening = v1._player.get("session") is not None
                user_busy = listening or v1.USER_WANTS.is_set() or time.time() - v1.LAST_USER_PLAY[0] < 600 or (SDRT["target"] and not SDRT.get("auto"))
                if user_busy: res = {"error": "user is listening - automatic SDRTrunk sessions never take the dongle from a person", "skipped": True}
                else:
                    res = sdrt_start(what)
                    if res.get("ok"): SDRT["auto"] = True                 # started by the hook (overnight automation), not by a person
            res["state"] = sdrt_state(); res["t"] = time.time()
            os.makedirs(os.path.join(HERE, "diag"), exist_ok=True)
            json.dump(res, open(os.path.join(HERE, "diag", "sdrt.json"), "w"), indent=1)
        except Exception as e:
            try: json.dump({"error": repr(e)}, open(os.path.join(HERE, "diag", "sdrt.json"), "w"))
            except Exception: pass
def sdrt_restore():
    """After a dashboard restart: if SDRTrunk is still decoding, keep showing it as the player (and keep the sweep parked)."""
    try:
        if os.path.exists(SDRT_FLAG) and sdrt_running():
            j = json.loads(open(SDRT_FLAG).read())
            SDRT.update(target=j.get("target"), name=j.get("name"), started=j.get("t", time.time()), note="")
    except Exception: pass
sdrt_restore()
__import__("threading").Thread(target=sdrt_watcher, daemon=True).start()

SNOWTO_DEDUPE = os.path.join(HERE, "_state", "snowto_last.txt")
SNOW_CODES = {71, 73, 75, 77, 85, 86}      # WMO weather codes: snow fall / snow grains / snow showers
def _snow_recently_handled():
    try: return time.time() - float(open(SNOWTO_DEDUPE).read().strip()) < 4 * 3600
    except Exception: return False
def snow_watch():
    """(v169) Folded in from the external scheduled task ('SDR Scanner - snow/plow auto-listen'): every ~3h, check
    Open-Meteo for snow present or imminent (within 3h) in Etobicoke, and if so try to start the ROADS Toronto DMR
    trunk - same remote-hook mechanism and user-busy safety check as sdrt_watcher, so a live listener is never
    preempted. 4h dedupe so one storm doesn't retrigger every cycle. A bad/slow/failed weather lookup never crashes
    the thread or the app - this whole function is best-effort."""
    time.sleep(30)                                                     # let the server come up first
    while True:
        try:
            if not _snow_recently_handled():
                import urllib.request
                url = (f"https://api.open-meteo.com/v1/forecast?latitude={HOME_LL[0]:.3f}&longitude={HOME_LL[1]:.3f}"
                       "&current=weather_code&hourly=weather_code&forecast_hours=3&timezone=America%2FToronto")
                with urllib.request.urlopen(url, timeout=10) as r: data = json.loads(r.read())
                cur_code = (data.get("current") or {}).get("weather_code")
                hr_codes = (data.get("hourly") or {}).get("weather_code") or []
                snowing = cur_code in SNOW_CODES or any(c in SNOW_CODES for c in hr_codes)
                if snowing:
                    with v1._lock: listening = v1._player.get("session") is not None
                    user_busy = listening or v1.USER_WANTS.is_set() or time.time() - v1.LAST_USER_PLAY[0] < 600 or (SDRT["target"] and not SDRT.get("auto"))
                    if not user_busy:
                        os.makedirs(os.path.dirname(SNOWTO_DEDUPE), exist_ok=True)
                        res = sdrt_start("dmrt:939.1375")
                        if res.get("ok"): SDRT["auto"] = True
                        open(SNOWTO_DEDUPE, "w").write(str(time.time()))
                        try:
                            with open(os.path.join(HERE, "README.txt"), "a") as f:
                                f.write(f"\n{dt.datetime.now():%Y-%m-%d %H:%M} - snow-watch (in-app): snow detected/imminent in Etobicoke - "
                                        f"{'started' if res.get('ok') else 'failed to start'} ROADS Toronto DMR"
                                        f"{(' (' + str(res.get('error')) + ')') if res.get('error') else ''}.\n")
                        except Exception: pass
                        try: v1.say(f"snow-watch: {'started' if res.get('ok') else 'skipped/failed'} ROADS Toronto DMR (snow in Etobicoke)")
                        except Exception: pass
                    # busy: stay silent and try again next cycle, exactly like the external trigger did
        except Exception as e:
            try: v1.say(f"snow-watch check failed: {e!r}")
            except Exception: pass
        time.sleep(3 * 3600)
__import__("threading").Thread(target=snow_watch, daemon=True).start()

_SC_CALLS = {"t": 0.0, "n": 0, "v": []}
def sdrt_calls(n=12):
    if time.time() - _SC_CALLS["t"] < 1.5 and _SC_CALLS["n"] >= n: return _SC_CALLS["v"][:n]
    v = _sdrt_calls(max(n, 60)); _SC_CALLS.update(t=time.time(), n=max(n, 60), v=v); return v[:n]
def _sdrt_calls(n=12):
    """Latest decoded calls from SDRTrunk's call-event logs (~/SDRTrunk/event_logs/*_call_events.log) since this listen started."""
    import csv
    out = []
    try:
        files = sorted(glob.glob(os.path.expanduser("~/SDRTrunk/event_logs/*call_events*.log")), key=os.path.getmtime, reverse=True)[:12]   # a DMR window = one log per channel
        for f in files:
            if os.path.getmtime(f) < SDRT["started"] - 5: continue
            mf = re.search(r"_(\d{8,10})_Hz_", os.path.basename(f)); fmhz = round(int(mf.group(1)) / 1e6, 4) if mf else None   # (v143) which channel the log is for
            with open(f, errors="ignore") as fh: lines = fh.readlines()[-80:]
            for r in csv.reader(l for l in lines if l.startswith('"')):
                if len(r) < 10: continue
                ev = r[3]; to = r[5].strip(); dur = r[1]
                if "call" not in ev.lower() or "data" in ev.lower(): continue     # only voice calls: no registrations, affiliations, data
                enc = "ncrypted" in ev or "ENCRYPT" in r[9]
                if enc and not to.startswith("["): to = f"Police (encrypted) {to}".strip()
                try: fm = round(float(r[7]) / 1e6, 4) if r[7] else fmhz
                except ValueError: fm = fmhz
                out.append({"t": r[0][-8:] if len(r[0]) >= 8 else r[0], "ev": ev, "to": to, "from": r[4], "dur": dur, "det": r[9][:60], "enc": enc, "mhz": fm or fmhz,
                            "ts": r[6] if len(r) > 6 else ""})
        # SDRTrunk re-logs a call as it grows: keep the last line per (start time, talkgroup, from)
        last = {}
        for c in out: last[(c["t"], c["to"], c["from"])] = c
        out = sorted(last.values(), key=lambda x: x["t"], reverse=True)
    except Exception: pass
    return out[:n]
_TP = {"t": 0, "v": None}
_AUTO_RETRY = {"target": None, "pending": False, "times": []}      # (v170) see tuner_problem()
def tuner_problem(since):
    """(v161) Plain-English reason when SDRTrunk has no radio: it logs 'No Tuner Available' / 'Transfer Buffers Exhausted' / 'Unable to start tuner' when the dongle is missing or drops off USB.
    (v170) Confirmed empirically: when the dongle IS still present on the Mac's USB (ioreg sees it) but SDRTrunk just
    lost a claim race right after a stop->start hand-off, a fresh relaunch alone clears it - no physical unplug/replug
    needed. So that case queues one automatic retry (sdrt_watcher picks it up) instead of immediately telling Matt to
    touch the dongle; only a dongle that's genuinely missing from USB, or that keeps failing after retries (capped at
    2 per rolling 5-minute window so a truly broken dongle doesn't retry forever), gets the manual-action message."""
    if time.time() - _TP["t"] < 3 and _TP.get("s") == since: return _TP["v"]
    prob = None
    try:
        p = os.path.expanduser("~/SDRTrunk/logs/sdrtrunk_app.log")
        with open(p, "rb") as f:
            f.seek(0, 2); n = f.tell(); f.seek(max(0, n - 40000)); tail = f.read().decode("utf-8", "replace").splitlines()
        bad = None
        for ln in tail:
            if not any(k in ln for k in ("No Tuner Available", "Buffers Exhausted", "Unable to start tuner", "couldn't discover USB")): continue
            try: ts = time.mktime(time.strptime(ln[:15], "%Y%m%d %H%M%S"))
            except Exception: continue
            if ts >= since - 3: bad = ln
        if bad:
            try: usb = subprocess.run(["ioreg", "-p", "IOUSB", "-w0"], capture_output=True, text=True, timeout=4).stdout
            except Exception: usb = ""
            if "NESDR" in usb or "RTL28" in usb:
                now = time.time()
                _AUTO_RETRY["times"] = [t for t in _AUTO_RETRY["times"] if now - t < 300]
                if len(_AUTO_RETRY["times"]) < 2 and not _AUTO_RETRY["pending"]:
                    _AUTO_RETRY["times"].append(now); _AUTO_RETRY.update(target=SDRT["target"], pending=True)
                    prob = None
                else:
                    prob = "SDRTrunk lost the dongle (USB error). Unplug and replug it - ideally straight into a port on the Mac, not the dock."
            else:
                prob = "The dongle is not on the Mac's USB. Unplug and replug it - ideally straight into a port on the Mac, not the dock."
    except Exception: prob = None
    _TP.update(t=time.time(), v=prob, s=since); return prob

def sdrt_state():
    if not SDRT["target"]: return None
    alive = sdrt_running(); el = int(time.time() - SDRT["started"])
    if not alive and el > 15:                                           # it quit (or was closed) - hand the dongle back
        if SDRT_LOCK.acquire(blocking=False):                           # but never while a start is still in progress
            try: sdrt_stop()
            finally: SDRT_LOCK.release()
        return None
    note = "starting SDRTrunk (a few seconds)" if el < 6 else ("decoding - calls show by talkgroup in the SDRTrunk window; audio comes out of the Mac" if alive else "SDRTrunk closed")
    stream_live = icecast_live() if el > 8 else False
    return {"engine": "sdrtrunk", "target": SDRT["target"], "name": SDRT["name"], "secs": el, "alive": alive, "stream": stream_live, "note": note, "calls": sdrt_calls(), "problem": (tuner_problem(SDRT["started"]) if el > 3 else None)}

def talkgroups():
    """Every talkgroup heard on the P25 system, rolled up from the call log: who, how many calls, talk time, last heard,
    and a 12-slot activity strip (last 60 min). Group = Fire / Paramedics / Police (encrypted) / Other."""
    try: log = json.load(open(CALLS_FILE))
    except Exception: log = []
    now = dt.datetime.now(); agg = {}
    for c in log:
        to = c.get("to") or ""; m = re.search(r"\((\d+)\)\s*$", to); tid = m.group(1) if m else ""
        nm = re.sub(r"\s*\(\d+\)\s*$", "", to).strip("[] ") or ("talkgroup " + tid)
        if c.get("enc") or nm.lower().startswith("police"): grp = "Police"
        elif re.search(r"\bEMS\b|paramedic|medic", nm, re.I): grp = "Paramedics"
        elif re.search(r"TFS|fire|rescue|tac \d", nm, re.I): grp = "Fire"
        else: grp = "Other"
        a = agg.setdefault(tid or nm, {"id": tid, "name": nm if grp != "Police" else "Police (encrypted)", "group": grp, "calls": 0, "secs": 0.0, "last": "", "enc": bool(c.get("enc")), "strip": [0] * 12, "units": set(), "solo": (solo_load()["tg"] == int(tid)) if tid.isdigit() else False})
        a["calls"] += 1
        try: a["secs"] += float(c.get("dur") or 0) / 1000
        except ValueError: pass
        stamp = (c.get("day") or "") + (c.get("t") or ""); a["last"] = max(a["last"], stamp)
        if c.get("from"): a["units"].add(c["from"])
        try:
            t = dt.datetime.strptime(str(now.year) + " " + stamp.strip(), "%Y %b %d %H:%M:%S"); age = (now - t).total_seconds() / 60
            if 0 <= age < 60: a["strip"][11 - int(age // 5)] += 1
        except Exception: pass
    clips = {}                                                    # newest imported SDRTrunk clip per talkgroup (<mhz>_TG<id>_<stamp>.wav)
    for f in sorted(glob.glob(os.path.join(HERE, "scan_*", "recordings", "*_TG*_*.wav")), key=os.path.getmtime):
        m = re.search(r"_TG(\d+)_", os.path.basename(f))
        if m: clips[m.group(1)] = os.path.basename(f)
    out = []
    for a in agg.values():
        a["units"] = len(a["units"]); a["secs"] = round(a["secs"], 1); a["clip"] = clips.get(str(a.get("id") or ""), ""); out.append(a)
    order = {"Fire": 0, "Paramedics": 1, "Police": 2, "Other": 3}
    return sorted(out, key=lambda a: (order[a["group"]], -a["calls"]))

_UNITS = {"t": 0, "sig": None, "data": []}
def units_roster(limit=80):
    """Radio IDs heard calling, from every SDRTrunk call-event log: which talkgroups each unit uses, how often, last time.
    (A P25 radio ID is a unit/portable, not a person's name; the pattern of talkgroups says what it is - a pumper, a medic, a yard tug.)"""
    import csv, collections
    files = sorted(glob.glob(os.path.expanduser("~/SDRTrunk/event_logs/*call_events*.log")))
    sig = (len(files), max([os.path.getmtime(f) for f in files] or [0]))
    if _UNITS["sig"] is not None and (time.time() - _UNITS["t"] < 90 or (_UNITS["sig"] == sig and time.time() - _UNITS["t"] < 600)): return _UNITS["data"]
    agg = {}
    for f in files[-400:]:
        sysn = "Metrolinx" if "Metrolinx" in f else ("City services" if "City_of_Toronto_services" in f else ("Billy Bishop" if "Billy_Bishop" in f else "Toronto Fire/EMS"))
        try:
            with open(f, errors="ignore") as fh:
                seen = set()
                for r in csv.reader(l for l in fh if l.startswith('"')):
                    if len(r) < 10 or "call" not in r[3].lower() or "data" in r[3].lower(): continue
                    frm = r[4].strip(); to = re.sub(r"\s*\(\d+\)\s*$", "", r[5].strip()).strip("[] ")
                    if not frm or not frm.isdigit(): continue
                    key = (r[0], frm, to)
                    if key in seen: continue                                 # SDRTrunk re-logs a growing call
                    seen.add(key)
                    enc = "ncrypted" in r[3] or "ENCRYPT" in r[9]
                    a = agg.setdefault((sysn, frm), {"system": sysn, "unit": frm, "calls": 0, "tgs": collections.Counter(), "last": "", "first": r[0], "enc": 0})
                    a["calls"] += 1; a["enc"] += 1 if enc else 0
                    if not enc and to: a["tgs"][to] += 1
                    a["last"] = max(a["last"], r[0]); a["first"] = min(a["first"], r[0])
        except OSError: pass
    out = []
    for a in agg.values():
        top = a["tgs"].most_common(3)
        out.append({"system": a["system"], "unit": a["unit"], "calls": a["calls"], "enc": a["enc"], "tgs": [f"{n} x{c}" for n, c in top],
                    "home": top[0][0] if top else ("Police (encrypted)" if a["enc"] else ""), "last": call_stamp(a["last"])[1], "last_raw": a["last"], "first": call_stamp(a["first"])[1]})
    out.sort(key=lambda u: u["last_raw"], reverse=True)
    _UNITS.update(t=time.time(), sig=sig, data=out[:limit]); return _UNITS["data"]

_AC = {"t": 0.0, "data": None, "routes": {}, "src": ""}
AIR_SOURCE = os.path.join(HERE, "aircraft_source.txt")       # (v130) URL of Matt's own ADS-B receiver (dump1090/readsb/tar1090 aircraft.json)
FR24_KEY = os.path.join(HERE, "fr24_api_key.txt")            # (v130) optional official Flightradar24 API token (Bearer)
def _home_ll():
    """(v174) Where the receiver is: home_location.json ({"lat":..,"lon":..}, kept out of git) or downtown Toronto."""
    try: j = json.load(open(os.path.join(HERE, "home_location.json"))); return (float(j["lat"]), float(j["lon"]))
    except Exception: return (43.6532, -79.3832)
HOME_LL = _home_ll()
AIR_RADIUS_NM = 60

def _geo(lat, lon):
    import math
    dlat = math.radians(lat - HOME_LL[0]); dlon = math.radians(lon - HOME_LL[1])
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(HOME_LL[0])) * math.cos(math.radians(lat)) * math.sin(dlon / 2) ** 2
    km = 2 * 6371 * math.asin(math.sqrt(a))
    brg = (math.degrees(math.atan2(math.sin(dlon) * math.cos(math.radians(lat)), math.cos(math.radians(HOME_LL[0])) * math.sin(math.radians(lat)) - math.sin(math.radians(HOME_LL[0])) * math.cos(math.radians(lat)) * math.cos(dlon))) + 360) % 360
    return round(km, 1), round(brg)

def _fetch_json(url, headers=None, data=None, timeout=8):
    import urllib.request
    req = urllib.request.Request(url, data=data, headers=dict({"Accept": "application/json", "User-Agent": "SDR-Scanner-dashboard/1.0 (home scanner; Toronto)"}, **(headers or {})))
    with urllib.request.urlopen(req, timeout=timeout) as r: return json.load(r)

def _from_readsb(j):
    """dump1090 / readsb / tar1090 / adsb.fi / adsb.lol all speak the same aircraft.json dialect - one parser for Matt's own receiver and the public feeds."""
    out = []
    for a in j.get("aircraft") or j.get("ac") or []:
        lat, lon = a.get("lat"), a.get("lon")
        if lat is None or lon is None: continue
        alt = a.get("alt_baro") if a.get("alt_baro") not in (None, "ground") else a.get("alt_geom")
        on_ground = a.get("alt_baro") == "ground" or (alt is not None and alt < 50 and (a.get("gs") or 0) < 40)
        km, brg = _geo(lat, lon)
        out.append({"icao": a.get("hex", ""), "callsign": (a.get("flight") or "").strip(), "reg": a.get("r", ""), "type": a.get("t", ""), "category": a.get("category", ""),
                    "desc": a.get("desc", "") or "", "operator": a.get("ownOp", "") or "", "lat": lat, "lon": lon, "alt_ft": int(alt) if isinstance(alt, (int, float)) else None,
                    "on_ground": bool(on_ground), "speed_kt": round(a.get("gs") or 0), "track": round(a.get("track") or a.get("true_heading") or 0),
                    "vrate": a.get("baro_rate") or a.get("geom_rate"), "squawk": a.get("squawk", ""), "emergency": (a.get("emergency") or "none") != "none" or str(a.get("squawk", "")) in ("7500", "7600", "7700"),
                    "km": km, "bearing": brg, "seen": a.get("seen")})
    return out

def _from_opensky(j):
    out = []
    for st in j.get("states") or []:
        lat, lon = st[6], st[5]
        if lat is None or lon is None: continue
        km, brg = _geo(lat, lon); alt = st[7] or st[13]
        out.append({"icao": st[0], "callsign": (st[1] or "").strip(), "reg": "", "type": "", "desc": "", "operator": "", "lat": lat, "lon": lon,
                    "alt_ft": round(alt * 3.281) if alt else None, "on_ground": bool(st[8]), "speed_kt": round((st[9] or 0) * 1.944), "track": round(st[10] or 0),
                    "vrate": st[11], "squawk": st[14] or "", "emergency": str(st[14]) in ("7500", "7600", "7700"), "km": km, "bearing": brg, "seen": None})
    return out

def _from_fr24(j):
    out = []
    for a in j.get("data") or []:
        lat, lon = a.get("lat"), a.get("lon")
        if lat is None or lon is None: continue
        km, brg = _geo(lat, lon)
        out.append({"icao": (a.get("hex") or "").lower(), "callsign": (a.get("callsign") or "").strip(), "reg": a.get("reg", "") or "", "type": a.get("type", "") or "",
                    "desc": "", "operator": a.get("painted_as", "") or "", "lat": lat, "lon": lon, "alt_ft": a.get("alt"), "on_ground": (a.get("alt") or 0) == 0,
                    "speed_kt": a.get("gspeed") or 0, "track": a.get("track") or 0, "vrate": a.get("vspeed"), "squawk": a.get("squawk", "") or "",
                    "emergency": str(a.get("squawk", "")) in ("7500", "7600", "7700"), "km": km, "bearing": brg, "seen": None,
                    "route": f"{a.get('orig_iata') or '?'} → {a.get('dest_iata') or '?'}" if a.get("orig_iata") or a.get("dest_iata") else ""})
    return out

HELI_TYPES = re.compile(r"^(R22|R44|R66|EC[0-9]|H1[0-9]|H6[0-9]|AS[0-9]|A109|A139|A169|AW[0-9]|B06|B105|B407|B412|B427|B429|B505|S76|S92|UH1|MD5|MD6|BK17|EH10|A189)", re.I)
def air_extras(x):
    """(v146) helicopter flag (ADS-B category A7 or a rotorcraft type), minutes to Pearson for arrivals, runway guess from the heading."""
    x["heli"] = bool(x.get("category") == "A7" or HELI_TYPES.match(x.get("type") or ""))
    p = x.get("phase") or ""
    if p.startswith("arriving") and (x.get("speed_kt") or 0) > 40:
        ap = YYZ_LL if "YYZ" in p else YTZ_LL
        km, _ = _dist_brg((x["lat"], x["lon"]), ap)
        x["eta_min"] = round(km / (x["speed_kt"] * 1.852 / 60), 1)
    else: x["eta_min"] = None
    if "YYZ" in p:
        tr = (x.get("track") or 0) % 360
        rw = next((n for c, n in ((57, "05/06"), (237, "23/24"), (147, "15"), (327, "33")) if abs((tr - c + 180) % 360 - 180) <= 25), "")
        x["runway"] = x.get("rwy") or rw                                    # (v181) the exact runway when it is lined up with one, else the direction
    else: x["runway"] = ""
    return x

AIR_DB = os.path.join(HERE, "aircraft_log.sqlite")
_AIRLOG = {"lock": __import__("threading").Lock(), "last": {}}
def air_log(out):
    """(v147) Overflight log: every aircraft inside 6 km of home (or any helicopter inside 15 km) is written once per minute with its
    closest distance, so the Aircraft tab can show what came over the house today even if nobody was watching."""
    now = time.time(); rows = []
    for x in out:
        if x["km"] <= 6 or (x.get("heli") and x["km"] <= 15):
            k = x["icao"]; last = _AIRLOG["last"].get(k, 0)
            if now - last < 60: continue
            _AIRLOG["last"][k] = now
            rows.append((now, k, x["callsign"], x.get("type", ""), x.get("operator", ""), x["km"], x["bearing"], x.get("alt_ft"), x.get("route", ""), 1 if x.get("heli") else 0, x.get("phase", "")))
    if not rows: return
    with _AIRLOG["lock"]:
        c = sqlite3.connect(AIR_DB)
        c.execute("create table if not exists passes (t real, icao text, callsign text, type text, operator text, km real, bearing int, alt_ft int, route text, heli int, phase text)")
        c.executemany("insert into passes values (?,?,?,?,?,?,?,?,?,?,?)", rows); c.commit(); c.close()
def air_passes_today(limit=12):
    try:
        with _AIRLOG["lock"]:
            c = sqlite3.connect(AIR_DB)
            day0 = time.mktime(dt.date.today().timetuple())
            q = c.execute("select icao, callsign, type, operator, min(km), alt_ft, route, heli, max(t), count(*) from passes where t >= ? group by icao order by min(km) asc limit ?", (day0, limit)).fetchall()
            n = c.execute("select count(distinct icao) from passes where t >= ?", (day0,)).fetchone()[0]
            c.close()
        return {"n": n, "rows": [{"icao": r[0], "callsign": r[1], "type": r[2], "operator": r[3], "km": round(r[4], 1), "alt_ft": r[5], "route": r[6], "heli": bool(r[7]), "when": dt.datetime.fromtimestamp(r[8]).strftime("%H:%M"), "samples": r[9]} for r in q]}
    except Exception: return {"n": 0, "rows": []}

def aircraft_sources():
    """The ADS-B sources in the order they are tried. Matt's own SDR receiver first (aircraft_source.txt = its aircraft.json URL, or the
    dump1090/tar1090 defaults on this Mac), then Flightradar24's official API if fr24_api_key.txt holds a token, then the open
    community feeds (adsb.fi, adsb.lol - no key, same data dialect as the local receiver), then OpenSky as the last resort."""
    srcs = []
    try: own = [l.strip() for l in open(AIR_SOURCE) if l.strip() and not l.startswith("#")]
    except Exception: own = []
    for u in own or ["http://127.0.0.1:8080/data/aircraft.json", "http://127.0.0.1:8078/data/aircraft.json"]:
        srcs.append(("my SDR receiver" if own else "local dump1090", u, _from_readsb, {}, 2))
    try: key = open(FR24_KEY).read().strip()
    except Exception: key = ""
    if key:
        b = f"{HOME_LL[0] + 0.6:.2f},{HOME_LL[0] - 0.5:.2f},{HOME_LL[1] - 0.95:.2f},{HOME_LL[1] + 0.95:.2f}"
        srcs.append(("Flightradar24 API", f"https://fr24api.flightradar24.com/api/live/flight-positions/full?bounds={b}", _from_fr24,
                     {"Authorization": f"Bearer {key}", "Accept-Version": "v1"}, 8))
    srcs.append(("adsb.fi", f"https://opendata.adsb.fi/api/v2/lat/{HOME_LL[0]}/lon/{HOME_LL[1]}/dist/{AIR_RADIUS_NM}", _from_readsb, {}, 8))
    srcs.append(("adsb.lol", f"https://api.adsb.lol/v2/lat/{HOME_LL[0]}/lon/{HOME_LL[1]}/dist/{AIR_RADIUS_NM}", _from_readsb, {}, 8))
    srcs.append(("OpenSky", "https://opensky-network.org/api/states/all?lamin=43.1&lomin=-80.5&lamax=44.2&lomax=-78.5", _from_opensky, {}, 8))
    return srcs

YYZ_LL = (43.6777, -79.6248); YTZ_LL = (43.6275, -79.3962)
AIR_CH = {"twrE": (118.700, "YYZ TWR N"), "twrW": (118.350, "YYZ TWR S"), "gndN": (121.650, "YYZ GND N"), "gndS": (121.900, "YYZ GND S"),
          "apron": (122.825, "YYZ APRON"), "clnc": (121.300, "YYZ CLNC"), "atis": (120.825, "YYZ ATIS"), "ytzTwr": (118.200, "YTZ TWR"),
          "ytzGnd": (121.700, "YTZ GND"), "vfr": (133.400, "TOR VFR"), "guard": (121.500, "AIR GUARD"),
          "arr": [(132.800, "YYZ ARR 3"), (124.475, "YYZ ARR 1"), (125.400, "YYZ ARR 2")],      # (v181) main arrival channel first; 134.175 is the ILS monitor
          "dep": [(128.800, "YYZ DEP 2"), (127.575, "YYZ DEP 1")]}

def _dist_brg(a, b):
    import math
    dlat = math.radians(b[0] - a[0]); dlon = math.radians(b[1] - a[1])
    h = math.sin(dlat / 2) ** 2 + math.cos(math.radians(a[0])) * math.cos(math.radians(b[0])) * math.sin(dlon / 2) ** 2
    km = 2 * 6371 * math.asin(math.sqrt(h))
    brg = (math.degrees(math.atan2(math.sin(dlon) * math.cos(math.radians(b[0])), math.cos(math.radians(a[0])) * math.sin(math.radians(b[0])) - math.sin(math.radians(a[0])) * math.cos(math.radians(b[0])) * math.cos(dlon))) + 360) % 360
    return km, brg

def air_phase(x):
    """(v132) Fallback flight phase (altitude, vertical rate, distance to and heading toward YYZ / YTZ). When _helpers/follow_traffic.py
    is present its route- and type-aware phase() overrides this (aircraft_now); this stays so the Aircraft tab works without that file."""
    if x.get("on_ground"): return "on the ground"
    alt = x.get("alt_ft") or 0; vr = x.get("vrate") or 0; tr = x.get("track") or 0
    for n, ap in (("YYZ", YYZ_LL), ("YTZ", YTZ_LL)):
        d, brg = _dist_brg((x["lat"], x["lon"]), ap); diff = abs((tr - brg + 540) % 360 - 180)
        if d < 45 and alt < 9000 and vr <= 0 and diff < 50: return f"arriving {n}"
        if d < 35 and alt < 9000 and vr > 200 and diff > 110: return f"departing {n}"
    return "climbing" if vr > 300 else ("descending" if vr < -300 else "level")

def air_freqs(x):
    """(v132) Which airband channel(s) this aircraft is most likely talking on right now -> (why, [(mhz, name), ...]), best first.
    Tower/ground sides follow the aircraft's side of the field; arrivals and departures get the whole Arrival / Departure set."""
    p = x.get("phase") or air_phase(x); alt = x.get("alt_ft") or 0
    dz, _ = _dist_brg((x["lat"], x["lon"]), YYZ_LL); dt, _ = _dist_brg((x["lat"], x["lon"]), YTZ_LL); C = AIR_CH
    east = x["lon"] > YYZ_LL[1]; north = x["lat"] > YYZ_LL[0]
    if x.get("on_ground"):
        if dz < 4: return "on the ground at Pearson", [C["gndN"] if north else C["gndS"], C["apron"], C["clnc"], C["twrE"] if east else C["twrW"]]
        if dt < 2: return "on the ground at Billy Bishop", [C["ytzGnd"], C["ytzTwr"]]
        return "on the ground", []
    if "YTZ" in p: return f"{p} - Billy Bishop tower", [C["ytzTwr"], C["ytzGnd"]]
    if p == "arriving YYZ":
        if dz < 14 and alt < 3500: return "short final - Pearson tower", [C["twrE"] if east else C["twrW"], C["twrW"] if east else C["twrE"]] + C["arr"][:2]
        return "on approach - Toronto Arrival", list(C["arr"]) + [C["twrE"] if east else C["twrW"]]
    if p == "departing YYZ":
        if dz < 9 and alt < 3000: return "just airborne - Pearson tower", [C["twrE"] if east else C["twrW"]] + list(C["dep"])
        return "climbing out - Toronto Departure", list(C["dep"])
    if alt < 4000 and dz > 10 and dt > 6: return "low and local - VFR advisory", [C["vfr"], C["guard"]]
    if alt < 9000 and dz < 30: return f"{p} near Pearson - Arrival/Departure sectors", list(C["arr"]) + list(C["dep"])
    return f"{p} - Toronto Centre (sector frequency not in the bank; try the active airband channels)", []

def aircraft_routes(planes, budget=8):
    """Origin -> destination per callsign from hexdb.io's open route table (one small GET per new callsign, at most `budget`
    lookups per refresh, cached 1 h per callsign, misses cached 15 min); silent on failure."""
    from concurrent.futures import ThreadPoolExecutor
    want = [p for p in planes if p["callsign"] and not p.get("route") and (time.time() - _AC["routes"].get(p["callsign"], (0, ""))[0] > 900)][:budget]
    def look(p):
        try:
            j = _fetch_json(f"https://hexdb.io/api/v1/route/icao/{p['callsign']}", timeout=5)
            r = (j.get("route") or "").replace("-", " → ") if isinstance(j, dict) else ""
            _AC["routes"][p["callsign"]] = (time.time() + (2700 if r else 0), r)
        except Exception: _AC["routes"][p["callsign"]] = (time.time(), "")
    if want:
        with ThreadPoolExecutor(max_workers=4) as ex: list(ex.map(look, want))
    for p in planes:
        if not p.get("route"): p["route"] = _AC["routes"].get(p["callsign"], (0, ""))[1]

_AV = {"t": 0.0, "data": {}}
def airband_voice():
    """(v182) Minutes since speech was last CONFIRMED on each airband channel, e.g. {"124.475": 3.2}, read from the end of
    monitor_opens.log: the channelizer writes one line per squelch opening and marks the ones that passed its speech test (the sweep
    and Monitor both go through it). The bank's last_heard cannot answer this - the sweep stamps it for any carrier, speech or not."""
    now = time.time()
    if now - _AV["t"] < 10: return _AV["data"]
    out = {}
    try:
        with open(os.path.join(HERE, "monitor_opens.log"), "rb") as f:
            f.seek(0, 2); cut = f.tell() > 150000; f.seek(-150000 if cut else 0, 2 if cut else 0)
            lines = f.read().decode("utf-8", "ignore").splitlines()[1 if cut else 0:]                # a tail starts mid-line: drop that piece
        today = dt.datetime.now()
        for ln in lines:
            m = re.match(r"(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d) .* (\d{3}\.\d{4}) am \S+ max_mod \S+ mean_snr \S+ voice yes$", ln)
            if not m: continue
            mo, dd, hh, mi, ss = (int(m.group(i)) for i in range(1, 6))
            try: age = (now - today.replace(month=mo, day=dd, hour=hh, minute=mi, second=ss, microsecond=0).timestamp()) / 60
            except ValueError: continue
            if 0 <= age <= 60: out[f"{float(m.group(6)):.3f}"] = round(age, 1)          # later lines overwrite earlier ones: the newest wins
    except OSError: pass
    _AV.update(t=now, data=out); return out

def aircraft_now(max_age=10):
    """Aircraft around Toronto right now (first source in aircraft_sources() that answers, cached 10 s): callsign, registration,
    type, route, altitude, speed, heading, distance/bearing from home, emergency squawks, and the spoken form of each callsign for
    the airband transcripts. Failures -> {"error": ...} with the last good list."""
    if _AC["data"] and time.time() - _AC["t"] < max_age: return _AC["data"]
    errs = []
    for name, url, parse, hdr, to in aircraft_sources():
        try:
            out = parse(_fetch_json(url, hdr, timeout=to))
        except Exception as e:
            errs.append(f"{name}: {e.__class__.__name__}"); continue
        out = [x for x in out if x["km"] <= AIR_RADIUS_NM * 1.852 + 5]
        out.sort(key=lambda x: (x["on_ground"], x["km"]))                     # airborne first, nearest first; Pearson ground traffic after
        try: aircraft_routes(out[:60])
        except Exception: pass
        try:
            import asr_atc
            spoken = {x["callsign"]: asr_atc.spoken(x["callsign"]) for x in out if x["callsign"]}
        except Exception: spoken = {}
        for x in out:
            try: x["phase"] = air_phase(x); x["why"], x["freqs"] = air_freqs(x)
            except Exception: x["phase"], x["why"], x["freqs"] = "", "", []
        try:                                                                     # (v135) _helpers/follow_traffic.py (route- and type-aware phases) is the shared truth when present
            sys.path.insert(0, os.path.join(HERE, "_helpers")); import follow_traffic as FT
            for x in out:
                try:
                    p2 = FT.phase(x)
                    if p2: x["phase"] = p2
                    if hasattr(FT, "detail"):                                    # (v181) one rule set: why, channels (most likely first), the runway it is lined up with
                        x["why"], f2, x["rwy"] = FT.detail(x); x["freqs"] = [tuple(f) for f in f2]
                    else:
                        f2 = [tuple(f) for f in FT.freqs(dict(x, phase=p2))]
                        if f2: x["freqs"] = f2 + [f for f in x["freqs"] if f not in f2]
                except Exception: pass
        except Exception: pass
        for x in out:
            try: air_extras(x)
            except Exception: x["heli"] = False; x["eta_min"] = None; x["runway"] = ""
            x["fr24"] = f"https://www.flightradar24.com/{x['callsign']}" if x["callsign"] else f"https://www.flightradar24.com/data/aircraft/{x['icao']}"
            x["alt_m"] = round(x["alt_ft"] / 3.281) if x["alt_ft"] else None
        try: air_log(out)
        except Exception: pass
        arrivals = sorted([x for x in out if x.get("eta_min") is not None and "YYZ" in (x.get("phase") or "")], key=lambda x: x["eta_min"])[:8]
        data = {"t": time.time(), "n": len(out), "aircraft": out[:60], "spoken": spoken, "home": HOME_LL, "source": name,
                "arrivals": [{"callsign": x["callsign"] or x["icao"], "icao": x["icao"], "type": x["type"], "route": x.get("route", ""), "eta_min": x["eta_min"], "runway": x["runway"], "alt_ft": x["alt_ft"], "km": x["km"], "freqs": (x.get("freqs") or [])[:2]} for x in arrivals],
                "helis": [{"callsign": x["callsign"] or x["icao"], "icao": x["icao"], "type": x["type"], "operator": x.get("operator", ""), "km": x["km"], "bearing": x["bearing"], "alt_ft": x["alt_ft"]} for x in out if x.get("heli")][:8],
                "sources_tried": errs, "emergencies": [x["callsign"] or x["icao"] for x in out if x["emergency"]]}
        _AC.update(t=time.time(), data=data, src=name); return data
    return dict(_AC["data"] or {"aircraft": [], "n": 0}, error="; ".join(errs))

def spectrum(key, points=1400):
    """The live spectrum of one band, like SDRTrunk's strip: the newest sweep pass of power_<key>.csv in the newest scan folder
    (max of the last 3 passes so brief transmissions still show), downsampled to <= `points` bins, plus the known channel labels in range."""
    folder = v1.newest_scan()
    if not folder: return {"error": "no sweep yet"}
    p = os.path.join(folder, f"power_{key}.csv")
    if not os.path.exists(p):
        for d in sorted(glob.glob(os.path.join(HERE, "scan_*")), reverse=True)[:12]:
            if os.path.exists(os.path.join(d, f"power_{key}.csv")): p = os.path.join(d, f"power_{key}.csv"); break
        else: return {"error": f"no data for {key}"}
    import csv
    with open(p, "rb") as fh:
        fh.seek(max(0, os.path.getsize(p) - 400000)); lines = fh.read().decode(errors="ignore").splitlines()[1:]
    rows = []
    for r in csv.reader(lines):
        try: rows.append((r[0] + " " + r[1], float(r[2]), float(r[3]), float(r[4]), [float(x) for x in r[6:]]))
        except (ValueError, IndexError): continue
    if not rows: return {"error": "empty"}
    # one pass = consecutive rows with increasing start frequency; take the last 3 complete passes
    passes = []; cur = []; last_lo = None
    for row in rows:
        if last_lo is not None and row[1] < last_lo: passes.append(cur); cur = []       # (fastscan writes several rows per window: same lo = same pass)
        cur.append(row); last_lo = row[1]
    if cur: passes.append(cur)
    use = passes[-3:]
    bins = {}
    for ps in use:
        for ts, lo, hi, step, vals in ps:
            for i, v in enumerate(vals):
                if v > -1e9:
                    f = lo + i * step; bins[f] = max(bins.get(f, -999.0), v)
    fr = sorted(bins); n = len(fr)
    if n == 0: return {"error": "empty"}
    stride = max(1, n // points)
    xs = []; ys = []
    for i in range(0, n, stride):
        chunk = fr[i:i + stride]; xs.append(round(chunk[0] / 1e6, 5)); ys.append(round(max(bins[f] for f in chunk), 1))
    lo_m, hi_m = fr[0] / 1e6, fr[-1] / 1e6
    labels = []
    for f, v in v1.sdr_scan.REFERENCE.items():
        if lo_m <= f <= hi_m: labels.append({"mhz": f, "name": v[0], "kind": "ref"})
    for k, e in bank_load().items():
        try: m = float(k)
        except ValueError: continue
        if lo_m <= m <= hi_m and e.get("name") and not e["name"].replace(".", "").isdigit() and e.get("ver") in ("VOICE", "KNOWN"):
            if not any(abs(l["mhz"] - m) < 0.003 for l in labels): labels.append({"mhz": m, "name": e["name"], "kind": "bank"})
    for n_, _, frs in SP_SITES():
        for f in frs:
            if lo_m <= f <= hi_m: labels.append({"mhz": f, "name": n_ + " CC", "kind": "cc"})
    floor = sorted(ys)[len(ys) // 2]
    return {"key": key, "lo": lo_m, "hi": hi_m, "x": xs, "y": ys, "floor": floor, "labels": labels, "when": use[-1][0][0] if use and use[-1] else "", "passes": len(use)}
def SP_SITES():
    try:
        import sdrtrunk_playlist as SP; return SP.P25_SITES
    except Exception: return []

# ---------------------------------------------------------------- Cloudflare tunnel (optional): _launchers/Set up remote access (Cloudflare).command
TUN = {"proc": None}
def tunnel_loop():
    """Keeps cloudflared running (one child of the dashboard, no extra windows) while sdrtrunk/cloudflare_tunnel.txt and ~/.cloudflared/config.yml exist."""
    cfg = os.path.expanduser("~/.cloudflared/config.yml"); flag = os.path.join(HERE, "sdrtrunk", "cloudflare_tunnel.txt")
    binp = next((c for c in ("/opt/homebrew/bin/cloudflared", "/usr/local/bin/cloudflared") if os.path.exists(c)), None)
    while True:
        try:
            if binp and os.path.exists(cfg) and os.path.exists(flag) and (TUN["proc"] is None or TUN["proc"].poll() is not None):
                lg = open(os.path.join(HERE, "sdrtrunk", "cloudflared.log"), "a")
                TUN["proc"] = subprocess.Popen([binp, "tunnel", "--config", cfg, "run"], stdout=lg, stderr=subprocess.STDOUT)
                v1.say("Cloudflare tunnel started: https://" + open(flag).read().strip())
        except Exception as e: print("tunnel:", e)
        time.sleep(30)
__import__("threading").Thread(target=tunnel_loop, daemon=True).start()

# ---------------------------------------------------------------- remote access gate (Cloudflare tunnel / Tailscale / port-forward)
REMOTE_KEY_FILE = os.path.join(HERE, "remote_access.txt")        # one line: the password remote visitors must enter. Missing file = LAN only.
def remote_key():
    try: return open(REMOTE_KEY_FILE).read().strip()
    except OSError: return ""
def is_remote(handler):
    if handler.headers.get("CF-Connecting-IP") or handler.headers.get("X-Forwarded-For"): return True
    ip = handler.client_address[0]
    return not (ip.startswith(("127.", "10.", "192.168.", "100.", "::1", "fd")) or any(ip.startswith(f"172.{n}.") for n in range(16, 32)))
LOGIN_HTML = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SDR Scanner</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b0e13;color:#e8edf3;font:16px system-ui}form{background:#141a23;border:1px solid #243041;border-radius:14px;padding:28px 24px;width:min(92vw,360px)}
h1{font-size:1.2rem;margin:0 0 6px}p{color:#8a97a8;margin:0 0 16px;font-size:.9rem}input{width:100%;box-sizing:border-box;font:inherit;padding:12px;border-radius:10px;border:1px solid #243041;background:#0b0e13;color:#fff;margin-bottom:12px}
button{width:100%;font:inherit;padding:12px;border-radius:10px;border:0;background:#3ddc84;color:#06240f;font-weight:700}</style></head>
<body><form method="post" action="/login"><h1>SDR Scanner</h1><p>Toronto live scanner - enter the access password.</p><input name="key" type="password" placeholder="password" autofocus><button>Listen</button>__MSG__</form></body></html>"""
def gate(handler):
    """True = request allowed. Remote visitors need the cookie set by /login when remote_access.txt exists; without that file remote is refused."""
    if not is_remote(handler): return True
    key = remote_key()
    if not key: handler.send(LOGIN_HTML.replace("__MSG__", '<p style="color:#f6a">Remote access is not enabled on this scanner.</p>'), "text/html", 403); return False
    import http.cookies
    c = http.cookies.SimpleCookie(handler.headers.get("Cookie", ""))
    if c.get("sdrkey") and c["sdrkey"].value == _key_token(key): return True
    handler.send(LOGIN_HTML.replace("__MSG__", ""), "text/html", 401); return False
def _key_token(key):
    import hashlib; return hashlib.sha256(("sdr-scanner:" + key).encode()).hexdigest()[:32]

class H2(v1.H):
    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/login":
            n = int(self.headers.get("Content-Length", "0") or 0); body = self.rfile.read(n).decode(errors="ignore")
            key = parse_qs(body).get("key", [""])[0]
            if key and key == remote_key():
                self.send_response(302); self.send_header("Set-Cookie", f"sdrkey={_key_token(key)}; Path=/; Max-Age=2592000; SameSite=Lax; HttpOnly"); self.send_header("Location", "/"); self.end_headers()
                v1.say(f"remote login OK from {self.headers.get('CF-Connecting-IP') or self.client_address[0]}"); return
            time.sleep(1.5); return self.send(LOGIN_HTML.replace("__MSG__", '<p style="color:#f6a">Wrong password.</p>'), "text/html", 401)
        self.send_response(404); self.end_headers()
    def do_GET(self):
        u = urlparse(self.path)
        if not gate(self): return
        what = dg_what(u)
        if not what: return self._get(u)
        ok, cur = dg_enter(what)
        if not ok:
            msg = f"One command at a time: the dongle is still busy with {cur}. Try again in a moment."
            if u.path in ("/playmac", "/stream"): return self.send(msg, "text/plain", 409)
            return self.send(json.dumps({"error": msg, "busy": True}), "application/json")
        state = {"done": False}
        def done():
            if not state["done"]: state["done"] = True; dg_exit()
        self._dongle_done = done                                      # /stream calls this once the receiver is running (it then streams for as long as you listen)
        try: return self._get(u)
        finally: done()
    def _get(self, u):
        if u.path == "/playmac" and is_remote(self): return self.send("Mac speakers are not available remotely - set Output to 'this device'.", "text/plain", 403)
        if u.path in ("/playmac", "/stream") and (sdrt_running() or os.path.exists(SDRT_FLAG)):
            sdrt_stop(handback=False)                    # an analog listen takes the dongle from SDRTrunk first (the sweep stays paused), and waits until Java has let go
        if u.path in ICON_FILES:                                            # (v183) the program's icon: browser tab, iPhone / iPad home screen, installed web app
            try: return self.send(open(os.path.join(HERE, "icons", ICON_FILES[u.path][0]), "rb").read(), ICON_FILES[u.path][1])
            except OSError: return self.send("no icon", "text/plain", 404)
        if u.path == "/m":                                                  # (v157) the phone-first screen
            try: return self.send(open(os.path.join(HERE, "mobile_page_v7.html"), encoding="utf-8").read())
            except OSError: return self.send("mobile page missing", "text/plain", 404)
        if u.path in ("/", "/live"):                                        # (v133) /live = the same app, player + spectrum + Monitor only
            try: panel = open(os.path.join(HERE, "monitor_panel.html"), encoding="utf-8").read()      # Monitor card lives in its own file
            except OSError: panel = ""
            return self.send(APP.replace("</body>", panel + "</body>", 1) if panel else APP)
        if u.path == "/api/state":
            t0 = time.time(); st = dict(state_cached()); st["ms"] = int((time.time() - t0) * 1000)
            return self.send(json.dumps(st), "application/json")
        if u.path == "/api/recordings": return self.send(json.dumps(recordings()), "application/json")
        if u.path == "/api/radio": return self.send(json.dumps(RADIO), "application/json")
        if u.path == "/api/sound": return self.send(json.dumps(mac_sound()), "application/json")
        if u.path == "/api/digital":
            import sdrtrunk_playlist as SP
            cc = digital_carriers()
            sites = [{"site": n, "enabled": en, "cc": fr, "label": (SP.SITE_SYSTEM.get(n, ("Toronto Fire + Paramedics",))[0] + " - " + n)} for n, en, fr in SP.P25_SITES]
            try: extras = SP.p25_site_extras()                                        # (v138) Metrolinx's other UHF licences = voice channels of the same P25 system
            except Exception: extras = []
            for st in sites:
                if st["site"].startswith("Metrolinx"): st["voice"] = [{"mhz": f, "name": n} for f, n in extras]
            dmr_trunked = [{"name": n, "cc": cc, "mhz": fr} for n, cc, fr in SP.DMR_TRUNKED]
            nx = sorted(SP.NXDN_KNOWN.items()); nightly = "sdr-trunk-nightly" in (sdrt_binary() or "")
            nxdn = [{"name": "GO Transit rail traffic control - Lakeshore West / Union corridor (NXDN)", "mhz": [m for m, _ in nx],
                     "names": [n for _, n in nx], "ready": nightly}]
            pl = {"path": SP.OUT, "exists": os.path.exists(SP.OUT), "fire": len(SP.FIRE), "ems": len(SP.EMS), "dmr": len(SP.DMR_KNOWN) + len(SP.dmr_from_data()),
                  "in_sdrtrunk": os.path.exists(os.path.expanduser("~/SDRTrunk/playlist/SDR_Scanner.xml"))}
            dmr = dict(SP.DMR_KNOWN); dmr.update(SP.dmr_from_data())
            try: groups = SP.dmr_groups()
            except Exception: groups = []
            return self.send(json.dumps({"cc": cc[:16], "sites": sites, "playlist": pl, "dmr": [{"mhz": m, "name": n} for m, n in sorted(dmr.items())], "tgs": dmr_tgs(), "dmr_groups": groups, "dmr_trunked": dmr_trunked, "nxdn": nxdn, "stream": {"icecast": bool(icecast_bin()), "live": icecast_live()},
                                         "sdrtrunk": {"installed": bool(sdrt_binary()), "running": sdrt_running()}, "handed": os.path.exists(SDRT_FLAG)}), "application/json")
        if u.path == "/meter":
            st = sdrt_state()
            if st: return self.send(json.dumps({"playing": None, "sdrtrunk": st, "paused": True, "fm": ""}), "application/json")
            return super().do_GET()
        if u.path == "/api/sdrt/play":
            q = parse_qs(u.query)
            try: return self.send(json.dumps(sdrt_user_start(q.get("target", ["p25:West Simulcast"])[0])), "application/json")
            except Exception as e: return self.send(json.dumps({"error": str(e)}), "application/json")
        if u.path == "/api/sdrt/rotation":
            q = parse_qs(u.query); rot = rotation_load()
            if "on" in q: rot["on"] = q["on"][0] == "1"
            if "minutes" in q: rot["minutes"] = max(1, min(30, int(q["minutes"][0])))
            if "every" in q: rot["every"] = max(5, min(120, int(q["every"][0])))
            if "target" in q: rot["target"] = q["target"][0]
            if "on" in q and rot["on"]: rot["last"] = 0                  # first slot right away
            rotation_save(rot); return self.send(json.dumps(rot), "application/json")
        if u.path == "/api/sdrt/calls":
            try: log = json.load(open(CALLS_FILE))
            except Exception: log = []
            return self.send(json.dumps(list(reversed(log[-200:]))), "application/json")
        if u.path == "/info":
            try:
                m = float(parse_qs(u.query).get("mhz", ["0"])[0])
                if 764 <= m <= 776 and not any(abs(m - f) < 0.0001 for _, _, fr in __import__("sdrtrunk_playlist").P25_SITES for f in fr):
                    j = v1.freq_info(m); j["lines"] = p25_voice_info(m) + [l for l in j.get("lines", []) if "recording" in l]; j["name"] = p25_voice_name(m)
                    return self.send(json.dumps(j), "application/json")
            except Exception: pass
            return super().do_GET()
        if u.path == "/api/map": return self.send(json.dumps(map_data()), "application/json")
        if u.path == "/api/map/home":
            q = parse_qs(u.query)
            try: json.dump({"lat": float(q["lat"][0]), "lon": float(q["lon"][0]), "set": True}, open(MAP_FILE, "w"))
            except Exception as e: return self.send(json.dumps({"error": str(e)}), "application/json")
            return self.send(json.dumps(map_data()), "application/json")
        if u.path == "/api/sirens": return self.send(json.dumps(siren_press()), "application/json")
        if u.path == "/api/sirens/status": return self.send(json.dumps(siren_status()), "application/json")
        if u.path == "/api/sdrt/solo":
            q = parse_qs(u.query)
            try:
                tg = int(q.get("tg", ["0"])[0]); solo_set(tg); restarted = False
                if SDRT.get("target") and sdrt_running():                       # SDRTrunk reads aliases at start: restart it (about 3 s)
                    _LAST_UREQ["t"] = 0; sdrt_user_start(SDRT["target"]); restarted = True
                return self.send(json.dumps({"ok": True, "solo": tg, "restarted": restarted}), "application/json")
            except Exception as e: return self.send(json.dumps({"error": str(e)}), "application/json")
        if u.path == "/api/sdrt/solo/state": return self.send(json.dumps(solo_load()), "application/json")
        if u.path == "/api/sdrt/talkgroups":
            return self.send(json.dumps(talkgroups()), "application/json")
        if u.path == "/sdrt_audio":
            import sdrtrunk_playlist as SP, urllib.request
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{SP.ICECAST_PORT}{SP.ICECAST_MOUNT}", headers={"Icy-MetaData": "0"})
                up = urllib.request.urlopen(req, timeout=5)
            except Exception as e:
                return self.send(f"SDRTrunk is not streaming right now ({e.__class__.__name__}). Press ▶ on a digital system first.", "text/plain", 503)
            self.send_response(200); self.send_header("Content-Type", "audio/mpeg"); self.send_header("Cache-Control", "no-store"); self.send_header("Access-Control-Allow-Origin", "*"); self.end_headers()
            try:
                while True:
                    b = up.read(2048)
                    if not b: break
                    self.wfile.write(b); self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError): pass
            finally:
                try: up.close()
                except Exception: pass
            return
        if u.path == "/api/recent_text":
            # callsign -> "HH:MM on 132.800" for aircraft named in today's airband transcripts (spoken form match)
            out = {}
            try:
                import asr_atc
                ac = aircraft_now().get("aircraft", []); spoken = {x["callsign"]: asr_atc.spoken(x["callsign"]).lower() for x in ac if x["callsign"]}
                for f in sorted(glob.glob(os.path.join(HERE, "scan_*", "recordings", "*.txt")), key=os.path.getmtime, reverse=True)[:80]:
                    try: mhz = float(os.path.basename(f).split("_")[0])
                    except ValueError: continue
                    if not 108 <= mhz <= 137: continue
                    txt = open(f, errors="ignore").read().lower()
                    if not txt: continue
                    stamp = dt.datetime.fromtimestamp(os.path.getmtime(f)).strftime("%H:%M")
                    if time.time() - os.path.getmtime(f) > 900: continue                 # (v180) only the last 15 min says where a plane is NOW
                    norm = _say_norm(txt)
                    for cs, sp in spoken.items():
                        if cs in out: continue
                        m = re.match(r"([A-Z]{3})(\d+)", cs)                               # (v180) the WHOLE flight number has to be heard: the old test
                        if not m or sp == cs.lower(): continue                             # ("air canada two") matched every Air Canada 2xx on the air
                        tel = _say_norm(sp.rsplit(" ", len(m.group(2)))[0]) if len(sp.split()) > len(m.group(2)) else ""
                        if tel and re.search(r"\b" + re.escape(tel) + r" ?" + m.group(2).lstrip("0") + r"(?!\d)", norm): out[cs] = f"{stamp} on {mhz:.3f}"
            except Exception: pass
            return self.send(json.dumps(out), "application/json")
        if u.path == "/api/aircraft":
            return self.send(json.dumps(dict(aircraft_now(), voice=airband_voice())), "application/json")   # (v182) + when speech was last confirmed per airband channel
        if u.path == "/api/aircraft/today":
            return self.send(json.dumps(air_passes_today()), "application/json")
        if u.path == "/api/spectrum":
            key = parse_qs(u.query).get("band", ["air"])[0]
            return self.send(json.dumps(spectrum(re.sub(r"[^a-z0-9_]", "", key.lower()))), "application/json")
        if u.path == "/api/sdrt/units":
            return self.send(json.dumps(units_roster()), "application/json")
        if u.path == "/api/sdrt/stop":
            DMRSCAN["on"] = False; dmrscan_persist(); sdrt_stop(); return self.send(json.dumps({"ok": True}), "application/json")
        if u.path == "/api/dmr/alias":
            # (v145) name a talkgroup on a conventional DMR channel: ?mhz=422.3&tg=2&name=Site%20crew (empty name = forget). Takes effect
            # the next time SDRTrunk starts (the playlist is rebuilt at every start).
            q = parse_qs(u.query)
            try: k = f"{float(q.get('mhz', ['0'])[0]):.4f}"; tg = str(int(q.get("tg", [""])[0])); name = (q.get("name", [""])[0] or "").strip()[:40]
            except ValueError: return self.send(json.dumps({"error": "bad mhz/tg"}), "application/json")
            import sdrtrunk_playlist as SP
            try: al = json.load(open(SP.DMR_ALIAS_FILE))
            except Exception: al = {}
            e = al.setdefault(k, {})
            if name: e[tg] = name
            else: e.pop(tg, None)
            if not e: al.pop(k, None)
            try: json.dump(al, open(SP.DMR_ALIAS_FILE, "w"))
            except Exception as ex: return self.send(json.dumps({"error": str(ex)}), "application/json")
            v1.say(f"DMR alias: {k} TG {tg} = {name or '(forgotten)'}")
            return self.send(json.dumps({"ok": True, "aliases": al.get(k, {})}), "application/json")
        if u.path == "/api/sdrt/dmrscan":
            q = parse_qs(u.query)
            if q.get("on", [""])[0] == "1": return self.send(json.dumps(dmrscan_start(q.get("step", ["90"])[0], q.get("kind", ["dmr"])[0])), "application/json")
            if q.get("on", [""])[0] == "0": return self.send(json.dumps(dict(dmrscan_stop(), scan=dmrscan_state())), "application/json")
            if q.get("next", [""])[0] == "1" and DMRSCAN["on"]:
                with DMRSCAN_LOCK: r = dmrscan_next()
                return self.send(json.dumps(dict(r, scan=dmrscan_state())), "application/json")
            return self.send(json.dumps(dmrscan_state()), "application/json")
        if u.path == "/api/playlist":
            try: return self.send(json.dumps(build_playlist()), "application/json")
            except Exception as e: return self.send(json.dumps({"error": str(e)}), "application/json")
        if u.path == "/api/sdrtrunk":
            q = parse_qs(u.query); give = q.get("give", ["1"])[0] == "1"; flag = os.path.join(HERE, "SDRTRUNK_HAS_DONGLE")
            if give:
                try: v1.stop_player()
                except Exception: pass
                v1.take_dongle(); open(flag, "w").write(str(time.time()))
                try: build_playlist()
                except Exception as e: print("playlist:", e)
                cands = []
                try: cands.append(open(os.path.join(HERE, "sdrtrunk", "path.txt")).read().strip())
                except Exception: pass
                cands += glob.glob(os.path.expanduser("~/Applications/sdr-trunk*/bin/sdr-trunk")) + glob.glob("/Applications/sdr-trunk*/bin/sdr-trunk")
                cands += ["/Applications/SDRTrunk.app", os.path.expanduser("~/SDRTrunk/bin/sdr-trunk"), os.path.expanduser("~/Applications/SDRTrunk.app")]
                for app in cands:
                    if app and os.path.exists(app):
                        if subprocess.call(["pgrep", "-f", "sdr-trunk"], stdout=subprocess.DEVNULL) == 0: subprocess.call(["osascript", "-e", 'tell application "System Events" to set frontmost of (first process whose name contains "sdr") to true'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        else: subprocess.Popen(["open", app] if app.endswith(".app") else [app], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=os.path.dirname(app))
                        break
                return self.send(json.dumps({"msg": "dongle released, playlist built - in SDRTrunk press Play on Fire + Paramedics - West Simulcast"}), "application/json")
            sdrt_stop()
            return self.send(json.dumps({"msg": "dongle back - sweep resumes"}), "application/json")
        if u.path == "/api/uv32":
            # (v142) which memory channel the Baofeng has for each frequency = the last CHIRP file exported from here (chirp_bank.csv);
            # the page shows "UV-32 ch N" next to a frequency so you can turn the handheld to it without the laptop.
            import csv
            out = {"channels": {}, "exported": None}
            try:
                pth = os.path.join(HERE, "chirp_bank.csv"); out["exported"] = dt.datetime.fromtimestamp(os.path.getmtime(pth)).strftime("%b %d %H:%M")
                with open(pth, newline="") as fh:
                    for r in csv.DictReader(fh):
                        try: out["channels"][f"{float(r['Frequency']):.4f}"] = int(r["Location"])
                        except (KeyError, ValueError): pass
            except OSError: pass
            return self.send(json.dumps(out), "application/json")
        if u.path == "/api/chirp":
            # CHIRP file straight from the bank: every voice channel the UV-32 can tune, with heard tones (DCS too) and repeater offsets
            import csv, io
            S = v1.sdr_scan; bank = bank_load(); rows = []
            for k, e in bank.items():
                if e.get("ver") == "DATA": continue
                m = float(e.get("mhz", k))
                if not S.uv32_can_hear(m): continue
                ref = S.reference_for(m); label, dup, off, _ = S.service_for(m)
                name = (ref[0] if ref else e.get("name") or f"{m:.4f}")[:16]
                tone = ""; dcs = ""
                heard = e.get("tone", "") or ""
                mm = re.search(r"CTCSS ([\d.]+)", heard); md = re.search(r"DCS (\d{3})", heard)
                if mm: tone = mm.group(1)
                elif md: dcs = md.group(1)
                elif ref and ref[1]: tone = ref[1]
                if ref and ref[2]: dup, off = ref[2], ref[3]
                age = (time.time() - e.get("last_heard", 0)) / 86400 if e.get("last_heard") else 999
                score = (2 if e.get("ver") == "VOICE" else 1 if e.get("ver") == "KNOWN" else 0) * 1000 + e.get("times", 0) * 10 - min(age, 90)
                rows.append((score, m, name, dup, off, tone, dcs, e))
            rows.sort(key=lambda r: -r[0])
            # (v142) stable memory layout so "UV-32 ch N" means the same thing after every export: 0-19 Top 20 today, then the
            # Walkies bank (FRS 1-14, GMRS 15-22, MURS 1-5, business dot/star), then the Pearson / Billy Bishop airband set, then
            # everything else by score. A channel appears once, in the first bank it belongs to.
            def fixed_rows():
                by_m = {round(r[1], 4): r for r in rows}; used = set(); out = []
                def take(m, name, mode_hint=None):
                    k = round(m, 4)
                    if k in used: return
                    used.add(k)
                    r = by_m.get(k)
                    if r: out.append(r); return
                    if not S.uv32_can_hear(m): return
                    ref = S.reference_for(m); nm = (ref[0] if ref else name)[:16]
                    out.append((0, m, nm, ref[2] if ref else "", ref[3] if ref else 0, ref[1] if ref else "", "", {"ver": "KNOWN", "cat": "bank"}))
                for m, _, nm in v1.top_today(20): take(m, nm)
                for m, _, nm in v1.WALKIES: take(m, nm)
                for m, (nm, *_rest) in sorted(S.REFERENCE.items()):
                    if 118 <= m < 137: take(m, nm)
                for r in rows:
                    if round(r[1], 4) not in used: used.add(round(r[1], 4)); out.append(r)
                return out
            try: rows = fixed_rows()
            except Exception: pass
            cols = ["Location","Name","Frequency","Duplex","Offset","Tone","rToneFreq","cToneFreq","DtcsCode","DtcsPolarity","RxDtcsCode","CrossMode","Mode","TStep","Skip","Power","Comment","URCALL","RPT1CALL","RPT2CALL","DVCODE"]
            out = io.StringIO(); w = csv.writer(out); w.writerow(cols)
            for loc, (score, m, name, dup, off, tone, dcs, e) in enumerate(rows[:200]):
                tmode = "DTCS" if dcs else ("Tone" if tone else "")
                heard_when = dt.datetime.fromtimestamp(e["last_heard"]).strftime("%b %d %H:%M") if e.get("last_heard") else ""
                w.writerow([loc, name, f"{m:.6f}", dup or "", f"{(off or 0):.6f}", tmode, tone or "88.5", tone or "88.5", dcs or "023", "NN", dcs or "023", "Tone->Tone",
                            "AM" if m < 137 else "NFM", "5.00", "", "10.0W", f"{e.get('ver') or 'unverified'} | heard {e.get('times', 0)}x last {heard_when} | {e.get('cat', '')}", "", "", "", ""])
            data = out.getvalue().encode()
            try: open(os.path.join(HERE, "chirp_bank.csv"), "wb").write(data)
            except OSError: pass
            self.send_response(200); self.send_header("Content-Type", "text/csv"); self.send_header("Content-Disposition", 'attachment; filename="chirp_bank.csv"'); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data); return
        if u.path == "/api/alerts":
            q = parse_qs(u.query); rules = v1.alerts_load()
            if "add" in q:
                kind = q["add"][0]; val = q.get("value", [""])[0].strip()
                if kind in ("mhz", "cat", "kw") and val:
                    rules.append({"id": int(time.time() * 1000), "kind": kind, "value": val}); v1.alerts_save(rules)
            if "del" in q:
                rules = [r for r in rules if str(r.get("id")) != q["del"][0]]; v1.alerts_save(rules)
            try: log = json.load(open(v1.ALERT_LOG))[-30:]
            except Exception: log = []
            for e in log: e["time"] = dt.datetime.fromtimestamp(e["when"]).strftime("%b %d %H:%M")
            return self.send(json.dumps({"rules": rules, "log": list(reversed(log))}), "application/json")
        if u.path == "/api/wide":                                       # 'open more' for one channel: wider squelch + longer hang when it plays
            q = parse_qs(u.query); k = f"{float(q.get('mhz', ['0'])[0]):.4f}"; on = q.get("on", ["1"])[0] == "1"
            b = bank_load(); e = b.setdefault(k, {"mhz": float(k), "name": k, "service": "", "mode": "NFM", "cat": "Other", "uv32": True, "first_heard": time.time(), "last_heard": 0, "best_snr": 0, "times": 0, "tone": "", "ver": ""})
            e["wide"] = on; bank_save(); return self.send(json.dumps({"ok": True, "wide": on}), "application/json")
        if u.path == "/api/forget":
            q = parse_qs(u.query); k = f"{float(q.get('mhz', ['0'])[0]):.4f}"
            bank_load().pop(k, None); bank_save(); return self.send(json.dumps({"ok": True}), "application/json")
        if u.path == "/api/scan":
            q = parse_qs(u.query)
            cat = q.get("cat", ["All"])[0]
            bands = v1.sdr_scan.CATEGORY_BANDS.get(cat)
            if not bands: return self.send(json.dumps({"error": f"no band set for {cat}"}), "application/json")
            key = cat.lower().replace("/", "").replace(" ", "_")
            names = [v1.sdr_scan.BANDS[b][0] for b in bands]
            try:
                # A listen is never interrupted by a sweep: while something plays, the new sweep is queued and starts
                # 20 s after the player stops (sweep_when_idle thread).
                with v1._lock: listening = v1._player.get("session") is not None
                if listening or SDRT["target"]:
                    PENDING_SCAN.update(cat=cat, key=key, t=time.time())
                    return self.send(json.dumps({"deferred": True, "bands": names, "cat": cat}), "application/json")
                PENDING_SCAN.update(cat=None, key=None)
                folder = v1.start_scan(key, cat, "chip pressed in the browser")
                if cat == "Digital": DIG_LISTEN["at"] = time.time() + 6.5 * 60; v1.say("digital: SDRTrunk will decode the strongest site for 5 min once the 700/800 MHz slices are done")
                return self.send(json.dumps({"folder": folder, "bands": names, "cat": cat}), "application/json")
            except Exception as e:
                return self.send(json.dumps({"error": f"{type(e).__name__}: {e}"}), "application/json")
        if u.path == "/api/scanner":
            return self.send(json.dumps(v1.scanner_state()), "application/json")
        if u.path == "/scanner/autoscan":
            q = parse_qs(u.query); bank = q.get("bank", [""])[0]
            if bank not in ("", "Top", "Walkies", "Airport", "TTC Etobicoke", "Union Station", "CP Etobicoke Yard", "GFL Environmental", "Solid Waste Mgmt", "Follow traffic", "All"): return self.send(json.dumps({"error": "unknown bank"}), "application/json")
            return self.send(json.dumps(autoscan_set(bank)), "application/json")
        if u.path == "/stop" and AUTO["ours"]:
            AUTO["ours"] = False; AUTO["paused_until"] = time.time() + 1800; v1.say("auto-scan parked for 30 min (you pressed Stop)")
        if u.path.startswith("/scanner/"):
            if is_remote(self) and not self.headers.get("Cookie", "").count("sdrkey"): return self.send("login first", "text/plain", 401)
            return self.send(json.dumps(v1.scanner_cmd(u.path.split("/")[2], parse_qs(u.query))), "application/json")
        if u.path == "/api/restart":
            force = parse_qs(u.query).get("force", ["0"])[0] == "1"
            if force: v1.LAST_USER_PLAY[0] = 0; v1.USER_WANTS.clear()
            open(v1.RESTART_REQ, "w").write("force" if force else "idle")
            v1.say("restart requested from the browser" + (" (force: now)" if force else " (when nobody is listening)"))
            return self.send(json.dumps({"ok": True, "when": "now" if force else "when idle"}), "application/json")
        if u.path == "/api/macbeep":
            subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return self.send(json.dumps(mac_sound()), "application/json")
        if u.path == "/api/history":
            q = parse_qs(u.query); m = float(q.get("mhz", ["0"])[0])
            return self.send(json.dumps({"series": hist_series(m, 24), "hourly": hist_hourly(m, 7)}), "application/json")
        if u.path == "/api/directory":
            from build_hub import entries
            out = [{"mhz": e["mhz"], "name": e["name"], "cat": e["cat"], "group": e["group"], "mode": e["mode"], "tone": e.get("tone", ""), "duplex": e.get("duplex", ""), "offset": e.get("offset", 0), "note": e.get("note", ""), "nets": e.get("nets", ""), "uv32": e["uv32"]} for e in entries()]
            return self.send(json.dumps(out), "application/json")
        return super().do_GET()                     # /stream /playmac /stop /mode /meter /info /debug /rec/ /delrec

ICON_FILES = {"/favicon.ico": ("favicon.ico", "image/x-icon"), "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"), "/apple-touch-icon-precomposed.png": ("apple-touch-icon.png", "image/png"),
              "/icon-192.png": ("icon-192.png", "image/png"), "/icon-512.png": ("icon-512.png", "image/png"), "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json")}
APP = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><link rel="icon" href="/favicon.ico" sizes="any"><link rel="icon" type="image/png" sizes="192x192" href="/icon-192.png"><link rel="apple-touch-icon" href="/apple-touch-icon.png"><link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#0b0e13"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><title>SDR Scanner</title>
<style>
:root{--bg:#0b0e13;--bg2:#10151d;--card:#151b25;--card2:#1b2330;--line:#242d3b;--ink:#e6ebf2;--mute:#8a95a6;--dim:#5b6676;--acc:#3ddc84;--acc2:#5aa9ff;--warn:#f0b429;--red:#ff5c5c;--mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased}
button{font:inherit;color:inherit;background:var(--card2);border:1px solid var(--line);border-radius:9px;padding:7px 12px;cursor:pointer;transition:background .15s,border-color .15s,transform .05s}
button:hover{background:#222c3b;border-color:#34405a}button:active{transform:translateY(1px)}button.pri{background:var(--acc);color:#04130a;border-color:var(--acc);font-weight:700}button.pri:hover{background:#4ff39a}
button.danger{background:var(--red);border-color:var(--red);color:#fff;font-weight:700}button.ghost{background:transparent}
input[type=search]{font:inherit;background:var(--card);border:1px solid var(--line);color:var(--ink);border-radius:9px;padding:8px 12px;min-width:220px}
input[type=range]{accent-color:var(--acc)}
.app{display:grid;grid-template-rows:auto 1fr auto;min-height:100vh}
header{position:sticky;top:0;z-index:20;background:rgba(11,14,19,.85);backdrop-filter:blur(12px);border-bottom:1px solid var(--line);padding:10px 18px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
header h1{font-size:1.05rem;margin:0;letter-spacing:.2px}header .sub{color:var(--mute);font-size:.82rem}
.pill{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;font-size:.78rem;font-weight:600;border:1px solid var(--line);background:var(--card)}
.pill .dot{width:8px;height:8px;border-radius:50%;background:var(--dim)}.pill.run .dot{background:var(--acc);box-shadow:0 0 10px var(--acc)}.pill.pause .dot{background:var(--warn);box-shadow:0 0 10px var(--warn)}.pill.dead .dot{background:var(--red)}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:10px;overflow:hidden}.seg button{border:0;border-radius:0;background:transparent;padding:7px 14px}.seg button.on{background:var(--acc2);color:#061120;font-weight:700}
main{padding:16px 18px 130px;max-width:1400px;width:100%;margin:0 auto}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:14px}
.tile{background:linear-gradient(180deg,var(--card),var(--bg2));border:1px solid var(--line);border-radius:14px;padding:12px 14px;position:relative;overflow:hidden}.tile .tplay{position:absolute;right:10px;top:10px;width:32px;height:32px}.tile.clickable{cursor:pointer}.tile.clickable:hover{border-color:var(--acc)}.alist{width:100%;border-collapse:collapse}.alist th{font-size:.7rem;text-transform:uppercase;letter-spacing:.06em;color:var(--mute);text-align:left;padding:6px}.drawer:has(#dactive:not([style*='none'])){width:min(820px,96vw)}.alist td{padding:8px 6px;border-bottom:1px solid var(--line);vertical-align:middle}.alist tr.row{cursor:pointer}.alist tr.row:hover td{background:rgba(90,169,255,.06)}.tile.playing{border-color:var(--acc)}.tile span{display:block;padding-right:38px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tile b{display:block;font-size:1.55rem;font-weight:700;letter-spacing:-.5px;font-family:var(--mono)}.tile span{color:var(--mute);font-size:.78rem;text-transform:uppercase;letter-spacing:.06em}
.tile.flash{}
.bands{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}.band{display:flex;align-items:center;gap:8px;padding:6px 10px;border:1px solid var(--line);border-radius:10px;background:var(--card);font-size:.82rem;color:var(--mute)}
.band i{display:block;width:8px;height:8px;border-radius:50%;background:var(--dim)}.band.on i{background:var(--acc);box-shadow:0 0 8px var(--acc)}.band b{color:var(--ink);font-weight:600}
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin-bottom:12px}.tabs button{border:0;border-radius:9px 9px 0 0;background:transparent;color:var(--mute);padding:9px 14px}.tabs button.on{color:var(--ink);background:var(--card);border:1px solid var(--line);border-bottom-color:var(--card)}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px}.chip{padding:5px 11px;border-radius:999px;border:1px solid var(--line);background:var(--card);color:var(--mute);font-size:.8rem;cursor:pointer;user-select:none}.chip.on{background:var(--acc);color:#04130a;border-color:var(--acc);font-weight:600}
.tablewrap{border:1px solid var(--line);border-radius:14px;overflow:auto;background:var(--card)}
table{border-collapse:separate;border-spacing:0;width:100%;min-width:860px}th{position:sticky;top:0;background:var(--card2);font-size:.72rem;color:var(--mute);text-transform:uppercase;letter-spacing:.07em;text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);cursor:pointer;user-select:none;white-space:nowrap}
.play.stopb{background:#b33b3b;color:#fff;border-color:#b33b3b}.tx{color:var(--ink);font-size:.86rem;line-height:1.35;margin:4px 0 6px;font-style:italic}tr.prev td{opacity:.6}tr.prev .play{opacity:1}tr.hot td{opacity:1}.heard b{font-family:var(--mono);font-weight:500}.btnlink{display:inline-flex;align-items:center;padding:7px 12px;border:1px solid var(--line);border-radius:9px;color:var(--ink);text-decoration:none;font-size:.9rem;margin-right:10px}.btnlink:hover{border-color:var(--acc)}.spark{display:flex;align-items:flex-end;gap:1px;height:28px}.spark i{flex:1;background:var(--acc);border-radius:1px 1px 0 0;min-width:1px}.hod{display:flex;gap:2px;align-items:flex-end}.hod span{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end}.hod i{display:block;width:100%;background:var(--acc2,#5aa9ff);border-radius:2px 2px 0 0}.hod b{font-size:.55rem;color:var(--dim);font-weight:500;height:10px}.dig{display:grid;gap:14px}.dig .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px}.dig h3{margin:0 0 6px}.steps{margin:6px 0 12px 18px;color:var(--ink);line-height:1.5}.vtag{display:inline-block;padding:3px 8px;border-radius:999px;font-size:.74rem;font-weight:600;white-space:nowrap}.vtag.v1{background:rgba(61,220,132,.18);color:var(--acc);border:1px solid #1f5c3a}.vtag.v2{background:rgba(61,220,132,.08);color:var(--acc);border:1px solid #1f5c3a}.vtag.v4{color:#c9a3ff;border-color:#8a5cf6;background:rgba(138,92,246,.12)}.vtag.v3{background:transparent;color:var(--mute);border:1px solid var(--line)}.heard b.hot{color:var(--acc)}th.asc:after{content:" ▲"}th.desc:after{content:" ▼"}th:hover{color:var(--ink)}
td{padding:9px 12px;border-bottom:1px solid var(--line);white-space:nowrap;vertical-align:middle}tr.row{cursor:pointer;transition:background .15s}tr.row:hover td{background:rgba(90,169,255,.06)}tr.row.playing td{background:rgba(61,220,132,.08)}tr.row.quiet .f,tr.row.quiet .nm{opacity:.55}
tr.row.new td{}td .chg{}
.f{font-family:var(--mono);font-weight:700;font-size:1rem}.nm{font-weight:600}.sv{color:var(--mute);font-size:.8rem}
.tag{display:inline-block;font-size:.68rem;padding:2px 7px;border-radius:999px;border:1px solid var(--line);color:var(--mute);margin-left:4px;vertical-align:middle}.tag.uv{border-color:#2d6b4a;color:var(--acc)}.tag.sdr{border-color:#6b5520;color:var(--warn)}.tag.tone{border-color:#2d4b6b;color:var(--acc2)}
.meter{display:inline-block;width:96px;height:8px;background:#0e131a;border-radius:4px;vertical-align:middle;margin-right:8px;overflow:hidden}.meter i{display:block;height:100%;background:linear-gradient(90deg,#2d9c62,var(--acc));border-radius:4px;transition:width .4s}
.act{display:flex;flex-direction:column;gap:2px;min-width:120px}.actl{font-size:.8rem}.actl b{font-family:var(--mono)}
.actb{display:flex;gap:2px}.actb i{display:block;width:9px;height:12px;border-radius:2px;background:var(--line)}.actb i.on{background:var(--acc)}.actl{font-size:.78rem;margin-top:3px}
.play{width:34px;height:34px;border-radius:50%;padding:0;display:inline-grid;place-items:center;background:var(--acc);border-color:var(--acc);color:#04130a;font-size:.8rem}.play:hover{background:#4ff39a}
.empty{padding:28px;text-align:center;color:var(--mute)}
/* player bar */
.player{position:fixed;left:0;right:0;bottom:0;z-index:30;background:rgba(16,21,29,.92);backdrop-filter:blur(14px);border-top:1px solid var(--line);padding:10px 18px;display:grid;grid-template-columns:auto 1fr auto;gap:16px;align-items:center}
.player .np{display:flex;flex-direction:column;min-width:220px}.player .np .freq{font:700 1.5rem var(--mono);letter-spacing:-.5px;line-height:1.1}.player .np .name{color:var(--mute);font-size:.85rem}
.player .mid{display:flex;flex-direction:column;gap:6px;min-width:0}.lvl{height:10px;background:#0e131a;border-radius:5px;overflow:hidden;position:relative}.lvl i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--acc2),var(--acc));transition:width .12s}.lvl.open i{background:linear-gradient(90deg,#2d9c62,var(--acc))}.lvl s{position:absolute;top:0;bottom:0;width:2px;background:var(--warn);left:0;transition:left .2s}
.player .ctl{display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-size:.8rem;color:var(--mute)}.player .ctl label{display:flex;align-items:center;gap:6px}
.player .stat{font-size:.78rem;color:var(--mute)}.player .stat b{color:var(--ink)}
.player .btns{display:flex;gap:8px;align-items:center}
/* drawer */
.drawer{position:fixed;top:0;right:-440px;width:420px;max-width:92vw;height:100vh;background:var(--bg2);border-left:1px solid var(--line);z-index:40;transition:right .25s;padding:18px;overflow:auto;box-shadow:-20px 0 40px rgba(0,0,0,.4)}
.drawer.open{right:0}.drawer h3{margin:0 0 2px;font:700 1.6rem var(--mono)}.drawer .sub{color:var(--mute);margin-bottom:12px}.drawer ul{padding-left:18px;color:var(--mute)}.drawer li{margin-bottom:6px}
.drawer .close{position:absolute;top:12px;right:12px}
.radio{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}.radio button.play{width:auto;height:auto;border-radius:12px;background:var(--card);border-color:var(--line);color:var(--ink);display:block;font-size:inherit}.radio button.play.on{border-color:var(--acc);background:rgba(61,220,132,.12)}.radio button.play.stopb{background:rgba(179,59,59,.25);border-color:#b33b3b}.radio button{padding:12px;text-align:left;border-radius:12px}.radio button b{display:block;font-family:var(--mono);font-size:1.05rem}.radio button span{color:var(--mute);font-size:.8rem}
.rec{display:grid;grid-template-columns:150px 1fr 1fr auto;gap:10px;align-items:center;padding:8px 12px;border-bottom:1px solid var(--line)}.rec audio{width:100%;height:34px}
pre{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;font:.76rem/1.4 var(--mono);color:var(--mute);overflow:auto;max-height:420px}
.toast{position:fixed;bottom:110px;left:50%;transform:translateX(-50%);background:var(--card2);border:1px solid var(--line);padding:10px 16px;border-radius:12px;z-index:50;opacity:0;transition:opacity .2s;pointer-events:none}.toast.show{opacity:1}
@media(max-width:760px){html,body{overflow-x:hidden}header{padding:8px 12px;gap:8px}header h1{font-size:1.05rem}#phone{display:none}.btnlink{display:none}main{padding:10px 10px 250px}.player{grid-template-columns:1fr;gap:8px;padding:8px 10px}.tiles{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.tile{padding:10px 12px;min-width:0}.tile b{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:block;padding-right:36px}.tabs{overflow-x:auto;white-space:nowrap;-webkit-overflow-scrolling:touch}.tabs button{white-space:nowrap}#beep,#macbeep,#macbtn,#outhint,label:has(#voice){display:none}.player{padding:6px 10px 10px}.ctl{gap:6px}.tile b{font-size:1.1rem}.tile span{font-size:.62rem}.tile:nth-child(2),.tile:nth-child(5){display:none}#chips{display:flex;flex-wrap:wrap;gap:6px;width:100%}.toolbar>*{max-width:100%}.chip{padding:7px 10px;font-size:.78rem}.tablewrap{overflow-x:auto;-webkit-overflow-scrolling:touch}table{min-width:0;width:100%}#tab-freq th:nth-child(2),#tab-freq td:nth-child(2),#tab-freq th:nth-child(6),#tab-freq td:nth-child(6),#tab-freq th:nth-child(7),#tab-freq td:nth-child(7),#tab-freq th:nth-child(8),#tab-freq td:nth-child(8),#tab-freq th:nth-child(9),#tab-freq td:nth-child(9),#tab-freq th:nth-child(10),#tab-freq td:nth-child(10),#tab-freq th:nth-child(13),#tab-freq td:nth-child(13),#tab-freq th:nth-child(14),#tab-freq td:nth-child(14){display:none}td,th{padding:8px 6px}.play{width:42px;height:42px}.ctl{flex-wrap:wrap}.btns{flex-wrap:wrap}.drawer{width:100vw}}

#expbtn{display:none}
@media(max-width:760px){
html{-webkit-text-size-adjust:100%}body{font-size:15px;padding-bottom:0}
input,select,textarea{font-size:16px!important}
input[type=search]{min-width:0;width:100%;min-height:46px;border-radius:12px}
button{min-height:44px}.chip{min-height:38px;display:inline-flex;align-items:center}
header{padding:calc(8px + env(safe-area-inset-top)) 12px 8px;gap:8px;flex-wrap:wrap}
header>div:first-child{flex:1 1 auto;min-width:0}header h1{font-size:1.15rem}#hdrsub{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
header .pill{font-size:.72rem;padding:3px 8px}#engine{display:none}#modeseg{display:none}
#sirenbtn{order:9;flex:1 1 100%;min-height:48px;font-size:1rem;border-radius:12px;background:rgba(229,72,77,.12)}#sirenmsg{order:10;flex:1 1 100%}
main{padding:10px 12px calc(235px + env(safe-area-inset-bottom))}
.tiles{display:flex;overflow-x:auto;gap:8px;scroll-snap-type:x proximity;margin:0 -12px 10px;padding:0 12px 4px;-webkit-overflow-scrolling:touch}
.tiles::-webkit-scrollbar,.bands::-webkit-scrollbar,.tabs::-webkit-scrollbar{display:none}
.tile,.tile:nth-child(2),.tile:nth-child(5){display:block;flex:0 0 132px;scroll-snap-align:start}
.bands{flex-wrap:nowrap;overflow-x:auto;margin:0 -12px 10px;padding:0 12px}.band{flex:0 0 auto;white-space:nowrap}
/* tab bar becomes a bottom nav */
.tabs{position:fixed;left:0;right:0;bottom:0;z-index:40;margin:0;gap:0;border:0;border-top:1px solid var(--line);background:rgba(11,14,19,.96);backdrop-filter:blur(14px);padding:0 4px env(safe-area-inset-bottom);overflow-x:auto;display:flex}
.tabs button,.tabs button.on{flex:1 0 auto;border:0;border-radius:0;min-height:54px;padding:6px 14px;font-size:.78rem;background:transparent;color:var(--mute);border-top:3px solid transparent}
.tabs button.on{color:var(--acc);border-top-color:var(--acc);background:transparent}
/* player: compact, controls behind a toggle */
.player{bottom:calc(54px + env(safe-area-inset-bottom));grid-template-columns:1fr auto;gap:6px 10px;padding:8px 12px;border-radius:16px 16px 0 0;box-shadow:0 -8px 24px rgba(0,0,0,.45)}
.player .np{min-width:0}.player .np .freq{font-size:1.35rem}.player .np .name{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.player .mid{grid-column:1/-1;grid-row:2}.player .btns{grid-column:2;grid-row:1;display:flex;gap:6px;flex-wrap:wrap;min-width:0}
.player .btns>*{display:none}.player .btns>#stop,.player .btns>#monbtn,.player .btns>#monsel,.player .btns>#quickbtn,.player .btns>#expbtn{display:inline-flex;align-items:center;justify-content:center}
#expbtn{min-width:44px}
.player .ctl{display:none}.player .stat{display:block}.player .mid .ctl .stat{display:inline}
.player.exp .ctl{display:flex}.player.exp .btns{flex-wrap:wrap;grid-column:1/-1;grid-row:3}.player.exp .btns>#tune,.player.exp .btns>#tunemode,.player.exp .btns>#tunebtn,.player.exp .btns>#outsel{display:inline-block}
.player.exp .btns>#tune{width:100px}
.player .mid .ctl{gap:10px}.player.exp .ctl label{min-height:40px}
.player .ctl:not(.x) .stat{}
#pstat{display:inline!important}
/* tables become cards */
.tablewrap{border:0;background:transparent;overflow:visible}
table,tbody{display:block;min-width:0;width:100%}thead{display:none}
td{white-space:normal;border:0;padding:0}
tr.row,#dirrows tr{display:grid;grid-template-columns:54px 1fr auto;column-gap:12px;row-gap:3px;align-items:center;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px;margin-bottom:8px}
tr.row.playing{border-color:var(--acc);background:rgba(61,220,132,.07)}
#rows td:nth-child(1){grid-area:1/1/4/2}#rows td:nth-child(3){grid-area:1/2}#rows td:nth-child(12){grid-area:1/3/3/4;text-align:right}
#rows td:nth-child(4){grid-area:2/2}#rows td:nth-child(5){grid-area:3/2}#rows td:nth-child(11){grid-area:3/3;justify-self:end}
#rows td:nth-child(12) br{display:none}
.f{font-size:1.15rem}.nm{font-size:.95rem}.play{width:48px;height:48px;font-size:1rem}
#dirrows td{display:block!important}#dirrows td:nth-child(5),#dirrows td:nth-child(7),#dirrows td:nth-child(8),#dirrows td:nth-child(9){display:none!important}
#dirrows td:nth-child(1){grid-area:1/1/4/2}#dirrows td:nth-child(2){grid-area:1/2/2/4}#dirrows td:nth-child(3){grid-area:2/2/3/4}#dirrows td:nth-child(4){grid-area:3/2}#dirrows td:nth-child(6){grid-area:3/3;justify-self:end}
#tab-recs .tablewrap,#tab-digital .tablewrap,#tab-digital .card table{overflow-x:auto;display:block}
.dig .card{padding:12px}.dig table{display:table;width:100%}.dig tbody{display:table-row-group}.dig thead{display:table-header-group}.dig td,.dig th{white-space:normal;padding:8px 6px;display:table-cell}
.rec{grid-template-columns:1fr;gap:6px}.rec audio{height:44px}
.radio{grid-template-columns:repeat(2,minmax(0,1fr))}.radio button{min-height:64px}
#mapbox{height:58vh!important}
.drawer{width:100vw;max-width:100vw;height:100dvh;padding:calc(16px + env(safe-area-inset-top)) 16px calc(24px + env(safe-area-inset-bottom))}
.drawer .close{top:calc(10px + env(safe-area-inset-top));min-width:44px}
.toast{bottom:calc(190px + env(safe-area-inset-bottom));max-width:90vw}
#expbtn{display:inline-flex}
}
@media(min-width:761px) and (max-width:1100px){main{padding-left:14px;padding-right:14px}.tiles{grid-template-columns:repeat(4,1fr)}.tabs{overflow-x:auto}.tabs button{white-space:nowrap}header{gap:10px}}

.app{grid-template-columns:minmax(0,1fr)}main{min-width:0}.tablewrap,.toolbar,.tiles,.bands,.tabs{min-width:0;max-width:100%}
@media(min-width:761px) and (max-width:1100px){.player{grid-template-columns:auto 1fr;grid-template-rows:auto auto}.player .btns{grid-column:1/-1;flex-wrap:wrap}.player .ctl{gap:8px 14px}.player .ctl label{white-space:nowrap}main{padding-bottom:230px}}

@media(max-width:760px){.tiles,.bands{margin-left:0;margin-right:0;padding-left:0;padding-right:0}.dig{grid-template-columns:minmax(0,1fr)}.dig .card{min-width:0;overflow-x:auto}#rows td:nth-child(12) .sv{display:block}#rows td:nth-child(12) b{display:block}}
@media(min-width:761px){main{max-width:none;padding-left:clamp(12px,1.6vw,32px);padding-right:clamp(12px,1.6vw,32px)}table{min-width:0;table-layout:auto}th{padding:9px 8px;white-space:normal;line-height:1.15}td{padding:7px 8px;white-space:normal;font-size:clamp(.74rem,.62vw + .4rem,.9rem)}td.f,td:first-child{white-space:nowrap}.tablewrap{overflow-x:auto}td .nm{word-break:break-word}.tag{white-space:nowrap}td.sv{max-width:none!important}}
@media(min-width:761px) and (max-width:1100px){table:has(#rows) :is(th,td):nth-child(7),table:has(#rows) :is(th,td):nth-child(10),table:has(#rows) :is(th,td):nth-child(13){display:none}}
@media(min-width:761px) and (max-width:900px){table:has(#rows) :is(th,td):nth-child(2),table:has(#rows) :is(th,td):nth-child(11){display:none}}
.band.sel{outline:1px solid var(--acc)}
@media(max-width:760px){.drawer{right:-110vw}.drawer.open{right:0}.player .btns{flex-wrap:wrap;max-width:100%}.player .btns>*{max-width:48vw}}
body.live .tabs,body.live main>section,body.live .tiles,body.live #popout,body.live .btnlink,body.live #sirenbtn{display:none!important}body.live{padding:10px}body.live main{padding-bottom:110px}
.capped{display:none!important}
.headunit{padding:12px 14px;border:1px solid var(--acc);box-shadow:0 0 0 1px rgba(63,185,80,.15) inset}.hu-top{display:flex;gap:14px;align-items:center;flex-wrap:wrap}.hu-lcd{flex:1 1 260px;min-width:0}
.hu-stat{font:600 .75rem/1 var(--mono);letter-spacing:.14em;color:var(--mute)}.hu-stat.on{color:var(--acc)}.hu-stat.hold{color:var(--warn)}.hu-freq{font:700 2.2rem/1.1 var(--mono);margin:4px 0 2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.hu-name{font-size:1.05rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hu-keys{display:flex;gap:6px;flex-wrap:wrap;align-items:center}.hu-keys button{min-width:70px;font-weight:600;letter-spacing:.04em}.hu-keys button.on{background:var(--warn);color:#000;border-color:var(--warn)}
@media(max-width:760px){.hu-freq{font-size:1.7rem}.hu-keys button{min-width:0;flex:1 1 auto;padding:8px 6px}}
.drawer:not(.open){pointer-events:none;visibility:hidden}.drawer.open{visibility:visible;pointer-events:auto}

/* (v138) one explicit player layout for desktop/iPad - earlier rules from two authors fought over the grid; this block wins by order */
@media(min-width:761px){.player{display:grid;grid-template-columns:minmax(200px,300px) minmax(0,1fr);grid-template-rows:auto auto;gap:6px 16px;align-items:center}
.player .np{grid-column:1;grid-row:1/3;min-width:0}.player .btns{grid-column:2;grid-row:1;display:flex;flex-wrap:wrap;gap:6px;min-width:0;justify-content:flex-start}
.player .mid{grid-column:2;grid-row:2;min-width:0}.player .ctl{display:flex;flex-wrap:wrap}.player .stat{white-space:normal;max-height:2.6em;overflow:hidden}}
/* (v148) in-card lists wrap instead of forcing the page 4,800 px wide (the Digital tab's systems table did) */
.alist{min-width:0}.alist td{white-space:normal}.dg-name{font-size:1.35em;font-weight:700;line-height:1.2}.dg-st{font-size:1.1em}.dg-said{font-weight:700;color:var(--acc)}.dg-heard{color:var(--acc)}tr.dg-roads td{background:rgba(255,210,60,.20)}tr.dg-roads .dg-name::before{content:'❄ '}tr.dg-best td{background:rgba(80,200,120,.16)}tr.dg-best .dg-name::before{content:'★ '}.alist td.f{white-space:nowrap}.card{overflow:hidden}
@media(max-width:1000px){#beep,#macbeep{display:none!important}.player .np .name{font-size:.85rem;max-height:2.4em;overflow:hidden}}
/* (v160) phone play bar: freq + name on top, level, then big equal buttons; the (…) button opens volume / squelch / tune / output */
@media(max-width:760px){
.player{display:grid!important;grid-template-columns:1fr!important;grid-template-rows:auto auto auto auto;gap:6px!important;padding:10px 12px calc(8px + 0px)!important}
.player .np{grid-column:1!important;grid-row:1!important;display:flex;flex-direction:row!important;align-items:baseline;gap:10px;min-width:0!important}
.player .np .freq{font-size:1.7rem!important;flex:0 0 auto}.player .np .name{flex:1 1 auto;min-width:0;font-size:.95rem!important;color:var(--ink)!important}
.player .mid{grid-column:1!important;grid-row:2!important}
.player .btns{grid-column:1!important;grid-row:3!important;display:grid!important;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px!important;max-width:none!important}
.player .btns>*{max-width:none!important}
.player .btns>#monsel{grid-column:1/-1;grid-row:1;width:100%;max-width:none!important;min-height:44px;font-size:1rem}
.player .btns>#monbtn{grid-column:1/3;grid-row:2}.player .btns>#quickbtn{grid-column:3/4;grid-row:2}.player .btns>#stop{grid-column:4/5;grid-row:2}
.player .btns>#expbtn{grid-column:1/-1;grid-row:3;min-height:40px}
.player .btns>button{min-height:48px;font-size:1rem;padding:6px}
.player.exp .btns{grid-row:3!important}.player.exp .btns>#tune,.player.exp .btns>#tunemode,.player.exp .btns>#tunebtn,.player.exp .btns>#outsel{grid-column:1/-1;width:100%!important;min-height:44px;font-size:1rem}
.player .ctl{font-size:.95rem}.player .ctl input[type=range]{width:100%!important;min-width:120px}
main{padding-bottom:330px!important}
}
/* (v175) layout pass: no empty block beside the player, a shorter dock, tighter tiles */
@media(min-width:761px){
.player{grid-template-columns:minmax(140px,240px) minmax(0,1fr)!important;grid-template-rows:auto auto!important;gap:6px 14px!important;padding:8px clamp(12px,1.6vw,32px) 10px!important}
.player .np{grid-column:1!important;grid-row:1!important;flex-direction:row!important;align-items:baseline;gap:10px;min-width:0!important}
.player .np .freq{font-size:1.25rem!important;flex:0 0 auto}
.player .np .name{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;max-height:none!important;color:var(--ink)}
.player .btns{grid-column:2!important;grid-row:1!important;justify-content:flex-end!important}
.player .mid{grid-column:1/-1!important;grid-row:2!important}
.player .ctl{flex-wrap:nowrap!important}.player .ctl label{white-space:nowrap}
.player .stat{white-space:nowrap!important;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:1 1 0;max-height:none!important}
#outsel{max-width:150px}#tune{width:100px!important;min-width:90px!important}
main{padding-bottom:130px!important}
.tile{padding:8px 12px}.tile b{font-size:1.25rem}.tiles{margin-bottom:10px}
}
@media(min-width:761px) and (max-width:1500px){#outhint{display:none}}
@media(min-width:761px) and (max-width:1100px){
.tiles{grid-template-columns:repeat(3,minmax(0,1fr))!important}
.player .stat{flex:1 1 100%!important}
.player{grid-template-columns:minmax(0,1fr)!important;grid-template-rows:auto auto auto!important}
.player .btns{grid-column:1!important;grid-row:2!important;justify-content:flex-start!important}
.player .mid{grid-row:3!important}
.player .ctl{flex-wrap:wrap!important}
main{padding-bottom:210px!important}
}
@media(max-width:760px){
.player .btns>#monsel{grid-column:1/4!important}
.player .btns>#expbtn{grid-column:4/5!important;grid-row:1!important;min-height:44px!important}
.player .btns>button{min-height:44px!important}
main{padding-bottom:270px!important}
#popout{display:none!important}
#sirenbtn{flex:1 1 140px!important;width:auto!important;min-height:40px!important;padding:6px 10px!important;font-size:.95rem!important;margin:0!important}
}
#chips{display:flex;flex-wrap:wrap;gap:6px}.chip{white-space:nowrap}
/* (v178) Digital tab: one list of systems, each row opens to its frequencies */
.dgh{font:600 .74rem/1 var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--mute);margin:16px 2px 6px}
/* (v187) spectrum panel: data fields (small caption, large value) over a plot with real scales; numbered flags + a legend, no text on the trace */
.specbox{margin:0 0 10px;border:1px solid var(--line);border-radius:12px;background:var(--card);padding:8px 10px;position:relative}
.specbar{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px;margin-bottom:6px}
.sf{background:var(--bg2);border:1px solid var(--line);border-radius:8px;padding:5px 10px 6px;min-width:0}
.sf i{display:block;font:600 .64rem/1.3 var(--mono);letter-spacing:.12em;color:var(--mute);font-style:normal;text-transform:uppercase}
.sf b{display:block;font:700 1.3rem/1.15 var(--mono);color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sf span{display:block;font-size:.8rem;line-height:1.25;color:var(--mute);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sf.cur b{color:var(--acc)}
#spec{width:100%;height:230px;display:block;cursor:crosshair;touch-action:pan-y}
.speclegend{display:flex;flex-wrap:wrap;gap:5px;margin-top:6px}
.sl{display:inline-flex;align-items:center;gap:6px;padding:3px 10px 3px 4px;border-radius:999px;font-size:.84rem;background:transparent;max-width:100%;min-height:0}
.sl i{display:inline-flex;align-items:center;justify-content:center;width:21px;height:21px;border-radius:50%;background:var(--ink);color:#0b0e13;font:700 .74rem var(--mono);font-style:normal;flex:0 0 auto}
.sl.cc i{background:#ffb050}.sl b{font-family:var(--mono)}.sl span{color:var(--mute);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:230px}.sl u{text-decoration:none;color:var(--acc);font-family:var(--mono)}
@media(max-width:760px){.specbar{grid-template-columns:repeat(2,minmax(0,1fr))}.sf b{font-size:1.08rem}#spec{height:190px}.speclegend{flex-wrap:nowrap;overflow-x:auto;-webkit-overflow-scrolling:touch}.sl{flex:0 0 auto}}
.dgsec>summary{cursor:pointer;list-style:none;padding:8px 2px}.dgsec>summary::-webkit-details-marker{display:none}.dgsec>summary:before{content:'▸ '}.dgsec[open]>summary:before{content:'▾ '}.dgsec>summary .sv{text-transform:none;letter-spacing:0;font-family:inherit}.dgsec[open]>summary .sv{display:none}
.dgx{border:1px solid var(--line);border-radius:12px;margin:6px 0;background:rgba(255,255,255,.02)}
.dgx[open]{border-color:var(--acc)}
.dgx>summary{list-style:none;display:flex;align-items:center;gap:10px;padding:7px 12px;cursor:pointer;min-height:48px}
.dgx>summary::-webkit-details-marker{display:none}
.dgx>summary::after{content:'▾';margin-left:auto;color:var(--mute);flex:0 0 auto}
.dgx[open]>summary::after{content:'▴'}
.dgx>summary .play{flex:0 0 auto}
.dgn{font-weight:700;font-size:1.02rem;min-width:0;flex:1 1 180px}
.dgx>summary .tag{flex:0 0 auto;white-space:nowrap}
.dgs{color:var(--acc);font-size:.9rem;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:40%}.dgs:empty{display:none}
.dgb{padding:6px 12px 10px;border-top:1px solid var(--line)}
.dgf{display:flex;align-items:center;gap:10px;padding:5px 0;border-bottom:1px solid rgba(255,255,255,.05);flex-wrap:wrap}
.dgf:last-child{border-bottom:0}.dgf .f{font-family:var(--mono);font-weight:600;min-width:96px}
.dgx.dg-roads{background:rgba(255,210,60,.10)}
details.card>summary{cursor:pointer;list-style:none;display:flex;align-items:center;gap:8px}
details.card>summary::-webkit-details-marker{display:none}
details.card>summary h3{margin:0;flex:1 1 auto}
details.card>summary::after{content:'▾';color:var(--mute)}details.card[open]>summary::after{content:'▴'}
details.card[open]>summary{margin-bottom:10px}
@media(max-width:760px){.dgx>summary .tag{display:none}.dgn{flex:1 1 0;font-size:1rem}.dgs{max-width:34%}}
</style></head><body><div class="app">
<div id="dbanner" style="display:none;background:#b33b3b;color:#fff;padding:10px 16px;font-weight:600;line-height:1.35"></div>
<header>
 <div><h1>SDR Scanner</h1><div class="sub" id="hdrsub">connecting…</div></div>
 <span class="pill" id="status"><i class="dot"></i><span>…</span></span><span class="pill" id="engine" title="what the dongle is doing right now"><i class="dot"></i><span>dongle: …</span></span>
 <a class="ghost btnlink" href="/api/chirp" title="download a CHIRP file of every voice channel in the bank, tones and offsets filled in (UV-32 range only)">⬇ CHIRP file</a><div class="seg" id="modeseg"><button data-m="scan" class="on">Scan</button><button data-m="listen">Listen</button></div>
 <button id="sirenbtn" class="ghost" style="border-color:#e5484d;color:#e5484d;font-weight:600" title="Press when you hear sirens: goes straight to the Toronto Fire / Paramedic channels and searches the sites until it finds traffic">🚨 I hear sirens</button><span class="sv" id="sirenmsg"></span>
 <button class="ghost" id="popout" title="open the live player, spectrum and Monitor in a small window of their own - the main page stays for browsing">⧉ Live window</button>
 <span class="sub" style="margin-left:auto" id="phone"></span>
</header>
<main>
 <!-- (v137) the head unit: what a real scanner radio shows - the channel it is on, SCANNING / STOPPED ON / HOLD, and the keys -->
 <div class="card headunit" id="headunit" style="display:none"><div class="hu-top"><div class="hu-lcd"><div class="hu-stat" id="hu-stat">SCANNING</div><div class="hu-freq" id="hu-freq">—</div><div class="hu-name" id="hu-name"></div><div class="hu-sub sub" id="hu-sub"></div></div>
  <div class="hu-keys"><button class="ghost" id="hu-hold" title="stay on this channel / window (space)">HOLD</button><button class="ghost" id="hu-skip" title="hop to the next one now (s)">SKIP</button><button class="ghost" id="hu-lock" title="never stop on this channel again (l) - undo in the lockout list">LOCK OUT</button><button class="ghost" id="hu-stop" title="stop the scanner">STOP</button>
  <label class="sub" style="display:flex;align-items:center;gap:6px;margin-left:4px" title="scan by itself whenever nobody is using the dongle; yields to the discovery sweep 2 min in every 10">auto <select id="hu-auto" style="font:inherit;background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:4px 6px"><option value="">off</option><option value="Top">Top 20 today</option><option value="All">all heard</option><option value="Walkies">Walkies</option><option value="Airport">Airport</option><option value="TTC Etobicoke">TTC Etobicoke (analog)</option><option value="Union Station">Union Station (platform ops)</option><option value="CP Etobicoke Yard">CP Rail Etobicoke Yard (freight)</option><option value="GFL Environmental">GFL Environmental (Oakville)</option><option value="Solid Waste Mgmt">Solid Waste Mgmt (Etobicoke)</option><option value="Follow traffic">Follow traffic</option></select></label><label class="sub" style="display:flex;align-items:center;gap:6px;margin-left:4px">delay <input id="hu-delay" type="range" min="0.5" max="20" step="0.5" style="width:90px"><span id="hu-delayv" class="sv">6 s</span></label></div></div>
  <div class="sub" id="hu-lockouts"></div></div>
 <div class="tiles" id="tiles"></div>
 <div class="bands" id="bands"></div>
 <div id="specwrap" class="specbox">
  <div class="specbar">
   <div class="sf"><i>Band</i><b id="sfband">—</b><span id="sfrange"></span></div>
   <div class="sf"><i>Strongest</i><b id="sfpeak">—</b><span id="sfpeakn"></span></div>
   <div class="sf"><i>Noise floor</i><b id="sffloor">—</b><span id="sfwhen"></span></div>
   <div class="sf cur"><i>Cursor</i><b id="sfcur">—</b><span id="specread">point at the trace</span></div>
  </div>
  <canvas id="spec"></canvas>
  <div id="speclegend" class="speclegend"></div>
  <div class="sub" id="spectitle" style="margin-top:4px"></div>
 </div>
 <div class="tabs"><button class="on" data-t="freq">Frequencies</button><button data-t="dir">Directory</button><button data-t="radio">FM Radio</button><button data-t="recs">Recordings <span id="reccount" class="tag"></span></button><button data-t="digital">Digital</button><button data-t="map">Map</button><button data-t="air">Aircraft</button><button data-t="debug">Debug</button></div>
 <section id="tab-freq">
  <div class="toolbar"><input id="q" type="search" placeholder="Search frequency, callsign, service…"><div id="chips"></div><button id="digtog" class="ghost" style="margin-left:auto;padding:4px 10px;font-size:.82rem;display:none" title="P25 and DMR channels are decoded by SDRTrunk - they play from the Digital tab. Tap to show or hide them in this list (a search always finds them)."></button><input id="hidecont" type="checkbox" checked hidden></div>
  <div class="tablewrap"><table><thead><tr><th style="cursor:default"></th><th data-k="rank" data-t="n">#</th><th data-k="mhz" data-t="n">MHz</th><th data-k="name" data-t="s">Name</th><th data-k="vrank" data-t="n">Voice</th><th data-k="cat" data-t="s">Category</th><th data-k="service" data-t="s">Service</th><th data-k="mode" data-t="s">Mode</th><th data-k="snr" data-t="n">Signal</th><th data-k="busy" data-t="n">Busy</th><th data-k="recent" data-t="n">Activity</th><th data-k="heard_ts" data-t="n" class="desc">Last heard</th><th data-k="tone" data-t="s">Tone</th><th data-k="uv32" data-t="n">Radio</th></tr></thead><tbody id="rows"></tbody></table><div class="empty" id="empty" style="display:none">Nothing above the noise floor yet - the first band takes a few minutes.</div><div id="morerow" style="display:none;text-align:center;padding:10px"><button class="ghost" id="morebtn">Show 20 more</button> <button class="ghost" id="allbtn">Show all</button> <span class="sv" id="morenote"></span></div></div>
 </section>
 <section id="tab-dir" style="display:none"><div class="toolbar"><input id="dq" type="search" placeholder="Search the directory: tower, VE3, GMRS, marine…"><span class="sub" style="color:var(--mute)">Every known channel - press ▶ to tune it whether or not the scan has heard it yet.</span></div>
  <div class="tablewrap"><table><thead><tr><th style="cursor:default"></th><th data-dk="mhz" data-dt="n">MHz</th><th data-dk="name" data-dt="s">Name</th><th data-dk="cat" data-dt="s">Category</th><th data-dk="mode" data-dt="s">Mode</th><th style="cursor:default">Program</th><th data-dk="snr" data-dt="n" class="desc">Heard in this scan</th><th style="cursor:default">Notes</th><th data-dk="uv32" data-dt="n">Radio</th></tr></thead><tbody id="dirrows"></tbody></table></div></section>
 <section id="tab-radio" style="display:none"><p class="sub" style="color:var(--mute);margin-top:0">Broadcast FM - the audio test. If a station plays, dongle, antenna and player are all good.</p><div class="radio" id="radio"></div></section>
 <section id="tab-recs" style="display:none">
  <div class="dig" style="margin-bottom:14px"><div class="card"><h3>Alerts <span class="sv">(Mac notification + log; phone too if you keep the dashboard open there)</span></h3>
   <div class="btns" style="flex-wrap:wrap"><input id="alval" type="search" placeholder="frequency, category or a word said on air" style="min-width:260px"><select id="alkind" style="font:inherit;background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:9px;padding:7px"><option value="mhz">when this MHz is heard</option><option value="cat">when this category is heard</option><option value="kw">when this word is said</option></select><button class="pri" id="aladd">Add alert</button></div>
   <div id="alrules" class="sv" style="margin:8px 0"></div><div id="allog"></div></div></div>
  <div class="tablewrap" id="recs"></div></section>
 <section id="tab-air" style="display:none">
  <div class="card"><h3>Every aircraft within 60 nm <span id="aircount" class="tag"></span> <span id="airsrc" class="tag" style="font-weight:normal"></span> <span style="float:right;display:inline-flex;gap:6px"><button class="pri" id="airtune" title="tune the radio to the channel the nearest airborne aircraft is most likely on">🎯 Tune nearest</button><button class="ghost" id="airfollow" title="keep re-tuning to whoever is nearest (every 15 s, only while this started the listen)">Follow nearest</button><button class="ghost" id="airtraffic" title="Monitor every channel the planes are on right now, all at once - the list re-derives itself from ADS-B at the start of every lap (_helpers/follow_traffic.py)">✈ Follow traffic</button></span></h3>
   <details class="airhelp"><summary>How this works</summary><p class="sub">Positions are live ADS-B within 60 nm of home, nearest first, refreshed every 10 s: <b>your own receiver</b> if <code>aircraft_source.txt</code> holds its aircraft.json URL, else Flightradar24 (token in <code>fr24_api_key.txt</code>), else the open adsb.fi / adsb.lol feeds, else OpenSky. The channel shown for an aircraft is <b>worked out from where it is and what it is doing</b> (lined up with a runway, climbing out, on the ground) - it is not read off the radio. It is certain only once the scanner hears that callsign on a channel, which shows as ✓ heard. Tap a row for the details, ▶ to tune. Emergency squawks (7500/7600/7700) are red.</p></details>
   <div id="airemerg"></div>
   <div id="airboard" class="sub" style="margin:6px 0 10px"></div>
   <div id="airtoday" class="sub" style="margin:0 0 10px"></div>
   <div class="tablewrap"><table class="alist"><thead><tr><th>Flight</th><th>Said on air as</th><th>Route</th><th>Aircraft</th><th>Distance</th><th>Altitude</th><th>Speed</th><th>Hdg</th><th>Heard on airband</th></tr></thead><tbody id="airrows"><tr><td colspan="9" class="empty">loading…</td></tr></tbody></table></div>
</div>
  </div>
 </section>
 <section id="tab-map" style="display:none"><div class="card"><h3>Map</h3><p class="sub">Tap the map to set <b>home</b> (saved). Rings are 5 / 10 / 20 km. Pins are the airports and the CN Tower repeater site. P25 tower coordinates are not published, so they are not drawn; unit GPS is not carried on this system.</p><div id="mapbox" style="height:420px;border-radius:12px;border:1px solid var(--line,#444)"></div><div class="sv" id="mapinfo" style="margin-top:6px"></div></div></section>
 <section id="tab-digital" style="display:none">
  <div class="dig">
   <div class="card"><h3>Digital systems</h3>
    <p class="sub">Tap a row to open it - every frequency inside has its own ▶. SDRTrunk does the decoding; encrypted traffic (police, most unnamed talkgroups) stays silent. One dongle decodes one system at a time.</p>
    <div class="btns" style="margin-bottom:4px"><button class="pri" id="dmrscanbtn">▶ Scan all DMR</button><button class="pri" id="p25scanbtn" title="steps through every P25 site (Fire/EMS first, then City, PATH, Metrolinx) - one dongle hears one site at a time">▶ Scan all P25 sites</button><button class="ghost" id="dmrscannext" title="skip to the next window now" style="display:none">SKIP window</button><select id="dmrscanstep" style="font:inherit;background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:6px 8px"><option value="60">60 s per window</option><option value="90" selected>90 s per window</option><option value="180">3 min per window</option><option value="300">5 min per window</option></select><span id="dmrscanst" class="sv"></span><span id="dmrscanwin" class="sv"></span></div>
    <div id="dglist"><div class="empty">loading…</div></div>
   </div>
   <div class="card"><h3>Calls decoded right now (SDRTrunk)</h3>
    <table class="alist"><thead><tr><th>Time</th><th>Talkgroup</th><th>Event</th><th>From</th></tr></thead><tbody id="digcalls"><tr><td colspan="4" class="empty">press ▶ on a system above</td></tr></tbody></table>
   </div>
   <details class="card"><summary><h3>Talkgroups and radios heard</h3></summary>
    <p class="sub">Rolled up from every call SDRTrunk decodes (kept across restarts). SDRTrunk plays <b>everything</b> it decodes by default. <b>Hone in</b> on a talkgroup to hear only that one (SDRTrunk restarts, about 3 s); press it again to hear everything. Police is encrypted: you see that it is talking, how often and for how long, but not the audio.</p>
    <div class="btns" id="tgchips"></div>
    <table class="alist"><thead><tr><th>Group</th><th>Talkgroup</th><th>Calls</th><th>Talk time</th><th>Last 60 min</th><th>Last heard</th><th title="hone in = hear only this talkgroup">Hone in</th></tr></thead><tbody id="tgrows"><tr><td colspan="7" class="empty">nothing decoded yet - press ▶ on a P25 site</td></tr></tbody></table>
    <h3 style="margin-top:14px">Who is talking - radios heard (unit IDs)</h3>
    <p class="sub">Every call carries the sending radio's ID. The talkgroups a radio uses say what it is (a pumper on TFS W Ops, a medic on EMS A3, a Willowbrook yard tug). Newest first; the table is the roll-up of every SDRTrunk session.</p>
    <table class="alist"><thead><tr><th>System</th><th>Radio ID</th><th>Mostly on</th><th>Talkgroups used</th><th>Calls</th><th>First / last heard</th></tr></thead><tbody id="unitrows"><tr><td colspan="6" class="empty">nothing decoded yet</td></tr></tbody></table>
   </details>
   <details class="card"><summary><h3>Take turns between the sweep and P25 (rotation)</h3></summary>
    <p class="sub">The dongle can only do one thing at a time, so the dashboard takes turns: the normal sweep runs, and every so often it hands the dongle to SDRTrunk for a few minutes, logs every call it decodes (list below, kept across restarts) and stamps the P25 site's Last heard. Your own ▶ always wins - a slot never starts while you listen, and a listen you start is never cut.</p>
    <div class="btns"><label class="sv"><input type="checkbox" id="roton"> include P25 in the rotation</label>
     <label class="sv">site <select id="rotsite"><option>West Simulcast</option><option>South Simulcast</option><option>North Simulcast</option><option>East Simulcast</option></select></label>
     <label class="sv">for <input id="rotmin" type="number" min="1" max="30" value="3" style="width:52px"> min</label>
     <label class="sv">every <input id="rotevery" type="number" min="5" max="120" value="15" style="width:56px"> min</label><span class="sv" id="rotstat"></span></div>
   </details>
   <details class="card"><summary><h3>Call history</h3></summary>
    <table class="alist"><thead><tr><th>When</th><th>Talkgroup</th><th>Event</th><th>From</th></tr></thead><tbody id="dighist"><tr><td colspan="4" class="empty">nothing logged yet</td></tr></tbody></table>
   </details>
   <details class="card"><summary><h3>How it plays, playlist and SDRTrunk controls</h3></summary>
    <p class="sub">Press ▶ on any P25 or DMR row (here, or in the Frequencies table) and the dashboard stops the sweep, writes the SDRTrunk playlist with that channel auto-starting, launches SDRTrunk and lets it decode: the audio comes out of the Mac, calls are listed by talkgroup name (TFS Disp 1, EMS A3 SW SB…) in the SDRTrunk window that opens. ■ Stop (or the space bar) closes SDRTrunk and the sweep resumes. SDRTrunk needs 10-20 s to start - the status line counts it down.</p>
    <div class="btns"><button class="ghost" id="takeback">Stop SDRTrunk, take the dongle back</button><button class="ghost" id="buildpl">Rebuild playlist only</button><span class="sv" id="sdrtstat"></span></div>
    <p class="sub">Playlist: <span id="plinfo">…</span>. <span id="plpath"></span></p>
    <p class="sub" id="plpath"></p>
   </details>
  </div>
 </section>
 <section id="tab-debug" style="display:none"><div class="toolbar"><button onclick="loadDebug()">Refresh</button><button onclick="appRestart(0)" title="restarts the dashboard program once nobody is listening; the sweep keeps running">Restart app (when idle)</button><button onclick="appRestart(1)" class="stopb" title="restarts right now, even mid-listen">Restart now</button><span id="dbgsum" class="sub" style="color:var(--mute)"></span></div><pre id="dbgout">loading…</pre></section>
</main>
<div class="player">
 <div class="np"><div class="freq" id="pfreq">—</div><div class="name" id="pname">Nothing playing</div></div>
 <div class="mid">
  <div class="lvl" id="lvl"><i></i><s></s></div>
  <div class="ctl">
   <label>Vol <input id="vol" type="range" min="0" max="1" step="0.05" value="0.8"></label>
   <label><input id="auto" type="checkbox" checked> Auto squelch</label>
   <label>Manual <input id="sq" type="range" min="0" max="60" value="15" style="width:90px"> <span id="sqv">15</span></label>
   <label><input id="voice" type="checkbox" checked> Voice filter</label>
   <span class="stat" id="pstat">idle</span>
  </div>
 </div>
 <div class="btns"><input id="tune" type="search" placeholder="Tune MHz" style="min-width:110px;width:120px;font-family:var(--mono)"><select id="tunemode" style="font:inherit;background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:9px;padding:7px"><option value="fm">NFM</option><option value="am">AM</option><option value="wbfm">WFM</option></select><button id="tunebtn">Tune</button><select id="outsel" title="where the audio comes out" style="font:inherit;background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:9px;padding:7px"><option value="auto">Output: auto</option><option value="mac">Output: Mac speakers</option><option value="browser">Output: this device</option></select><span id="outhint" class="sv" style="align-self:center"></span><select id="monsel" title="what Monitor hops through" style="font:inherit;background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:6px 8px;max-width:132px"><option value="">Bank: all heard</option><option value="Top">Top 20 today</option><option value="Walkies">Walkies</option><option value="Airport">Airport</option><option value="Follow traffic">Follow traffic (ADS-B)</option></select><button class="pri" id="monbtn" title="scanner mode: hops through the chosen list as fast as the dongle allows and locks on whoever is talking">Monitor</button><button id="quickbtn" title="quick scan: every channel heard in the last 24 h, busiest first - the fastest sweep of what is actually used here; locks on whoever talks">Quick scan</button><button class="ghost" id="beep" title="plays a short beep through this browser - if you hear it, the player works">Test sound</button><button class="ghost" id="macbeep" title="plays the Mac Glass sound through the Mac speakers and shows which output device / volume the Mac is using">Mac beep</button><button class="ghost" id="macbtn" title="play through the Mac speakers instead">Mac</button><button class="danger" id="stop">Stop</button></div>
</div>
<div class="drawer" id="drawer"><button class="close ghost" onclick="closeDrawer()">✕</button><div id="dinfo"><h3 id="dfreq">—</h3><div class="sub" id="dname"></div><div class="btns" style="margin-bottom:12px"><button class="pri" id="dplay">▶ Listen</button><button class="ghost" id="dalert" title="notify me whenever this channel is heard">🔔 Alert me</button><button class="ghost" id="dforget" title="remove this channel from the bank (it comes back if heard again)">Forget</button><button class="ghost" id="dwide" title="squelch opens on weaker audio and hangs 2.5 s on this channel (for expected transmissions)">open more</button></div><ul id="dlines"></ul><div id="dhist"></div><div id="drecs"></div></div><div id="dair" style="display:none"></div><div id="dactive" style="display:none"><h3>Active voice channels</h3><div class="sub">every channel with voice in the last 30 minutes, most active first</div><table class="alist"><thead><tr><th></th><th>MHz</th><th>Channel</th><th>Last heard</th><th>Activity 30 min</th><th>Signal</th></tr></thead><tbody id="alist"></tbody></table></div></div>
<div class="toast" id="toast"></div>
</div>
<script>
const $=s=>document.querySelector(s);const $$=s=>[...document.querySelectorAll(s)];
let S={hits:[]},rowsByMhz=new Map(),sortK='heard_ts',sortDir=-1,sortT='n',filter='All',query='',mode='scan',tab='freq';
try{const sv=JSON.parse(localStorage.getItem('sort3')||'null');if(sv){sortK=sv.k;sortDir=sv.d;sortT=sv.t;}filter=localStorage.getItem('filter2')||'All';}catch(e){}
const CATS=['All','Ham repeater','Ham','Airport','FRS/GMRS','Retail','City services','Marine','MURS','Railway','Business','Digital','Other','UV-32 only'];let hideCont=true;try{hideCont=localStorage.getItem('hidecont')!=='0';}catch(e){}
// (v184) P25 / DMR channels are decoded by SDRTrunk from the Digital tab; in this list they were 277 of 300 rows and always on top
let hideDig=true;try{hideDig=localStorage.getItem('hidedig')!=='0';}catch(e){}
async function appRestart(f){if(f&&!confirm('Restart the dashboard right now? Anything playing stops.'))return;const r=await (await fetch('/api/restart?force='+f)).json();toast('restart '+r.when+' - the page reconnects by itself');}
const SPEC={key:null,pin:false,data:null,t:0};
let AIRLAST=null;async function loadAir(){try{const a=await (await fetch('/api/aircraft')).json();AIRLAST=a;$('#aircount').textContent=a.n||0;$('#airsrc').textContent=a.source?('via '+a.source):'';let heard={};try{const s=await (await fetch('/api/recent_text')).json();heard=s||{};}catch(e){}
 $('#airemerg').innerHTML=(a.emergencies||[]).length?`<p style="color:var(--red);font-weight:600">⚠ Emergency squawk: ${a.emergencies.map(esc).join(', ')}</p>`:'';
 const hel=(a.helis||[]);
 $('#airboard').innerHTML=(hel.length?`<div class="card" style="padding:8px 12px"><b>🚁 Helicopters</b> ${hel.map(x=>`<button class="ghost" style="padding:2px 8px;margin-left:6px" onclick="openAir('${esc(x.icao)}')">${esc(x.callsign)} <span class="sv">${esc(x.type||'')}${x.operator?' · '+esc(x.operator):''} · ${x.km} km ${['N','NE','E','SE','S','SW','W','NW'][Math.round((x.bearing||0)/45)%8]}${x.alt_ft?' · '+x.alt_ft.toLocaleString()+' ft':''}</span></button>`).join('')}</div>`:'');
 const dirn=b=>['N','NE','E','SE','S','SW','W','NW'][Math.round(b/45)%8];
 const vr=x=>x.vrate?(x.vrate>200?' ↑':x.vrate<-200?' ↓':''):'';
 $('#airrows').innerHTML=(a.aircraft||[]).length?a.aircraft.map(x=>`<tr class="acrow" data-icao="${esc(x.icao)}" style="cursor:pointer${x.emergency?';color:var(--red)':''}"><td><a href="${x.fr24}" target="_blank" rel="noopener" style="color:var(--acc);font-family:var(--mono)">${esc(x.callsign||x.icao)}</a>${x.heli?' 🚁':''}${x.on_ground?' <span class="tag">ground</span>':''}${x.emergency?' <span class="tag" style="background:var(--red);color:#fff">'+esc(x.squawk)+'</span>':''}</td><td class="sv">${esc((a.spoken||{})[x.callsign]||'')}</td><td class="sv">${esc(x.route||'')}</td><td class="sv" title="${esc(x.desc||'')}">${esc(x.type||'')}${x.reg?' <span class="mute">'+esc(x.reg)+'</span>':''}${x.operator?'<br><span class="mute" style="font-size:.85em">'+esc(x.operator)+'</span>':''}</td><td>${x.km} km ${dirn(x.bearing)}</td><td class="sv">${x.alt_ft?x.alt_ft.toLocaleString()+' ft'+vr(x):'—'}</td><td class="sv">${x.speed_kt?x.speed_kt+' kt':'—'}</td><td class="sv">${x.track}°</td><td class="sv">${heard[x.callsign]?'✓ '+esc(heard[x.callsign]):''}</td></tr>`).join(''):`<tr><td colspan="9" class="empty">${a.error?'no ADS-B source reachable ('+esc(a.error)+')':'nothing airborne within 60 nm right now'}</td></tr>`;$$('#airrows tr.acrow').forEach(r=>r.onclick=e=>{if(e.target.closest('a'))return;openAir(r.dataset.icao);});capList($('#airrows').closest('table'),'tbody>tr','air');followTick();}catch(e){$('#airrows').innerHTML='<tr><td colspan="9" class="empty">'+esc(e.message)+'</td></tr>';}}
setInterval(()=>{if(tab==='air')loadAir();},10000);
// ---- (v132) aircraft detail drawer + one-tap tune. Any element can call openAir(icao) or openAirCs(callsign); the Monitor panel's
// "nearest now" links are hooked by delegation so a tap opens the drawer instead of leaving for Flightradar24.
let UV32={channels:{},exported:null};async function loadUV32(){try{UV32=await (await fetch('/api/uv32',{cache:'no-store'})).json();}catch(e){}}loadUV32();setInterval(loadUV32,300000);
function uv32(m){const k=Number(m).toFixed(4);return k in UV32.channels?('UV-32 ch '+UV32.channels[k]):'';}
async function loadAirToday(){try{const r=await (await fetch('/api/aircraft/today',{cache:'no-store'})).json();$('#airtoday').innerHTML=r.rows.length?`<details><summary style="cursor:pointer"><b>Over the house today</b> <span class="sv">${r.n} aircraft within 6 km (helicopters within 15) - closest first</span></summary><table class="alist" style="margin-top:4px"><thead><tr><th>Closest</th><th>Flight</th><th>Type</th><th>Alt</th><th>Route</th><th>Last</th></tr></thead><tbody>${r.rows.map(x=>`<tr><td><b>${x.km} km</b></td><td style="font-family:var(--mono)">${x.heli?'🚁 ':''}${esc(x.callsign||x.icao)}</td><td class="sv">${esc(x.type||'')}${x.operator?' · '+esc(x.operator):''}</td><td class="sv">${x.alt_ft?x.alt_ft.toLocaleString()+' ft':'—'}</td><td class="sv">${esc(x.route||'')}</td><td class="sv">${x.when}</td></tr>`).join('')}</tbody></table></details>`:'';}catch(e){}}
setInterval(()=>{if(tab==='air'&&!document.hidden)loadAirToday();},60000);
async function airData(){if(AIRLAST&&Date.now()-(AIRLAST._t||0)<12000)return AIRLAST;try{AIRLAST=await (await fetch('/api/aircraft',{cache:'no-store'})).json();AIRLAST._t=Date.now();}catch(e){}return AIRLAST;}
function tuneAir(f,n){if(P&&P.play)P.play(+f,'am',n||String(f));toast('tuning '+n+' · '+Number(f).toFixed(3));}
async function openAirCs(cs){const a=await airData();const x=(a&&a.aircraft||[]).find(y=>(y.callsign||y.icao)===cs||y.callsign===cs||y.icao===cs);if(x)openAir(x.icao);}
async function openAir(icao){const a=await airData();const x=(a&&a.aircraft||[]).find(y=>y.icao===icao);if(!x)return;let heard={};try{heard=await (await fetch('/api/recent_text')).json();}catch(e){}
 const ft=v=>v!=null?Number(v).toLocaleString()+' ft':'—';const vr=x.vrate?(x.vrate>0?'↑ ':'↓ ')+Math.abs(x.vrate)+' ft/min':'level';
 const fq=(x.freqs||[]);const hot=new Set(((typeof S!=='undefined'&&S&&S.hits)||[]).filter(h=>h.mhz<137&&(h.recent||0)>0).map(h=>(+h.mhz).toFixed(4)));
 $('#dinfo').style.display='none';$('#dactive').style.display='none';const d=$('#dair');d.style.display='';
 d.innerHTML=`<h3 style="font-family:var(--mono)">${esc(x.callsign||x.icao)} ${x.emergency?'<span class="tag" style="background:var(--red);color:#fff">SQUAWK '+esc(x.squawk)+'</span>':''}</h3>
 <div class="sub">${esc(x.operator||'')}${x.operator&&(x.type||x.desc)?' · ':''}${esc(x.desc||x.type||'')}${x.reg?' · '+esc(x.reg):''}${x.route?' · <b>'+esc(x.route)+'</b>':''}</div>
 <div class="btns" style="margin:10px 0 12px;flex-wrap:wrap">${fq.length?`<button class="pri" onclick="tuneAir(${fq[0][0]},'${esc(fq[0][1])}')">▶ Tune now · ${esc(fq[0][1])} ${fq[0][0].toFixed(3)}</button>`:''}${fq.slice(1).map(f=>`<button class="ghost${hot.has(f[0].toFixed(4))?' hot':''}" onclick="tuneAir(${f[0]},'${esc(f[1])}')" title="${f[0].toFixed(3)} AM${uv32(f[0])?' · '+uv32(f[0]):''}">▶ ${esc(f[1])}${uv32(f[0])?' <span class="sv">'+uv32(f[0]).replace('UV-32 ch ','#')+'</span>':''}</button>`).join('')}<a class="ghost" style="padding:6px 10px;border:1px solid var(--line);border-radius:8px;color:var(--acc2)" href="${x.fr24}" target="_blank" rel="noopener">Flightradar24 ↗</a></div>
 <div class="sub" style="margin-bottom:10px"><b>${esc(x.phase||'')}</b> · ${esc(x.why||'')}</div>
 <ul id="dlines"><li><b>Position</b> ${x.km} km ${['N','NE','E','SE','S','SW','W','NW'][Math.round((x.bearing||0)/45)%8]} of home · ${x.lat.toFixed(3)}, ${x.lon.toFixed(3)}</li>
 <li><b>Altitude</b> ${ft(x.alt_ft)} · ${vr}${x.on_ground?' · on the ground':''}</li><li><b>Speed / heading</b> ${x.speed_kt||'—'} kt · ${x.track}°</li>
 <li><b>Squawk</b> ${esc(x.squawk||'—')} · <b>ICAO</b> ${esc(x.icao)}${x.seen!=null?' · seen '+x.seen+' s ago':''}</li>
 <li><b>Heard on airband</b> ${heard[x.callsign]?'✓ '+esc(heard[x.callsign]):'not in the recent transcripts'}</li>
 ${fq.length&&uv32(fq[0][0])?`<li><b>On your Baofeng</b> ${uv32(fq[0][0])} (${fq[0][1]})${fq.slice(1).filter(f=>uv32(f[0])).map(f=>' · '+uv32(f[0]).replace('UV-32 ch ','#')+' '+esc(f[1])).join('')}</li>`:''}<li><b>Feed</b> ${esc(a.source||'')}${fq.length?'':' · no channel in the bank for this phase - the active airband rows on the Frequencies tab are the best bet'}</li></ul>`;
 $('#drawer').classList.add('open');}
document.addEventListener('click',e=>{const a=e.target.closest('#monair a');if(!a)return;e.preventDefault();openAirCs(a.textContent.trim());});
function nearestAir(){const a=AIRLAST;if(!a)return null;return (a.aircraft||[]).find(x=>!x.on_ground&&(x.freqs||[]).length)||null;}
const FOLLOW={on:false,mhz:0};
async function tuneNearest(){await airData();const x=nearestAir();if(!x){toast('nobody airborne with a known channel right now');return;}FOLLOW.mhz=x.freqs[0][0];tuneAir(x.freqs[0][0],x.freqs[0][1]+' · '+(x.callsign||x.icao));}
function followTick(){if(!FOLLOW.on)return;const x=nearestAir();if(!x)return;const f=x.freqs[0][0];const cur=P&&P.cur?P.cur.mhz:0;if(cur&&Math.abs(cur-FOLLOW.mhz)>0.0005)return;/* the person tuned elsewhere - leave it */if(Math.abs(f-FOLLOW.mhz)>0.0005||!cur){FOLLOW.mhz=f;tuneAir(f,x.freqs[0][1]+' · '+(x.callsign||x.icao));}}
$('#popout').onclick=()=>{const w=window.open('/live','sdrlive','width=600,height=860,menubar=no,toolbar=no');if(!w)toast('allow pop-ups for this page');};
$('#airtune').onclick=tuneNearest;$('#airtraffic').onclick=()=>{if(P.cur&&P.cur.mode==='monitor'&&MONCAT){P.stop();MONCAT='';return;}QUICK=false;MONCAT='Follow traffic';P.play(1,'monitor','Follow traffic: the channels the planes are on');};$('#airfollow').onclick=()=>{FOLLOW.on=!FOLLOW.on;$('#airfollow').classList.toggle('pri',FOLLOW.on);$('#airfollow').textContent=FOLLOW.on?'Following nearest ✓':'Follow nearest';if(FOLLOW.on)tuneNearest();};
setInterval(()=>{if(FOLLOW.on){airData().then(followTick);}},15000);

/* (v162) iOS: an Audio element may only start from a tap. So one element is created and "unlocked" on the first tap anywhere, then reused for the
   SDRTrunk stream (started later from the timer), and a watchdog reconnects it if it stalls, ends or errors (phone sleep, SDRTrunk restart, silent gaps). */
let DIGA=null,DIGEL=null,DIGT=0,DIGN=0;const SILWAV="data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA=";
function unlockDig(){if(DIGEL)return;try{DIGEL=new Audio();DIGEL.setAttribute('playsinline','');DIGEL.src=SILWAV;const p=DIGEL.play();if(p&&p.catch)p.catch(()=>{DIGEL=null;});}catch(e){DIGEL=null;}}
['touchend','click','keydown'].forEach(ev=>document.addEventListener(ev,unlockDig,{passive:true}));
function digConnect(){if(!DIGEL)return false;DIGA=DIGEL;DIGA.src='/sdrt_audio?t='+Date.now();DIGA.volume=+$('#vol').value;DIGT=Date.now();DIGN=0;const p=DIGA.play();if(p&&p.then)p.then(()=>{toast('digital audio: streaming to this device');}).catch(e=>{$('#pstat').textContent='tap the screen once to allow audio ('+e.message+')';});return true;}
function digAudio(st){const want=!!(st&&st.stream)&&!onMac;
 if(want&&!DIGA){if(!digConnect()){$('#pstat').textContent='tap the screen once to allow audio';}}
 if(!want&&DIGA){try{DIGA.pause();DIGA.removeAttribute('src');DIGA.load();}catch(e){}DIGA=null;}}
setInterval(()=>{if(!DIGA)return;const a=DIGA,stuck=a.paused||a.ended||!!a.error||(a.currentTime===DIGN&&Date.now()-DIGT>12000);
 if(a.currentTime!==DIGN){DIGN=a.currentTime;DIGT=Date.now();}
 if(stuck&&Date.now()-DIGT>3000){DIGT=Date.now();try{a.src='/sdrt_audio?t='+Date.now();a.volume=+$('#vol').value;const p=a.play();if(p&&p.catch)p.catch(()=>{});}catch(e){}}},4000);
$('#vol').addEventListener('input',()=>{if(DIGA)DIGA.volume=+$('#vol').value;});
/* (v163) the spectrum panel only shows while it has something live to draw; with SDRTrunk holding the dongle (or no sweep running) it was an empty black box */
function specVis(){const w=$('#specwrap');if(!w)return;const live=!!SPEC.data&&(Date.now()-SPEC.t<150000)&&!(typeof P!=='undefined'&&P.sdrtOn);w.style.display=live?'':'none';}
setInterval(specVis,1500);setTimeout(specVis,300);
async function loadSpec(){if(!SPEC.key)return;try{const d=await (await fetch('/api/spectrum?band='+SPEC.key,{cache:'no-store'})).json();if(d.error){$('#spectitle').textContent=SPEC.key+': '+d.error;return;}SPEC.data=d;SPEC.t=Date.now();drawSpec();}catch(e){}}
// (v187) The strongest signals in the sweep, at most n and never closer together than minSep MHz, each matched to the known channel at
// that frequency when there is one. They get a numbered flag on the plot and a line in the legend under it.
function specPeaks(d,n,minSep){const c=[],w=Math.max(8,Math.round(d.y.length/60));
 for(let i=1;i<d.y.length-1;i++){const v=d.y[i];if(v-d.floor<8||v<d.y[i-1]||v<=d.y[i+1])continue;
  const nb=d.y.slice(Math.max(0,i-w),i-2).concat(d.y.slice(i+3,i+w+1)).sort((p,q)=>p-q);            // it has to stand 8 dB clear of its own neighbourhood too:
  if(nb.length&&v-nb[nb.length>>1]<8)continue;c.push({mhz:d.x[i],db:v});}                              // a hump in the receiver's passband is not a signal
 c.sort((p,q)=>q.db-p.db);const out=[],tol=Math.max(0.0065,(d.hi-d.lo)/400);
 for(const p of c){if(out.length>=n)break;if(out.some(o=>Math.abs(o.at-p.mhz)<minSep))continue;
  let best=null;d.labels.forEach(l=>{const dd=Math.abs(l.mhz-p.mhz);if(dd<=tol&&(!best||dd<Math.abs(best.mhz-p.mhz)))best=l;});
  const hit=best?null:((typeof S!=='undefined'&&S.hits)||[]).find(h=>Math.abs(h.mhz-p.mhz)<=tol);       // not a labelled channel: the bank may still know it
  out.push({at:p.mhz,mhz:best?best.mhz:(hit?hit.mhz:p.mhz),db:p.db,name:best?best.name:(hit?hit.name:''),cc:!!(best&&best.kind==='cc')||!!(hit&&hit.cat==='Digital')});}
 return out.sort((p,q)=>p.at-q.at);}
function drawSpec(){const d=SPEC.data;if(!d)return;const c=$('#spec');const W=c.clientWidth||800,H=c.clientHeight||230,dpr=window.devicePixelRatio||1;c.width=W*dpr;c.height=H*dpr;const g=c.getContext('2d');g.scale(dpr,dpr);g.clearRect(0,0,W,H);
 const lo=d.lo,hi=d.hi,span=hi-lo||1,PL=40,PR=8,PT=24,PB=20;                 // plot box: dB scale on the left, MHz underneath, flags along the top
 const top=Math.max(...d.y),ymin=d.floor-4,ymax=Math.max(top+3,d.floor+30);
 const X=m=>PL+(m-lo)/span*(W-PL-PR),Y=v=>H-PB-(Math.max(ymin,Math.min(ymax,v))-ymin)/(ymax-ymin)*(H-PT-PB);
 g.lineWidth=1;g.font='12px ui-monospace,Menlo,monospace';
 // horizontal lines every 10 dB above the noise floor
 g.textBaseline='middle';g.textAlign='right';for(let v=0;d.floor+v<=ymax;v+=10){const y=Math.round(Y(d.floor+v))+.5;g.strokeStyle=v?'rgba(140,165,200,.15)':'rgba(140,165,200,.45)';g.beginPath();g.moveTo(PL,y);g.lineTo(W-PR,y);g.stroke();g.fillStyle='#8a97a8';g.fillText(v?'+'+v:'floor',PL-5,y);}
 // vertical lines at a round MHz step (1 - 2 - 2.5 - 5), as many as fit without the numbers touching
 const raw=span/Math.max(3,Math.floor((W-PL-PR)/95)),p10=Math.pow(10,Math.floor(Math.log10(raw))),step=[1,2,2.5,5,10].map(k=>k*p10).find(v=>v>=raw)||raw,dec=step>=1?0:(step>=0.1?1:(step>=0.01?2:3));
 g.textBaseline='alphabetic';for(let m=Math.ceil(lo/step-1e-9)*step;m<=hi+1e-9;m+=step){const x=Math.round(X(m))+.5;g.strokeStyle='rgba(140,165,200,.15)';g.beginPath();g.moveTo(x,PT);g.lineTo(x,H-PB);g.stroke();g.fillStyle='#aab4c2';g.textAlign=x<PL+20?'left':(x>W-PR-20?'right':'center');g.fillText(m.toFixed(dec),x,H-5);}
 // every known channel is a short tick on the baseline; its name shows in the Cursor field, never as text over the trace
 d.labels.forEach(l=>{const x=Math.round(X(l.mhz))+.5;g.strokeStyle=l.kind==='cc'?'rgba(255,176,80,.8)':'rgba(170,180,194,.6)';g.beginPath();g.moveTo(x,H-PB);g.lineTo(x,H-PB-5);g.stroke();});
 // the trace
 const path=()=>{g.beginPath();d.x.forEach((m,i)=>{const x=X(m),y=Y(d.y[i]);i?g.lineTo(x,y):g.moveTo(x,y);});};
 path();g.lineTo(X(d.x[d.x.length-1]),H-PB);g.lineTo(X(d.x[0]),H-PB);g.closePath();const gr=g.createLinearGradient(0,PT,0,H-PB);gr.addColorStop(0,'rgba(61,220,132,.55)');gr.addColorStop(1,'rgba(61,220,132,.10)');g.fillStyle=gr;g.fill();
 path();g.strokeStyle='#3ddc84';g.lineWidth=1.3;g.stroke();g.lineWidth=1;
 // numbered flags on the strongest signals
 const pk=specPeaks(d,Math.max(3,Math.min(9,Math.floor((W-PL-PR)/105))),span*19/(W-PL-PR));SPEC.peaks=pk;
 g.textAlign='center';g.textBaseline='middle';g.font='bold 11px ui-monospace,Menlo,monospace';
 pk.forEach((p,i)=>{const x=X(p.at),y=Y(p.db);g.strokeStyle='rgba(230,235,242,.45)';g.beginPath();g.moveTo(x,Math.max(PT-2,y-3));g.lineTo(x,PT-3);g.stroke();
  g.fillStyle=p.cc?'#ffb050':'#e6ebf2';g.beginPath();g.arc(x,11,9,0,7);g.fill();g.fillStyle='#0b0e13';g.fillText(String(i+1),x,11.5);});
 // data fields and legend
 const best=pk.slice().sort((p,q)=>q.db-p.db)[0];
 $('#sfband').textContent=d.key.toUpperCase();$('#sfrange').textContent=lo.toFixed(3)+' - '+hi.toFixed(3)+' MHz';
 $('#sfpeak').textContent=best?best.mhz.toFixed(4):'—';$('#sfpeakn').textContent=best?('+'+Math.round(best.db-d.floor)+' dB'+(best.name?' · '+best.name:'')):'nothing 8 dB above the floor';
 $('#sffloor').textContent=d.floor+' dB';$('#sfwhen').textContent='sweep '+String(d.when||'').split(' ').pop();
 $('#speclegend').innerHTML=pk.map((p,i)=>`<button class="ghost sl${p.cc?' cc':''}" data-m="${p.mhz}" data-cc="${p.cc?1:0}" data-n="${esc(p.name)}" title="${p.cc?'digital - opens the Digital tab':'listen'}"><i>${i+1}</i><b>${p.mhz.toFixed(4)}</b><span>${esc(p.name||'not in the bank')}</span><u>+${Math.round(p.db-d.floor)} dB</u></button>`).join('');
 $('#spectitle').textContent=SPEC.pin?'pinned to this band - click the sweeping band to follow the sweep again':'';}
(function(){const c=$('#spec');
 const at=e=>{const d=SPEC.data;if(!d)return null;const r=c.getBoundingClientRect(),PL=40,PR=8;const m=d.lo+Math.max(0,Math.min(1,(e.clientX-r.left-PL)/(r.width-PL-PR)))*(d.hi-d.lo);let bi=0,bd=1e9;d.x.forEach((x,i)=>{const dd=Math.abs(x-m);if(dd<bd){bd=dd;bi=i;}});
  const lab=d.labels.find(l=>Math.abs(l.mhz-m)<(d.hi-d.lo)/120);return {d,m,bi,lab};};
 c.onpointermove=e=>{const q=at(e);if(!q)return;$('#sfcur').textContent=q.m.toFixed(4);$('#specread').textContent=(q.d.y[q.bi]-q.d.floor>=0?'+':'')+Math.round(q.d.y[q.bi]-q.d.floor)+' dB over the floor'+(q.lab?' · '+q.lab.name:'');};
 c.onclick=e=>{const q=at(e);if(!q)return;let m=q.lab?q.lab.mhz:Math.round(q.m/0.0125)*0.0125;const md=m<137?'am':'fm';P.play(+m.toFixed(4),md,q.lab?q.lab.name:m.toFixed(4));};
 $('#speclegend').onclick=e=>{const b=e.target.closest('button.sl');if(!b)return;if(b.dataset.cc==='1'){const t=$('.tabs button[data-t="digital"]');if(t)t.click();toast('digital channel - play it from the Digital tab');return;}const m=+b.dataset.m;P.play(+m.toFixed(4),m<137?'am':'fm',b.dataset.n||m.toFixed(4));};
 window.addEventListener('resize',drawSpec);setInterval(()=>{if(SPEC.key&&Date.now()-SPEC.t>12000)loadSpec();},4000);})();
function toast(m){const t=$('#toast');t.textContent=m;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2200);}
function fmt(m){return Number(m).toFixed(4);}
// (v136) no list is longer than 20 items unless you ask: hide the rest and put a "show 20 more" button after the container.
const CAP={n:{},step:20};function capList(el,sel,key){const id=key||el.id||'x';const lim=CAP.n[id]||CAP.step;let i=0,hid=0;el.querySelectorAll(sel).forEach(r=>{if(r.style.display==='none'){r.classList.remove('capped');return;}i++;if(i>lim){r.classList.add('capped');hid++;}else r.classList.remove('capped');});
 let btn=el.parentElement.querySelector(':scope > .capmore');if(!btn){btn=document.createElement('button');btn.className='ghost capmore';btn.style.cssText='margin:8px auto;display:block';el.parentElement.appendChild(btn);btn.onclick=()=>{CAP.n[id]=(CAP.n[id]||CAP.step)+CAP.step;capList(el,sel,id);};}
 btn.textContent='show 20 more ('+hid+' hidden)';btn.style.display=hid?'block':'none';}
/* ---------- state polling ---------- */
async function poll(){try{const r=await fetch('/api/state',{cache:'no-store'});S=await r.json();render();}catch(e){$('#hdrsub').textContent='dashboard unreachable - is Dashboard.command running?';}}
const LIVE=location.pathname==='/live';if(LIVE){document.body.classList.add('live');document.title='SDR Live';}
function render(){
 $('#hdrsub').textContent=S.folder+' · updated '+S.now+(S.ms>400?' · server '+S.ms+' ms':'');$('#phone').textContent='phone: http://'+S.ip+':'+S.port;
 const st=$('#status');st.className='pill '+(S.running?(S.paused?'pause':'run'):'dead');st.lastElementChild.textContent=S.running?(S.paused?'PAUSED FOR LISTENING':'SCANNING'):'SCAN NOT RUNNING';
 const en=$('#engine');const act=S.bands.find(b=>b.active);{const db=$('#dbanner');if(S.dongle_flat){db.style.display='';db.innerHTML='&#9888; DONGLE NOT RECEIVING - '+esc(S.dongle_flat)+'.<br>Unplug it, wait 10 seconds, plug it into a USB port directly on the Mac mini (not the dock). The sweep resumes by itself.';}else db.style.display='none';}const usb=S.usb_errors||0;en.className='pill '+((usb||S.dongle_lost)?'pause':'run');en.lastElementChild.textContent=S.dongle_lost?'dongle: NOT RESPONDING - reconnect it (auto-resume)':'dongle: '+(S.player&&S.player.mhz?(S.player.mhz===1?'monitor (channelizer)':'listening '+fmt(S.player.mhz)):(S.background?S.background:(act?'sweeping '+act.band:'idle')))+(usb?' · '+usb+' USB errors':'');
 st.lastElementChild.textContent=S.running?(S.paused?(S.background?'PAUSED - '+S.background.split(':')[0].toUpperCase():(S.player&&S.player.mhz?'PAUSED FOR LISTENING':'PAUSED')):'SCANNING'):'SCAN NOT RUNNING';
 const nm=S.paused||S.player.mhz?'listen':'scan';if(nm!==mode){mode=nm;paintMode();}
 const voice=S.hits.filter(h=>!h.cont&&h.ver!=='DIGITAL'&&h.age_min!=null&&h.age_min<=30);const uv=voice.filter(h=>h.uv32).length;
 // strongest / busiest only among channels that are KNOWN voice (airport, repeater, FRS tables) or auditioned as voice - never a bare carrier
 const sure=voice.filter(h=>h.ver==='VOICE'||h.ver==='KNOWN');const pool=sure.length?sure:voice;const tag=sure.length?'':' · unverified';
 const top=pool.length?pool.reduce((a,b)=>b.snr>a.snr?b:a):null,busiest=pool.length?pool.reduce((a,b)=>(b.recent||0)>(a.recent||0)||((b.recent||0)===(a.recent||0)&&b.busy>a.busy)?b:a):null;
 setTiles([[voice.length,'voice channels heard in the last 30 min · '+S.hits.filter(h=>h.cat!=='Digital').length+' banked'+(S.hits.some(h=>h.cat==='Digital')?' + '+S.hits.filter(h=>h.cat==='Digital').length+' digital':'')],[uv,'UV-32 can tune'],[top?fmt(top.mhz):'—','strongest voice'+(top?' · '+top.name:'')+tag,top],[busiest?fmt(busiest.mhz):'—','busiest voice'+(busiest?' · '+busiest.name:'')+tag,busiest],[S.recordings,'recordings'],[S.bands.filter(b=>b.active).map(b=>b.band).join(', ')||'—',S.bands.length>1?'sweeping now · rotates through '+S.bands.length+' bands':'sweeping now']]);
 $('#reccount').textContent=S.recordings||'';
 $('#bands').innerHTML=S.bands.map(b=>`<div class="band ${b.active?'on':''}${SPEC.key===b.key?' sel':''}" data-key="${b.key}" title="show this band's spectrum"><i></i><b>${b.band}</b> ${b.rows} sweeps · ${b.kb} KB · ${b.last}</div>`).join('')||'<div class="band"><i></i>no band data yet</div>';
 $$('#bands .band[data-key]').forEach(el=>el.onclick=()=>{SPEC.key=el.dataset.key;SPEC.pin=true;loadSpec();});
 {const ab=S.bands.find(b=>b.active);if(!SPEC.pin&&ab&&ab.key!==SPEC.key){SPEC.key=ab.key;loadSpec();}}
 if(LIVE)return;renderRows();if(tab==='dir'&&DIR)renderDir();renderActive();
}
let lastTiles=[];function setTiles(v){lastTiles=v;const el=$('#tiles');if(!el.children.length){el.innerHTML=v.map(()=>'<div class="tile"><b></b><span></span><button class="play tplay" style="display:none" title="listen">▶</button></div>').join('');el.children[0].classList.add('clickable');el.children[0].title='click for the full list, most active first';el.children[0].onclick=e=>{if(e.target.closest('button'))return;openActive();};}
 v.forEach((t,i)=>{const c=el.children[i];const b=c.querySelector('b');if(b.textContent!==String(t[0])){b.textContent=t[0];c.classList.remove('flash');void c.offsetWidth;c.classList.add('flash');}
  const sp=c.querySelector('span');if(sp.textContent!==t[1])sp.textContent=t[1];
  const h=t[2],pb=c.querySelector('.tplay');if(h){pb.style.display='';pb.dataset.mhz=h.mhz;pb.dataset.mode=h.mode==='AM'?'am':'fm';pb.dataset.name=h.name;}else{pb.style.display='none';delete pb.dataset.mhz;}});P.paint();}
/* ---------- rows: patch in place ---------- */
function visible(h){if(hideCont&&h.cont)return false;if(hideDig&&filter==='All'&&!query&&h.cat==='Digital')return false;if(filter==='UV-32 only'&&!h.uv32)return false;if(filter!=='All'&&filter!=='UV-32 only'&&h.cat!==filter)return false;
 if(!query)return true;const hay=(fmt(h.mhz)+' '+h.name+' '+h.service+' '+h.cat+' '+h.tone).toLowerCase();return query.split(/\s+/).every(w=>hay.includes(w));}
function spark(hours,h){const pct=Math.max(0,Math.min(100,Math.round((h&&h.recent)||0)));const lit=Math.round(pct/10);
 const segs=Array.from({length:10},(_,i)=>`<i class="${i<lit?'on':''}"></i>`).join('');
 const lc=h&&h.last_call&&h.last_call.tg?`<div class="actl sv" title="last call SDRTrunk decoded on this channel (${h.last_call.calls} calls logged${h.last_call.enc?', '+h.last_call.enc+' encrypted':''})">📻 ${esc(h.last_call.tg)}${h.last_call.t?' '+h.last_call.t:''}</div>`:'';
 return `<div class="act" title="how much of the last 30 minutes of sweeps this channel was on the air (${pct}%)"><div class="actb">${segs}</div><div class="actl"><b>${pct}%</b> <span class="sv">${h&&h.last?'· last '+h.last:'· not yet'}</span></div>${lc}</div>`;}
function rowHTML(h){return `<td><button class="play" data-mhz="${h.mhz}" data-mode="${h.ver==='DIGITAL'?'digital':(h.mode==='AM'?'am':'fm')}" data-name="${esc(h.name)}" title="${h.ver==='DIGITAL'?'digital voice - decoded by SDRTrunk, plays through the Mac':'listen'}">▶</button></td><td class="sv rank">${h.live?h.rank:'<span class="tag" title="not in the current sweep - last known values">banked</span>'}</td><td class="f">${fmt(h.mhz)}</td><td><span class="nm">${esc(h.name)}</span></td><td class="vc">${voiceCell(h)}</td><td class="sv">${h.cat}</td><td class="sv">${esc(h.service)||'—'}</td><td class="sv">${h.mode}</td>
 <td><span class="meter"><i style="width:${Math.min(100,h.snr*2.5)}%"></i></span><span class="snr">${h.snr} dB</span></td><td class="busy">${h.busy}%${h.cont?' <span class="tag" style="border-color:#6b5520;color:var(--warn)" title="on 100% of the time - a data link, beacon or sweep artifact, not voice">cont.</span>':''}</td><td>${spark(h.hours,h)}</td><td class="heard">${heardCell(h)}</td><td>${h.tone?'<span class="tag tone">'+esc(h.tone)+'</span>':''}</td><td>${h.uv32?'<span class="tag uv">UV-32</span>':'<span class="tag sdr">SDR only</span>'}</td>`;}
function voiceCell(h){if(h.ver==='DIGITAL')return '<span class="vtag v4" title="digital voice (P25) - ▶ decodes it with SDRTrunk; audio plays through the Mac">◆ P25 digital</span>';if(h.ver==='VOICE')return '<span class="vtag v1" title="the dashboard listened and heard real speech here">✓ VOICE heard</span>';if(h.ver==='KNOWN')return '<span class="vtag v2" title="a known voice channel: airport, repeater, FRS/GMRS or store table">✓ known voice</span>';return '<span class="vtag v3" title="not auditioned yet - the dashboard checks it when nobody is listening">? unverified</span>';}
function agreeTag(a){if(a==null)return ' <span class="tag" style="opacity:.6" title="one engine only (whisper) - install professional transcription for a cross-check">1 engine</span>';if(a>=0.6)return ' <span class="tag" style="border-color:#1f5c3a;color:var(--acc)" title="two independent engines agree on '+Math.round(a*100)+'% of the words">✓ 2 engines agree</span>';return ' <span class="tag" style="border-color:#6b5520;color:var(--warn)" title="the two engines disagree ('+Math.round(a*100)+'% overlap) - treat as uncertain">? uncertain</span>';}
function heardCell(h){if(!h.heard_ts)return '<span class="sv">—</span>';const d=new Date(h.heard_ts*1000),a=h.age_min;const t=d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});const day=(Date.now()-d.getTime())>36e5*20?d.toLocaleDateString([],{month:'short',day:'numeric'})+' ':'';
 const rel=a==null?'':(a<1?'now':a<60?a+' min ago':a<1440?Math.round(a/60)+' h ago':Math.round(a/1440)+' d ago');const hot=a!=null&&a<=30;return `<b class="${hot?'hot':''}">${day}${t}</b><br><span class="sv">${rel}${h.times>1?' · '+h.times+'×':''}</span>`;}
function esc(s){return String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function renderRows(){paintDig();const tb=$('#rows');const seen=new Set();
 for(const h of S.hits){const k=fmt(h.mhz);seen.add(k);let tr=rowsByMhz.get(k);
  if(!tr){tr=document.createElement('tr');tr.className='row new';tr.dataset.mhz=k;tr.innerHTML=rowHTML(h);tr.onclick=e=>{if(e.target.closest('button'))return;openDrawer(tr._h);};rowsByMhz.set(k,tr);tb.appendChild(tr);}
  else{const d=tr._d||{};if(d.snr!==h.snr){tr.querySelector('.meter i').style.width=Math.min(100,h.snr*2.5)+'%';const s=tr.querySelector('.snr');s.textContent=h.snr+' dB';s.classList.remove('chg');void s.offsetWidth;s.classList.add('chg');}
   if(d.busy!==h.busy){const b=tr.querySelector('.busy');b.textContent=h.busy+'%';b.classList.remove('chg');void b.offsetWidth;b.classList.add('chg');}
   if(d.tone!==h.tone||d.name!==h.name||d.ver!==h.ver){tr.innerHTML=rowHTML(h);}
   if(d.recent!==h.recent||d.last!==h.last)tr.querySelector('.act').parentElement.innerHTML=spark(h.hours,h);if(d.heard_ts!==h.heard_ts||d.age_min!==h.age_min)tr.querySelector('.heard').innerHTML=heardCell(h);if(d.rank!==h.rank||d.live!==h.live)tr.querySelector('.rank').innerHTML=h.live?h.rank:'<span class="tag" title="not in the current sweep - last known values">banked</span>';}
  tr._d=h;tr._h=h;tr.classList.toggle('prev',!h.live);tr.classList.toggle('hot',h.age_min!=null&&h.age_min<=30);tr.style.display=visible(h)?'':'none';}
 for(const [k,tr] of rowsByMhz){if(!seen.has(k)){tr.remove();rowsByMhz.delete(k);}}
 applySort();$('#empty').style.display=S.hits.length?'none':'';P.paint();}
let ROWCAP=20;                                                        // no list longer than 20 rows on screen (phone scrolling); 'Show 20 more' / 'Show all'
function applySort(){const tb=$('#rows');const rows=[...tb.children];rows.sort((a,b)=>{let x=sortK==='rank'?a._h.rank:a._h[sortK],y=sortK==='rank'?b._h.rank:b._h[sortK];if(sortT==='n'){x=+x;y=+y;return (x-y)*sortDir;}return String(x).localeCompare(String(y))*sortDir;});rows.forEach(r=>tb.appendChild(r));
 let shown=0,hidden=0;rows.forEach(r=>{if(r.style.display==='none')return;if(shown>=ROWCAP){r.style.display='none';hidden++;}else shown++;});
 const mr=$('#morerow');if(mr){mr.style.display=hidden?'':'none';$('#morenote').textContent=hidden?shown+' of '+(shown+hidden)+' shown':'';}
 $$('th[data-k]').forEach(th=>{th.classList.remove('asc','desc');if(th.dataset.k===sortK)th.classList.add(sortDir>0?'asc':'desc');});}
$$('th[data-k]').forEach(th=>th.onclick=()=>{if(sortK===th.dataset.k)sortDir=-sortDir;else{sortK=th.dataset.k;sortT=th.dataset.t;sortDir=sortT==='n'?-1:1;}try{localStorage.setItem('sort3',JSON.stringify({k:sortK,d:sortDir,t:sortT}));}catch(e){}applySort();});
$('#chips').innerHTML=CATS.map(c=>`<span class="chip ${c===filter?'on':''}" data-c="${c}">${c}</span>`).join('');
function paintDig(){const n=(S.hits||[]).filter(h=>h.cat==='Digital').length,b=$('#digtog');if(!b)return;b.style.display=n?'':'none';b.textContent=hideDig?n+' digital hidden · show':'hide '+n+' digital';}
$('#digtog').onclick=()=>{hideDig=!hideDig;try{localStorage.setItem('hidedig',hideDig?'1':'0');}catch(e){}renderRows();};
$$('.chip').forEach(c=>c.onclick=async()=>{filter=c.dataset.c;$$('.chip').forEach(x=>x.classList.toggle('on',x.dataset.c===filter));try{localStorage.setItem('filter2',filter);}catch(e){}renderRows();
 if(filter==='UV-32 only')return;
 try{const r=await (await fetch('/api/scan?cat='+encodeURIComponent(filter))).json();if(r.error){toast(r.error);return;}
  if(r.deferred)toast('Showing '+filter+' - the '+(filter==='All'?'full':filter)+' sweep starts 20 s after you stop listening (your stream is not touched)');
  else toast((filter==='All'?'Scanning all bands':'Scanning only '+r.bands.join(' + '))+' - click All to go back to everything');setTimeout(poll,3000);}catch(e){toast('could not start the scan: '+e.message);}});
$('#q').oninput=e=>{query=e.target.value.trim().toLowerCase();renderRows();};
$('#morebtn').onclick=()=>{ROWCAP+=20;renderRows();};$('#allbtn').onclick=()=>{ROWCAP=100000;renderRows();};
$('#hidecont').checked=hideCont;$('#hidecont').onchange=e=>{hideCont=e.target.checked;try{localStorage.setItem('hidecont',hideCont?'1':'0');}catch(x){}renderRows();};
/* ---------- tabs ---------- */
$$('.tabs button').forEach(b=>b.onclick=()=>{tab=b.dataset.t;$$('.tabs button').forEach(x=>x.classList.toggle('on',x===b));['freq','dir','radio','recs','digital','map','air','debug'].forEach(t=>$('#tab-'+t).style.display=t===tab?'':'none');if(tab==='air'){loadAir();loadAirToday();}if(tab==='digital'){loadDigital();loadRotation();}if(tab==='map')loadMap();if(tab==='dir')loadDir();if(tab==='recs')loadRecs();if(tab==='debug')loadDebug();if(tab==='radio')loadRadio();});
let dSortK='snr',dSortDir=-1,dSortT='n';
$$('th[data-dk]').forEach(th=>th.onclick=()=>{if(dSortK===th.dataset.dk)dSortDir=-dSortDir;else{dSortK=th.dataset.dk;dSortT=th.dataset.dt;dSortDir=dSortT==='n'?-1:1;}$$('th[data-dk]').forEach(x=>{x.classList.remove('asc','desc');if(x.dataset.dk===dSortK)x.classList.add(dSortDir>0?'asc':'desc');});if(DIR)renderDir();});
let DIR=null;async function loadDir(){if(!DIR)DIR=await (await fetch('/api/directory')).json();renderDir();}
function heardAt(mhz){const h=S.hits.find(x=>Math.abs(x.mhz-mhz)<0.0026);return h&&!h.cont&&h.heard_ts?h:null;}
function renderDir(){const q=($('#dq').value||'').trim().toLowerCase();let rows=DIR.filter(e=>!q||q.split(/\s+/).every(w=>(fmt(e.mhz)+' '+e.name+' '+e.cat+' '+e.group+' '+e.note+' '+e.nets).toLowerCase().includes(w)));
 rows=rows.map(e=>({e,h:heardAt(e.mhz)}));
 rows.sort((A,B)=>{const a=A.e,b=B.e;let x,y;if(dSortK==='snr'){x=A.h&&!A.h.cont?A.h.snr:(A.h?A.h.snr-100:-999);y=B.h&&!B.h.cont?B.h.snr:(B.h?B.h.snr-100:-999);}else if(dSortK==='uv32'){x=+a.uv32;y=+b.uv32;}else{x=a[dSortK];y=b[dSortK];}
  if(dSortT==='n')return (x-y)*dSortDir;return String(x).localeCompare(String(y))*dSortDir;});
 $('#dirrows').innerHTML=rows.map(({e,h})=>{const md=e.digital?'digital':(e.mode==='AM'?'am':'fm');const prog=fmt(e.mhz)+(e.duplex?' '+e.duplex+e.offset:'')+(e.tone?' / '+e.tone:'');
  const heard=h?`<span class="meter"><i style="width:${Math.min(100,h.snr*2.5)}%"></i></span><span class="sv">${h.snr} dB · busy ${h.busy}%</span>${h.tone?' <span class="tag tone">'+esc(h.tone)+'</span>':''}`:'<span class="tag" style="opacity:.6">not heard yet</span>';
  return `<tr class="row ${h?'':'quiet'}"><td>${e.cat==='Digital'&&!e.digital?'':`<button class="play" data-mhz="${e.mhz}" data-mode="${md}" data-name="${esc(e.name)}" title="${md==='digital'?'decode with SDRTrunk':'listen'}">▶</button>`}</td><td class="f">${fmt(e.mhz)}</td><td><span class="nm">${esc(e.name)}</span></td><td class="sv">${esc(e.cat)} · ${esc(e.group)}</td><td class="sv">${e.mode}</td><td class="sv" style="font-family:var(--mono)">${prog}</td><td>${heard}</td><td class="sv" style="white-space:normal;max-width:320px">${esc(e.note)}${e.nets?'<br><i>'+esc(e.nets)+'</i>':''}</td><td>${e.uv32?'<span class="tag uv">UV-32</span>':'<span class="tag sdr">SDR only</span>'}</td></tr>`;}).join('')||'<tr><td colspan="9" class="empty">no match</td></tr>';capList($('#dirrows').closest('table'),'tbody>tr','dir');P.paint();}
$('#dq').oninput=()=>{if(DIR)renderDir();};
const DIGSYS=[
 ['Toronto Police','P25 Phase II - ENCRYPTED','700 MHz city system','Not hearable by anyone (all talkgroups encrypted)'],
 ['Toronto Fire','P25 Phase II - in the clear','City system, West/South simulcast sites (table below)','Hearable in SDRTrunk - in the playlist'],
 ['Toronto Paramedics','P25 Phase II - in the clear','Same city system','Hearable in SDRTrunk - in the playlist'],
 ['City of Toronto works / Transportation (plows)','P25 Phase II - mixed','700 MHz city system; contractor plows on commercial DMR / fleet GPS, not radio','Partly - city crews via SDRTrunk'],
 ['Toronto Fire backup','Analog FM','866.2375 N · 867.1125 E · 867.2625 W · 867.4625 S · 867.7375 citywide','Hearable here (Tune, NFM) - used when the trunked system fails'],
 ['Metrolinx / GO Transit','DMR','411.1625 and others; 462.5625 yard','Hearable in SDRTrunk if unencrypted'],
 ['TTC','TETRA','systemwide','Not with SDRTrunk (TETRA needs other tools)'],
 ['Universities (U of T, TMU, York, Humber, George Brown)','DMR / NXDN','454.3625, 454.2000, 460.1625...','Hearable in SDRTrunk'],
 ['Bearcom customers (CN Tower)','DMR','463.3125','Hearable in SDRTrunk if unencrypted'],
];
async function loadDigital(){
 try{const r=await (await fetch('/api/digital')).json();const heard=new Set(r.cc.map(c=>c.mhz.toFixed(5)));
  const heardU=new Set((S.hits||[]).filter(h=>h.mhz>=400&&h.mhz<=520).map(h=>h.mhz));const hu=f=>[...heardU].some(m=>Math.abs(m-f)<0.0063);
  const TG=r.tgs||{};
  const pb=(mhz,target,name)=>`<button class="play" data-mhz="${mhz}" data-mode="digital" data-target="${esc(target)}" data-name="${esc(name)}" title="decode with SDRTrunk">▶</button>`;
  const fq=(f,target,name,note)=>`<div class="dgf">${pb(f,target,name)}<span class="f">${(+f).toFixed(f>700?5:4)}</span><span class="sv">${note||''}</span></div>`;
  const tgcell=m=>{const t=TG[m.toFixed(4)];return t?`<span class="tag" style="border-color:var(--acc);color:var(--acc)">heard</span> ${t.tgs.map(x=>`<button class="ghost tgname" data-m="${m}" data-tg="${esc(x[0])}" data-n="${esc((t.names||{})[x[0]]||'')}" title="tap to name this talkgroup (it goes into the SDRTrunk playlist)" style="padding:1px 7px;font-size:.85em">${(t.names||{})[x[0]]?'<b>'+esc(t.names[x[0]])+'</b> <span class=sv>TG '+esc(x[0])+'</span>':'TG '+esc(x[0])} ×${x[1]}</button>`).join(' ')} <span class="sv">· last ${esc(t.last)}</span>`:'';};
  const row=(k,btn,name,tag,st,body,cls)=>`<details class="dgx ${cls||''}" data-k="${esc(k)}"><summary>${btn}<span class="dgn">${esc(name)}</span><span class="tag">${esc(tag)}</span><span class="dgs">${st||''}</span></summary><div class="dgb">${body}</div></details>`;
  const dgSaid=g=>g.mhz.filter(m=>TG[m.toFixed(4)]).map(m=>{const t=TG[m.toFixed(4)];return t.tgs.map(x=>(t.names||{})[x[0]]||('TG '+x[0])).join(', ');}).join(' · ');
  let h='<div class="dgh">Fire, paramedics and transit · P25</div>';
  h+=r.sites.map(s=>{const n=s.cc.filter(f=>heard.has(f.toFixed(5))).length;const name=s.label||('Toronto Fire + Paramedics - '+s.site);const direct=s.cc.length===1;
   const body='<div class="sv">'+(direct?'One voice channel, listened to directly - no control channel needed.':'SDRTrunk follows the control channel and the calls it hands out. ▶ on a single frequency tries just that one as the control channel.')+'</div>'+s.cc.map(f=>fq(f,direct?('p25:'+s.site):('probe:'+f.toFixed(4)),name+' '+f.toFixed(5),heard.has(f.toFixed(5))?'✓ heard by the sweep':'')).join('')+((s.voice||[]).length?'<div class="sv" style="margin-top:6px">Voice channels of the same system: '+s.voice.map(v=>v.mhz.toFixed(4)).join(', ')+'</div>':'');
   return row('p25:'+s.site,pb(s.cc[0],'p25:'+s.site,name),s.site,direct?'P25 direct':'P25 trunked · '+s.cc.length+' freq',n?'✓ heard':'',body);}).join('');
  h+='<div class="dgh">Trunked DMR systems</div>'+(r.dmr_trunked||[]).map(t=>row('dmrt:'+t.cc,pb(t.cc,'dmrt:'+t.cc.toFixed(4),t.name),t.name,'DMR trunked · '+t.mhz.length+' freq',t.mhz.filter(hu).length?'✓ heard':'','<div class="sv">Control channel first, then the voice channels. ▶ on a single frequency decodes just that channel.</div>'+[t.cc].concat(t.mhz.filter(f=>f!==t.cc)).map((f,i)=>fq(f,'dmr:'+f.toFixed(4),t.name+' '+f.toFixed(4),i===0?'control channel':'')).join(''),/^ROADS/.test(t.name)?'dg-roads':'')).join('');
  if((r.nxdn||[]).length)h+='<div class="dgh">GO Transit rail · NXDN</div>'+r.nxdn.map(t=>row('nxdn',t.ready?pb(t.mhz[0],'nxdng:'+t.mhz.map(f=>f.toFixed(4)).join(','),t.name):'<span class="tag sdr">needs nightly</span>',t.name,'NXDN · '+t.mhz.length+' ch',t.mhz.filter(hu).length?'✓ heard':'',t.mhz.map((f,i)=>t.ready?fq(f,'nxdng:'+f.toFixed(4),(t.names||[])[i]||t.name,esc((t.names||[])[i]||'')):`<div class="dgf"><span class="f">${f.toFixed(4)}</span><span class="sv">${esc((t.names||[])[i]||'')}</span></div>`).join(''))).join('');
  const grows=(r.dmr_groups||[]).map(g=>({g,said:dgSaid(g),n:g.mhz.filter(hu).length}));grows.sort((x,y)=>((y.said?20:0)+y.n)-((x.said?20:0)+x.n));
  const dmrHeard=grows.filter(x=>x.said||x.n).length;                    // (v184) one closed section: 12 groups were burying Fire / EMS
  h+='<details class="dgsec" data-k="sec:dmr"><summary class="dgh">DMR channels · '+(r.dmr||[]).length+' channels in '+grows.length+' groups'+(dmrHeard?' · '+dmrHeard+' heard':'')+' <span class="sv">- tap to open</span></summary>'+grows.map(x=>{const g=x.g;return row('dmrg:'+g.mhz[0],pb(g.mhz[0],'dmrg:'+g.mhz.map(f=>f.toFixed(4)).join(','),'DMR group: '+g.name),g.name,g.mhz.length+' ch · '+g.mhz[0].toFixed(3)+(g.mhz.length>1?'-'+g.mhz[g.mhz.length-1].toFixed(3):''),x.said?'<b class="dg-said">'+esc(x.said)+'</b>':(x.n?'✓ heard':''),'<div class="sv">▶ on the group decodes every channel in it at once; ▶ on a line decodes only that one.</div>'+g.mhz.map((f,i)=>`<div class="dgf">${pb(f,'dmr:'+f.toFixed(4),((g.names||[])[i]||'DMR')+' (DMR)')}<span class="f">${f.toFixed(4)}</span><span class="sv">${esc((g.names||[])[i]||'')}</span><span class="sv">${tgcell(f)}</span></div>`).join(''));}).join('')+'</details>';
  h+='<div class="dgh">Found by the sweep · 700 / 800 MHz</div>'+(r.cc.length?row('cc','','Always-on carriers',r.cc.length+' found','','<div class="sv">Carriers that never drop are usually control channels. ▶ tries a P25 decode on that frequency.</div>'+r.cc.map(c=>fq(c.mhz,'probe:'+c.mhz.toFixed(4),c.mhz.toFixed(5)+' MHz',c.snr+' dB · on air '+c.busy+'% · '+esc(c.likely))).join('')):'<div class="sv">nothing yet - the Digital chip sweeps 764-776 and 851-870 MHz</div>');
  h+='<div class="dgh">Reference</div>'+row('about','','What can and cannot be decoded','info','','<table class="alist"><thead><tr><th>System</th><th>Technology</th><th>Where</th><th>Status</th></tr></thead><tbody>'+DIGSYS.map(q=>`<tr><td><b>${q[0]}</b></td><td class="sv">${q[1]}</td><td class="sv">${q[2]}</td><td class="sv">${q[3]}</td></tr>`).join('')+'</tbody></table>');
  const wasOpen=new Set($$('#dglist details[open]').map(d=>d.dataset.k));$('#dglist').innerHTML=h;$$('#dglist details').forEach(d=>{if(wasOpen.has(d.dataset.k))d.open=true;});P.paint();
  $$('#dglist button.tgname').forEach(b=>b.onclick=async()=>{const n=prompt('Name for talkgroup '+b.dataset.tg+' on '+(+b.dataset.m).toFixed(4)+' MHz (empty = forget):',b.dataset.n||'');if(n===null)return;const r2=await (await fetch('/api/dmr/alias?mhz='+b.dataset.m+'&tg='+b.dataset.tg+'&name='+encodeURIComponent(n))).json();if(r2.error)toast(r2.error);else{toast(n?'named - SDRTrunk uses it from its next start':'forgotten');loadDigital();}});
  const p=r.playlist;$('#plinfo').textContent=`${p.fire} Fire + ${p.ems} Paramedics talkgroups, ${r.sites.length} P25 sites, ${p.dmr} DMR channels`;
  $('#plpath').textContent='Playlist file: '+p.path+(p.in_sdrtrunk?'  (copied into ~/SDRTrunk/playlist)':'  (not yet copied - press Give or Rebuild)');
  $('#sdrtstat').textContent=r.sdrtrunk&&!r.sdrtrunk.installed?'SDRTrunk is not installed yet - run Install SDRTrunk.command':(r.handed?'dongle is with SDRTrunk - sweep paused':'');}catch(e){}}
let MAP=null,MAPL=[];function loadLeaflet(){return new Promise((ok,no)=>{if(window.L)return ok();const c=document.createElement('link');c.rel='stylesheet';c.href='https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css';document.head.appendChild(c);const j=document.createElement('script');j.src='https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js';j.onload=ok;j.onerror=no;document.head.appendChild(j);});}
async function loadMap(){try{await loadLeaflet();}catch(e){$('#mapinfo').textContent='map library could not load (needs internet on this device)';return;}
 const d=await (await fetch('/api/map')).json();if(!MAP){MAP=L.map('mapbox').setView([d.home.lat,d.home.lon],11);L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:18,attribution:'&copy; OpenStreetMap'}).addTo(MAP);MAP.on('click',async e=>{await fetch('/api/map/home?lat='+e.latlng.lat+'&lon='+e.latlng.lng);toast('home set');loadMap();});}
 MAPL.forEach(x=>x.remove());MAPL=[];const h=[d.home.lat,d.home.lon];MAPL.push(L.marker(h).addTo(MAP).bindPopup('Home'+(d.home.set?'':' (default - tap the map to set yours)')));
 [5,10,20].forEach(k=>MAPL.push(L.circle(h,{radius:k*1000,color:'#e5484d',weight:1,fill:false}).addTo(MAP)));
 d.landmarks.forEach(m=>{const km=Math.round(MAP.distance(h,[m.lat,m.lon])/100)/10;MAPL.push(L.circleMarker([m.lat,m.lon],{radius:7,color:'#2f81f7'}).addTo(MAP).bindPopup(m.name+'<br>'+km+' km from home'));});
 $('#mapinfo').textContent=d.landmarks.map(m=>m.name.split(' - ')[0]+' '+(Math.round(MAP.distance(h,[m.lat,m.lon])/100)/10)+' km').join(' · ')+(d.home.set?'':' · home is a default - tap the map on your house');
 setTimeout(()=>MAP.invalidateSize(),200);}
let TGF='all';async function loadTG(){try{const t=await (await fetch('/api/sdrt/talkgroups')).json();const g=['all','Fire','Paramedics','Police','Other'];$('#tgchips').innerHTML=g.map(x=>`<button class="chip${TGF===x?' on':''}" data-g="${x}">${x==='all'?'All':x} ${x==='all'?t.length:t.filter(a=>a.group===x).length}</button>`).join('');$('#tgchips').querySelectorAll('button').forEach(b=>b.onclick=()=>{TGF=b.dataset.g;loadTG();});const bar=a=>{const m=Math.max(1,...a);return a.map(v=>'▁▂▃▄▅▆▇█'[v?Math.min(7,Math.ceil(v/m*7)):0]).join('');};const rows=t.filter(a=>TGF==='all'||a.group===TGF);$('#tgrows').innerHTML=rows.length?rows.map(a=>`<tr style="${a.enc?'opacity:.6':''}"><td class="sv">${a.group}</td><td><b>${esc(a.name)}</b> <span class="sv">${esc(a.id)}</span>${a.enc?' <span class="sv">silent</span>':''}</td><td>${a.calls}</td><td class="sv">${a.secs} s</td><td class="sv" style="letter-spacing:1px">${bar(a.strip)}</td><td class="sv">${esc(a.last)}${a.clip?` <button class="ghost" data-clip="${esc(a.clip)}" title="play the last recorded call on this talkgroup">▶ last call</button>`:''}</td><td>${a.id?`<button class="ghost" data-tg="${esc(a.id)}" data-p="${a.solo?0:1}" title="${a.solo?'back to hearing everything':'hear only this talkgroup'}">${a.solo?'◉ hearing only this - hear everything':'🎯 hone in'}</button>`:''}</td></tr>`).join(''):'<tr><td colspan="7" class="empty">nothing decoded yet - press ▶ on a P25 site</td></tr>';$('#tgrows').querySelectorAll('button[data-clip]').forEach(b=>b.onclick=()=>{if(window._clipA){window._clipA.pause();}window._clipA=new Audio('/rec/'+encodeURIComponent(b.dataset.clip));window._clipA.play().catch(e=>toast('could not play: '+e.message));toast('playing '+b.dataset.clip);});$('#tgrows').querySelectorAll('button[data-tg]').forEach(b=>b.onclick=async()=>{if(!b.dataset.tg)return;const r=await (await fetch('/api/sdrt/solo?tg='+(b.dataset.p==='1'?b.dataset.tg:0))).json();toast(r.error||(b.dataset.p==='1'?'Hearing only that talkgroup':'Hearing everything again')+(r.restarted?' - SDRTrunk restarting (about 3 s)':' - applies the next time SDRTrunk starts'));loadTG();});}catch(e){}}
async function loadUnits(){try{const u=await (await fetch('/api/sdrt/units')).json();$('#unitrows').innerHTML=u.length?u.map(x=>`<tr style="${x.enc&&!x.tgs.length?'opacity:.6':''}"><td class="sv">${esc(x.system)}</td><td><b>${esc(x.unit)}</b></td><td>${esc(x.home||'—')}</td><td class="sv">${x.tgs.map(esc).join(' · ')||(x.enc?'encrypted only':'')}</td><td>${x.calls}${x.enc?' <span class="sv">('+x.enc+' enc.)</span>':''}</td><td class="sv">${esc(x.first)} / ${esc(x.last)}</td></tr>`).join(''):'<tr><td colspan="6" class="empty">nothing decoded yet</td></tr>';}catch(e){}}
async function dmrscanPoll(){try{const r=await (await fetch('/api/sdrt/dmrscan',{cache:'no-store'})).json();const b=$('#dmrscanbtn'),b2=$('#p25scanbtn');b.textContent=(r.on&&r.kind!=='p25')?'■ Stop DMR scan':'▶ Scan all DMR';b.classList.toggle('stopb',!!r.on&&r.kind!=='p25');b2.textContent=(r.on&&r.kind==='p25')?'■ Stop P25 scan':'▶ Scan all P25 sites';b2.classList.toggle('stopb',!!r.on&&r.kind==='p25');b.style.display=(r.on&&r.kind==='p25')?'none':'';b2.style.display=(r.on&&r.kind!=='p25')?'none':'';$('#dmrscannext').style.display=r.on?'':'none';
 $('#dmrscanwin').textContent=r.windows?r.windows+' windows, '+r.channels+' channels':'—';
 $('#dmrscanst').textContent=r.on?('window '+r.window+'/'+r.windows+' · '+r.name+' · '+(r.mhz.length?r.mhz[0].toFixed(4)+'-'+r.mhz[r.mhz.length-1].toFixed(4):'')+' · '+r.left+' s left'+(r.running?'':' · SDRTrunk starting…')):'';}catch(e){}}
$('#dmrscanbtn').onclick=async()=>{const on=$('#dmrscanbtn').classList.contains('stopb');if(!on&&P.cur&&P.cur.mode!=='monitor'&&!confirm('Stop the analog player and start the DMR scan?'))return;if(!on&&P.cur)P.stop();const r=await (await fetch('/api/sdrt/dmrscan?on='+(on?0:1)+'&step='+$('#dmrscanstep').value)).json();if(r.error)toast(r.error);else toast(on?'DMR scan stopped - the sweep resumes':'DMR scan running - '+r.scan.windows+' windows');dmrscanPoll();};
$('#p25scanbtn').onclick=async()=>{const on=$('#p25scanbtn').classList.contains('stopb');if(!on&&P.cur&&P.cur.mode!=='monitor'&&!confirm('Stop the analog player and start the P25 scan?'))return;if(!on&&P.cur)P.stop();const r=await (await fetch('/api/sdrt/dmrscan?on='+(on?0:1)+'&kind=p25&step='+$('#dmrscanstep').value)).json();if(r.error)toast(r.error);else toast(on?'P25 scan stopped - the sweep resumes':'P25 scan running - '+r.scan.windows+' sites');dmrscanPoll();};
$('#dmrscannext').onclick=async()=>{await fetch('/api/sdrt/dmrscan?next=1');dmrscanPoll();};
setInterval(()=>{if(tab==='digital'&&!document.hidden)dmrscanPoll();},4000);
async function loadRotation(){dmrscanPoll();loadTG();loadUnits();try{const r=await (await fetch('/api/sdrt/rotation')).json();$('#roton').checked=!!r.on;$('#rotsite').value=(r.target||'p25:West Simulcast').split(':')[1]||'West Simulcast';$('#rotmin').value=r.minutes;$('#rotevery').value=r.every;$('#rotstat').textContent=r.on?('next slot '+(r.last?Math.max(0,Math.round((r.last+r.every*60-Date.now()/1000)/60))+' min':'now')):'off';}catch(e){}
 try{const h=await (await fetch('/api/sdrt/calls')).json();$('#dighist').innerHTML=h.length?h.slice(0,60).map(c=>`<tr style="${c.enc?'opacity:.5':''}"><td class="sv">${esc((c.day||'')+c.t)}</td><td><b>${esc(c.to||'—')}</b>${c.enc?' <span class="sv">silent</span>':''}</td><td class="sv">${esc(c.ev)}${c.dur?' · '+(c.dur/1000).toFixed(1)+' s':''}</td><td class="sv">${esc(c.from)}</td></tr>`).join(''):'<tr><td colspan="4" class="empty">nothing logged yet</td></tr>';}catch(e){}}
async function saveRotation(){const q='on='+($('#roton').checked?1:0)+'&target='+encodeURIComponent('p25:'+$('#rotsite').value)+'&minutes='+$('#rotmin').value+'&every='+$('#rotevery').value;const r=await (await fetch('/api/sdrt/rotation?'+q)).json();toast(r.on?('P25 slot: '+r.minutes+' min every '+r.every+' min - first one starts when the dongle is free'):'rotation off');loadRotation();}
$('#sirenbtn').onclick=async()=>{toast('Sirens: searching the emergency channels…');let r=null,err='';for(let i=0;i<3&&!r;i++){try{const x=await fetch('/api/sirens',{cache:'no-store'});r=await x.json();}catch(e){err=e.message;await new Promise(q=>setTimeout(q,2500));}}if(!r){toast('Sirens: could not reach the scanner ('+err+') - tried 3 times');return;}toast(r.error||('Sirens logged - '+r.msg));sirenPoll();};
async function sirenPoll(){try{const s=await (await fetch('/api/sirens/status')).json();const el=$('#sirenmsg');el.textContent=s.active?('🚨 '+s.msg+' · '+s.secs+' s'):(s.msg&&s.t0&&Date.now()/1000-s.t0<1200?'🚨 '+s.msg:'');}catch(e){}}
setInterval(sirenPoll,4000);
setInterval(()=>{if(tab==='digital')loadTG();},5000);
['roton','rotsite','rotmin','rotevery'].forEach(id=>$('#'+id).onchange=saveRotation);
$('#buildpl').onclick=async()=>{const r=await (await fetch('/api/playlist')).json();toast(r.error?('playlist: '+r.error):`playlist written (${r.talkgroups} talkgroups, ${r.dmr} DMR)${r.copied?' and copied into ~/SDRTrunk/playlist':''}`);loadDigital();};
$('#takeback').onclick=async()=>{const r=await (await fetch('/api/sdrtrunk?give=0')).json();$('#sdrtstat').textContent=r.msg;toast(r.msg);};
async function loadRadio(){const r=await (await fetch('/api/radio')).json();$('#radio').innerHTML=r.map(([f,n])=>`<button class="play rbtn" data-mhz="${f}" data-mode="wbfm" data-name="${esc(n)}"><b>${f}</b><span>${esc(n)}</span></button>`).join('');P.paint();}
async function loadAlerts(q){const r=await (await fetch('/api/alerts'+(q||''))).json();
 $('#alrules').innerHTML=r.rules.length?r.rules.map(x=>`<span class="tag" style="margin:2px 6px 2px 0">${x.kind==='mhz'?fmt(+x.value)+' MHz':x.kind==='cat'?esc(x.value):'“'+esc(x.value)+'”'} <a href="#" data-id="${x.id}" class="aldel" title="remove">✕</a></span>`).join(''):'no alerts yet';
 $$('.aldel').forEach(a=>a.onclick=e=>{e.preventDefault();loadAlerts('?del='+a.dataset.id);});
 $('#allog').innerHTML=r.log.length?'<h4 style="margin:10px 0 6px">Fired</h4>'+r.log.map(x=>`<div class="sv">${x.time} · ${esc(x.detail)}</div>`).join(''):'';}
$('#aladd').onclick=()=>{const v=$('#alval').value.trim();if(!v)return;loadAlerts('?add='+$('#alkind').value+'&value='+encodeURIComponent(v));$('#alval').value='';toast('alert added');};
async function loadRecs(){loadAlerts();const r=await (await fetch('/api/recordings')).json();const el=$('#recs');if(!r.length){el.innerHTML='<div class="empty">No recordings yet. Every transmission that opens the squelch while you listen is saved here.</div>';return;}
 el.innerHTML=r.map(x=>`<div class="rec"><div><span class="f">${x.mhz}</span><br><span class="sv">${esc(x.name)}</span></div><div class="sv">${x.time} · ${x.secs} s${x.text?'<div class="tx">“'+esc(x.text)+'”'+agreeTag(x.agree)+'</div>':''}</div><audio controls preload="none" src="/rec/${encodeURIComponent(x.file)}"></audio><button class="ghost" onclick="delRec('${x.file}')">Delete</button></div>`).join('');capList(el,'.rec','rec');}
async function delRec(f){await fetch('/delrec?f='+encodeURIComponent(f));loadRecs();toast('deleted');}
async function loadDebug(){try{const d=await (await fetch('/debug')).json();const NL=String.fromCharCode(10);$('#dbgsum').textContent='scan '+(d.paused?'PAUSED (listening)':'running')+' · '+d.folder+' · recent USB errors: '+d.usb_errors_recent+(d.player.mhz?' · player on '+d.player.mhz:'');
 let t='WHAT HAPPENED (events.log, newest last)'+NL+((d.events||[]).join(NL)||'(nothing yet)')+NL+NL+'PROCESSES'+NL+(d.procs||'(none)')+NL+NL+'rtl_fm (listener)'+NL+d.fm_log;for(const k in d.logs)t+=NL+NL+'── '+k+NL+d.logs[k];$('#dbgout').textContent=t;}catch(e){$('#dbgout').textContent='debug failed: '+e;}}
/* ---------- drawer ---------- */
function openActive(){$('#dinfo').style.display='none';$('#dactive').style.display='';renderActive();$('#drawer').classList.add('open');}
function renderActive(){if($('#dactive').style.display==='none')return;const v=S.hits.filter(h=>!h.cont&&h.ver!=='DIGITAL'&&h.age_min!=null&&h.age_min<=30).slice().sort((a,b)=>(b.recent-a.recent)||((b.heard_ts||0)-(a.heard_ts||0))||(b.snr-a.snr));
 $('#alist').innerHTML=v.map(h=>`<tr class="row" data-mhz="${h.mhz}"><td><button class="play" data-mhz="${h.mhz}" data-mode="${h.mode==='AM'?'am':'fm'}" data-name="${esc(h.name)}" title="listen">▶</button></td><td class="f">${fmt(h.mhz)}</td><td><span class="nm">${esc(h.name)}</span><br><span class="sv">${esc(h.cat)}${h.ver==='VOICE'?' · voice ✓':(h.ver==='KNOWN'?' · known voice':'')}${h.tone?' · '+esc(h.tone):''}</span></td><td class="heard">${heardCell(h)}</td><td>${spark(h.hours,h)}</td><td class="sv">${h.snr} dB<br>${h.busy}% busy</td></tr>`).join('')||'<tr><td class="empty">nothing heard in the last 30 minutes</td></tr>';capList($('#alist').closest('table'),'tbody>tr','alist');
 $$('#alist tr.row').forEach(tr=>tr.onclick=e=>{if(e.target.closest('button'))return;const h=S.hits.find(x=>Math.abs(x.mhz-+tr.dataset.mhz)<0.0026);if(h)openDrawer(h);});P.paint();}
async function openDrawer(h){$('#dactive').style.display='none';$('#dair').style.display='none';$('#dinfo').style.display='';const d=await (await fetch('/info?mhz='+h.mhz)).json();$('#dfreq').textContent=fmt(h.mhz)+' MHz';$('#dname').textContent=(d.name||h.name)+' · '+(d.cat||h.cat)+(d.service?' · '+d.service:'');
 $('#dlines').innerHTML=(uv32(h.mhz)?'<li><b>Baofeng:</b> '+uv32(h.mhz)+' (from the last CHIRP export'+(UV32.exported?' '+esc(UV32.exported):'')+')</li>':'')+d.lines.map(l=>'<li>'+esc(l)+'</li>').join('');$('#dalert').onclick=async()=>{await fetch('/api/alerts?add=mhz&value='+h.mhz);toast('alert set for '+fmt(h.mhz));};$('#dforget').onclick=async()=>{await fetch('/api/forget?mhz='+h.mhz);closeDrawer();toast('forgotten');poll();};const wb=$('#dwide');if(wb){wb.textContent=h.wide?'◉ open more (on)':'open more';wb.onclick=async()=>{const r=await (await fetch('/api/wide?mhz='+h.mhz+'&on='+(h.wide?0:1))).json();h.wide=r.wide;wb.textContent=r.wide?'◉ open more (on)':'open more';toast(r.wide?'squelch opens wider on this channel from the next play':'back to the normal squelch');};};const dp=$('#dplay');dp.dataset.mhz=h.mhz;dp.dataset.mode=h.mode==='AM'?'am':'fm';dp.dataset.name=h.name;dp.classList.add('play');P.paint();
 const r=(await (await fetch('/api/recordings')).json()).filter(x=>Math.abs(parseFloat(x.mhz)-h.mhz)<0.0026);
 try{const hs=await (await fetch('/api/history?mhz='+h.mhz)).json();const mx=Math.max(1,...hs.series.map(p=>p.v));const bars=hs.series.map(p=>`<i title="${new Date(p.t*1000).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})} · ${Math.round(p.v)}%" style="height:${Math.max(1,Math.round(26*p.v/mx))}px;opacity:${p.v?1:.25}"></i>`).join('');
  const hrs=Array.from({length:24},(_,i)=>i);const hb=hrs.map(i=>`<span title="${String(i).padStart(2,'0')}:00 · ${hs.hourly[i]||0}% busy over 7 days"><i style="height:${Math.max(1,Math.round(18*(hs.hourly[i]||0)/100))}px"></i><b>${i%6===0?String(i).padStart(2,'0'):''}</b></span>`).join('');
  $('#dhist').innerHTML=`<h4 style="margin:12px 0 4px">Last 24 hours</h4><div class="spark">${bars}</div><h4 style="margin:12px 0 4px">By hour of day (7 days)</h4><div class="hod">${hb}</div>`;}catch(e){$('#dhist').innerHTML='';}
 const tx=(d.transcripts||[]);$('#drecs').innerHTML=(tx.length?'<h4 style="margin:14px 0 6px">What was said (latest first)</h4>'+tx.map(t=>`<div class="tx"><span class="sv">${t.time} · ${t.secs} s</span><br>“${esc(t.text)}”${agreeTag(t.agree)}</div>`).join(''):'')+(r.length?'<h4 style="margin:14px 0 6px">Recordings here</h4>'+r.map(x=>`<div class="sv" style="margin-bottom:6px">${x.time} · ${x.secs} s${x.text?'<div class="tx">“'+esc(x.text)+'”</div>':''}<br><audio controls preload="none" src="/rec/${encodeURIComponent(x.file)}" style="width:100%;height:32px"></audio></div>`).join(''):'');
 $('#drawer').classList.add('open');}
function closeDrawer(){$('#drawer').classList.remove('open');}
/* ---------- mode ---------- */
function paintMode(){$$('#modeseg button').forEach(b=>b.classList.toggle('on',b.dataset.m===mode));}
$$('#modeseg button').forEach(b=>b.onclick=()=>{mode=b.dataset.m;paintMode();if(P.cur){toast(mode==='scan'?'still listening - the sweep resumes by itself 20 s after you press ■ Stop':'listening');return;}fetch('/mode?m='+mode);toast(mode==='scan'?'sweep resumes':'sweep parked - press ▶ on any row');});
/* ---------- player: ONE object, every ▶ in the app is a <button class="play" data-mhz data-mode data-name> ---------- */
let actx=null,gain=null;
var WA=null,VIAEL=false;
const MOBILE=/iPhone|iPad|iPod|Android|Mobi/.test(navigator.userAgent)||(navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1);
function ensureCtx(){if(!actx){actx=new (window.AudioContext||window.webkitAudioContext)();gain=actx.createGain();
  /* (v157) phones: Web Audio straight to the output is silenced by the iPhone's silent switch and dies with the screen lock. Routed through a real <audio> element
     (MediaStream) it plays like any media: through the speaker / headphones / Bluetooth regardless of the switch, and keeps going with the screen off. */
  try{if(MOBILE&&actx.createMediaStreamDestination){const dest=actx.createMediaStreamDestination();WA=document.createElement('audio');WA.setAttribute('playsinline','');WA.setAttribute('webkit-playsinline','');WA.srcObject=dest.stream;WA.style.display='none';document.body.appendChild(WA);gain.connect(dest);VIAEL=true;}}catch(e){VIAEL=false;}
  if(!VIAEL)gain.connect(actx.destination);}
 if(actx.state!=='running')actx.resume();
 if(VIAEL&&WA&&WA.paused){const pr=WA.play();if(pr&&pr.catch)pr.catch(()=>{try{gain.disconnect();gain.connect(actx.destination);VIAEL=false;}catch(e){}});}
 try{if('mediaSession' in navigator){navigator.mediaSession.metadata=new MediaMetadata({title:(P&&P.cur&&P.cur.name)||'SDR Scanner',artist:'Live radio'});}}catch(e){}
 gain.gain.value=+$('#vol').value;}
$('#vol').oninput=e=>{if(gain)gain.gain.value=+e.target.value;};
$('#macbeep').onclick=async()=>{$('#pstat').textContent='beeping on the Mac…';try{const r=await (await fetch('/api/macbeep')).json();$('#pstat').textContent='Mac output: '+(r.device||'unknown device')+' · '+r.volume;}catch(e){$('#pstat').textContent='Mac beep failed: '+e.message;}};
$('#beep').onclick=()=>{ensureCtx();const o=actx.createOscillator();o.frequency.value=660;o.connect(gain);o.start();o.stop(actx.currentTime+0.4);$('#pstat').textContent='beep sent to this browser ('+Math.round(actx.sampleRate)+' Hz) - heard it? then browser playback works';};
function sqArg(){return $('#auto').checked?'auto':$('#sq').value;}
const onMac=['localhost','127.0.0.1'].includes(location.hostname);let outDev='auto';try{outDev=localStorage.getItem('out')||'auto';}catch(e){}if(MOBILE)outDev='browser';   /* (v160) a phone/tablet always plays through its own speaker - never the Mac's */
function useMac(){return outDev==='mac'||(outDev==='auto'&&onMac);}
function paintOut(){const b=$('#outsel');if(!b)return;b.value=outDev;$('#outhint').textContent=useMac()?'→ Mac speakers':'→ this device';}
const P={cur:null,abort:null,bytes:0,nextT:0,seq:0,err:'',sdrtOn:false,
 async sdrt(target,name,mhz){this.seq++;this.leftDig=0;if(this.abort){this.abort.abort();this.abort=null;}this.cur={mhz:+mhz||0,mode:'digital',name,where:'mac',target};this.sdrtOn=true;mode='listen';paintMode();$('#pfreq').textContent=mhz?fmt(+mhz):'P25';$('#pname').textContent=name+' · SDRTrunk · Mac speakers';$('#pstat').textContent='starting SDRTrunk…';this.paint();
  try{const r=await (await fetch('/api/sdrt/play?target='+encodeURIComponent(target))).json();if(r.error){$('#pstat').textContent=r.error;toast(r.error);this.cur=null;this.sdrtOn=false;this.paint();return;}$('#pname').textContent=r.name+' · SDRTrunk · Mac speakers';toast('SDRTrunk is decoding '+r.name);}catch(e){$('#pstat').textContent='SDRTrunk: '+e.message;}},
 isPlaying(m){return !!(this.cur&&Math.abs(this.cur.mhz-m)<0.0026);},
 label(md,name){return md==='monitor'?name:(md==='wbfm'?'FM ':md.toUpperCase()+' ')+name;},
 async play(mhz,md,name){mhz=+mhz;if(!(mhz>0))return;if(this.cur&&!this.cur.mirror&&Math.abs(this.cur.mhz-mhz)<0.0026&&this.cur.mode===md&&Date.now()-(this.t0||0)<4000){toast('already tuning '+(name||fmt(mhz)));return;}const seq=++this.seq;this.err='';gone=0;this.t0=Date.now();if(this.sdrtOn)this.leftDig=Date.now();
  if(this.abort){this.abort.abort();this.abort=null;}
  this.cur={mhz,mode:md,name,where:useMac()?'mac':'browser'};mode='listen';paintMode();
  $('#pfreq').textContent=fmt(mhz);$('#pname').textContent=this.label(md,name)+(this.cur.where==='mac'?' · Mac speakers':'');$('#pstat').textContent='tuning…';this.paint();
  if(this.sdrtOn){this.sdrtOn=false;fetch('/api/sdrt/stop').catch(()=>{});}
  if(this.cur.where==='mac'){
   const ac=new AbortController();const slow=setTimeout(()=>{if(seq===this.seq)$('#pstat').textContent='still tuning… (clearing the scanner off the dongle)';},1500);
   const dead=setTimeout(()=>ac.abort(),12000);
   try{const r=await fetch('/playmac?mhz='+mhz+'&mode='+md+'&sq='+sqArg()+'&name='+encodeURIComponent(name)+(md==='monitor'?monitorQuery():'')+'&t='+Date.now(),{signal:ac.signal});const t=await r.text();if(seq!==this.seq)return;if(!r.ok||!/ok/i.test(t)){this.err=t||('HTTP '+r.status);$('#pstat').textContent='Mac player error: '+this.err;}}
   catch(e){if(seq===this.seq){const msg=e.name==='AbortError'?'timed out waiting for the dongle - tap play again':e.message;this.err=msg;$('#pstat').textContent='Mac player error: '+msg;}}
   finally{clearTimeout(slow);clearTimeout(dead);}
   return;}
  ensureCtx();this.abort=new AbortController();this.bytes=0;this.nextT=0;const stat=$('#pstat');
  const slow=setTimeout(()=>{if(seq===this.seq)stat.textContent='still tuning… (clearing the scanner off the dongle)';},1500);
  const dead=setTimeout(()=>{if(seq===this.seq&&this.abort)this.abort.abort();},12000);
  try{const r=await fetch('/stream?raw=1&mhz='+mhz+'&mode='+md+'&sq='+sqArg()+'&voice='+($('#voice').checked?1:0)+'&name='+encodeURIComponent(name)+(md==='monitor'?monitorQuery():'')+'&t='+Date.now(),{signal:this.abort.signal});
   clearTimeout(slow);clearTimeout(dead);
   if(!r.ok){stat.textContent=r.status===409?await r.text():('player error: HTTP '+r.status);return;}
   const rd=r.body.getReader();let carry=new Uint8Array(0);
   while(true){const {value,done}=await rd.read();if(done)break;if(seq!==this.seq)break;this.bytes+=value.length;let buf=new Uint8Array(carry.length+value.length);buf.set(carry);buf.set(value,carry.length);
    const n=Math.floor(buf.length/2);if(n<1200){carry=buf;continue;}const pcm=new Int16Array(buf.buffer,0,n);carry=buf.slice(n*2);
    const ab=actx.createBuffer(1,n,24000);const ch=ab.getChannelData(0);for(let i=0;i<n;i++)ch[i]=pcm[i]/32768;const src=actx.createBufferSource();src.buffer=ab;src.connect(gain);
    if(actx.state!=='running')actx.resume();const now=actx.currentTime;if(this.nextT<now+0.1)this.nextT=now+0.4;src.start(this.nextT);this.nextT+=ab.duration;}
   if(seq===this.seq&&this.cur){const m=await (await fetch('/meter')).json();stat.textContent='stream ended'+(m.fm?' - rtl_fm: '+m.fm:'');this.cur=null;this.paint();}}
  catch(e){clearTimeout(slow);clearTimeout(dead);if(e.name!=='AbortError'&&seq===this.seq){stat.textContent='player error: '+e.message;this.err=e.message;}else if(e.name==='AbortError'&&seq===this.seq&&this.err===''){stat.textContent='timed out waiting for the dongle - tap play again';}}},
 stop(){this.seq++;this.cur=null;if(this.abort){this.abort.abort();this.abort=null;}$('#pfreq').textContent='—';$('#pname').textContent='Nothing playing';$('#pstat').textContent='idle';$('#lvl i').style.width='0';if(this.sdrtOn){this.sdrtOn=false;fetch('/api/sdrt/stop').catch(()=>{});}else fetch('/stop').catch(()=>{});this.paint();},
 paint(){const c=this.cur;$$('button.play').forEach(b=>{if(!b.dataset.mhz)return;const pl=!!(c&&Math.abs(+b.dataset.mhz-c.mhz)<0.0026);b.classList.toggle('stopb',pl);
   if(b.id==='dplay')b.textContent=pl?'■ Stop':'▶ Listen';else if(b.classList.contains('rbtn')){b.classList.toggle('on',pl);}else b.textContent=pl?'■':'▶';b.title=pl?'stop':'listen';
   const tr=b.closest('tr');if(tr)tr.classList.toggle('playing',pl);const tile=b.closest('.tile');if(tile)tile.classList.toggle('playing',pl);});}
};
document.addEventListener('click',e=>{const b=e.target.closest('button.play');if(!b||!b.dataset.mhz)return;e.stopPropagation();if(b.closest('summary'))e.preventDefault();if(Date.now()-(window.CMD_T||0)<900){toast('one command at a time - the dongle is still switching');return;}window.CMD_T=Date.now();if(b.dataset.mode==='digital'){if(P.sdrtOn&&P.isPlaying(+b.dataset.mhz)){P.stop();return;}digitalListen(b.dataset.name,b.dataset.mhz,b.dataset.target||'');return;}if(P.isPlaying(+b.dataset.mhz)&&Date.now()-(P.t0||0)>1500)P.stop();else P.play(+b.dataset.mhz,b.dataset.mode||'fm',b.dataset.name||'');});
async function digitalListen(name,mhz,tgt){let target=tgt||'p25:West Simulcast';const m=+mhz;if(tgt){P.sdrt(target,name,mhz);return;}
 const r=await (await fetch('/api/digital')).json();let hit=false;for(const st of r.sites)if(st.cc.some(f=>Math.abs(f-m)<0.0001)){target='p25:'+st.site;hit=true;}   // (v149) a P25 site first - Metrolinx Central's control channels are UHF and used to be treated as DMR
 if(!hit){if(m>=400&&m<=520)target='dmr:'+m.toFixed(4);else if((m>=764&&m<=776)||(m>=851&&m<=870))target='probe:'+m.toFixed(4);}
 P.sdrt(target,name);}
function play(mhz,md,name){P.play(mhz,md,name);}function stopAll(){P.stop();}
[$('#auto'),$('#sq'),$('#voice')].forEach(el=>el.onchange=()=>{if(P.cur)P.play(P.cur.mhz,P.cur.mode,P.cur.name);});
function doTune(){const v=parseFloat($('#tune').value);if(!v||v<24||v>1766){toast('enter a frequency in MHz, e.g. 118.7');return;}let md=$('#tunemode').value;if(v>=108&&v<137&&md==='fm'){md='am';$('#tunemode').value='am';}if(v>=88&&v<108&&md!=='wbfm'){md='wbfm';$('#tunemode').value='wbfm';}P.play(v,md,'manual tune');}
$('#tunebtn').onclick=doTune;$('#tune').addEventListener('keydown',e=>{if(e.key==='Enter')doTune();});
$('#macbtn').onclick=()=>{outDev='mac';try{localStorage.setItem('out','mac');}catch(e){}paintOut();if(P.cur)P.play(P.cur.mhz,P.cur.mode,P.cur.name);else toast('pick a channel first');};
$('#outsel').onchange=e=>{outDev=e.target.value;try{localStorage.setItem('out',outDev);}catch(e){}paintOut();if(P.cur)P.play(P.cur.mhz,P.cur.mode,P.cur.name);};paintOut();
$('#stop').onclick=()=>P.stop();
document.addEventListener('keydown',e=>{const tag=(e.target.tagName||'').toLowerCase();if(tag==='input'||tag==='select'||tag==='textarea'){if(e.key==='Escape')e.target.blur();return;}
 if(e.key===' '){e.preventDefault();if($('#headunit').style.display!=='none'){$('#hu-hold').click();return;}if(P.cur)P.stop();return;}
 if(e.key==='m'||e.key==='M'){$('#monbtn').click();return;}
 if(e.key==='/'){e.preventDefault();$('#q').focus();return;}
 if(e.key==='Escape'){closeDrawer();return;}
 if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();const rows=[...$$('#rows tr')].filter(r=>r.style.display!=='none');if(!rows.length)return;let i=rows.findIndex(r=>r.classList.contains('playing'));i=e.key==='ArrowDown'?Math.min(rows.length-1,i+1):Math.max(0,i-1);const h=rows[i]._h;if(h)P.play(h.mhz,h.mode==='AM'?'am':'fm',h.name);rows[i].scrollIntoView({block:'nearest'});}});
$('#monbtn').onclick=()=>{if(P.cur&&P.cur.mode==='monitor'){P.stop();return;}QUICK=false;const sel=$('#monsel').value;MONCAT=sel;try{localStorage.setItem('monsel',sel);}catch(e){}const lbl={'':'all heard channels','Top':'top 20 channels today','Walkies':'walkie-talkies - FRS / GMRS / MURS / business','Airport':'Airport','TTC Etobicoke':'TTC Etobicoke - Humber / Kipling analog channels','Union Station':'Union Station platform ops (Toronto Terminals Railway / Via Rail)','CP Etobicoke Yard':'CP Rail Etobicoke Yard - freight voice channels','GFL Environmental':'GFL Environmental - Oakville dispatch','Solid Waste Mgmt':'Solid Waste Mgmt - City of Toronto (Etobicoke + 4 more)','Follow traffic':'the channels the planes are on'}[sel]||sel;P.play(1,'monitor','Monitor: '+(sel?lbl:(filter==='All'||filter==='UV-32 only'?lbl:filter)));};try{$('#monsel').value=localStorage.getItem('monsel')||'';}catch(e){}
$('#quickbtn').onclick=()=>{if(P.cur&&P.cur.mode==='monitor'&&QUICK){P.stop();return;}QUICK=true;MONCAT='';P.play(1,'monitor','Quick scan: everything heard today'+(filter==='All'||filter==='UV-32 only'?'':' - '+filter));};
let QUICK=false,MONCAT='';function monitorQuery(){return '&cat='+encodeURIComponent(MONCAT||((filter==='UV-32 only'?'All':filter)+(QUICK?'|quick':'')));}
/* meter: the server is the source of truth - if it stopped (dongle drop, another device took over) the UI follows */
let peak=0,gone=0;async function meter(){try{const m0=await (await fetch('/meter')).json();if(m0.sdrtrunk&&!(Date.now()-(P.leftDig||0)<20000)){const st=m0.sdrtrunk;digAudio(st);if(!P.cur||P.cur.mode!=='digital'){P.cur={mhz:0,mode:'digital',name:st.name,where:'mac',target:st.target};P.sdrtOn=true;$('#pfreq').textContent='P25';$('#pname').textContent=st.name+' · SDRTrunk · Mac speakers';P.paint();}const c0=(st.calls||[]).find(c=>!c.enc)||(st.calls||[])[0];$('#pstat').innerHTML=(st.alive&&st.secs>=15?'<b style="color:var(--acc)">DECODING</b> · ':'')+(c0?'<b>'+esc(c0.to||c0.ev)+'</b> '+esc(c0.t)+' · ':'')+(st.problem?'<b style="color:var(--bad,#ff5a5f)">⚠ '+esc(st.problem)+'</b> · ':esc(st.note)+' · ')+st.secs+' s';if(st.problem&&P._tp!==st.problem){P._tp=st.problem;toast(st.problem);}if(!st.problem)P._tp=null;const cl=$('#digcalls');if(cl)cl.innerHTML=(st.calls||[]).map(c=>`<tr style="${c.enc?'opacity:.5':''}"><td class="sv">${esc(c.t)}</td><td><b>${esc(c.to||'—')}</b>${c.enc?' <span class="sv">silent</span>':''}</td><td class="sv">${esc(c.ev)}${c.dur?' · '+(c.dur/1000).toFixed(1)+' s':''}</td><td class="sv">${esc(c.from)}</td></tr>`).join('')||'<tr><td colspan="4" class="empty">no calls decoded yet</td></tr>';$('#lvl i').style.width=st.alive?'100%':'0';return;}else if(P.sdrtOn&&!m0.sdrtrunk){digAudio(null);P.sdrtOn=false;P.cur=null;$('#pname').textContent='Nothing playing';$('#pstat').textContent='SDRTrunk stopped';P.paint();}}catch(e){}
try{const m=await (await fetch('/meter',{cache:'no-store'})).json();const l=$('#lvl');
 if(!m.playing&&P.cur&&P.cur.mirror&&++gone>2){P.cur=null;P.paint();}
 if(!m.playing&&!P.cur&&!P.sdrtOn&&$('#pname').textContent!=='Nothing playing'&&!$('#pstat').textContent.startsWith('Mac player error')){$('#pfreq').textContent='—';$('#pname').textContent='Nothing playing';$('#pstat').textContent='';$('#monbtn').textContent='Monitor all';$('#monbtn').classList.remove('stopb');$('#quickbtn').classList.remove('stopb');P.paint();}
 if(!m.playing){l.querySelector('i').style.width='0';if(m.dongle_lost){gone=0;$('#pstat').innerHTML='<b style="color:#f6a">dongle not responding</b> - unplug it and plug it back in; the player resumes '+(m.resume?fmt(m.resume):'')+' by itself'+(m.dongle_note?' · '+esc(m.dongle_note):'');return;}if(P.cur&&P.cur.where==='mac'){if(++gone>8&&Date.now()-(P.t0||0)>10000){const why=m.fm?('rtl_fm: '+m.fm):'the server stopped the stream';P.cur=null;P.paint();$('#pstat').textContent='stopped - '+why;}}return;}
 gone=0;try{huPaint(m.monitor,m);}catch(e){}if(m.monitor){const mo=m.monitor;if(!P.cur)P.cur={mhz:1,mode:'monitor',name:'Monitor',where:'mac',mirror:true};P.cur.mhz=m.open?(mo.mhz||1):1;$('#pfreq').textContent=fmt(mo.mhz||0);$('#pname').textContent=(m.open?'LOCKED · ':'')+(mo.name||'')+' · '+(mo.groups?'slice '+mo.group+'/'+mo.groups+' · ':'')+mo.n+' ch';$('#monbtn').textContent='■ Stop monitor';$('#monbtn').classList.add('stopb');if(QUICK)$('#quickbtn').classList.add('stopb');P.paint();if(mo.heard&&mo.heard.length){$('#pstat').innerHTML=(m.open?'<b style="color:var(--acc)">ON AIR</b> · ':'')+'heard: '+mo.heard.slice(-4).reverse().map(x=>esc(x.name)+' '+x.when+' ('+x.secs+' s)').join(' · ');}else $('#pstat').textContent=(m.open?'ON AIR · ':'')+'scanning '+mo.n+' channels…';return;}else{$('#monbtn').textContent='Monitor all';$('#monbtn').classList.remove('stopb');}
 if(!P.cur&&m.playing&&m.playing>1.5){P.cur={mhz:m.playing,mode:m.mode||'fm',name:'(playing on the Mac)',where:'mac',mirror:true};P.t0=Date.now();$('#pfreq').textContent=fmt(m.playing);$('#pname').textContent=(m.mode||'fm').toUpperCase()+' · playing on the Mac speakers';P.paint();}
 if(P.cur&&P.cur.mode!=='monitor'&&Date.now()-(P.t0||0)>8000&&Math.abs(m.playing-P.cur.mhz)>0.0026){P.cur={mhz:m.playing,mode:m.mode||'fm',name:'(another device)',where:'mac',mirror:true};$('#pfreq').textContent=fmt(m.playing);$('#pname').textContent=(m.mode||'fm').toUpperCase()+' · playing on the Mac speakers';P.paint();}
 const pct=m.level>1?Math.max(0,Math.min(100,Math.round(100*(20*Math.log10(m.level/16384)+60)/60))):0;l.querySelector('i').style.width=pct+'%';l.classList.toggle('open',!!m.open);peak=Math.max(pct,peak*0.96);l.querySelector('s').style.left=peak+'%';
 if(m.denoise_off&&!P._dnOff){P._dnOff=true;toast('noise suppression turned itself off for this session (fail-safe) - audio still plays normally');}
 if(P.cur&&!P.err){const ps=$('#pstat');const where=P.cur.where==='mac'?('Mac sound out: '+(macOut||'?')):('this device · '+(P.bytes/1024).toFixed(0)+' KB · audio '+(actx?actx.state:'-'));
  ps.innerHTML=(m.data?'<span class="tag sdr">DATA - muted (not voice)</span>':(m.open?'<b style="color:var(--acc)">ON AIR</b>':'squelch closed · nothing on this channel right now'))+' · '+where;}
 const base=$('#pname').textContent.replace(/ · (CTCSS|DATA).*$/,'');if(m.tones)$('#pname').innerHTML=esc(base)+' · <span class="tag tone">'+esc(m.tones)+'</span>';}catch(e){}}
let macOut='';async function getOut(){try{const r=await (await fetch('/api/sound')).json();macOut=r.device||'';}catch(e){}}getOut();setInterval(getOut,15000);
// (v136) breathe: no state/meter work while the tab is hidden, and no table re-render while the finger is on the page (iOS scroll jank)
let SCROLLING=0;addEventListener('scroll',()=>{SCROLLING=Date.now();},{passive:true});addEventListener('touchstart',()=>{SCROLLING=Date.now();},{passive:true});
setInterval(()=>{if(!document.hidden)meter();},250);setInterval(()=>{if(document.hidden)return;if(Date.now()-SCROLLING<700){setTimeout(poll,800);return;}poll();},2500);poll();paintMode();loadRadio();
document.addEventListener('visibilitychange',()=>{if(!document.hidden){poll();meter();}});
// ---- (v137) head unit
const HU={last:null,hold:false,delay:6};
function huShow(on){const h=$('#headunit');if(h.style.display===(on?'':'none'))return;h.style.display=on?'':'none';}
function huPaint(mo,m){if(!mo){if(HU.auto){huShow(true);const st=$('#hu-stat');st.textContent='IDLE · auto-scan '+HU.auto+' starts when nobody is listening';st.className='hu-stat';$('#hu-freq').textContent='—';$('#hu-name').textContent='';$('#hu-sub').textContent='';}else huShow(false);return;}huShow(true);const locked=!!m.open&&mo.active;const st=$('#hu-stat');st.textContent=HU.hold?'HOLD · '+(locked?'ON AIR':'listening'):(locked?'STOPPED ON':'SCANNING');st.className='hu-stat '+(HU.hold?'hold':(locked?'on':''));
 $('#hu-freq').textContent=locked||mo.active?fmt(mo.mhz)+' MHz'+(uv32(mo.mhz)?'  · '+uv32(mo.mhz).replace('UV-32 ch ','#'):''):(mo.groups?'window '+mo.group+'/'+mo.groups:'—');$('#hu-name').textContent=locked||mo.active?(mo.name||''):(mo.n+' channels · '+(mo.engine==='channelizer'?'all at once per window':'hopping'));
 const last=(mo.heard||[]).slice(-1)[0];$('#hu-sub').textContent=last?'last: '+last.name+' · '+last.when+' · '+last.secs+' s':'';$('#hu-hold').classList.toggle('on',HU.hold);}
async function huCmd(c,q){try{const r=await (await fetch('/scanner/'+c+(q||''),{cache:'no-store'})).json();if(r.error)toast(r.error);else if(c==='hold'||c==='resume'){HU.hold=!!r.hold;}else if(c==='lockout'){toast('locked out - it will not stop there again');huLockouts();}else if(c==='skip')toast('skip');return r;}catch(e){toast(e.message);}}
async function huLockouts(){try{const r=await (await fetch('/api/scanner',{cache:'no-store'})).json();HU.hold=!!r.hold;HU.auto=r.autoscan||'';if(document.activeElement!==$('#hu-auto'))$('#hu-auto').value=HU.auto;if(!r.running&&HU.auto)huPaint(null,{});if(r.delay&&!HU.drag){HU.delay=r.delay;$('#hu-delay').value=r.delay;$('#hu-delayv').textContent=r.delay+' s';}
 $('#hu-lockouts').innerHTML=(r.lockouts||[]).length?'locked out: '+r.lockouts.map(l=>`<button class="ghost" style="padding:2px 8px;font-size:.8em" data-m="${l.mhz}" title="allow the scanner to stop here again">${esc(l.name||fmt(l.mhz))} ✕</button>`).join(' '):'';$$('#hu-lockouts button').forEach(b=>b.onclick=()=>huCmd('unlock','?mhz='+b.dataset.m));}catch(e){}}
$('#hu-hold').onclick=e=>{huCmd(HU.hold?'resume':'hold');e.currentTarget.blur();};$('#hu-skip').onclick=()=>huCmd('skip');$('#hu-lock').onclick=()=>huCmd('lockout');$('#hu-stop').onclick=()=>P.stop();
$('#hu-auto').onchange=async e=>{const r=await (await fetch('/scanner/autoscan?bank='+encodeURIComponent(e.target.value))).json();if(r.error)toast(r.error);else{HU.auto=r.bank||'';toast(r.bank?'auto-scan on: '+r.bank+' - starts 90 s after the player goes idle':'auto-scan off');huLockouts();}};
$('#hu-delay').oninput=e=>{HU.drag=true;$('#hu-delayv').textContent=e.target.value+' s';};$('#hu-delay').onchange=e=>{HU.drag=false;huCmd('delay','?s='+e.target.value);};
setInterval(()=>{if(!document.hidden&&$('#headunit').style.display!=='none')huLockouts();},3000);setInterval(()=>{if(!document.hidden&&$('#headunit').style.display==='none')huLockouts();},10000);huLockouts();
document.addEventListener('keydown',e=>{if(e.target.matches('input,select,textarea,button')||$('#headunit').style.display==='none')return;if(e.key==='s'||e.key==='S'){$('#hu-skip').click();}else if(e.key==='l'||e.key==='L'){$('#hu-lock').click();}});

try{(function(){var p=document.querySelector('.player'),bt=document.querySelector('.player .btns');if(!p||!bt)return;var b=document.createElement('button');b.id='expbtn';b.className='ghost';b.textContent='\u22EF';b.title='volume, squelch, tune, output';b.onclick=function(){p.classList.toggle('exp');b.textContent=p.classList.contains('exp')?'\u2715':'\u22EF';};bt.insertBefore(b,bt.firstChild);})();}catch(e){}
</script></body></html>"""

if __name__ == "__main__":
    subprocess.call(["pkill", "-x", "rtl_fm"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)   # a fresh start owns the dongle (restart_self already waited for silence)
    for _ in range(30):
        if subprocess.call(["pgrep", "-x", "rtl_fm"], stdout=subprocess.DEVNULL) != 0: break
        time.sleep(0.2)
    time.sleep(1.5); v1.give_dongle_back()
    print(f"Dashboard v2: http://localhost:{PORT}   (phone: http://{v1.lan_ip()}:{PORT})   Ctrl-C to stop")
    if sys.platform == "darwin" and not os.environ.get("SDR_RESTARTED"):   # (v185) a cold start opens the dashboard once; a restart of itself does not open another tab
        subprocess.Popen(["open", f"http://localhost:{PORT}"])
    try: ThreadingHTTPServer(("0.0.0.0", PORT), H2).serve_forever()
    finally: v1.stop_player()
