#!/usr/bin/env python3
"""Idempotent: makes sure dashboard2.py injects monitor_panel.html when it serves "/". Run by Dashboard.command before every start.
Whole-file rewrites of dashboard2.py drop the hook; this puts it back (and keeps the audio.py / channelizer.py meter() 'chans' fields)."""
import os, re, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKERS = {"hook": "monitor_panel.html", "follow route": "follow_traffic", "follow refresh": "refresh=follow", "channelizer refresh arg": "refresh=None",
           "channelizer lap refresh": "self.refresh()", "am margin": 'carrier_margin = 1.5 if mode == "am"', "am hysteresis": "self.carrier_margin - 0.7", "am floor seed": "med_am",
           "dwell": "    DWELL = ", "list prune": "LIST_CAP", "row cap": "ROWCAP", "more row": 'id="morerow"', "more buttons": "#morebtn", "dongle holders": '"rtl_tcp"))',
           "take_dongle fastscan": 'pkill", "-f", "fastscan.py"]', "sdr fastscan": "fastscan_available", "sdr fastscan cmd": "FASTSCAN_STATE[\"engine\"] = engine", "sdr sigterm": "_term(*_)", "sdr early death": "engine died early",
           "sdr powerfast": "import power_fast", "sdr stats": 'hasattr(bins, "stats")', "spectrum passes": "same lo = same pass", "sdr quick retry": "quick = elapsed < 3", "sdr signal kill": "(p.returncode or 0) > 0", "sdr signal pause": "(p.returncode or 0) < 0", "am hybrid": "zopen = g.open", "am floor creep": "self.open_blocks >= 200", "fm floor creep": "same as AM: creep while open", "am settle": "the floor is learned after that", "fm settle": "no floor from the retune ramp", "settle init": "self.nblk = 0  ", "am settle open": "self.nblk > 5", "flush 5": "# flush the retune transient", "pinned am": "self.open_blocks = 0; self.pinned = True   #", "pinned fm": "self.pinned = not self.gate.open", "pinned init": "self.pinned = False  ", "pinned lockout": "pinned = [ch for ch in self.chans if ch.pinned]", "heard min": "stay out of the heard log", "confirmed flag": "self.confirmed = False           # a new open", "confirmed init": "self.confirmed = True   # confirmed", "am zcr opener": "self.voice_ok or self.am_blocks", "voicemod state": "self.env_hist = []", "voicemod calc": "self.voice_mod = float(h.std()", "voicemod opener": "self.voice_ok or self.am_blocks", "voicemod meter": '"mod": round(ch.voice_mod, 2)', "rec gate": "self.pending.append(out)", "open log": "if self.open_n and OPEN_LOG", "openlog state": "self.pending_raw = []; self.live = b\"\"; self.max_mod", "openlog const": "OPEN_LOG = os.path.join", "live gate": "self.pending_raw.append(gated)", "live close": "self.pending = []; self.pending_raw = []; self.live", "live fm": "self.live = gated", "live sox": "if ch.live: self.sox.stdin.write(ch.live)", "am floor creep init": "self.open_blocks = 0  ", "sox raw": "self.last_raw = gated", "sox raw init": 'self.last_raw = b""', "sox init": "_sox_reader, daemon", "sox none": "        self.sox = None\n        self.groups", "sox write": "self.sox.stdin.write(ch.", "sox reader": "def _sox_reader", "sox stop": "self.sox.stdin.close(); self.sox.terminate()", "am hybrid init": "self.weak_blocks = 0  ", "start_scan fastscan": 'fastscan.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n    time.sleep(1.5)', "channelizer chans": '"chans": chans', "audio chans": '"chans": chans'}
def patch(path, old, new, label):
    """idempotent: skip when the feature is already there (by marker text or the exact new text), else replace the exact anchor"""
    p = os.path.join(HERE, path)
    try: s = open(p, encoding="utf-8").read()
    except OSError: return
    mk = MARKERS.get(label)
    if new.strip() in s or (mk and mk in s): return
    if s.count(old) != 1: print(f"ensure_monitor_hook: {label}: anchor not found in {path} - leaving it"); return
    open(p, "w", encoding="utf-8").write(s.replace(old, new)); print(f"ensure_monitor_hook: {label} re-added to {path}")
patch("dashboard2.py", '        if u.path == "/": return self.send(APP)',
'''        if u.path == "/":
            try: panel = open(os.path.join(HERE, "monitor_panel.html"), encoding="utf-8").read()      # Monitor card lives in its own file
            except OSError: panel = ""
            return self.send(APP.replace("</body>", panel + "</body>", 1) if panel else APP)''', "hook")
patch("channelizer.py", '''        m["monitor"] = {"mhz": self.mhz, "name": self.name, "n": n_all, "idx": (sum(len(c) for _, c in self.groups[:self.gi]) if self.gi >= 0 else 0), "heard": self.heard[-8:],
                        "group": self.gi + 1, "groups": len(self.groups), "load": round(self.load, 2), "engine": "channelizer"}
        return m''', '''        chans = []
        for ch in list(self.chans):                                              # the window as it is right now (reactive UI)
            try:
                g = ch.gate
                chans.append({"mhz": ch.mhz, "name": ch.name, "mode": ch.mode, "level": round(g.level), "open": bool(g.open), "carrier": bool(ch.carrier),
                              "snr": round(ch.pow_db - ch.floor_db, 1) if (ch.floor_db is not None) else None, "data": bool(g.data or g.data_latched),
                              "pow": round(ch.pow_db, 1), "floor": round(ch.floor_db, 1) if ch.floor_db is not None else None, "n": g.n})
            except Exception: pass
        m["monitor"] = {"mhz": self.mhz, "name": self.name, "n": n_all, "idx": (sum(len(c) for _, c in self.groups[:self.gi]) if self.gi >= 0 else 0), "heard": self.heard[-8:],
                        "group": self.gi + 1, "groups": len(self.groups), "load": round(self.load, 2), "engine": "channelizer", "chans": chans,
                        "active": a.mhz if a else None, "center": round((self.center or 0) / 1e6, 4), "blocks": self.blocks, "audio": "sox" if self.sox is not None else "dsp"}
        return m''', "channelizer chans")
