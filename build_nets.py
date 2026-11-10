#!/usr/bin/env python3
"""Builds 'GTA Nets.html' and 'gta_nets_chirp.csv' from the tables below."""
import csv, datetime as dt

# callsign: (output MHz, duplex, offset, tone, where)
REPEATERS = {
    "VA3GTU":  (145.130, "-", 0.6, "103.5", "Toronto - Toronto ARES"),
    "VA3GTU 4": (442.375, "+", 5.0, "103.5", "Toronto - Toronto ARES UHF"),
    "VE3TOR":  (146.940, "-", 0.6, "103.5", "Scarborough - East Toronto Rptr Group"),
    "VE3TNC":  (147.270, "+", 0.6, "103.5", "Toronto (FM + Fusion)"),
    "VE3SKY":  (146.985, "-", 0.6, "103.5", "Toronto - Skywide ARC (FM + Fusion + EchoLink/AllStar)"),
    "VE3NIB":  (443.500, "+", 5.0, "103.5", "Toronto - CNIB"),
    "VE3NOR":  (443.650, "+", 5.0, "103.5", "Toronto - Nortown ARC"),
    "VE3YYZ":  (443.050, "+", 5.0, "156.7", "Toronto - First Canadian Place"),
    "VE3WOO":  (145.110, "-", 0.6, "82.5",  "Toronto - First Canadian Place"),
    "VE3WOO 70": (443.900, "+", 5.0, "127.3", "Toronto - First Canadian Place"),
    "VA3RCI":  (444.800, "+", 5.0, "100.0", "Toronto - CN Tower (FM + EchoLink) - heard active at night from Mississauga"),
    "VE3OBN":  (145.230, "-", 0.6, "103.5", "Toronto"),
    "VE3MIS":  (145.430, "-", 0.6, "103.5", "Mississauga ARC"),
    "VE3YPL":  (145.450, "-", 0.6, "85.4",  "Mississauga"),
    "VA3PMO":  (444.250, "+", 5.0, "103.5", "Mississauga"),
    "VA3PCS":  (443.8375, "+", 5.0, "103.5", "Mississauga (FM + Fusion/WIRES-X)"),
    "VE3MIS 70": (444.575, "+", 5.0, "", "Mississauga - Fusion DIGITAL ONLY (a Baofeng cannot use it)"),
    "VA3DVN":  (444.7375, "+", 5.0, "", "Toronto - DMR DIGITAL ONLY, private (a Baofeng cannot use it)"),
    "VE3PRC":  (146.880, "-", 0.6, "103.5", "Brampton - Peel ARC"),
    "VE3PRC 70": (443.550, "+", 5.0, "103.5", "Brampton - Peel ARC"),
    "VA3OPR":  (444.950, "+", 5.0, "103.5", "Brampton"),
    "VE3YRA":  (145.350, "-", 0.6, "103.5", "Ballantrae - York Region ARC"),
    "VE3YRC":  (444.225, "+", 5.0, "103.5", "Richmond Hill - York Region ARC"),
    "VE3YRC 2": (147.225, "+", 0.6, "103.5", "Aurora - York Region ARC"),
    "VA3CTR":  (442.275, "+", 5.0, "103.5", "Markham"),
    "VE3RTC":  (146.745, "-", 0.6, "136.5", "Scarborough"),
    "VE3RTC 70": (443.350, "+", 5.0, "131.8", "Scarborough"),
    "VA3TVB":  (443.825, "+", 5.0, "103.5", "Scarborough"),
    "VE3SPA":  (147.375, "+", 0.6, "103.5", "Ajax - South Pickering ARC"),
    "VE3DAX":  (444.600, "+", 5.0, "103.5", "Ajax - South Pickering ARC"),
    "VE3WOM":  (147.150, "+", 0.6, "103.5", "Whitby"),
    "VE3WOM 70": (443.475, "+", 5.0, "103.5", "Whitby"),
    "VA3SUP":  (146.970, "-", 0.6, "156.7", "Whitby"),
    "VE3WOQ":  (444.375, "+", 5.0, "103.5", "Whitby"),
    "VE3OSH":  (147.120, "+", 0.6, "156.7", "Oshawa - North Shore ARC"),
    "VE3OAK":  (444.325, "+", 5.0, "131.8", "Oakville ARES"),
    "VE3OKR":  (442.450, "+", 5.0, "",      "Oakville"),
    "VE3HAL":  (442.300, "+", 5.0, "131.8", "Milton - Halton"),
    "VE3ADT":  (444.125, "+", 5.0, "131.8", "Milton"),
    "VE3URU":  (444.475, "+", 5.0, "103.5", "Milton / Toronto"),
    "VE3RSB":  (147.210, "+", 0.6, "131.8", "Burlington"),
    "VE3RSB 70": (444.825, "+", 5.0, "131.8", "Burlington"),
    "VA3BUR":  (443.200, "+", 5.0, "131.8", "Burlington"),
    "VE3BUR":  (443.150, "+", 5.0, "",      "Burlington"),
    "VE3OD":   (147.135, "+", 0.6, "131.8", "Halton ARC"),
    "VE3SKV":  (146.805, "-", 0.6, "110.9", "Caledon"),
    "VE3BRA":  (442.000, "+", 5.0, "103.5", "Toronto - mixed mode FM + DMR + P25 (digital bursts are muted)"),
    "VA3YYZ":  (444.1625, "+", 5.0, "103.5", "Toronto - mixed mode FM + DMR + Fusion (digital bursts are muted)"),
    "VA3BMI":  (444.450, "+", 5.0, "103.5", "Toronto - mixed mode FM + P25 (digital bursts are muted)"),
    "VE3RAK":  (444.700, "+", 5.0, "103.5", "Toronto"),
    "VA3ATL":  (444.775, "+", 5.0, "110.9", "Toronto downtown - FM + NXDN + EchoLink"),
    "VE3SKI":  (443.100, "+", 5.0, "",      "Toronto"),
    "VA3GTU 2": (442.975, "+", 5.0, "103.5", "Toronto"),
    "VA3GTU 3": (442.650, "+", 5.0, "100.0", "Toronto"),
    "VA3GTU 5": (444.850, "+", 5.0, "103.5", "Toronto"),
    "VA3GTU 6": (443.750, "+", 5.0, "103.5", "Scarborough"),
    "VA3GTU 7": (442.750, "+", 5.0, "103.5", "Toronto (VE3OBN 70cm)"),
}
SIMPLEX = {
    "SMPLX 21": (146.460, "Toronto ARES Simplex 21 (Tue 8 pm) / Regional D-Star"),
    "SMPLX 41": (446.025, "Toronto ARES Simplex 41 (Thu 8 pm), Skywide Sun 9 pm, ARES Sun 8:30 pm"),
    "SKY SMPLX": (146.540, "Skywide Saturday Simplex Net 8 pm"),
    "FVL SMPLX": (147.405, "VA3FVL Thursday Night Simplex Net 8 pm"),
    "TECHNET": (146.550, "TechNet Friday 8-10 pm"),
    "2M CALL": (146.520, "National 2m calling"),
    "70CM CALL": (446.000, "National 70cm calling"),
}

