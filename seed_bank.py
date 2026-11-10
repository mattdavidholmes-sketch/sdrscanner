#!/usr/bin/env python3
"""One-off: seed bank.json from every scan folder's results.csv (voice candidates only), so the bank starts with history."""
import csv, glob, json, os
HERE = os.path.dirname(os.path.abspath(__file__))
bank = {}
try: bank = json.load(open(os.path.join(HERE, "bank.json")))
except Exception: pass
for d in sorted(glob.glob(os.path.join(HERE, "scan_*"))):
    p = os.path.join(d, "results.csv")
    if not os.path.exists(p): continue
    ts = os.path.getmtime(p)
    for r in csv.DictReader(open(p, newline="", encoding="utf-8")):
        if r.get("spur") == "True" or r.get("carrier") == "True": continue
        try: mhz = round(float(r["mhz"]), 4); snr = float(r["snr_db"]); busy = float(r["active_pct"])
        except (ValueError, KeyError): continue
        if snr < 10 or busy < 1: continue
        if r.get("mode") == "NFM" and busy >= 98: continue
        k = f"{mhz:.4f}"; e = bank.get(k)
        if not e:
            bank[k] = {"mhz": mhz, "name": r.get("name", ""), "service": r.get("service", ""), "mode": r.get("mode", "NFM"), "cat": "", "uv32": r.get("uv32") == "True",
                       "first_heard": ts, "last_heard": ts, "best_snr": snr, "times": 1, "tone": r.get("tone", ""), "ver": ""}
        else:
            if ts > e.get("last_heard", 0): e["last_heard"] = ts; e["times"] = e.get("times", 0) + 1
            e["best_snr"] = max(e.get("best_snr", 0), snr)
json.dump(bank, open(os.path.join(HERE, "bank.json"), "w"))
print(f"bank.json: {len(bank)} channels")