patch("audio.py", '''        m["monitor"] = {"mhz": self.mhz, "name": self.name, "n": len(self.channels), "idx": self.idx, "heard": self.heard[-8:]}
        return m''', '''        g = ses.gate if ses else None
        chans = [{"mhz": c[0], "name": c[2], "mode": c[1], "level": round(g.level) if (g and i == self.idx) else 0, "open": bool(g and g.open and i == self.idx), "carrier": False, "snr": None, "data": False}
                 for i, c in enumerate(self.channels)]
        m["monitor"] = {"mhz": self.mhz, "name": self.name, "n": len(self.channels), "idx": self.idx, "heard": self.heard[-8:], "engine": "hop", "chans": chans,
                        "active": self.mhz if (g and g.open) else None, "group": self.idx + 1, "groups": len(self.channels), "load": 0}
        return m''', "audio chans")
# Monitor dwell: 2.5 s per window (0.9 was too fast to catch an AM carrier after the retune flush and squelch settle)
patch("channelizer.py", "    DWELL = 0.9            # seconds on a group with nothing open before hopping on (retune flush 0.3 s + gate settle 0.4 s come first, so this is the floor)",
      "    DWELL = 2.5            # seconds on a group with nothing open before hopping on (5 Nov: 0.9 was too fast - retune flush + gate settle ate most of it)", "dwell")

# ---- Follow traffic (5 Nov): dashboard.py routes cat "Follow traffic" to _helpers/follow_traffic.py and passes a refresh callback;
#      channelizer.py re-reads the list at the start of every lap. AM sensitivity: 1.5 dB open / 0.8 dB hold, floor seeded from the window's quiet channels.
patch("dashboard.py", '    quick = cat.endswith("|quick"); cat = cat.split("|")[0]\n    if quick: minutes, limit = 1440, 80',
      '    quick = cat.endswith("|quick"); cat = cat.split("|")[0]\n    if cat.lower().startswith("follow"):                                             # Follow traffic: channels from live ADS-B (_helpers/follow_traffic.py)\n        sys.path.insert(0, os.path.join(HERE, "_helpers")); import follow_traffic; return follow_traffic.follow_list()\n    if quick: minutes, limit = 1440, 80', "follow route")
patch("dashboard.py", '                return CH.Channelizer(chans, manual, voice, "49.6", record, FM_LOG, on_heard=monitor_heard, trusted=trusted_set(chans))',
      '                follow = (lambda: monitor_list(cat)) if cat.lower().startswith("follow") else None\n                return CH.Channelizer(chans, manual, voice, "49.6", record, FM_LOG, on_heard=monitor_heard, trusted=trusted_set(chans), refresh=follow)', "follow refresh")
patch("channelizer.py", '    def __init__(self, channels, manual=None, voice=True, gain="49.6", record_dir=None, log_path=None, on_heard=None, trusted=()):\n        self.groups = group_channels([c for c in channels if c[0] > 0] or [(146.52, "fm", "146.52")])',
      '    def __init__(self, channels, manual=None, voice=True, gain="49.6", record_dir=None, log_path=None, on_heard=None, trusted=(), refresh=None):\n        self.refresh = refresh                     # Follow traffic: callable returning a new channel list; read at the start of every lap (no audio cut)\n        self.groups = group_channels([c for c in channels if c[0] > 0] or [(146.52, "fm", "146.52")])', "channelizer refresh arg")
patch("channelizer.py", '            while self.alive:\n                self.gi = (self.gi + 1) % len(self.groups)\n                center, chans = self.groups[self.gi]',
      '''            while self.alive:
                if self.refresh and self.gi + 1 >= len(self.groups):                   # end of a lap: re-read the list (planes moved)
                    try:
                        new = group_channels([c for c in self.refresh() if c[0] > 0])
                        if new and [g[1] for g in new] != [g[1] for g in self.groups]:
                            self.groups = new; self.gi = -1
                            if self.log:
                                try: self.log.write(f"follow: {len(new)} windows - " + ", ".join(n for _, cs in new for _, _, n in cs) + "\\n"); self.log.flush()
                                except Exception: pass
                    except Exception: pass
                self.gi = (self.gi + 1) % len(self.groups)
                center, chans = self.groups[self.gi]''', "channelizer lap refresh")
patch("channelizer.py", 'self.floor_db = None; self.pow_db = -99.0; self.am_open = False; self.am_hang = 0; self.carrier_margin = 4.0; self.carrier = False',
      'self.floor_db = None; self.pow_db = -99.0; self.am_open = False; self.am_hang = 0; self.carrier = False\n        # AM (airband): a weak tower/aircraft carrier lifts the envelope power by only ~3 dB here (measured 5 Nov on 118.35); idle jitter is +-0.3 dB, so 1.5 dB is safe\n        self.carrier_margin = 1.5 if mode == "am" else 4.0', "am margin")
patch("channelizer.py", '                self.am_open = snr > self.carrier_margin or (self.am_open and snr > self.carrier_margin - 2.0); self.carrier = self.am_open',
      '                self.am_open = snr > self.carrier_margin or (self.am_open and snr > self.carrier_margin - 0.7); self.carrier = self.am_open', "am hysteresis")
patch("channelizer.py", '''                        med = float(np.median([c.pow_db for c in self.chans]))
                        for c in self.chans:
                            if c.mode != "am": c.carrier = (c.pow_db - med) > c.carrier_margin''', '''                        med = float(np.median([c.pow_db for c in self.chans]))
                        for c in self.chans:
                            if c.mode != "am": c.carrier = (c.pow_db - med) > c.carrier_margin
                        ams = [c for c in self.chans if c.mode == "am"]
                        if len(ams) >= 3:                                              # AM floor = the quiet channels in the window: a transmission already
                            med_am = float(np.median([c.pow_db for c in ams]))        # in progress when we arrive is not learned as "the floor"
                            for c in ams:
                                if c.floor_db is not None and c.floor_db > med_am + 0.5: c.floor_db = med_am''', "am floor seed")

