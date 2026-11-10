#!/usr/bin/env python3
"""Overnight report: what the scanner did while Matt slept - one page in diag/overnight_report.txt (and printed).
Reads events.log, rtl_fm.log (monitor: / follow: lines), recordings + transcripts, the scan folders (rows per band, hits, engine), SESSIONS.log, diag/selftest.txt."""
import os, sys, glob, time, re, collections, datetime as dt
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); os.chdir(HERE); sys.path.insert(0, HERE)
since = time.time() - float(sys.argv[1]) * 3600 if len(sys.argv) > 1 else time.time() - 8 * 3600
out = []; P = out.append
P(f"SDR SCANNER - OVERNIGHT REPORT  {dt.datetime.now():%Y-%m-%d %H:%M}   (window: last {(time.time()-since)/3600:.1f} h)")
P("=" * 96)
def mt(p):
    try: return os.path.getmtime(p)
    except OSError: return 0
# ---- events
ev = [l.rstrip() for l in open("events.log", errors="ignore")] if os.path.exists("events.log") else []
def ev_t(l):
    try: return dt.datetime.strptime(f"{dt.date.today().year}-{l[:14]}", "%Y-%m-%d %H:%M:%S").timestamp()
    except Exception: return 0
evw = [l for l in ev if ev_t(l) >= since - 86400 * 0.02]
cnt = collections.Counter()
for l in evw:
    body = l[16:]
    for key, pat in (("restarts", "restarting the dashboard"), ("crash restarts", "dashboard exited"), ("sweeps started", "sweep: starting"), ("listens", "listening on"),
                     ("monitor sessions", "monitor Monitor"), ("SDRTrunk starts", "SDRTrunk starting"), ("dongle not responding", "dongle not responding")):
        if pat in body: cnt[key] += 1
P("EVENTS: " + ", ".join(f"{k} {v}" for k, v in cnt.items()) if cnt else "EVENTS: none in window")
crash = [l for l in evw if "dashboard exited" in l]
if crash: P(f"  !! crash-restart lines: {len(crash)} (last: {crash[-1]})")
# ---- monitor
mon = [l.strip() for l in open("rtl_fm.log", errors="ignore") if l.startswith("monitor:") or l.startswith("follow:")] if os.path.exists("rtl_fm.log") else []
lock = [l for l in mon if "locked out" in l]; fol = [l for l in mon if l.startswith("follow:")]
P(f"MONITOR: {len(lock)} continuous-carrier lockouts, {len(fol)} follow-traffic retunes logged")
for l in lock[-6:]: P("  " + l[:110])
# ---- recordings + transcripts
recs = [f for f in glob.glob("scan_*/recordings/*.wav") if mt(f) >= since]
byc = collections.Counter(os.path.basename(f).split("_2026")[0] for f in recs)
P(f"RECORDINGS: {len(recs)} clips since window start; by channel: " + ", ".join(f"{k} x{v}" for k, v in byc.most_common(8)))
txt = []
for f in sorted(recs, key=mt)[-200:]:
    t = f[:-4] + ".txt"
    if os.path.exists(t):
        s = open(t, errors="ignore").read().strip()
        if len(s) > 12: txt.append((os.path.basename(f).split("_2026")[0], dt.datetime.fromtimestamp(mt(f)).strftime("%H:%M"), s[:120]))
P(f"TRANSCRIPTS with speech: {len(txt)} of {len(recs)} clips")
for c, t, s in txt[-10:]: P(f"  {t} {c}: {s}")
# ---- sweeps
P("SWEEPS:")
try:
    import sdr_scan
    for d in sorted(glob.glob("scan_*"), key=mt)[-6:]:
        if mt(d) < since - 3600: continue
        log = os.path.join(d, "scan.log"); eng = collections.Counter(re.findall(r"\[(fastscan|rtl_power)\]", open(log, errors="ignore").read())) if os.path.exists(log) else {}
        line = f"  {d}: engine " + (", ".join(f"{k} x{v}" for k, v in eng.items()) or "?")
        for k, (name, lo, hi, mode, r) in sdr_scan.BANDS.items():
            p = os.path.join(d, f"power_{k}.csv")
            if not os.path.exists(p): continue
            rows = sum(1 for _ in open(p, errors="ignore"))
            try: hs = sdr_scan.find_hits(sdr_scan.parse_power(p), mode, r, 10.0, 0.5); inter = sum(1 for h in hs if h["active_pct"] < 98)
            except Exception as e: hs = []; inter = f"err {e}"
            line += f" | {k}: {rows} rows, {len(hs)} hits ({inter} intermittent)"
        P(line)
        bad = [l.strip() for l in open(log, errors="ignore") if "dropped" in l or "failed" in l or "still busy" in l] if os.path.exists(log) else []
        if bad: P(f"      retries: {len(bad)} (last: {bad[-1][:90]})")
except Exception as e: P(f"  (sweep stats unavailable: {e})")
# ---- self test + sessions
if os.path.exists("diag/selftest.txt"):
    st = open("diag/selftest.txt", errors="ignore").read(); P(f"SELF TEST ({dt.datetime.fromtimestamp(mt('diag/selftest.txt')):%H:%M}): {st.count('PASS')} PASS / {st.count('FAIL')} FAIL" + ("".join("\n  " + l for l in st.splitlines() if l.startswith("FAIL"))))
if os.path.exists("SESSIONS.log"):
    P("SESSIONS.log (last 8):"); [P("  " + l.rstrip()[:120]) for l in open("SESSIONS.log", errors="ignore").readlines()[-8:]]
rep = "\n".join(out); os.makedirs("diag", exist_ok=True); open("diag/overnight_report.txt", "w").write(rep + "\n"); print(rep)