# (day, time, net, repeater key or simplex key, notes)
NETS = [
    ("Daily", "11:00 am", "Toronto ARES Morning Net", "VA3GTU", "Sat on VE3TOR, Sun on VE3TNC"),
    ("Mon-Sat", "5:45 pm", "Toronto ARES Mobile/Base Roundtable", "VA3GTU", ""),
    ("Daily", "8:30 pm", "Richmond Hill Wellness Net", "VE3YRA", "York Region ARC"),
    ("Mon/Wed/Fri", "4:00 pm", "Ragchew Ramble", "VE3YRA", ""),
    ("Monday", "7:30 pm", "Skywide ARC Net", "VE3SKY", "1st and 3rd Mondays"),
    ("Monday", "7:30 pm", "South Pickering ARC", "VE3SPA", ""),
    ("Monday", "7:30 pm", "Oakville ARES", "VE3OAK", "listed on 147.015+ VE3OAK (Fusion/FM) - also try 444.325"),
    ("Monday", "8:00 pm", "Toronto ARES Net", "VA3GTU", ""),
    ("Monday", "8:00 pm", "Hamchat", "VE3SKY", "Skywide"),
    ("Tuesday", "7:00 pm", "CNIB Net", "VE3NIB", ""),
    ("Tuesday", "7:00 pm", "Nostalgia Net", "VE3SKY", "Skywide"),
    ("Tuesday", "8:00 pm", "Toronto ARES Simplex 21", "SMPLX 21", "tone 103.5"),
    ("Tuesday", "8:00 pm", "Nortown Club Net", "VE3NOR", "also Thursday"),
    ("Tuesday", "8:00 pm", "Halton ARC", "VE3OD", ""),
    ("Tuesday", "8:00 pm", "Techlink Conference", "VE3MIS", ""),
    ("Tuesday", "8:30 pm", "Mississauga ARC Net", "VE3MIS", "also 444.575+ (Fusion/FM)"),
    ("Wednesday", "7:30 pm", "North Shore ARC", "VE3OSH", ""),
    ("Wednesday", "8:00 pm", "Toronto ARES Net (UHF)", "VA3GTU 4", ""),
    ("Wednesday", "8:00 pm", "Peel ARC Net", "VE3PRC", ""),
    ("Thursday", "7:30 pm", "Scarborough ARC Net", "VE3TOR", "net control VE3WE"),
    ("Thursday", "8:00 pm", "Toronto ARES Simplex 41", "SMPLX 41", "tone 103.5"),
    ("Thursday", "8:00 pm", "Nortown Club Net", "VE3NOR", ""),
    ("Thursday", "8:00 pm", "Procom Net", "VE3YYZ", "also Sunday"),
    ("Thursday", "8:00 pm", "Thursday Night Simplex Net", "FVL SMPLX", "VA3FVL"),
    ("Thursday", "8:00 pm", "Provincial Emergency Net", "VE3YYZ", "linked, IRLP reflector 9035"),
    ("Friday", "8:00 pm", "Toronto ARES Net", "VA3GTU", ""),
    ("Friday", "8:00 pm", "SkyNet - Skywide weekly net", "VE3SKY", ""),
    ("Friday", "8:00 pm", "TechNet", "TECHNET", "runs to 10 pm, many repeaters"),
    ("Saturday", "11:00 am", "Toronto ARES Information Net", "VE3TOR", ""),
    ("Saturday", "8:00 pm", "Toronto ARES Net", "VE3TOR", ""),
    ("Saturday", "8:00 pm", "Skywide Simplex Net", "SKY SMPLX", "also 446.025"),
    ("Saturday", "9:00 pm", "Science Net", "VE3WOO", "Skywide, linked"),
    ("Sunday", "10:30 am", "Scarborough ARC Net", "VE3TOR", "net control VE3WE"),
    ("Sunday", "11:00 am", "Toronto ARES Information Net", "VE3TNC", ""),
    ("Sunday", "7:30 pm", "South Pickering ARC", "VE3DAX", ""),
    ("Sunday", "8:00 pm", "Procom Net", "VE3YYZ", ""),
    ("Sunday", "8:15 pm", "Mississauga ARES", "VE3MIS", ""),
    ("Sunday", "8:30 pm", "Toronto ARES 440 Simplex Net", "SMPLX 41", ""),
    ("Sunday", "9:00 pm", "Skywide 70cm Simplex Net", "SMPLX 41", ""),
]
DAY_ORDER = ["Daily", "Mon-Sat", "Mon/Wed/Fri", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

def freq_of(key):
    if key in REPEATERS:
        f, d, o, t, w = REPEATERS[key]; return f, d, o, t, w
    f, w = SIMPLEX[key]; return f, "", 0, "", w

def build_chirp(path):
    cols = ["Location","Name","Frequency","Duplex","Offset","Tone","rToneFreq","cToneFreq","DtcsCode","DtcsPolarity","RxDtcsCode","CrossMode","Mode","TStep","Skip","Power","Comment","URCALL","RPT1CALL","RPT2CALL","DVCODE"]
    rows = []
    # net repeaters first, in the order nets use them, then the rest, then simplex
    used = []
    for d, t, n, k, note in NETS:
        if k not in used: used.append(k)
    order = [k for k in used if k in REPEATERS] + [k for k in REPEATERS if k not in used] + list(SIMPLEX)
    for i, k in enumerate(order):
        f, dup, off, tone, where = freq_of(k)
        nets = "; ".join(f"{d} {t} {n}" for d, t, n, kk, _ in NETS if kk == k)
        rows.append([i, k[:10], f"{f:.6f}", dup, f"{off:.6f}", "Tone" if tone else "", tone or "88.5", tone or "88.5", "023", "NN", "023", "Tone->Tone",
                     "NFM", "5.00", "", "10.0W", (where + (" | " + nets if nets else ""))[:200], "", "", "", ""])
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(cols); w.writerows(rows)
    return len(rows)

def build_html(path):
    by_day = {}
    for d, t, n, k, note in NETS: by_day.setdefault(d, []).append((t, n, k, note))
    def tkey(t):
        h, m = t.split()[0].split(":"); h = int(h); m = int(m)
        if "pm" in t and h != 12: h += 12
        if "am" in t and h == 12: h = 0
        return h * 60 + m
    sections = []
    for d in DAY_ORDER:
        if d not in by_day: continue
        rows = ""
        for t, n, k, note in sorted(by_day[d], key=lambda r: tkey(r[0])):
            f, dup, off, tone, where = freq_of(k)
            prog = f"{f:.4f}" + (f" {dup}{off:g}" if dup else " simplex") + (f" / {tone}" if tone else "")
            rows += f"<tr><td class='t'>{t}</td><td><b>{n}</b>{('<br><small>' + note + '</small>') if note else ''}</td><td class='f'>{prog}</td><td>{k.split()[0]}<br><small>{where}</small></td></tr>"
        sections.append(f"<h2>{d}</h2><div class='wrap'><table><thead><tr><th>Time</th><th>Net</th><th>Program (MHz, offset / tone)</th><th>Repeater</th></tr></thead><tbody>{rows}</tbody></table></div>")
    rep_rows = "".join(f"<tr><td class='f'>{v[0]:.4f}</td><td>{v[1]}{v[2]:g}</td><td>{v[3] or '-'}</td><td>{k.split()[0]}</td><td>{v[4]}</td></tr>" for k, v in sorted(REPEATERS.items(), key=lambda kv: kv[1][0]))
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GTA Nets</title>
<style>
:root{{--bg:#0e1116;--card:#171c24;--ink:#e8edf3;--mute:#8b96a5;--acc:#3fb950;--line:#262d38}}
@media (prefers-color-scheme:light){{:root{{--bg:#f5f7fa;--card:#fff;--ink:#101418;--mute:#5c6673;--line:#e1e6ec}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 -apple-system,system-ui,Helvetica,Arial,sans-serif;padding:16px;max-width:1100px;margin:auto}}
h1{{font-size:1.4rem;margin:0 0 4px}}h2{{font-size:1.05rem;margin:22px 0 8px;color:var(--acc)}}.sub{{color:var(--mute);margin-bottom:8px}}
.wrap{{overflow-x:auto;-webkit-overflow-scrolling:touch;border:1px solid var(--line);border-radius:10px;background:var(--card)}}
table{{border-collapse:collapse;width:100%;min-width:640px}}th,td{{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{font-size:.78rem;color:var(--mute);text-transform:uppercase;letter-spacing:.04em}}td.f{{font-family:ui-monospace,Menlo,monospace;font-weight:600;white-space:nowrap}}td.t{{white-space:nowrap}}
small{{color:var(--mute)}}.hint{{padding:12px;border-left:3px solid var(--acc);background:var(--card);border-radius:0 10px 10px 0;margin:14px 0}}
</style></head><body>
<h1>GTA Amateur Radio Nets</h1>
<div class="sub">Built {dt.date.today():%B %d, %Y} - all times Eastern - {len(NETS)} nets, {len(REPEATERS)} repeaters</div>
<div class="hint"><b>Checking in:</b> listen for net control to call for check-ins, then key up with your callsign only, phonetically, and unkey. Net control acknowledges and may ask for name and location. First-timers say so - every net welcomes new check-ins. Short transmissions; the repeater has a time-out timer. Program from <code>gta_nets_chirp.csv</code> - every row carries the tone and offset so you can transmit, not just listen.</div>
{''.join(sections)}
<h2>All repeaters</h2><div class="wrap"><table><thead><tr><th>Output</th><th>Offset</th><th>Tone</th><th>Call</th><th>Where</th></tr></thead><tbody>{rep_rows}</tbody></table></div>
<p class="sub" style="margin-top:14px">Schedules drift and clubs move repeaters - confirm on air or on the club site before counting on one. Run the SDR scan during net hours to see which of these actually reach the car antenna. Sources: va3ldm local nets list, skywide.ca, ve3we.org, yrarc.org, RepeaterBook.</p>
</body></html>"""
    with open(path, "w") as fh: fh.write(html)

if __name__ == "__main__":
    n = build_chirp("gta_nets_chirp.csv"); build_html("GTA Nets.html")
    print(f"gta_nets_chirp.csv: {n} channels; GTA Nets.html: {len(NETS)} nets")