# ---- list size (5 Nov): server prunes the bank to what matters (cap 300) and the page shows 20 rows at a time
patch("dashboard2.py", '''    out.sort(key=lambda r: -(r["heard_ts"] or 0))
    for i, r in enumerate(out, 1):
        if not r["live"]: r["rank"] = 1000 + i
    return out
''', '''    out.sort(key=lambda r: -(r["heard_ts"] or 0))
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
''', "list prune")
patch("dashboard2.py", "function applySort(){const tb=$('#rows');const rows=[...tb.children];rows.sort((a,b)=>{let x=sortK==='rank'?a._h.rank:a._h[sortK],y=sortK==='rank'?b._h.rank:b._h[sortK];if(sortT==='n'){x=+x;y=+y;return (x-y)*sortDir;}return String(x).localeCompare(String(y))*sortDir;});rows.forEach(r=>tb.appendChild(r));",
"""let ROWCAP=20;                                                        // no list longer than 20 rows on screen (phone scrolling); 'Show 20 more' / 'Show all'
function applySort(){const tb=$('#rows');const rows=[...tb.children];rows.sort((a,b)=>{let x=sortK==='rank'?a._h.rank:a._h[sortK],y=sortK==='rank'?b._h.rank:b._h[sortK];if(sortT==='n'){x=+x;y=+y;return (x-y)*sortDir;}return String(x).localeCompare(String(y))*sortDir;});rows.forEach(r=>tb.appendChild(r));
 let shown=0,hidden=0;rows.forEach(r=>{if(r.style.display==='none')return;if(shown>=ROWCAP){r.style.display='none';hidden++;}else shown++;});
 const mr=$('#morerow');if(mr){mr.style.display=hidden?'':'none';$('#morenote').textContent=hidden?shown+' of '+(shown+hidden)+' shown':'';}""", "row cap")
patch("dashboard2.py", '<div class="empty" id="empty" style="display:none">Nothing above the noise floor yet - the first band takes a few minutes.</div>',
'<div class="empty" id="empty" style="display:none">Nothing above the noise floor yet - the first band takes a few minutes.</div><div id="morerow" style="display:none;text-align:center;padding:10px"><button class="ghost" id="morebtn">Show 20 more</button> <button class="ghost" id="allbtn">Show all</button> <span class="sv" id="morenote"></span></div>', "more row")
patch("dashboard2.py", "$('#q').oninput=e=>{query=e.target.value.trim().toLowerCase();renderRows();};",
"$('#q').oninput=e=>{query=e.target.value.trim().toLowerCase();renderRows();};\n$('#morebtn').onclick=()=>{ROWCAP+=20;renderRows();};$('#allbtn').onclick=()=>{ROWCAP=100000;renderRows();};", "more buttons")

# ---- fastscan (5 Nov): the backend must know rtl_tcp / fastscan.py hold the dongle
patch("dashboard.py", '''def dongle_holders():
    return subprocess.call(["pgrep", "-x", "rtl_power"], stdout=subprocess.DEVNULL) == 0 or subprocess.call(["pgrep", "-x", "rtl_fm"], stdout=subprocess.DEVNULL) == 0''',
'''def dongle_holders():
    return any(subprocess.call(["pgrep", "-x", n], stdout=subprocess.DEVNULL) == 0 for n in ("rtl_power", "rtl_fm", "rtl_tcp"))   # rtl_tcp: channelizer monitor / fastscan sweep''', "dongle holders")
patch("dashboard.py", '''        subprocess.call(["pkill", "-x", "rtl_power"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.call(["pkill", "-x", "rtl_fm"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.2)
        if not dongle_holders(): break''', '''        subprocess.call(["pkill", "-x", "rtl_power"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.call(["pkill", "-x", "rtl_fm"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.call(["pkill", "-f", "fastscan.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)     # the sweep's engine (it closes its rtl_tcp)
        time.sleep(0.2)
        if not dongle_holders(): break''', "take_dongle fastscan")
patch("dashboard.py", '''    subprocess.call(["pkill", "-x", "rtl_power"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)''', '''    subprocess.call(["pkill", "-x", "rtl_power"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.call(["pkill", "-f", "fastscan.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)''', "start_scan fastscan")


# ---- sdr_scan.py (5 Nov): fastscan engine, SIGTERM clean stop, early-death retry, power_fast parse + stats
patch("sdr_scan.py", 'def run_rtl_power(band_key, seconds, gain, outdir, bin_khz=None):', '''HERE_DIR = os.path.dirname(os.path.abspath(__file__))
FASTSCAN_STATE = {"fails": 0, "engine": ""}        # two early deaths of fastscan in one scan -> back to rtl_power for the rest of it
def fastscan_available():
    """fastscan needs numpy + scipy + rtl_tcp (the channelizer's requirements); FASTSCAN=0 in the environment or a fastscan_off file forces rtl_power"""
    if os.environ.get("FASTSCAN", "1") == "0" or os.path.exists(os.path.join(HERE_DIR, "fastscan_off")): return False
    try:
        import numpy, scipy  # noqa
        return bool(shutil.which("rtl_tcp")) and os.path.exists(os.path.join(HERE_DIR, "fastscan.py"))
    except Exception: return False
def run_rtl_power(band_key, seconds, gain, outdir, bin_khz=None):''', "sdr fastscan")
patch("sdr_scan.py", '''    cmd = ["rtl_power", "-f", f"{int(lo*1e6)}:{int(hi*1e6)}:{bin_hz}",
           "-i", "10", "-e", f"{int(seconds)}s", "-g", str(gain), "-c", "25%"]
    print(f"  {dt.datetime.now():%H:%M}  {name:<22} {seconds/60:.0f} min", flush=True)''', '''    cmd = ["rtl_power", "-f", f"{int(lo*1e6)}:{int(hi*1e6)}:{bin_hz}",
           "-i", "10", "-e", f"{int(seconds)}s", "-g", str(gain), "-c", "25%"]
    engine = "rtl_power"
    if fastscan_available() and FASTSCAN_STATE["fails"] < 2:                                   # (5 Nov) fastscan: same CSV, full-rate FFT per 2 MHz window, a row a second
        cmd = [sys.executable, os.path.join(HERE_DIR, "fastscan.py"), "-f", f"{int(lo*1e6)}:{int(hi*1e6)}:{bin_hz}", "-e", f"{int(seconds)}s", "-g", str(gain)]
        engine = "fastscan"
    FASTSCAN_STATE["engine"] = engine
    print(f"  {dt.datetime.now():%H:%M}  {name:<22} {seconds/60:.0f} min  [{engine}]", flush=True)''', "sdr fastscan cmd")
