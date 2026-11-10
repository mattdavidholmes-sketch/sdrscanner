#!/bin/bash
# SDR Scanner self test - no dongle needed. Checks the pieces added 5 Nov (fastscan, power_fast, follow traffic, monitor panel hooks)
# and writes diag/selftest.txt. Double-click, or the dashboard runs it headless via open_request.txt.
cd "$(dirname "$0")/.."; export PATH="/opt/homebrew/bin:$PATH"
python3 - <<'PY' > diag/selftest.txt 2>&1
import os, sys, time, json, subprocess, shutil
sys.path.insert(0, os.getcwd()); sys.path.insert(0, os.path.join(os.getcwd(), "_helpers"))
ok = lambda c, m: print(("PASS " if c else "FAIL ") + m)
print(time.strftime("%Y-%m-%d %H:%M:%S"), sys.version.split()[0], sys.executable)
try: import numpy, scipy; ok(True, f"numpy {numpy.__version__} scipy {scipy.__version__}")
except Exception as e: ok(False, f"numpy/scipy: {e}")
ok(bool(shutil.which("rtl_tcp")), "rtl_tcp on PATH"); ok(bool(shutil.which("rtl_power")), "rtl_power on PATH"); ok(bool(shutil.which("rtl_fm")), "rtl_fm on PATH")
import sdr_scan; ok(sdr_scan.fastscan_available(), "sdr_scan.fastscan_available()")
try: import power_fast; ok(True, "power_fast imports")
except Exception as e: ok(False, f"power_fast: {e}")
import glob
files = sorted(glob.glob("scan_*/power_*.csv"), key=os.path.getmtime)[-3:]
for p in files:
    k = os.path.basename(p)[6:-4]; name, lo, hi, mode, raster = sdr_scan.BANDS.get(k, ("?", 0, 0, "NFM", 2.5))
    t = time.time(); b = sdr_scan.parse_power(p); hs = sdr_scan.find_hits(b, mode, raster, 10.0, 0.5); t1 = time.time() - t
    t = time.time(); b = sdr_scan.parse_power(p); hs = sdr_scan.find_hits(b, mode, raster, 10.0, 0.5); t2 = time.time() - t
    ok(t2 < 0.5, f"{p}: {len(hs)} hits, first {t1:.2f}s, cached {t2:.3f}s ({os.path.getsize(p)//1024} KB)")
try:
    import follow_traffic as FT
    chans = FT.follow_list(); ok(True, f"follow_list -> {len(chans)} channels from {FT._last.get('n_ac')} aircraft: " + ", ".join(n for _, _, n in chans))
except Exception as e: ok(False, f"follow_traffic: {e!r}")
import channelizer as CH; ok(hasattr(CH.Channelizer.__init__, "__code__") and "refresh" in CH.Channelizer.__init__.__code__.co_varnames, "channelizer accepts refresh=")
ok(CH.Channelizer.DWELL >= 1.0, f"channelizer DWELL {CH.Channelizer.DWELL} (v151: flush happens before the dwell clock)")
import audio as AU; ok("chans" in open("audio.py").read(), "audio.Monitor.meter has chans")
for f, needle, label in (("dashboard2.py", "monitor_panel.html", "dashboard2 injects monitor_panel.html"), ("dashboard2.py", "LIST_CAP", "dashboard2 LIST_CAP"), ("dashboard2.py", "ROWCAP", "dashboard2 ROWCAP"),
                         ("dashboard.py", "follow_traffic", "dashboard routes Follow traffic"), ("dashboard.py", '"rtl_tcp"', "dashboard knows rtl_tcp holds the dongle"), ("Dashboard.command", "ensure_monitor_hook", "Dashboard.command runs the self-heal")):
    ok(needle in open(f).read(), label)
ok(os.path.exists("monitor_panel.html"), "monitor_panel.html present")
r = subprocess.run([sys.executable, "_helpers/ensure_monitor_hook.py"], capture_output=True, text=True); ok(r.returncode == 0 and not r.stdout.strip(), "ensure_monitor_hook silent: " + (r.stdout.strip() or "ok"))
try:
    import urllib.request
    j = json.load(urllib.request.urlopen("http://127.0.0.1:8765/api/aircraft", timeout=5)); ok(True, f"/api/aircraft: {j.get('n')} aircraft via {j.get('source')}")
    h = urllib.request.urlopen("http://127.0.0.1:8765/", timeout=5).read().decode(); ok("moncard" in h, "page served with the Monitor card")
    st = json.load(urllib.request.urlopen("http://127.0.0.1:8765/api/state", timeout=5)); ok(len(st.get("hits", [])) <= 300, f"/api/state hits {len(st.get('hits', []))} (cap 300), {st.get('ms')} ms")
except Exception as e: ok(False, f"dashboard http: {e}")
PY
exit 0