patch("sdr_scan.py", 'def cmd_scan(a):\n', 'def _term(*_): raise KeyboardInterrupt\ndef cmd_scan(a):\n    signal.signal(signal.SIGTERM, _term)                                     # pkill from the dashboard: stop the child sweep, write results\n', "sdr sigterm")
patch("sdr_scan.py", '''                    elapsed = time.time() - t_start
                    if os.path.exists(pause_flag):''', '''                    elapsed = time.time() - t_start
                    if p.returncode and not os.path.exists(pause_flag) and elapsed < remaining - 5:
                        bad = True                                                                        # engine died early (dongle busy / USB): retry path
                        if FASTSCAN_STATE["engine"] == "fastscan":
                            try:
                                with open(log, "rb") as fh: fh.seek(max(0, os.path.getsize(log) - 600)); tl = fh.read().decode(errors="ignore")
                            except OSError: tl = ""
                            contention = any(x in tl for x in ("did not start", "dongle busy", "ConnectionReset", "timed out", "Broken pipe", "closed the stream"))
                            if not contention:                                               # losing the dongle to the dashboard is not a fastscan fault
                                FASTSCAN_STATE["fails"] += 1
                                if FASTSCAN_STATE["fails"] >= 2: print("  fastscan failed twice - using rtl_power for the rest of this scan", flush=True)
                    if os.path.exists(pause_flag):''', "sdr early death")
patch("sdr_scan.py", '''def parse_power(path):
    """Returns dict bin_hz -> list of (hour, dB) readings over time."""
    bins = defaultdict(list); centers = set()''', '''def parse_power(path):
    """Returns dict-like bin_hz -> list of (hour, dB) readings over time. (5 Nov) power_fast.py: incremental + numpy when available."""
    try:
        import power_fast
        pd = power_fast.parse_power(path); HOP_CENTERS[path] = pd.centers; return pd
    except ImportError: pass
    return parse_power_slow(path)
def parse_power_slow(path):
    bins = defaultdict(list); centers = set()''', "sdr powerfast")
patch("sdr_scan.py", '''    freqs = sorted(bins)
    mx  = [max(d for _, d in bins[f]) for f in freqs]
    med = [statistics.median(d for _, d in bins[f]) for f in freqs]
    # rolling noise floor: median of the median, over a 2 MHz window
    floor = []
    half = max(3, int(1_000_000 / (freqs[1] - freqs[0])) if len(freqs) > 1 else 3)
    for i in range(len(freqs)):
        seg = med[max(0, i-half): i+half+1]
        floor.append(statistics.median(seg))''', '''    if hasattr(bins, "stats"):
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
            floor.append(statistics.median(seg))''', "sdr stats")
patch("dashboard2.py", "        if last_lo is not None and row[1] <= last_lo: passes.append(cur); cur = []",
      "        if last_lo is not None and row[1] < last_lo: passes.append(cur); cur = []       # (fastscan writes several rows per window: same lo = same pass)", "spectrum passes")

# ---- AM hybrid squelch (5 Nov 01:05): weak carriers need voice evidence from the ZCR gate after 1.5 s
patch("channelizer.py", '''        if self.mode == "am" and g.manual is None:
            # carrier squelch decides for AM; the ZCR gate only vetoes obvious data/digital
            g.open = self.am_open and g.n > AU.SETTLE                 # airband is analog by definition: no digital veto''', '''        if self.mode == "am" and g.manual is None:
            # carrier squelch decides for AM. A strong carrier (>= 3 dB over the floor) opens on its own; a weak one (1.5-3 dB,
            # the 5 Nov threshold) also needs the audio noise-squelch to hear voice-like audio, so a noisy channel cannot pin the hop
            zopen = g.open                                             # the ZCR gate's own verdict on this block's audio
            strong = (self.pow_db - (self.floor_db if self.floor_db is not None else self.pow_db)) >= 3.0
            g.open = self.am_open and g.n > AU.SETTLE and (strong or zopen or self.weak_blocks < 15)   # weak: 1.5 s of grace, then voice evidence
            self.weak_blocks = 0 if (not self.am_open or strong or zopen) else self.weak_blocks + 1
            # airband is analog by definition: no digital veto''', "am hybrid")
patch("channelizer.py", "        self.carrier_margin = 1.5 if mode == \"am\" else 4.0", "        self.carrier_margin = 1.5 if mode == \"am\" else 4.0\n        self.weak_blocks = 0                       # consecutive blocks open on a weak carrier without voice evidence", "am hybrid init")

# ---- Monitor live audio through the same sox chain as a direct listen (5 Nov 01:10)
patch("channelizer.py", '''            out = y.astype(np.int16).tobytes()
            if self.rec: self.rec.feed(out, True)
            return out, True''', '''            out = y.astype(np.int16).tobytes()
            self.last_raw = gated                                        # the un-filtered squelched audio: the live path sends THIS through sox (same chain as a direct listen)
            if self.rec: self.rec.feed(out, True)
            return out, True''', "sox raw")
patch("channelizer.py", "        self.rec = AU.Recorder(record_dir, mhz, name) if record_dir else None", "        self.rec = AU.Recorder(record_dir, mhz, name) if record_dir else None\n        self.last_raw = b\"\"", "sox raw init")
patch("channelizer.py", '''        self.refresh = refresh                     # Follow traffic: callable returning a new channel list; read at the start of every lap (no audio cut)''', '''        self.refresh = refresh                     # Follow traffic: callable returning a new channel list; read at the start of every lap (no audio cut)
        self.sox = None''', "sox none")
patch("channelizer.py", '''        self.tcp = None; self.blocks = 0; self.load = 0.0
        self.t = threading.Thread(target=self._run, daemon=True); self.t.start()''', '''        self.tcp = None; self.blocks = 0; self.load = 0.0
        # (5 Nov) the live audio goes through the SAME sox chain as a direct listen (voice band-pass + compand, 100 ms look-ahead):
        # Monitor sounds exactly like pressing play on the channel. Without sox the old scipy chain is used. (started last: the reader needs qcond/alive)
        try:
            self.sox = subprocess.Popen(AU.sox_cmd("fm", voice), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            threading.Thread(target=self._sox_reader, daemon=True).start()
        except Exception: self.sox = None
        self.t = threading.Thread(target=self._run, daemon=True); self.t.start()''', "sox init")
patch("channelizer.py", '''                        lvl, ch, out = outs[0]
                        self.active = ch; self.mhz, self.mode, self.name = ch.mhz, ch.mode, ch.name
                        with self.qcond:
                            self.q.append(out); self.q = self.q[-40:]; self.qcond.notify_all()''', '''                        lvl, ch, out = outs[0]
                        self.active = ch; self.mhz, self.mode, self.name = ch.mhz, ch.mode, ch.name
                        if self.sox is not None:
                            try: self.sox.stdin.write(ch.last_raw); self.sox.stdin.flush()
                            except Exception: self.sox = None                     # sox died: fall back to the in-process chain
                        if self.sox is None:
                            with self.qcond:
                                self.q.append(out); self.q = self.q[-40:]; self.qcond.notify_all()''', "sox write")
patch("channelizer.py", '''    def read(self, n=4800):
        with self.qcond:''', '''    def _sox_reader(self):
        try:
            while self.alive and self.sox is not None:
                b = self.sox.stdout.read(4800)
                if not b: break
                with self.qcond:
                    self.q.append(b); self.q = self.q[-40:]; self.qcond.notify_all()
        except Exception: pass
    def read(self, n=4800):
        with self.qcond:''', "sox reader")
patch("channelizer.py", '''        if self.tcp: self.tcp.close()
        for ch in self.chans:
            try:
                if ch.rec: ch.rec.close()
            except Exception: pass
    def meter(self):''', '''        if self.tcp: self.tcp.close()
        if self.sox is not None:
            try: self.sox.stdin.close(); self.sox.terminate()
            except Exception: pass
        for ch in self.chans:
            try:
                if ch.rec: ch.rec.close()
            except Exception: pass
    def meter(self):''', "sox stop")
patch("sdr_scan.py", '''                        print(f"  dongle dropped off USB during {k} - waiting 8 s and retrying ({attempts}/4)", flush=True)
                        subprocess.call(["pkill", "-x", "rtl_power"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        time.sleep(8)''', '''                        quick = elapsed < 3                                      # died at once = the dongle was still held by the player that just stopped: short wait
                        print(f"  dongle {'still busy' if quick else 'dropped off USB'} during {k} - waiting {2 if quick else 8} s and retrying ({attempts}/4)", flush=True)
                        subprocess.call(["pkill", "-x", "rtl_power"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        time.sleep(2 if quick else 8)''', "sdr quick retry")

# ---- AM floor keeps learning while open; 20 s continuous open = re-learn (5 Nov 01:27)
patch("channelizer.py", '''                if self.floor_db is None: self.floor_db = self.pow_db
                elif not self.am_open: self.floor_db = min(self.floor_db + 0.05, 0.9 * self.floor_db + 0.1 * self.pow_db)   # track the floor slowly upward, quickly downward
                snr = self.pow_db - self.floor_db''', '''                if self.floor_db is None: self.floor_db = self.pow_db
                elif not self.am_open: self.floor_db = min(self.floor_db + 0.05, 0.9 * self.floor_db + 0.1 * self.pow_db)   # track the floor slowly upward, quickly downward
                else:
                    # (5 Nov) while open the floor still creeps up 0.02 dB/block (a 5 s call loses 1 dB), and after 20 s of continuous "open"
                    # the level IS the floor (a tuner settle step or a continuous carrier had frozen the old one and pinned the window)
                    self.floor_db = min(self.floor_db + 0.02, self.pow_db); self.open_blocks += 1
                    if self.open_blocks >= 200: self.floor_db = self.pow_db; self.open_blocks = 0
                if not self.am_open: self.open_blocks = 0
                snr = self.pow_db - self.floor_db''', "am floor creep")
patch("channelizer.py", "        self.weak_blocks = 0                       # consecutive blocks open on a weak carrier without voice evidence", "        self.weak_blocks = 0                       # consecutive blocks open on a weak carrier without voice evidence\n        self.open_blocks = 0                       # consecutive blocks the AM carrier squelch has been open", "am floor creep init")
patch("channelizer.py", '''                if self.floor_db is None: self.floor_db = self.pow_db
                elif not self.carrier: self.floor_db = min(self.floor_db + 0.05, 0.9 * self.floor_db + 0.1 * self.pow_db)
                self.carrier = (self.pow_db - self.floor_db) > (self.carrier_margin if not self.carrier else self.carrier_margin - 2.0)''', '''                if self.floor_db is None: self.floor_db = self.pow_db
                elif not self.carrier: self.floor_db = min(self.floor_db + 0.05, 0.9 * self.floor_db + 0.1 * self.pow_db)
                else:                                                            # (5 Nov) same as AM: creep while open, re-learn after 20 s continuous
                    self.floor_db = min(self.floor_db + 0.02, self.pow_db); self.open_blocks += 1
                    if self.open_blocks >= 200: self.floor_db = self.pow_db; self.open_blocks = 0
                if not self.carrier: self.open_blocks = 0
                self.carrier = (self.pow_db - self.floor_db) > (self.carrier_margin if not self.carrier else self.carrier_margin - 2.0)''', "fm floor creep")

# ---- retune settle (5 Nov 01:30): no floor learning / no open for the first 5 blocks of a channel, flush 5 blocks after retune
patch("channelizer.py", '''                if self.floor_db is None: self.floor_db = self.pow_db
                elif not self.am_open: self.floor_db = min(self.floor_db + 0.05, 0.9 * self.floor_db + 0.1 * self.pow_db)   # track the floor slowly upward, quickly downward
                else:''', '''                self.nblk += 1
                if self.nblk <= 5: self.floor_db = self.pow_db                       # (5 Nov) tuner gain ramps for ~0.5 s after a retune: the floor is learned after that, never from the quiet start
                elif not self.am_open: self.floor_db = min(self.floor_db + 0.05, 0.9 * self.floor_db + 0.1 * self.pow_db)   # track the floor slowly upward, quickly downward
                else:''', "am settle")
patch("channelizer.py", '''                if self.floor_db is None: self.floor_db = self.pow_db
                elif not self.carrier: self.floor_db = min(self.floor_db + 0.05, 0.9 * self.floor_db + 0.1 * self.pow_db)
                else:                                                            # (5 Nov) same as AM: creep while open, re-learn after 20 s continuous''', '''                self.nblk += 1
                if self.nblk <= 5: self.floor_db = self.pow_db                       # (5 Nov) see the AM branch: no floor from the retune ramp
                elif not self.carrier: self.floor_db = min(self.floor_db + 0.05, 0.9 * self.floor_db + 0.1 * self.pow_db)
                else:                                                            # (5 Nov) same as AM: creep while open, re-learn after 20 s continuous''', "fm settle")
patch("channelizer.py", "        self.open_blocks = 0                       # consecutive blocks the AM carrier squelch has been open", "        self.open_blocks = 0                       # consecutive blocks the AM carrier squelch has been open\n        self.nblk = 0                              # blocks processed since this channel object was made (retune)", "settle init")
patch("channelizer.py", "                self.am_open = snr > self.carrier_margin or (self.am_open and snr > self.carrier_margin - 0.7); self.carrier = self.am_open", "                self.am_open = self.nblk > 5 and (snr > self.carrier_margin or (self.am_open and snr > self.carrier_margin - 0.7)); self.carrier = self.am_open", "am settle open")
patch("channelizer.py", "                    for _ in range(3): self.tcp.block()                                 # flush the retune transient", "                    for _ in range(5): self.tcp.block()                                 # flush the retune transient (0.5 s: the tuner's gain ramps after a retune)", "flush 5")

# ---- continuous carrier -> lockout (5 Nov 01:35)
patch("channelizer.py", '''                    self.floor_db = min(self.floor_db + 0.02, self.pow_db); self.open_blocks += 1
                    if self.open_blocks >= 200: self.floor_db = self.pow_db; self.open_blocks = 0
                if not self.am_open: self.open_blocks = 0
                snr = self.pow_db - self.floor_db''', '''                    self.floor_db = min(self.floor_db + 0.02, self.pow_db); self.open_blocks += 1
                    if self.open_blocks >= 200: self.floor_db = self.pow_db; self.open_blocks = 0; self.pinned = True   # continuous carrier: not voice
                if not self.am_open: self.open_blocks = 0
                snr = self.pow_db - self.floor_db''', "pinned am")
patch("channelizer.py", '''                    self.floor_db = min(self.floor_db + 0.02, self.pow_db); self.open_blocks += 1
                    if self.open_blocks >= 200: self.floor_db = self.pow_db; self.open_blocks = 0
                if not self.carrier: self.open_blocks = 0''', '''                    self.floor_db = min(self.floor_db + 0.02, self.pow_db); self.open_blocks += 1
                    if self.open_blocks >= 200: self.floor_db = self.pow_db; self.open_blocks = 0; self.pinned = True
                if not self.carrier: self.open_blocks = 0''', "pinned fm")
patch("channelizer.py", "        self.nblk = 0                              # blocks processed since this channel object was made (retune)", "        self.nblk = 0                              # blocks processed since this channel object was made (retune)\n        self.pinned = False                        # set when the carrier never dropped for 20 s: a beacon / ATIS / data, not a conversation", "pinned init")
patch("channelizer.py", '''                    if self.skip_req: self.skip_req = False; break                      # SKIP: hop now, whoever is talking''', '''                    pinned = [ch for ch in self.chans if ch.pinned]                      # (5 Nov) 20 s of continuous carrier = not voice: lock it out, move on
                    for ch in pinned:
                        self.lockout.add(round(ch.mhz, 4)); ch.pinned = False
                        if self.log:
                            try: self.log.write(f"monitor: {ch.name} {ch.mhz:.4f} open 20 s without a break - continuous carrier, locked out for this session\\n"); self.log.flush()
                            except Exception: pass
                        if self.active is ch: self.active = None; last_any = None
                        if ch.rec: ch.rec.close()
                    if pinned and len(self.chans) > len(pinned): self.chans = [c for c in self.chans if c not in pinned]
                    if self.skip_req: self.skip_req = False; break                      # SKIP: hop now, whoever is talking''', "pinned lockout")
patch("channelizer.py", '''                            secs = round(tb - opened.pop(ch.mhz), 1)
                            self.heard.append({"mhz": ch.mhz, "name": ch.name, "when": dt.datetime.now().strftime("%H:%M:%S"), "secs": secs, "tones": ch.tones.summary()})
                            self.heard = self.heard[-50:]''', '''                            secs = round(tb - opened.pop(ch.mhz), 1)
                            if secs >= 0.5:                                                   # (5 Nov) a 0.2 s flicker is not a transmission: keep it out of the heard log
                                self.heard.append({"mhz": ch.mhz, "name": ch.name, "when": dt.datetime.now().strftime("%H:%M:%S"), "secs": secs, "tones": ch.tones.summary()})
                                self.heard = self.heard[-50:]''', "heard min")
patch("channelizer.py", '''            g.open = self.am_open and g.n > AU.SETTLE and (strong or zopen or self.weak_blocks < 15)   # weak: 1.5 s of grace, then voice evidence
            self.weak_blocks = 0 if (not self.am_open or strong or zopen) else self.weak_blocks + 1''', '''            # (5 Nov 01:50) the audio noise-squelch (the one the clean rtl_fm path uses) is the opener; a strong carrier opens on its own.
            # A weak carrier alone (1.5-3 dB) only locks the hop - overnight it produced noise clips that whisper read as "Thank you".
            g.open = g.n > AU.SETTLE and ((strong and self.am_open) or (zopen and self.am_open))
            self.weak_blocks = 0''', "am zcr opener")

# ---- AM speech detector (5 Nov 02:00): envelope modulation depth decides, carrier only locks the hop
patch("channelizer.py", "        self.pinned = False                        # set when the carrier never dropped for 20 s: a beacon / ATIS / data, not a conversation", '''        self.pinned = False                        # set when the carrier never dropped for 20 s: a beacon / ATIS / data, not a conversation
        self.env_hist = []; self.voice_mod = 0.0; self.voice_ok = False; self.am_blocks = 0   # syllabic modulation detector (AM): speech ~0.5-1.0, steady noise/carrier < 0.15''', "voicemod state")
patch("channelizer.py", '''        raw = np.clip(audio, -32767, 32767).astype(np.int16).tobytes()
        gated = self.gate.process(raw)                                                   # squelch on the raw discriminator audio''', '''        if self.mode == "am":
            fr = 480                                                                     # 20 ms frames at 24 kHz; 1 s history
            seg = audio[:len(audio) - len(audio) % fr]
            if len(seg) >= fr:
                rms = np.sqrt(np.mean(seg.reshape(-1, fr) ** 2, axis=1)); self.env_hist.extend(rms.tolist()); self.env_hist = self.env_hist[-50:]
                h = np.array(self.env_hist); self.voice_mod = float(h.std() / (h.mean() + 1e-9)) if len(h) >= 20 else 0.0
        raw = np.clip(audio, -32767, 32767).astype(np.int16).tobytes()
        gated = self.gate.process(raw)                                                   # squelch on the raw discriminator audio''', "voicemod calc")
patch("channelizer.py", '''            g.open = g.n > AU.SETTLE and ((strong and self.am_open) or (zopen and self.am_open))
            self.weak_blocks = 0''', '''            if self.am_open: self.am_blocks += 1
            else: self.am_blocks = 0; self.voice_ok = False
            if self.am_open and self.voice_mod > 0.3: self.voice_ok = True
            g.open = g.n > AU.SETTLE and self.am_open and (self.voice_ok or self.am_blocks <= 6)
            self.weak_blocks = 0''', "voicemod opener")
patch("channelizer.py", '''                              "pow": round(ch.pow_db, 1), "floor": round(ch.floor_db, 1) if ch.floor_db is not None else None, "n": g.n})''', '''                              "pow": round(ch.pow_db, 1), "floor": round(ch.floor_db, 1) if ch.floor_db is not None else None, "n": g.n, "mod": round(ch.voice_mod, 2), "voice": bool(ch.voice_ok)})''', "voicemod meter")

# ---- recordings wait for the speech verdict; one log line per open (5 Nov 12:40)
patch("channelizer.py", '''            out = y.astype(np.int16).tobytes()
            self.last_raw = gated                                        # the un-filtered squelched audio: the live path sends THIS through sox (same chain as a direct listen)
            if self.rec: self.rec.feed(out, True)
            return out, True''', '''            out = y.astype(np.int16).tobytes()
            self.last_raw = gated                                        # the un-filtered squelched audio: the live path sends THIS through sox (same chain as a direct listen)
            if self.mode == "am" and not self.voice_ok:                  # (5 Nov) grace period: hold the audio back until the speech detector confirms
                self.pending.append(out); self.pending = self.pending[-8:]
            elif self.rec:
                for b in self.pending: self.rec.feed(b, True)
                self.pending = []; self.rec.feed(out, True)
            self.max_mod = max(self.max_mod, self.voice_mod); self.open_n += 1; self.snr_sum += (self.pow_db - (self.floor_db or self.pow_db))
            return out, True''', "rec gate")
patch("channelizer.py", '''        if self.open_since is not None:
            self.secs_open += (self.last_open - self.open_since) + 0.1; self.open_since = None
        if self.rec: self.rec.feed(b"", False)
        return None, False''', '''        if self.open_since is not None:
            self.secs_open += (self.last_open - self.open_since) + 0.1; self.open_since = None
            if self.open_n and OPEN_LOG:                                 # one line per open: tune the speech detector from real data
                try:
                    with open(OPEN_LOG, "a") as f: f.write(f"{dt.datetime.now():%m-%d %H:%M:%S} {self.name} {self.mhz:.4f} {self.mode} {self.open_n/10:.1f}s max_mod {self.max_mod:.2f} mean_snr {self.snr_sum/self.open_n:.1f} voice {'yes' if self.voice_ok else 'no'}\\n")
                except Exception: pass
            self.max_mod = 0.0; self.open_n = 0; self.snr_sum = 0.0
        self.pending = []
        if self.rec: self.rec.feed(b"", False)
        return None, False''', "open log")
patch("channelizer.py", "TCP_PORT = 1234\n", "TCP_PORT = 1234\nOPEN_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), \"monitor_opens.log\")   # one line per squelch open (name, secs, max modulation, mean SNR, voice verdict)\n", "openlog const")

# ---- live audio held during the AM grace period too (5 Nov 12:45)
patch("channelizer.py", '''            if self.mode == "am" and not self.voice_ok:                  # (5 Nov) grace period: hold the audio back until the speech detector confirms
                self.pending.append(out); self.pending = self.pending[-8:]
            elif self.rec:
                for b in self.pending: self.rec.feed(b, True)
                self.pending = []; self.rec.feed(out, True)''', '''            if self.mode == "am" and not self.voice_ok:                  # (5 Nov) grace period: hold the audio back until the speech detector confirms
                self.pending.append(out); self.pending = self.pending[-8:]; self.pending_raw.append(gated); self.pending_raw = self.pending_raw[-8:]
                self.live = b""                                          # nothing to the speakers yet either: no 0.6 s noise burst on a false carrier
            else:
                if self.rec:
                    for b in self.pending: self.rec.feed(b, True)
                    self.rec.feed(out, True)
                self.live = b"".join(self.pending_raw) + gated           # confirmed: the held 0.6 s plays first, then live
                self.pending = []; self.pending_raw = []''', "live gate")
patch("channelizer.py", "        self.pending = []\n        if self.rec: self.rec.feed(b\"\", False)\n        return None, False", "        self.pending = []; self.pending_raw = []; self.live = b\"\"\n        if self.rec: self.rec.feed(b\"\", False)\n        return None, False", "live close")
patch("channelizer.py", '''            self.last_raw = gated                                        # the un-filtered squelched audio: the live path sends THIS through sox (same chain as a direct listen)''', '''            self.last_raw = gated                                        # the un-filtered squelched audio: the live path sends THIS through sox (same chain as a direct listen)
            self.live = gated''', "live fm")
patch("channelizer.py", '''                        if self.sox is not None:
                            try: self.sox.stdin.write(ch.last_raw); self.sox.stdin.flush()
                            except Exception: self.sox = None                     # sox died: fall back to the in-process chain''', '''                        if self.sox is not None:
                            try:
                                if ch.live: self.sox.stdin.write(ch.live); self.sox.stdin.flush()
                            except Exception: self.sox = None                     # sox died: fall back to the in-process chain''', "live sox")
patch("channelizer.py", "        self.env_hist = []; self.voice_mod = 0.0; self.voice_ok = False; self.am_blocks = 0   # syllabic modulation detector (AM): speech ~0.5-1.0, steady noise/carrier < 0.15", "        self.env_hist = []; self.voice_mod = 0.0; self.voice_ok = False; self.am_blocks = 0   # syllabic modulation detector (AM): speech ~0.5-1.0, steady noise/carrier < 0.15\n        self.pending = []; self.pending_raw = []; self.live = b\"\"; self.max_mod = 0.0; self.open_n = 0; self.snr_sum = 0.0; self.confirmed = True   # confirmed: the last AM open carried speech (FM: always)", "openlog state")

# ---- a kill by signal (the dashboard took the dongle) is a pause, not a fastscan failure (5 Nov 13:40)
patch("sdr_scan.py", '''                    if p.returncode and not os.path.exists(pause_flag) and elapsed < remaining - 5:
                        bad = True                                                                        # engine died early (dongle busy / USB): retry path
                        if FASTSCAN_STATE["engine"] == "fastscan":''', '''                    if p.returncode and not os.path.exists(pause_flag) and elapsed < remaining - 5 and (p.returncode or 0) > 0:
                        bad = True                                                                        # engine died early (dongle busy / USB): retry path
                        if FASTSCAN_STATE["engine"] == "fastscan":''', "sdr signal kill")
patch("sdr_scan.py", '''                    if os.path.exists(pause_flag):
                        # the dashboard took the dongle (it may have killed rtl_power itself): that is a pause, not a USB fault
                        bad = False; interrupted = True''', '''                    if os.path.exists(pause_flag) or (p.returncode or 0) < 0:
                        # the dashboard took the dongle (it may have killed rtl_power / fastscan itself): that is a pause, not a USB fault
                        bad = False; interrupted = True''', "sdr signal pause")

# ---- heard log only lists confirmed AM opens (5 Nov 16:35)
patch("channelizer.py", '''            if self.am_open: self.am_blocks += 1
            else: self.am_blocks = 0; self.voice_ok = False
            if self.am_open and self.voice_mod > 0.3: self.voice_ok = True''', '''            if self.am_open: self.am_blocks += 1
            else: self.am_blocks = 0; self.voice_ok = False
            if self.am_open and self.am_blocks == 1: self.confirmed = False           # a new open: nothing proven yet
            if self.am_open and self.voice_mod > 0.3: self.voice_ok = True; self.confirmed = True''', "confirmed flag")
patch("channelizer.py", "        self.pending = []; self.pending_raw = []; self.live = b\"\"; self.max_mod = 0.0; self.open_n = 0; self.snr_sum = 0.0\n", "        self.pending = []; self.pending_raw = []; self.live = b\"\"; self.max_mod = 0.0; self.open_n = 0; self.snr_sum = 0.0; self.confirmed = True   # confirmed: the last AM open carried speech (FM: always)\n", "confirmed init")
patch("channelizer.py", '''                            if secs >= 0.5:                                                   # (5 Nov) a 0.2 s flicker is not a transmission: keep it out of the heard log''', '''                            if secs >= 0.5 and (ch.mode != "am" or ch.confirmed):            # (5 Nov) flickers and refused AM carriers stay out of the heard log''', "heard min")
